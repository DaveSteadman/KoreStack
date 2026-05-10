# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreFile virtual file system: SQLite adjacency-list + FTS5 for KoreDocs.
#
# Folder hierarchy:
#   Adjacency-list model with a materialised path string (e.g. "/Projects/KoreDocs").
#   The root folder (id=1) has path='/' and no parent.
#
# File storage:
#   Content is zlib-compressed.  The FTS5 index is a contentless table kept in sync
#   on every write — same pattern as KoreData/KoreRAG.
#
# Public API:
#   configure(db_path)  -- set the database path (call once at startup before init_db)
#   init_db()           -- create tables if absent
#   ConflictError       -- raised on duplicate name within a folder
#
# Related modules:
#   - app/server.py         -- calls configure() and init_db() at startup
#   - app/_mcp_shared.py    -- folder/file helpers built on top of korefile
#   - app/koredocs_mcp.py   -- MCP tool layer
# ====================================================================================================

from __future__ import annotations

import json
import re
import sqlite3
import zlib
from contextlib import contextmanager
from pathlib import Path
from typing import Optional


class ConflictError(ValueError):
    pass

# ── Configuration ──────────────────────────────────────────────────────────

_DB_PATH: Path | None = None


def configure(db_path: Path) -> None:
    """Set the database path.  Must be called before init_db()."""
    global _DB_PATH
    _DB_PATH = db_path
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _db_path() -> Path:
    if _DB_PATH is None:
        raise RuntimeError('korefile.configure() has not been called')
    return _DB_PATH


# ── Database connection ────────────────────────────────────────────────────

@contextmanager
def _db():
    conn = sqlite3.connect(str(_db_path()), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Compression ────────────────────────────────────────────────────────────

def _compress(text: str) -> bytes:
    return zlib.compress(text.encode('utf-8'), level=6)


def _decompress(blob: bytes | None) -> str | None:
    if not blob:
        return None
    return zlib.decompress(blob).decode('utf-8')


# ── FTS helpers ────────────────────────────────────────────────────────────

def _fts_query(q: str) -> str:
    """Convert a raw search string to a safe FTS5 MATCH expression."""
    parts: list[str] = []
    for m in re.finditer(r'"([^"]+)"|(\S+)', (q or '').strip()):
        phrase, word = m.group(1), m.group(2)
        if phrase:
            inner = phrase.replace('"', '""')
            if inner:
                parts.append(f'"{inner}"')
        elif word:
            clean = word.replace('"', '')
            if clean:
                parts.append(f'"{clean}"')
    return ' '.join(parts)


def _fts_insert(conn: sqlite3.Connection, file_id: int,
                name: str, metadata: str, content: str) -> None:
    conn.execute(
        'INSERT INTO files_fts(rowid, name, metadata, content) VALUES (?,?,?,?)',
        (file_id, name, metadata or '', content or ''),
    )


def _fts_delete(conn: sqlite3.Connection, file_id: int,
                name: str, metadata: str, content: str) -> None:
    conn.execute(
        "INSERT INTO files_fts(files_fts, rowid, name, metadata, content) "
        "VALUES ('delete',?,?,?,?)",
        (file_id, name, metadata or '', content or ''),
    )


# ── Schema init ────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create tables if they do not exist and ensure the root folder exists."""
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS folders (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_id  INTEGER REFERENCES folders(id) ON DELETE RESTRICT,
                name       TEXT NOT NULL,
                path       TEXT NOT NULL UNIQUE,
                revision   INTEGER NOT NULL DEFAULT 1,
                modified_at TEXT DEFAULT (datetime('now','utc')),
                created_at TEXT DEFAULT (datetime('now','utc'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_id   INTEGER NOT NULL REFERENCES folders(id) ON DELETE RESTRICT,
                name        TEXT NOT NULL,
                ext         TEXT NOT NULL,
                content     BLOB,
                metadata    TEXT,
                word_count  INTEGER,
                revision    INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT DEFAULT (datetime('now','utc')),
                modified_at TEXT DEFAULT (datetime('now','utc')),
                UNIQUE (folder_id, name)
            )
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
                name, metadata, content,
                tokenize = 'unicode61 remove_diacritics 1',
                content  = ''
            )
        """)
        # Root folder — INSERT OR IGNORE so re-init is safe
        conn.execute(
            "INSERT OR IGNORE INTO folders (id, parent_id, name, path) "
            "VALUES (1, NULL, 'Root', '/')"
        )
        _ensure_files_schema(conn)
        _ensure_folders_schema(conn)


def _ensure_files_schema(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(files)").fetchall()
    }
    if "revision" not in columns:
        conn.execute(
            "ALTER TABLE files ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
        )


def _ensure_folders_schema(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(folders)").fetchall()
    }
    if "revision" not in columns:
        conn.execute(
            "ALTER TABLE folders ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
        )
    if "modified_at" not in columns:
        conn.execute(
            "ALTER TABLE folders ADD COLUMN modified_at TEXT"
        )
        conn.execute(
            "UPDATE folders SET modified_at = COALESCE(modified_at, created_at, datetime('now','utc'))"
        )


# ── Folder helpers ──────────────────────────────────────────────────────────

def _row_to_folder(row: sqlite3.Row) -> dict:
    return {
        'id': row['id'], 'parent_id': row['parent_id'],
        'name': row['name'], 'path': row['path'],
        'revision': row['revision'],
        'modified_at': row['modified_at'],
        'created_at': row['created_at'],
    }


def _get_folder(conn: sqlite3.Connection, folder_id: int) -> sqlite3.Row | None:
    return conn.execute(
        'SELECT * FROM folders WHERE id=?', (folder_id,)
    ).fetchone()


def _ensure_folder_path(conn: sqlite3.Connection, path: str) -> int:
    """Return the id of the folder at *path*, creating it and parents if needed."""
    row = conn.execute('SELECT id FROM folders WHERE path=?', (path,)).fetchone()
    if row:
        return row['id']
    parts = [p for p in path.split('/') if p]
    current_path = '/'
    current_id = 1  # root
    for part in parts:
        child_path = current_path.rstrip('/') + '/' + part
        row = conn.execute(
            'SELECT id FROM folders WHERE path=?', (child_path,)
        ).fetchone()
        if row:
            current_id = row['id']
        else:
            cur = conn.execute(
                'INSERT INTO folders (parent_id, name, path) VALUES (?,?,?)',
                (current_id, part, child_path),
            )
            current_id = cur.lastrowid
        current_path = child_path
    return current_id


# ── Folders API ─────────────────────────────────────────────────────────────

def list_folders() -> list[dict]:
    """Return all folders as a flat list ordered by path."""
    with _db() as conn:
        rows = conn.execute('SELECT * FROM folders ORDER BY path').fetchall()
    return [_row_to_folder(r) for r in rows]


def get_folder_by_path(path: str) -> dict | None:
    with _db() as conn:
        row = conn.execute('SELECT * FROM folders WHERE path=?', (path,)).fetchone()
    return _row_to_folder(row) if row else None


def create_folder(name: str, parent_id: int) -> dict:
    """Create a folder under *parent_id*.  Returns the new folder dict."""
    _validate_simple_name(name, kind='Folder')
    with _db() as conn:
        parent = _get_folder(conn, parent_id)
        if parent is None:
            raise ValueError(f'Parent folder {parent_id} not found')
        new_path = parent['path'].rstrip('/') + '/' + name
        cur = conn.execute(
            'INSERT INTO folders (parent_id, name, path, revision, modified_at) VALUES (?,?,?,?,datetime(\'now\',\'utc\'))',
            (parent_id, name, new_path, 1),
        )
        conn.execute(
            'UPDATE folders SET revision=revision+1, modified_at=datetime(\'now\',\'utc\') WHERE id=?',
            (parent_id,),
        )
        row = conn.execute(
            'SELECT * FROM folders WHERE id=?', (cur.lastrowid,)
        ).fetchone()
    return _row_to_folder(row)


def rename_folder(folder_id: int, new_name: str, *, expected_revision: int | None = None) -> dict:
    """Rename a folder and update the paths of all descendants."""
    _validate_simple_name(new_name, kind='Folder')
    with _db() as conn:
        folder = _get_folder(conn, folder_id)
        if folder is None:
            raise ValueError(f'Folder {folder_id} not found')
        current_revision = int(folder['revision'])
        if expected_revision is not None and current_revision != expected_revision:
            raise ConflictError(
                f'Folder {folder_id} revision mismatch: expected {expected_revision}, current {current_revision}'
            )
        if folder_id == 1:
            raise ValueError('Cannot rename the root folder')
        old_path = folder['path']
        parent = _get_folder(conn, folder['parent_id'])
        new_path = parent['path'].rstrip('/') + '/' + new_name
        # Update descendant paths first (substr is 1-based in SQLite)
        conn.execute(
            'UPDATE folders SET path = ? || substr(path, ?) WHERE path LIKE ?',
            (new_path, len(old_path) + 1, old_path + '/%'),
        )
        conn.execute(
            'UPDATE folders SET name=?, path=?, revision=revision+1, modified_at=datetime(\'now\',\'utc\') WHERE id=?',
            (new_name, new_path, folder_id),
        )
        row = conn.execute('SELECT * FROM folders WHERE id=?', (folder_id,)).fetchone()
    return _row_to_folder(row)


def move_folder(folder_id: int, new_parent_id: int, *, expected_revision: int | None = None) -> dict:
    """Move a folder under a new parent, updating all descendant paths."""
    with _db() as conn:
        folder = _get_folder(conn, folder_id)
        if folder is None:
            raise ValueError(f'Folder {folder_id} not found')
        current_revision = int(folder['revision'])
        if expected_revision is not None and current_revision != expected_revision:
            raise ConflictError(
                f'Folder {folder_id} revision mismatch: expected {expected_revision}, current {current_revision}'
            )
        if folder_id == 1:
            raise ValueError('Cannot move the root folder')
        new_parent = _get_folder(conn, new_parent_id)
        if new_parent is None:
            raise ValueError(f'Parent folder {new_parent_id} not found')
        old_path = folder['path']
        np_path  = new_parent['path']
        # Prevent moving a folder into itself or any of its descendants
        if np_path == old_path or np_path.startswith(old_path + '/'):
            raise ValueError('Cannot move a folder into itself or one of its descendants')
        new_path = np_path.rstrip('/') + '/' + folder['name']
        # Update descendant paths first
        conn.execute(
            'UPDATE folders SET path = ? || substr(path, ?) WHERE path LIKE ?',
            (new_path, len(old_path) + 1, old_path + '/%'),
        )
        conn.execute(
            'UPDATE folders SET parent_id=?, path=?, revision=revision+1, modified_at=datetime(\'now\',\'utc\') WHERE id=?',
            (new_parent_id, new_path, folder_id),
        )
        row = conn.execute('SELECT * FROM folders WHERE id=?', (folder_id,)).fetchone()
    return _row_to_folder(row)


def delete_folder(folder_id: int, *, expected_revision: int | None = None) -> bool:
    """Delete a folder.  Raises ValueError if it has children or files."""
    with _db() as conn:
        folder = _get_folder(conn, folder_id)
        if not folder:
            return False
        current_revision = int(folder['revision'])
        if expected_revision is not None and current_revision != expected_revision:
            raise ConflictError(
                f'Folder {folder_id} revision mismatch: expected {expected_revision}, current {current_revision}'
            )
        parent_id = folder['parent_id']
        # FK RESTRICT handles the child/file guard — let it propagate as ValueError
        conn.execute('DELETE FROM folders WHERE id=?', (folder_id,))
        if parent_id is not None:
            conn.execute(
                'UPDATE folders SET revision=revision+1, modified_at=datetime(\'now\',\'utc\') WHERE id=?',
                (parent_id,),
            )
    return True


# ── File helpers ────────────────────────────────────────────────────────────

_FILE_COLS = (
    'id', 'folder_id', 'name', 'ext', 'metadata',
    'word_count', 'revision', 'created_at', 'modified_at',
)
_FILE_COLS_FULL = _FILE_COLS + ('content',)


def _row_to_file(row: sqlite3.Row, include_content: bool = False) -> dict:
    cols = _FILE_COLS_FULL if include_content else _FILE_COLS
    d = {c: row[c] for c in cols}
    if include_content:
        d['content'] = _decompress(d['content'])
    if d.get('metadata'):
        try:
            d['metadata'] = json.loads(d['metadata'])
        except (ValueError, TypeError):
            pass
    return d


def _word_count(text: str) -> int:
    return len(text.split())


def _extract_metadata(name: str, content: str) -> dict:
    """Best-effort extraction of title/tags from document content."""
    ext = Path(name).suffix.lstrip('.')
    meta: dict = {}
    if ext == 'koredoc':
        # YAML frontmatter between --- delimiters
        m = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
        if m:
            for line in m.group(1).splitlines():
                if ':' in line:
                    k, _, v = line.partition(':')
                    meta[k.strip()] = v.strip()
        if 'title' not in meta:
            hm = re.search(r'^#{1,3}\s+(.+)$', content, re.MULTILINE)
            if hm:
                meta['title'] = hm.group(1).strip()
    elif ext in ('koresheet', 'kodiag'):
        try:
            obj = json.loads(content)
            if isinstance(obj, dict):
                meta['title'] = (
                    (obj.get('meta') or {}).get('title')
                    or obj.get('title', '')
                )
        except (ValueError, TypeError):
            pass
    meta.setdefault('title', Path(name).stem)
    return meta


def _validate_serialized_content(name: str, content: str) -> None:
    ext = Path(name).suffix.lstrip('.')
    if ext == 'koredoc':
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

    if ext == 'kodiag':
        required = {'koreDiag', 'id', 'title', 'settings', 'nodes', 'edges'}
        missing = sorted(required - obj.keys())
        if missing:
            raise ValueError(f'{name} is missing required fields: {", ".join(missing)}')
        if not isinstance(obj.get('settings'), dict):
            raise ValueError(f'{name} field "settings" must be an object')
        if not isinstance(obj.get('nodes'), list) or not isinstance(obj.get('edges'), list):
            raise ValueError(f'{name} fields "nodes" and "edges" must be arrays')


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


# ── Files API ───────────────────────────────────────────────────────────────

def list_files(folder_id: int | None = None,
               folder_path: str | None = None,
               ext: str | None = None,
               name: str | None = None,
               limit: int | None = None) -> list[dict]:
    """List files (metadata only). Filter by folder_id, folder_path, ext, or exact name."""
    with _db() as conn:
        if folder_path is not None:
            row = conn.execute(
                'SELECT id FROM folders WHERE path=?', (folder_path,)
            ).fetchone()
            folder_id = row['id'] if row else None
            if folder_id is None:
                return []
        cols = ','.join(_FILE_COLS)
        clauses: list[str] = []
        params: list[object] = []
        if folder_id is not None:
            clauses.append('folder_id=?')
            params.append(folder_id)
        if ext is not None:
            clauses.append('ext=?')
            params.append(ext)
        if name is not None:
            clauses.append('name=?')
            params.append(name)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ''
        limit_sql = ''
        if limit is not None:
            limit_sql = ' LIMIT ?'
            params.append(limit)
        rows = conn.execute(
            f'SELECT {cols} FROM files{where} ORDER BY name{limit_sql}',
            tuple(params),
        ).fetchall()
    return [_row_to_file(r) for r in rows]


def get_file(file_id: int, include_content: bool = True) -> dict | None:
    cols = ','.join(_FILE_COLS_FULL if include_content else _FILE_COLS)
    with _db() as conn:
        row = conn.execute(
            f'SELECT {cols} FROM files WHERE id=?', (file_id,)
        ).fetchone()
    return _row_to_file(row, include_content=include_content) if row else None


def create_file(folder_id: int, name: str, content: str,
                metadata: dict | None = None) -> dict:
    _validate_simple_name(name, kind='File', require_extension=True)
    ext = Path(name).suffix.lstrip('.')
    _validate_serialized_content(name, content)
    if metadata is None:
        metadata = _extract_metadata(name, content)
    meta_json = json.dumps(metadata)
    compressed = _compress(content)
    wc = _word_count(content)
    with _db() as conn:
        cur = conn.execute(
            'INSERT INTO files (folder_id, name, ext, content, metadata, word_count, revision) '
            'VALUES (?,?,?,?,?,?,?)',
            (folder_id, name, ext, compressed, meta_json, wc, 1),
        )
        file_id = cur.lastrowid
        _fts_insert(conn, file_id, name, meta_json, content)
        row = conn.execute(
            f'SELECT {",".join(_FILE_COLS)} FROM files WHERE id=?', (file_id,)
        ).fetchone()
    return _row_to_file(row)


def update_file(file_id: int, content: str | None = None,
                metadata: dict | None = None,
                expected_revision: int | None = None) -> dict | None:
    with _db() as conn:
        row = conn.execute(
            f'SELECT {",".join(_FILE_COLS_FULL)} FROM files WHERE id=?', (file_id,)
        ).fetchone()
        if row is None:
            return None
        current_revision = int(row['revision'])
        if expected_revision is not None and current_revision != expected_revision:
            raise ConflictError(
                f'File {file_id} revision mismatch: expected {expected_revision}, current {current_revision}'
            )
        old_content  = _decompress(row['content']) or ''
        old_meta_json = row['metadata'] or '{}'
        new_content  = content if content is not None else old_content
        _validate_serialized_content(row['name'], new_content)
        if metadata is not None:
            new_meta_dict = metadata
        elif content is not None:
            new_meta_dict = _extract_metadata(row['name'], new_content)
        else:
            new_meta_dict = json.loads(old_meta_json)
        new_meta_json = json.dumps(new_meta_dict)
        compressed    = _compress(new_content)
        wc            = _word_count(new_content)
        _fts_delete(conn, file_id, row['name'], old_meta_json, old_content)
        conn.execute(
            "UPDATE files SET content=?, metadata=?, word_count=?, revision=revision+1, "
            "modified_at=datetime('now','utc') WHERE id=?",
            (compressed, new_meta_json, wc, file_id),
        )
        _fts_insert(conn, file_id, row['name'], new_meta_json, new_content)
        updated = conn.execute(
            f'SELECT {",".join(_FILE_COLS)} FROM files WHERE id=?', (file_id,)
        ).fetchone()
    return _row_to_file(updated)


def rename_file(file_id: int, new_name: str,
                expected_revision: int | None = None) -> dict | None:
    _validate_simple_name(new_name, kind='File', require_extension=True)
    ext = Path(new_name).suffix.lstrip('.')
    with _db() as conn:
        row = conn.execute(
            f'SELECT {",".join(_FILE_COLS_FULL)} FROM files WHERE id=?', (file_id,)
        ).fetchone()
        if row is None:
            return None
        current_revision = int(row['revision'])
        if expected_revision is not None and current_revision != expected_revision:
            raise ConflictError(
                f'File {file_id} revision mismatch: expected {expected_revision}, current {current_revision}'
            )
        old_content = _decompress(row['content']) or ''
        old_meta_json = row['metadata'] or '{}'
        _validate_serialized_content(new_name, old_content)
        new_meta = _extract_metadata(new_name, old_content)
        new_meta_json = json.dumps(new_meta)
        _fts_delete(conn, file_id, row['name'], old_meta_json, old_content)
        conn.execute(
            'UPDATE files SET name=?, ext=?, metadata=?, revision=revision+1, modified_at=datetime(\'now\',\'utc\') WHERE id=?',
            (new_name, ext, new_meta_json, file_id),
        )
        _fts_insert(conn, file_id, new_name, new_meta_json, old_content)
        updated = conn.execute(
            f'SELECT {",".join(_FILE_COLS)} FROM files WHERE id=?', (file_id,)
        ).fetchone()
    return _row_to_file(updated)


def move_file(file_id: int, new_folder_id: int,
              expected_revision: int | None = None) -> dict | None:
    with _db() as conn:
        row = conn.execute(
            f'SELECT {",".join(_FILE_COLS_FULL)} FROM files WHERE id=?', (file_id,)
        ).fetchone()
        if row is None:
            return None
        current_revision = int(row['revision'])
        if expected_revision is not None and current_revision != expected_revision:
            raise ConflictError(
                f'File {file_id} revision mismatch: expected {expected_revision}, current {current_revision}'
            )
        if not _get_folder(conn, new_folder_id):
            raise ValueError(f'Folder {new_folder_id} not found')
        conn.execute(
            'UPDATE files SET folder_id=?, revision=revision+1, modified_at=datetime(\'now\',\'utc\') WHERE id=?',
            (new_folder_id, file_id),
        )
        updated = conn.execute(
            f'SELECT {",".join(_FILE_COLS)} FROM files WHERE id=?', (file_id,)
        ).fetchone()
    return _row_to_file(updated)


def delete_file(file_id: int, expected_revision: int | None = None) -> bool:
    with _db() as conn:
        row = conn.execute(
            f'SELECT {",".join(_FILE_COLS_FULL)} FROM files WHERE id=?', (file_id,)
        ).fetchone()
        if row is None:
            return False
        current_revision = int(row['revision'])
        if expected_revision is not None and current_revision != expected_revision:
            raise ConflictError(
                f'File {file_id} revision mismatch: expected {expected_revision}, current {current_revision}'
            )
        content = _decompress(row['content']) or ''
        _fts_delete(conn, file_id, row['name'], row['metadata'] or '', content)
        conn.execute('DELETE FROM files WHERE id=?', (file_id,))
    return True


# ── Search ──────────────────────────────────────────────────────────────────

def search(query: str, ext: str | None = None,
           folder_path: str | None = None, limit: int = 20) -> list[dict]:
    """Full-text search across all files.  Returns metadata + BM25 score."""
    fts_q = _fts_query(query)
    if not fts_q:
        return []
    clauses = ['f.id = fts.rowid', 'fts.files_fts MATCH ?']
    params: list = [fts_q]
    if ext:
        clauses.append('f.ext = ?')
        params.append(ext)
    if folder_path:
        clauses.append(
            'f.folder_id IN '
            '(SELECT id FROM folders WHERE path = ? OR path LIKE ?)'
        )
        params += [folder_path, folder_path.rstrip('/') + '/%']
    where = ' AND '.join(clauses)
    cols  = ', '.join(f'f.{c}' for c in _FILE_COLS)
    sql   = (
        f'SELECT {cols}, bm25(files_fts) AS score '
        f'FROM files f, files_fts fts '
        f'WHERE {where} '
        f'ORDER BY score LIMIT ?'
    )
    params.append(limit)
    with _db() as conn:
        rows = conn.execute(sql, params).fetchall()
    results = []
    for r in rows:
        d = _row_to_file(r)
        d['score'] = r['score']
        results.append(d)
    return results


# ── Import from flat file system ─────────────────────────────────────────────

_IMPORTABLE = frozenset({'.koredoc', '.koresheet', '.kodiag'})


def import_from_fs(data_dir: Path) -> dict:
    """Walk *data_dir* and import every *.kore* file into KoreFile.

    Files are placed in folders that mirror their relative OS directory path.
    Files already present (same folder + name) are skipped, not overwritten.
    Returns ``{'imported': N, 'skipped': N, 'errors': N, 'error_details': [...]}``.
    """
    imported = skipped = errors = 0
    error_details: list[dict] = []
    staged: list[dict] = []
    with _db() as conn:
        for p in sorted(data_dir.rglob('*')):
            if not p.is_file() or p.suffix not in _IMPORTABLE:
                continue
            rel = p.relative_to(data_dir)
            folder_path = (
                '/' + '/'.join(rel.parts[:-1]) if len(rel.parts) > 1 else '/'
            )
            try:
                row = conn.execute(
                    'SELECT id FROM folders WHERE path=?',
                    (folder_path,),
                ).fetchone()
                if row is not None:
                    exists = conn.execute(
                        'SELECT id FROM files WHERE folder_id=? AND name=?',
                        (row['id'], p.name),
                    ).fetchone()
                    if exists:
                        skipped += 1
                        continue
                content = p.read_text(encoding='utf-8')
                _validate_simple_name(p.name, kind='File', require_extension=True)
                _validate_serialized_content(p.name, content)
                meta = _extract_metadata(p.name, content)
                staged.append({
                    'folder_path': folder_path,
                    'name': p.name,
                    'ext': p.suffix.lstrip('.'),
                    'content': content,
                    'meta_json': json.dumps(meta),
                    'compressed': _compress(content),
                    'word_count': _word_count(content),
                })
            except Exception as exc:
                errors += 1
                error_details.append({'file': str(rel).replace('\\', '/'), 'error': str(exc)})
    if errors:
        return {'imported': imported, 'skipped': skipped, 'errors': errors, 'error_details': error_details}
    with _db() as conn:
        for item in staged:
            folder_id = _ensure_folder_path(conn, item['folder_path'])
            exists = conn.execute(
                'SELECT id FROM files WHERE folder_id=? AND name=?',
                (folder_id, item['name']),
            ).fetchone()
            if exists:
                skipped += 1
                continue
            cur = conn.execute(
                'INSERT INTO files '
                '(folder_id, name, ext, content, metadata, word_count, revision) '
                'VALUES (?,?,?,?,?,?,?)',
                (
                    folder_id,
                    item['name'],
                    item['ext'],
                    item['compressed'],
                    item['meta_json'],
                    item['word_count'],
                    1,
                ),
            )
            _fts_insert(conn, cur.lastrowid, item['name'], item['meta_json'], item['content'])
            imported += 1
    return {'imported': imported, 'skipped': skipped, 'errors': errors, 'error_details': error_details}
