# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreDocs filesystem storage layer.
#
# This module preserves the old korefile.py API surface, but the source of truth is now the real
# filesystem rooted at the shared datauser directory. The legacy SQLite database is migrated once
# at startup, then deleted.
# MARK: FUNCTIONS
# Primary types: ConflictError.
# Function inventory:
# - configure: Implements the configure operation for this module.
# - _root_dir: Implements the  root dir operation for this module.
# - _legacy_db_path: Implements the  legacy db path operation for this module.
# - _normalize_folder_path: Implements the  normalize folder path operation for this module.
# - _relative_posix: Implements the  relative posix operation for this module.
# - _metadata_store_path: Implements the  metadata store path operation for this module.
# - _metadata_sidecar_path: Implements the  metadata sidecar path operation for this module.
# - _history_directory: Implements the  history directory operation for this module.
# - _metadata_key: Implements the  metadata key operation for this module.
# - _validate_metadata: Implements the  validate metadata operation for this module.
# - _merge_metadata: Implements the  merge metadata operation for this module.
# - _load_metadata_store: Implements the  load metadata store operation for this module.
# - _load_sidecar: Implements the  load sidecar operation for this module.
# - _save_sidecar: Implements the  save sidecar operation for this module.
# - _split_koredoc_json_header: Implements the  split koredoc json header operation for this module.
# - _koredoc_content_with_header: Implements the  koredoc content with header operation for this module.
# - _artifact_record: Implements the  artifact record operation for this module.
# - _write_artifact_record: Implements the  write artifact record operation for this module.
# - _now_iso: Implements the  now iso operation for this module.
# - _save_metadata_store: Implements the  save metadata store operation for this module.
# - _stored_metadata: Implements the  stored metadata operation for this module.
# - _set_stored_metadata: Implements the  set stored metadata operation for this module.
# - _delete_stored_metadata: Implements the  delete stored metadata operation for this module.
# - _move_stored_metadata: Implements the  move stored metadata operation for this module.
# - _move_stored_metadata_tree: Implements the  move stored metadata tree operation for this module.
# - _delete_stored_metadata_tree: Implements the  delete stored metadata tree operation for this module.
# - _write_history: Implements the  write history operation for this module.
# - list_history: Lists history for this module.
# - get_history_revision: Returns history revision for this module.
# - _folder_path_to_abs: Implements the  folder path to abs operation for this module.
# - _folder_abs_to_label: Implements the  folder abs to label operation for this module.
# - _iso_from_ts: Implements the  iso from ts operation for this module.
# - _stable_id: Implements the  stable id operation for this module.
# - _folder_id_for_abs: Implements the  folder id for abs operation for this module.
# - _file_id_for_abs: Implements the  file id for abs operation for this module.
# - _iter_folder_paths: Implements the  iter folder paths operation for this module.
# - _iter_file_paths: Implements the  iter file paths operation for this module.
# - _folder_record: Implements the  folder record operation for this module.
# - _decompress_legacy: Implements the  decompress legacy operation for this module.
# - _word_count: Implements the  word count operation for this module.
# - _extract_metadata: Implements the  extract metadata operation for this module.
# - _validate_simple_name: Implements the  validate simple name operation for this module.
# - _validate_serialized_content: Implements the  validate serialized content operation for this module.
# - validate_serialized_content: Validates serialized content for this module.
# - _file_record: Implements the  file record operation for this module.
# - _invalidate_file_record_cache: Implements the  invalidate file record cache operation for this module.
# - warm_file_record_cache: Implements the warm file record cache operation for this module.
# - _resolve_folder_abs_by_id: Implements the  resolve folder abs by id operation for this module.
# - _resolve_file_abs_by_id: Implements the  resolve file abs by id operation for this module.
# - _search_terms: Implements the  search terms operation for this module.
# - _delete_legacy_db_files: Implements the  delete legacy db files operation for this module.
# - _migrate_legacy_db_to_fs: Implements the  migrate legacy db to fs operation for this module.
# - init_db: Implements the init db operation for this module.
# - list_folders: Lists folders for this module.
# - get_folder_by_path: Returns folder by path for this module.
# - create_folder: Creates folder for this module.
# - rename_folder: Implements the rename folder operation for this module.
# - move_folder: Implements the move folder operation for this module.
# - delete_folder: Deletes folder for this module.
# - list_files: Lists files for this module.
# - get_file: Returns file for this module.
# - create_file: Creates file for this module.
# - create_serialized_file: Creates serialized file for this module.
# - update_file: Updates file for this module.
# - rename_file: Implements the rename file operation for this module.
# - move_file: Implements the move file operation for this module.
# - delete_file: Deletes file for this module.
# - search: Implements the search operation for this module.
# - _metadata_value: Implements the  metadata value operation for this module.
# - _matches_metadata_filter: Implements the  matches metadata filter operation for this module.
# - search_metadata: Implements the search metadata operation for this module.
# - import_from_fs: Implements the import from fs operation for this module.
# ====================================================================================================

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

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
_METADATA_STORE_NAME = '.koredocs_metadata.json'
_METADATA_SIDECAR_SUFFIX = '.koremeta.json'
_HISTORY_DIRECTORY_NAME = '.koredocs_history'
_METADATA_SCHEMA_VERSION = 1
_KOREDOC_JSON_HEADER_START = '---koredocs-json'
_KOREDOC_HEADER_END        = '---'

_file_record_cache: dict[Path, dict] = {}
_file_record_cache_lock = threading.RLock()


def configure(root_dir: Path, legacy_db_path: Path | None = None) -> None:
    global _ROOT_DIR, _LEGACY_DB_PATH
    _ROOT_DIR = Path(root_dir).resolve()
    ensure_datauser_root(_ROOT_DIR)
    _LEGACY_DB_PATH = Path(legacy_db_path).resolve() if legacy_db_path else None
    with _file_record_cache_lock:
        _file_record_cache.clear()
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


def _metadata_store_path() -> Path:
    return _root_dir() / _METADATA_STORE_NAME


def _metadata_sidecar_path(path: Path) -> Path:
    return path.with_name(f'.{path.name}{_METADATA_SIDECAR_SUFFIX}')


def _history_directory() -> Path:
    return _root_dir() / _HISTORY_DIRECTORY_NAME


def _metadata_key(path: Path) -> str:
    return _relative_posix(path)


def _validate_metadata(metadata: dict) -> dict:
    if not isinstance(metadata, dict):
        raise ValueError('metadata must be a JSON object')
    try:
        return json.loads(json.dumps(metadata, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise ValueError('metadata must contain only JSON-compatible values') from exc


def _merge_metadata(current: dict, patch: dict) -> dict:
    result = _validate_metadata(current)
    for key, value in _validate_metadata(patch).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_metadata(result[key], value)
        else:
            result[key] = value
    return result


def _load_metadata_store() -> dict[str, dict]:
    path = _metadata_store_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    files = raw.get('files') if isinstance(raw, dict) else None
    if not isinstance(files, dict):
        return {}
    return {
        str(key): value
        for key, value in files.items()
        if isinstance(key, str) and isinstance(value, dict)
    }


def _load_sidecar(path: Path) -> dict | None:
    sidecar = _metadata_sidecar_path(path)
    if not sidecar.exists():
        return None
    try:
        value = json.loads(sidecar.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or not isinstance(value.get('metadata'), dict):
        return None
    return value


def _save_sidecar(path: Path, record: dict) -> None:
    write_text_file(
        _metadata_sidecar_path(path),
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
        root_dir=_root_dir(),
    )


def _split_koredoc_json_header(content: str) -> tuple[dict | None, str]:
    """Return the embedded artefact record and Markdown body for a .koredoc."""
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip().lower() != _KOREDOC_JSON_HEADER_START:
        return None, content
    for index in range(1, len(lines)):
        if lines[index].strip() != _KOREDOC_HEADER_END:
            continue
        try:
            record = json.loads(''.join(lines[1:index]))
        except ValueError:
            return None, content
        if not isinstance(record, dict) or not isinstance(record.get('metadata'), dict):
            return None, content
        return record, ''.join(lines[index + 1:])
    return None, content


def _koredoc_content_with_header(content: str, record: dict) -> str:
    _, body = _split_koredoc_json_header(content)
    header = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True)
    return f'{_KOREDOC_JSON_HEADER_START}\n{header}\n{_KOREDOC_HEADER_END}\n{body.lstrip(chr(10))}'


def _artifact_record(path: Path, content: str | None = None) -> dict:
    source_content = content if content is not None else read_text_file(path, root_dir=_root_dir())
    if path.suffix.lower() == '.koredoc':
        embedded, _ = _split_koredoc_json_header(source_content)
        if embedded is not None:
            return embedded
    sidecar = _load_sidecar(path)
    if sidecar is not None:
        return sidecar
    legacy = _load_metadata_store().get(_metadata_key(path))
    metadata = _validate_metadata(legacy) if legacy is not None else _extract_metadata(path.name, source_content)
    record = {
        'schema_version': _METADATA_SCHEMA_VERSION,
        'artifact_id':      str(uuid4()),
        'created_at':       _iso_from_ts(getattr(path.stat(), 'st_ctime', path.stat().st_mtime)),
        'metadata':         metadata,
    }
    if path.suffix.lower() == '.koredoc':
        _write_artifact_record(
            path,
            metadata,
            artifact_id=record['artifact_id'],
            created_at=record['created_at'],
        )
    else:
        _save_sidecar(path, record)
    return record


def _write_artifact_record(path: Path, metadata: dict, *, artifact_id: str | None = None, created_at: str | None = None) -> dict:
    record = {
        'schema_version': _METADATA_SCHEMA_VERSION,
        'artifact_id':      artifact_id or str(uuid4()),
        'created_at':       created_at or _now_iso(),
        'metadata':         _validate_metadata(metadata),
    }
    if path.suffix.lower() == '.koredoc':
        content = read_text_file(path, root_dir=_root_dir())
        existing, _ = _split_koredoc_json_header(content)
        if existing is not None:
            record['artifact_id'] = artifact_id or existing.get('artifact_id') or record['artifact_id']
            record['created_at']  = created_at or existing.get('created_at') or record['created_at']
        write_text_file(path, _koredoc_content_with_header(content, record), root_dir=_root_dir())
        sidecar = _metadata_sidecar_path(path)
        if sidecar.exists():
            delete_datauser_file(sidecar, root_dir=_root_dir())
    else:
        _save_sidecar(path, record)
    files = _load_metadata_store()
    if files.pop(_metadata_key(path), None) is not None:
        _save_metadata_store(files)
    return record


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _save_metadata_store(files: dict[str, dict]) -> None:
    payload = {'version': 1, 'files': files}
    write_text_file(
        _metadata_store_path(),
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
        root_dir=_root_dir(),
    )


def _stored_metadata(path: Path) -> dict | None:
    if path.suffix.lower() == '.koredoc':
        embedded, _ = _split_koredoc_json_header(read_text_file(path, root_dir=_root_dir()))
        if embedded is not None:
            return _validate_metadata(embedded['metadata'])
    sidecar = _load_sidecar(path)
    if sidecar is not None:
        return _validate_metadata(sidecar['metadata'])
    metadata = _load_metadata_store().get(_metadata_key(path))
    return _validate_metadata(metadata) if metadata is not None else None


def _set_stored_metadata(path: Path, metadata: dict) -> None:
    existing = _artifact_record(path)
    _write_artifact_record(
        path,
        metadata,
        artifact_id=existing.get('artifact_id'),
        created_at=existing.get('created_at'),
    )


def _delete_stored_metadata(path: Path) -> None:
    files = _load_metadata_store()
    if files.pop(_metadata_key(path), None) is not None:
        _save_metadata_store(files)
    sidecar = _metadata_sidecar_path(path)
    if sidecar.exists():
        delete_datauser_file(sidecar, root_dir=_root_dir())


def _move_stored_metadata(source: Path, target: Path) -> None:
    source_key = _metadata_key(source)
    target_key = _metadata_key(target)
    files = _load_metadata_store()
    metadata = files.pop(source_key, None)
    if metadata is not None:
        files[target_key] = metadata
        _save_metadata_store(files)
    source_sidecar = _metadata_sidecar_path(source)
    target_sidecar = _metadata_sidecar_path(target)
    if source_sidecar.exists():
        source_sidecar.rename(target_sidecar)


def _move_stored_metadata_tree(source: Path, target: Path) -> None:
    source_key = _metadata_key(source).rstrip('/')
    target_key = _metadata_key(target).rstrip('/')
    files = _load_metadata_store()
    moved = {
        target_key + key[len(source_key):]: metadata
        for key, metadata in files.items()
        if key == source_key or key.startswith(source_key + '/')
    }
    if not moved:
        return
    for key in list(files):
        if key == source_key or key.startswith(source_key + '/'):
            del files[key]
    files.update(moved)
    _save_metadata_store(files)
    for sidecar in source.rglob(f'*{_METADATA_SIDECAR_SUFFIX}'):
        relative = sidecar.relative_to(source)
        target_sidecar = target / relative
        target_sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.rename(target_sidecar)


def _delete_stored_metadata_tree(folder: Path) -> None:
    folder_key = _metadata_key(folder).rstrip('/')
    files = _load_metadata_store()
    removed = [key for key in files if key == folder_key or key.startswith(folder_key + '/')]
    if not removed:
        return
    for key in removed:
        del files[key]
    _save_metadata_store(files)


def _write_history(path: Path, content: str, metadata: dict, *, action: str) -> None:
    if path.suffix.lower() == '.koredoc':
        content = read_text_file(path, root_dir=_root_dir())
    artifact = _artifact_record(path, content)
    body_content = content
    if path.suffix.lower() == '.koredoc':
        _, body_content = _split_koredoc_json_header(content)
    artifact_id = artifact['artifact_id']
    history_root = _history_directory() / artifact_id
    history_root.mkdir(parents=True, exist_ok=True)
    revision = f'{path.stat().st_mtime_ns}.json'
    payload = {
        'artifact_id': artifact_id,
        'action':      action,
        'recorded_at': _now_iso(),
        'path':        _relative_posix(path),
        'metadata':    metadata,
        'content':     content,
        'body_content': body_content,
    }
    write_text_file(history_root / revision, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', root_dir=_root_dir())


def list_history(file_id: int, limit: int = 50) -> list[dict]:
    path = _resolve_file_abs_by_id(file_id)
    if path is None:
        raise ValueError(f'File not found: {file_id}')
    artifact = _artifact_record(path)
    history_root = _history_directory() / artifact['artifact_id']
    if not history_root.exists():
        return []
    results = []
    for item in sorted(history_root.glob('*.json'), reverse=True)[:limit]:
        try:
            snapshot = json.loads(item.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        results.append({key: value for key, value in snapshot.items() if key != 'content'} | {'revision': item.stem})
    return results


def get_history_revision(file_id: int, revision: str) -> dict:
    path = _resolve_file_abs_by_id(file_id)
    if path is None:
        raise ValueError(f'File not found: {file_id}')
    artifact = _artifact_record(path)
    candidate = _history_directory() / artifact['artifact_id'] / f'{revision}.json'
    if not candidate.exists() or '/' in revision or '\\' in revision:
        raise ValueError(f'Revision not found: {revision}')
    return json.loads(candidate.read_text(encoding='utf-8'))


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


def _iter_file_paths(root: Path | None = None, *, recursive: bool = True) -> list[Path]:
    base = root.resolve() if root is not None else _root_dir()
    return [
        path
        for path in list_datauser_files(
            search_root        = _relative_posix(base),
            recursive          = recursive,
            allowed_extensions = set(_VISIBLE_EXTENSIONS),
            root_dir           = _root_dir(),
        )
        if path.name != _METADATA_STORE_NAME
        and not path.name.endswith(_METADATA_SIDECAR_SUFFIX)
        and _HISTORY_DIRECTORY_NAME not in path.parts
    ]


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
    if not include_content:
        with _file_record_cache_lock:
            cached = _file_record_cache.get(path)
            if cached is not None:
                return {**cached, 'metadata': dict(cached['metadata'])}

    content = read_text_file(path, root_dir=_root_dir())
    stat = path.stat()
    artifact = _artifact_record(path, content)
    metadata = artifact['metadata']
    body_content = content
    if path.suffix.lower() == '.koredoc':
        _, body_content = _split_koredoc_json_header(content)
    record = {
        'id': _file_id_for_abs(path),
        'folder_id': _folder_id_for_abs(path.parent),
        'folder_path': _folder_abs_to_label(path.parent),
        'path': _relative_posix(path),
        'name': path.name,
        'ext': path.suffix.lstrip('.'),
        'metadata': metadata,
        'artifact_id': artifact['artifact_id'],
        'metadata_schema_version': artifact.get('schema_version', _METADATA_SCHEMA_VERSION),
        'word_count': _word_count(body_content),
        'revision': int(stat.st_mtime_ns),
        'created_at': _iso_from_ts(getattr(stat, 'st_ctime', stat.st_mtime)),
        'modified_at': _iso_from_ts(stat.st_mtime),
    }
    if include_content:
        record['content'] = content
        if path.suffix.lower() == '.koredoc':
            record['body_content'] = body_content
    else:
        with _file_record_cache_lock:
            _file_record_cache[path] = record
    return record


def _invalidate_file_record_cache(*paths: Path) -> None:
    with _file_record_cache_lock:
        for path in paths:
            _file_record_cache.pop(path, None)


def warm_file_record_cache() -> None:
    """Warm direct-root file rows without delaying the KoreDocs web service."""
    paths = _iter_file_paths(_root_dir(), recursive=False)
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix='koredocs-file-cache') as executor:
        list(executor.map(lambda path: _file_record(path, include_content=False), paths))


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
    _move_stored_metadata_tree(folder_abs, target)
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
    _move_stored_metadata_tree(folder_abs, target)
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
        _delete_stored_metadata_tree(folder_abs)
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

    files = _iter_file_paths(folder_abs, recursive=folder_abs is None)
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
    _validate_simple_name(name, kind='File', require_extension=True)
    _validate_serialized_content(name, content)
    folder_abs = _resolve_folder_abs_by_id(folder_id)
    if folder_abs is None:
        raise ValueError(f'Folder {folder_id} not found')
    target = folder_abs / name
    if target.exists():
        raise ConflictError('UNIQUE constraint failed: files.folder_id, files.name')
    write_text_file(target, content, root_dir=_root_dir())
    _write_artifact_record(target, metadata if metadata is not None else _extract_metadata(name, content))
    _write_history(target, content, _stored_metadata(target) or {}, action='created')
    _invalidate_file_record_cache(target)
    return _file_record(target, include_content=False)


def create_serialized_file(
    folder_path: str,
    name: str,
    ext: str,
    content: str,
    metadata: dict | None = None,
) -> dict:
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
    return create_file(folder_id, normalized_name, content, metadata)


def update_file(
    file_id: int,
    content: str | None = None,
    metadata: dict | None = None,
    metadata_patch: dict | None = None,
    expected_revision: int | None = None,
) -> dict | None:
    file_abs = _resolve_file_abs_by_id(file_id)
    if file_abs is None:
        return None
    current_revision = int(file_abs.stat().st_mtime_ns)
    if expected_revision is not None and current_revision != expected_revision:
        raise ConflictError(f'File {file_id} revision mismatch: expected {expected_revision}, current {current_revision}')
    current_content = read_text_file(file_abs, root_dir=_root_dir())
    current_artifact = _artifact_record(file_abs, current_content)
    new_content = current_content if content is None else content
    _validate_serialized_content(file_abs.name, new_content)
    if metadata is not None and metadata_patch is not None:
        raise ValueError('Provide metadata or metadata_patch, not both')
    next_metadata = metadata if metadata is not None else current_artifact['metadata']
    if metadata_patch is not None:
        next_metadata = _merge_metadata(current_artifact['metadata'], metadata_patch)
    write_text_file(file_abs, new_content, root_dir=_root_dir())
    if file_abs.suffix.lower() == '.koredoc':
        _write_artifact_record(
            file_abs,
            next_metadata,
            artifact_id=current_artifact['artifact_id'],
            created_at=current_artifact['created_at'],
        )
    elif metadata is not None or metadata_patch is not None:
        _set_stored_metadata(file_abs, next_metadata)
    _write_history(file_abs, new_content, _stored_metadata(file_abs) or _extract_metadata(file_abs.name, new_content), action='updated')
    _invalidate_file_record_cache(file_abs)
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
    _move_stored_metadata(file_abs, target)
    _invalidate_file_record_cache(file_abs, target)
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
    _move_stored_metadata(file_abs, target)
    _invalidate_file_record_cache(file_abs, target)
    return _file_record(target, include_content=False)


def delete_file(file_id: int, expected_revision: int | None = None) -> bool:
    file_abs = _resolve_file_abs_by_id(file_id)
    if file_abs is None:
        return False
    current_revision = int(file_abs.stat().st_mtime_ns)
    if expected_revision is not None and current_revision != expected_revision:
        raise ConflictError(f'File {file_id} revision mismatch: expected {expected_revision}, current {current_revision}')
    delete_datauser_file(file_abs, root_dir=_root_dir())
    _delete_stored_metadata(file_abs)
    _invalidate_file_record_cache(file_abs)
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
        metadata = _stored_metadata(path)
        if metadata is None:
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


def _metadata_value(metadata: dict, field: str):
    value: object = metadata
    for part in field.split('.'):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _matches_metadata_filter(metadata: dict, expression: dict) -> bool:
    """Evaluate a compact JSON metadata filter.

    Field values match exactly.  A field may instead use ``exists``, ``contains``,
    ``in``, ``eq``, ``ne``, ``gt``, ``gte``, ``lt`` or ``lte``.  Nested values use
    dotted paths; ``$and``, ``$or`` and ``$not`` compose expressions.
    """
    if not isinstance(expression, dict):
        raise ValueError('metadata_filter must be a JSON object')
    for field, expected in expression.items():
        if field == '$and':
            if not isinstance(expected, list) or not all(_matches_metadata_filter(metadata, item) for item in expected):
                return False
            continue
        if field == '$or':
            if not isinstance(expected, list) or not any(_matches_metadata_filter(metadata, item) for item in expected):
                return False
            continue
        if field == '$not':
            if not isinstance(expected, dict) or _matches_metadata_filter(metadata, expected):
                return False
            continue
        actual = _metadata_value(metadata, field)
        if not isinstance(expected, dict):
            if actual != expected:
                return False
            continue
        for operator, operand in expected.items():
            if operator == 'exists':
                if bool(actual is not None) != bool(operand):
                    return False
            elif operator == 'contains':
                if not isinstance(actual, (str, list, dict)) or operand not in actual:
                    return False
            elif operator == 'in':
                if not isinstance(operand, list) or actual not in operand:
                    return False
            elif operator == 'eq':
                if actual != operand:
                    return False
            elif operator == 'ne':
                if actual == operand:
                    return False
            elif operator in {'gt', 'gte', 'lt', 'lte'}:
                if actual is None:
                    return False
                try:
                    comparisons = {
                        'gt':  actual > operand,
                        'gte': actual >= operand,
                        'lt':  actual < operand,
                        'lte': actual <= operand,
                    }
                except TypeError:
                    return False
                if not comparisons[operator]:
                    return False
            else:
                raise ValueError(f'Unsupported metadata operator: {operator}')
    return True


def search_metadata(
    metadata_filter: dict,
    *,
    ext: str | None = None,
    folder_path: str | None = None,
    limit: int = 20,
) -> list[dict]:
    if limit < 1 or limit > 200:
        raise ValueError('limit must be between 1 and 200')
    base_folder = _folder_path_to_abs(folder_path) if folder_path else _root_dir()
    if not base_folder.exists() or not base_folder.is_dir():
        return []
    matches = []
    for path in _iter_file_paths(base_folder):
        if ext is not None and path.suffix.lstrip('.') != ext:
            continue
        record = _file_record(path, include_content=False)
        if _matches_metadata_filter(record['metadata'], metadata_filter):
            matches.append(record)
    return sorted(matches, key=lambda item: item['path'])[:limit]


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
