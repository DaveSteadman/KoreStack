# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreDocs filesystem storage layer.
#
# This module preserves the old korefile.py API surface, but the source of truth is now the real
# filesystem rooted at the shared datauser directory. The legacy SQLite database is migrated once
# at startup, then deleted.
# ====================================================================================================

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import zlib
from datetime import datetime, timezone
from pathlib import Path

from KoreCommon.datauser_fs import create_folder as create_datauser_folder
from KoreCommon.datauser_fs import datauser_relative_path
from KoreCommon.datauser_fs import delete_file as delete_datauser_file
from KoreCommon.datauser_fs import ensure_datauser_root
from KoreCommon.datauser_fs import list_datauser_files
from KoreCommon.datauser_fs import list_datauser_folders
from KoreCommon.datauser_fs import normalize_datauser_relative_path
from KoreCommon.datauser_fs import read_text_file
from KoreCommon.datauser_fs import resolve_datauser_directory
from KoreCommon.datauser_fs import resolve_datauser_path
from KoreCommon.datauser_fs import write_text_file


class ConflictError(ValueError):
    pass


_ROOT_DIR: Path | None = None
_LEGACY_DB_PATH: Path | None = None
_NATIVE_EXTENSIONS = frozenset({'.koredoc', '.koresheet', '.korediag'})
_TEXT_EXTENSIONS = frozenset({
    '.csv',
    '.json',
    '.log',
    '.md',
    '.py',
    '.txt',
    '.xml',
    '.yaml',
    '.yml',
})
_VISIBLE_EXTENSIONS = _NATIVE_EXTENSIONS | _TEXT_EXTENSIONS


def configure(root_dir: Path, legacy_db_path: Path | None = None) -> None:
    global _ROOT_DIR, _LEGACY_DB_PATH
    _ROOT_DIR = Path(root_dir).resolve()
    ensure_datauser_root(_ROOT_DIR)
    _LEGACY_DB_PATH = Path(legacy_db_path).resolve() if legacy_db_path else None
    if _LEGACY_DB_PATH is not None:
        _LEGACY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _root_dir() -> Path:
    if _ROOT_DIR is None:
        raise RuntimeError('korefile.configure() has not been called')
    return _ROOT_DIR


def _legacy_db_path() -> Path | None:
    return _LEGACY_DB_PATH


def _normalize_folder_path(path: str | None) -> str:
    raw = str(path or '').strip().replace('\\', '/')
    if not raw or raw == '/':
        return '/'
    if raw.startswith('./'):
        raw = raw[2:]
    raw = raw.lstrip('/')
    normalized = normalize_datauser_relative_path(raw)
    parts = [part for part in normalized.split('/') if part]
    if any(part in ('.', '..') for part in parts):
        raise ValueError('folder_path must not contain . or .. segments')
    return '/' + '/'.join(parts) if parts else '/'


def _relative_posix(path: Path) -> str:
    return datauser_relative_path(path, root_dir=_root_dir())


def _folder_path_to_abs(path: str | None) -> Path:
    normalized = _normalize_folder_path(path)
    if normalized == '/':
        return _root_dir()
    return resolve_datauser_directory(normalized.lstrip('/'), root_dir=_root_dir())


def _folder_abs_to_label(path: Path) -> str:
    rel = _relative_posix(path)
    return '/' + rel if rel else '/'


def _iso_from_ts(timestamp: float | int | None) -> str:
    if not timestamp:
        return ''
    return datetime.fromtimestamp(float(timestamp), timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _stable_id(kind: str, relative_posix: str) -> int:
    if kind == 'folder' and not relative_posix:
        return 1
    digest = hashlib.blake2b(f'{kind}:{relative_posix}'.encode('utf-8'), digest_size=6).digest()
    value = int.from_bytes(digest, 'big')
    return value if value > 1 else value + 2


def _folder_id_for_abs(path: Path) -> int:
    return _stable_id('folder', _relative_posix(path))


def _file_id_for_abs(path: Path) -> int:
    return _stable_id('file', _relative_posix(path))


def _iter_folder_paths() -> list[Path]:
    root = _root_dir()
    folders = [root]
    folders.extend(list_datauser_folders(search_root='', recursive=True, root_dir=root))
    return folders


def _iter_file_paths(root: Path | None = None) -> list[Path]:
    base = root.resolve() if root is not None else _root_dir()
    return list_datauser_files(
        search_root=_relative_posix(base),
        recursive=True,
        allowed_extensions=set(_VISIBLE_EXTENSIONS),
        root_dir=_root_dir(),
    )


def _folder_record(path: Path) -> dict:
    stat = path.stat()
    parent_id = None if path == _root_dir() else _folder_id_for_abs(path.parent)
    return {
        'id': _folder_id_for_abs(path),
        'parent_id': parent_id,
        'name': 'Root' if path == _root_dir() else path.name,
        'path': _folder_abs_to_label(path),
        'revision': int(stat.st_mtime_ns),
        'modified_at': _iso_from_ts(stat.st_mtime),
        'created_at': _iso_from_ts(getattr(stat, 'st_ctime', stat.st_mtime)),
    }


def _decompress_legacy(blob: bytes | None) -> str:
    if not blob:
        return ''
    return zlib.decompress(blob).decode('utf-8')


def _word_count(text: str) -> int:
    return len(text.split())


def _extract_metadata(name: str, content: str) -> dict:
    ext = Path(name).suffix.lstrip('.')
    meta: dict = {}
    if ext == 'koredoc':
        match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
        if match:
            for line in match.group(1).splitlines():
                if ':' in line:
                    key, _, value = line.partition(':')
                    meta[key.strip()] = value.strip()
        if 'title' not in meta:
            heading = re.search(r'^#{1,3}\s+(.+)$', content, re.MULTILINE)
            if heading:
                meta['title'] = heading.group(1).strip()
    elif ext in ('koresheet', 'korediag'):
        try:
            obj = json.loads(content)
        except (TypeError, ValueError):
            obj = None
        if isinstance(obj, dict):
            meta['title'] = ((obj.get('meta') or {}).get('title') or obj.get('title') or '')
    meta.setdefault('title', Path(name).stem)
    return meta


def _validate_simple_name(name: str, *, kind: str, require_extension: bool = False) -> None:
    trimmed = (name or '').strip()
    if not trimmed:
        raise ValueError(f'{kind} name must not be empty')
    if trimmed != name:
        raise ValueError(f'{kind} name must not start or end with whitespace')
    if any(ch in name for ch in ('/', '\\', ':')):
        raise ValueError(f'{kind} name must not contain path separators')
    if name in {'.', '..'}:
        raise ValueError(f'{kind} name is invalid')
    if any(ord(ch) < 32 for ch in name):
        raise ValueError(f'{kind} name must not contain control characters')
    if require_extension and '.' not in name:
        raise ValueError('File name must include an extension')


def _validate_serialized_content(name: str, content: str) -> None:
    ext = Path(name).suffix.lstrip('.')
    if ext == 'koredoc':
        return
    if f'.{ext}' in _TEXT_EXTENSIONS:
        if ext == 'csv':
            if '\x00' in str(content):
                raise ValueError(f'{name} must not contain NUL bytes')
        return
    try:
        obj = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{name} must contain valid JSON') from exc

    if not isinstance(obj, dict):
        raise ValueError(f'{name} must contain a top-level JSON object')

    if ext == 'koresheet':
        required = {'version', 'meta', 'cols', 'rows', 'cells'}
        missing = sorted(required - obj.keys())
        if missing:
            raise ValueError(f'{name} is missing required fields: {", ".join(missing)}')
        if not isinstance(obj.get('meta'), dict):
            raise ValueError(f'{name} field "meta" must be an object')
        if not isinstance(obj.get('cells'), dict):
            raise ValueError(f'{name} field "cells" must be an object')
        if not isinstance(obj.get('cols'), int) or not isinstance(obj.get('rows'), int):
            raise ValueError(f'{name} fields "cols" and "rows" must be integers')
        return

    if ext == 'korediag':
        required = {'koreDiag', 'id', 'title', 'settings', 'nodes', 'edges'}
        missing = sorted(required - obj.keys())
        if missing:
            raise ValueError(f'{name} is missing required fields: {", ".join(missing)}')
        if not isinstance(obj.get('settings'), dict):
            raise ValueError(f'{name} field "settings" must be an object')
        if not isinstance(obj.get('nodes'), list) or not isinstance(obj.get('edges'), list):
            raise ValueError(f'{name} fields "nodes" and "edges" must be arrays')


def validate_serialized_content(name: str, content: str) -> None:
    _validate_serialized_content(name, content)


def _file_record(path: Path, *, include_content: bool) -> dict:
    content = read_text_file(path, root_dir=_root_dir())
    stat = path.stat()
    metadata = _extract_metadata(path.name, content)
    record = {
        'id': _file_id_for_abs(path),
        'folder_id': _folder_id_for_abs(path.parent),
        'folder_path': _folder_abs_to_label(path.parent),
        'path': _relative_posix(path),
        'name': path.name,
        'ext': path.suffix.lstrip('.'),
        'metadata': metadata,
        'word_count': _word_count(content),
        'revision': int(stat.st_mtime_ns),
        'created_at': _iso_from_ts(getattr(stat, 'st_ctime', stat.st_mtime)),
        'modified_at': _iso_from_ts(stat.st_mtime),
    }
    if include_content:
        record['content'] = content
    return record


def _resolve_folder_abs_by_id(folder_id: int) -> Path | None:
    if folder_id == 1:
        return _root_dir()
    for path in _iter_folder_paths():
        if _folder_id_for_abs(path) == folder_id:
            return path
    return None


def _resolve_file_abs_by_id(file_id: int) -> Path | None:
    for path in _iter_file_paths():
        if _file_id_for_abs(path) == file_id:
            return path
    return None


def _search_terms(query: str) -> list[str]:
    terms: list[str] = []
    for match in re.finditer(r'"([^"]+)"|(\S+)', (query or '').strip()):
        phrase, word = match.group(1), match.group(2)
        value = (phrase or word or '').strip().lower()
        if value:
            terms.append(value)
    return terms


def _delete_legacy_db_files() -> None:
    db_path = _legacy_db_path()
    if db_path is None:
        return
    candidates = [db_path, Path(str(db_path) + '-wal'), Path(str(db_path) + '-shm')]
    for candidate in candidates:
        try:
            if candidate.exists():
                candidate.unlink()
        except OSError:
            pass


def _migrate_legacy_db_to_fs() -> dict:
    db_path = _legacy_db_path()
    if db_path is None or not db_path.exists():
        return {'migrated': 0, 'folders': 0}

    imported_files = 0
    imported_folders = 0
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        table_names = {
            row['name']
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if 'folders' not in table_names or 'files' not in table_names:
            _delete_legacy_db_files()
            return {'migrated': 0, 'folders': 0}

        folder_rows = conn.execute('SELECT path FROM folders ORDER BY CASE WHEN path = "/" THEN 0 ELSE LENGTH(path) END, path').fetchall()
        for row in folder_rows:
            folder_abs = _folder_path_to_abs(row['path'])
            create_datauser_folder(folder_abs, root_dir=_root_dir())
            imported_folders += 1

        file_rows = conn.execute(
            'SELECT f.name, f.content, folders.path AS folder_path '
            'FROM files f JOIN folders ON folders.id = f.folder_id '
            'ORDER BY folders.path, f.name'
        ).fetchall()
        for row in file_rows:
            folder_abs = _folder_path_to_abs(row['folder_path'])
            create_datauser_folder(folder_abs, root_dir=_root_dir())
            target = folder_abs / row['name']
            write_text_file(target, _decompress_legacy(row['content']), root_dir=_root_dir())
            imported_files += 1
    finally:
        conn.close()

    _delete_legacy_db_files()
    return {'migrated': imported_files, 'folders': imported_folders}


def init_db() -> None:
    ensure_datauser_root(_root_dir())
    _migrate_legacy_db_to_fs()


def list_folders() -> list[dict]:
    return [_folder_record(path) for path in _iter_folder_paths()]


def get_folder_by_path(path: str) -> dict | None:
    folder_abs = _folder_path_to_abs(path)
    if not folder_abs.exists() or not folder_abs.is_dir():
        return None
    return _folder_record(folder_abs)


def create_folder(name: str, parent_id: int) -> dict:
    _validate_simple_name(name, kind='Folder')
    parent_abs = _resolve_folder_abs_by_id(parent_id)
    if parent_abs is None:
        raise ValueError(f'Parent folder {parent_id} not found')
    target = parent_abs / name
    if target.exists():
        raise ConflictError(f'Folder already exists: {_folder_abs_to_label(target)}')
    create_datauser_folder(target, root_dir=_root_dir())
    return _folder_record(target)


def rename_folder(folder_id: int, new_name: str, *, expected_revision: int | None = None) -> dict:
    _validate_simple_name(new_name, kind='Folder')
    folder_abs = _resolve_folder_abs_by_id(folder_id)
    if folder_abs is None:
        raise ValueError(f'Folder {folder_id} not found')
    if folder_abs == _root_dir():
        raise ValueError('Cannot rename the root folder')
    current_revision = int(folder_abs.stat().st_mtime_ns)
    if expected_revision is not None and current_revision != expected_revision:
        raise ConflictError(f'Folder {folder_id} revision mismatch: expected {expected_revision}, current {current_revision}')
    target = folder_abs.parent / new_name
    if target.exists():
        raise ConflictError(f'Folder already exists: {_folder_abs_to_label(target)}')
    folder_abs.rename(target)
    return _folder_record(target)


def move_folder(folder_id: int, new_parent_id: int, *, expected_revision: int | None = None) -> dict:
    folder_abs = _resolve_folder_abs_by_id(folder_id)
    if folder_abs is None:
        raise ValueError(f'Folder {folder_id} not found')
    if folder_abs == _root_dir():
        raise ValueError('Cannot move the root folder')
    current_revision = int(folder_abs.stat().st_mtime_ns)
    if expected_revision is not None and current_revision != expected_revision:
        raise ConflictError(f'Folder {folder_id} revision mismatch: expected {expected_revision}, current {current_revision}')
    parent_abs = _resolve_folder_abs_by_id(new_parent_id)
    if parent_abs is None:
        raise ValueError(f'Parent folder {new_parent_id} not found')
    if parent_abs == folder_abs or parent_abs.is_relative_to(folder_abs):
        raise ValueError('Cannot move a folder into itself or one of its descendants')
    target = parent_abs / folder_abs.name
    if target.exists():
        raise ConflictError(f'Folder already exists: {_folder_abs_to_label(target)}')
    folder_abs.rename(target)
    return _folder_record(target)


def delete_folder(folder_id: int, *, expected_revision: int | None = None, recursive: bool = False) -> bool:
    folder_abs = _resolve_folder_abs_by_id(folder_id)
    if folder_abs is None:
        return False
    if folder_abs == _root_dir():
        raise ValueError('Cannot delete the root folder')
    current_revision = int(folder_abs.stat().st_mtime_ns)
    if expected_revision is not None and current_revision != expected_revision:
        raise ConflictError(f'Folder {folder_id} revision mismatch: expected {expected_revision}, current {current_revision}')
    if recursive:
        shutil.rmtree(folder_abs)
        return True
    folder_rel = _relative_posix(folder_abs)
    if list_datauser_files(search_root=folder_rel, recursive=False, root_dir=_root_dir()) or list_datauser_folders(search_root=folder_rel, recursive=False, root_dir=_root_dir()):
        raise ValueError('Folder is not empty')
    folder_abs.rmdir()
    return True


def list_files(
    folder_id: int | None = None,
    folder_path: str | None = None,
    ext: str | None = None,
    name: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    if folder_path is not None:
        folder_abs = _folder_path_to_abs(folder_path)
        if not folder_abs.exists() or not folder_abs.is_dir():
            return []
    elif folder_id is not None:
        folder_abs = _resolve_folder_abs_by_id(folder_id)
        if folder_abs is None:
            return []
    else:
        folder_abs = None

    files = _iter_file_paths(folder_abs)
    results: list[dict] = []
    for path in files:
        if folder_abs is not None and path.parent != folder_abs:
            continue
        if ext is not None and path.suffix.lstrip('.') != ext:
            continue
        if name is not None and path.name != name:
            continue
        results.append(_file_record(path, include_content=False))
        if limit is not None and len(results) >= limit:
            break
    return results


def get_file(file_id: int, include_content: bool = True) -> dict | None:
    file_abs = _resolve_file_abs_by_id(file_id)
    if file_abs is None:
        return None
    return _file_record(file_abs, include_content=include_content)


def create_file(folder_id: int, name: str, content: str, metadata: dict | None = None) -> dict:
    del metadata
    _validate_simple_name(name, kind='File', require_extension=True)
    _validate_serialized_content(name, content)
    folder_abs = _resolve_folder_abs_by_id(folder_id)
    if folder_abs is None:
        raise ValueError(f'Folder {folder_id} not found')
    target = folder_abs / name
    if target.exists():
        raise ConflictError('UNIQUE constraint failed: files.folder_id, files.name')
    write_text_file(target, content, root_dir=_root_dir())
    return _file_record(target, include_content=False)


def create_serialized_file(
    folder_path: str,
    name: str,
    ext: str,
    content: str,
    metadata: dict | None = None,
) -> dict:
    del metadata
    normalized_name = name if name.endswith(f'.{ext}') else f'{name}.{ext}'
    _validate_simple_name(normalized_name, kind='File', require_extension=True)
    _validate_serialized_content(normalized_name, content)
    folder = get_folder_by_path(folder_path)
    if folder is None:
        parent_id = 1
        current_path = '/'
        for part in [p for p in _normalize_folder_path(folder_path).split('/') if p]:
            current_path = current_path.rstrip('/') + '/' + part
            existing = get_folder_by_path(current_path)
            if existing is not None:
                parent_id = existing['id']
                continue
            created = create_folder(part, parent_id)
            parent_id = created['id']
        folder_id = parent_id
    else:
        folder_id = folder['id']
    return create_file(folder_id, normalized_name, content)


def update_file(
    file_id: int,
    content: str | None = None,
    metadata: dict | None = None,
    expected_revision: int | None = None,
) -> dict | None:
    del metadata
    file_abs = _resolve_file_abs_by_id(file_id)
    if file_abs is None:
        return None
    current_revision = int(file_abs.stat().st_mtime_ns)
    if expected_revision is not None and current_revision != expected_revision:
        raise ConflictError(f'File {file_id} revision mismatch: expected {expected_revision}, current {current_revision}')
    current_content = read_text_file(file_abs, root_dir=_root_dir())
    new_content = current_content if content is None else content
    _validate_serialized_content(file_abs.name, new_content)
    write_text_file(file_abs, new_content, root_dir=_root_dir())
    return _file_record(file_abs, include_content=False)


def rename_file(file_id: int, new_name: str, expected_revision: int | None = None) -> dict | None:
    _validate_simple_name(new_name, kind='File', require_extension=True)
    file_abs = _resolve_file_abs_by_id(file_id)
    if file_abs is None:
        return None
    current_revision = int(file_abs.stat().st_mtime_ns)
    if expected_revision is not None and current_revision != expected_revision:
        raise ConflictError(f'File {file_id} revision mismatch: expected {expected_revision}, current {current_revision}')
    content = read_text_file(file_abs, root_dir=_root_dir())
    _validate_serialized_content(new_name, content)
    target = file_abs.with_name(new_name)
    if target.exists():
        raise ConflictError('UNIQUE constraint failed: files.folder_id, files.name')
    file_abs.rename(target)
    return _file_record(target, include_content=False)


def move_file(file_id: int, new_folder_id: int, expected_revision: int | None = None) -> dict | None:
    file_abs = _resolve_file_abs_by_id(file_id)
    if file_abs is None:
        return None
    current_revision = int(file_abs.stat().st_mtime_ns)
    if expected_revision is not None and current_revision != expected_revision:
        raise ConflictError(f'File {file_id} revision mismatch: expected {expected_revision}, current {current_revision}')
    folder_abs = _resolve_folder_abs_by_id(new_folder_id)
    if folder_abs is None:
        raise ValueError(f'Folder {new_folder_id} not found')
    target = folder_abs / file_abs.name
    if target.exists():
        raise ConflictError('UNIQUE constraint failed: files.folder_id, files.name')
    file_abs.rename(target)
    return _file_record(target, include_content=False)


def delete_file(file_id: int, expected_revision: int | None = None) -> bool:
    file_abs = _resolve_file_abs_by_id(file_id)
    if file_abs is None:
        return False
    current_revision = int(file_abs.stat().st_mtime_ns)
    if expected_revision is not None and current_revision != expected_revision:
        raise ConflictError(f'File {file_id} revision mismatch: expected {expected_revision}, current {current_revision}')
    delete_datauser_file(file_abs, root_dir=_root_dir())
    return True


def search(query: str, ext: str | None = None, folder_path: str | None = None, limit: int = 20) -> list[dict]:
    terms = _search_terms(query)
    if not terms:
        return []
    base_folder = _folder_path_to_abs(folder_path) if folder_path else _root_dir()
    if not base_folder.exists() or not base_folder.is_dir():
        return []

    scored: list[tuple[float, dict]] = []
    for path in _iter_file_paths(base_folder):
        if ext is not None and path.suffix.lstrip('.') != ext:
            continue
        content = read_text_file(path, root_dir=_root_dir())
        metadata = _extract_metadata(path.name, content)
        name_lower = path.name.lower()
        metadata_text = json.dumps(metadata, ensure_ascii=False).lower()
        content_lower = content.lower()

        score = 0.0
        matched_all = True
        for term in terms:
            if term not in name_lower and term not in metadata_text and term not in content_lower:
                matched_all = False
                break
            score += name_lower.count(term) * 6.0
            score += metadata_text.count(term) * 3.0
            score += content_lower.count(term) * 1.0
        if not matched_all:
            continue

        record = _file_record(path, include_content=False)
        record['score'] = round(score, 3)
        scored.append((score, record))

    scored.sort(key=lambda item: (-item[0], item[1]['path']))
    return [record for _, record in scored[:limit]]


def import_from_fs(data_dir: Path) -> dict:
    source_root = Path(data_dir).resolve()
    if source_root == _root_dir():
        count = len(_iter_file_paths(source_root))
        return {'imported': 0, 'skipped': count, 'errors': 0, 'error_details': []}

    imported = 0
    skipped = 0
    errors = 0
    error_details: list[dict] = []
    for path in sorted(source_root.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in _VISIBLE_EXTENSIONS:
            continue
        rel = path.relative_to(source_root)
        target = resolve_datauser_path(rel.as_posix(), root_dir=_root_dir())
        try:
            content = path.read_text(encoding='utf-8')
            _validate_simple_name(target.name, kind='File', require_extension=True)
            _validate_serialized_content(target.name, content)
            if target.exists():
                skipped += 1
                continue
            write_text_file(target, content, overwrite=False, root_dir=_root_dir())
            imported += 1
        except Exception as exc:
            errors += 1
            error_details.append({'file': rel.as_posix(), 'error': str(exc)})
    return {'imported': imported, 'skipped': skipped, 'errors': errors, 'error_details': error_details}
