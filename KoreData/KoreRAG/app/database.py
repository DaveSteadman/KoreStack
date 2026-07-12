# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# SQLite database layer for KoreRAG.
#
# Schema:
#   chunks  -- text chunks with FTS5 full-text search; content, title, source, tags, metadata
#
# Content is zlib-compressed.  FTS5 index is a contentless table kept in sync on every write.
# WAL mode enabled.  get_status() returns chunk count and database file size.
#
# Related modules:
#   - app/server.py         -- all read/write and search operations
#   - CommonCode/compress.py  -- body storage compression
#   - CommonCode/dbutil.py    -- fts_build_query
# ====================================================================================================
import sqlite3
from contextlib import contextmanager
from typing import Optional

from app.registry import get_db_path as _registry_get_db_path
from compress import compress as _compress, decompress as _decompress
from dbutil import fts_build_query, compute_word_count as _compute_word_count


@contextmanager
def db_connection(db: str = "default"):
    conn = sqlite3.connect(str(_registry_get_db_path(db)), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db: str = "default") -> None:
    with db_connection(db) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT,
                source      TEXT,
                tags        TEXT,
                content     BLOB,
                word_count  INTEGER,
                created_at  TEXT DEFAULT (datetime('now','utc'))
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                title, source, tags, content,
                tokenize='unicode61 remove_diacritics 1',
                content=''
            )
            """
        )
        for trigger_name in ("chunks_ai", "chunks_ad", "chunks_au"):
            conn.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")

        uncompressed = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE typeof(content)='text' AND content IS NOT NULL"
        ).fetchone()[0]
        if uncompressed:
            rows = conn.execute(
                "SELECT id, title, source, tags, content FROM chunks WHERE typeof(content)='text'"
            ).fetchall()
            conn.execute("DELETE FROM chunks_fts")
            for row in rows:
                plain = row["content"]
                conn.execute(
                    "UPDATE chunks SET content=?, word_count=? WHERE id=?",
                    (_compress(plain), _compute_word_count(plain), row["id"]),
                )
                conn.execute(
                    "INSERT INTO chunks_fts(rowid, title, source, tags, content) VALUES (?,?,?,?,?)",
                    (
                        row["id"],
                        row["title"]  or "",
                        row["source"] or "",
                        row["tags"]   or "",
                        plain,
                    ),
                )


_CHUNK_COLS              = ("id", "title", "source", "tags", "word_count", "created_at")
_CHUNK_COLS_WITH_CONTENT = _CHUNK_COLS + ("content",)


def _fts_delete(
    conn: sqlite3.Connection,
    chunk_id: int,
    title: str,
    source: str,
    tags: str,
    content: str,
) -> None:
    conn.execute(
        "INSERT INTO chunks_fts(chunks_fts, rowid, title, source, tags, content) "
        "VALUES ('delete', ?, ?, ?, ?, ?)",
        (chunk_id, title, source, tags, content),
    )


def _fts_insert(
    conn: sqlite3.Connection,
    chunk_id: int,
    title: str,
    source: str,
    tags: str,
    content: str,
) -> None:
    conn.execute(
        "INSERT INTO chunks_fts(rowid, title, source, tags, content) VALUES (?, ?, ?, ?, ?)",
        (chunk_id, title, source, tags, content),
    )


def _row_to_dict(row: sqlite3.Row, include_content: bool = False) -> dict:
    cols = _CHUNK_COLS_WITH_CONTENT if include_content else _CHUNK_COLS
    item = {col: row[col] for col in cols}
    if include_content:
        item["content"] = _decompress(item.get("content"))
    return item


def add_chunk(
    content: str,
    title: Optional[str] = None,
    source: Optional[str] = None,
    tags: Optional[str] = None,
    db: str = "default",
) -> dict:
    word_count = _compute_word_count(content)
    compressed = _compress(content)
    with db_connection(db) as conn:
        cur = conn.execute(
            "INSERT INTO chunks (title, source, tags, content, word_count) VALUES (?, ?, ?, ?, ?)",
            (title, source, tags, compressed, word_count),
        )
        chunk_id = cur.lastrowid
        conn.execute(
            "INSERT INTO chunks_fts(rowid, title, source, tags, content) VALUES (?, ?, ?, ?, ?)",
            (chunk_id, title or "", source or "", tags or "", content or ""),
        )
    return get_chunk(chunk_id, include_content=False, db=db)


def get_chunk(chunk_id: int, include_content: bool = True, db: str = "default") -> Optional[dict]:
    cols = ", ".join(_CHUNK_COLS_WITH_CONTENT if include_content else _CHUNK_COLS)
    with db_connection(db) as conn:
        row = conn.execute(
            f"SELECT {cols} FROM chunks WHERE id = ?",
            (chunk_id,),
        ).fetchone()
    return _row_to_dict(row, include_content=include_content) if row else None


def list_chunks(limit: int = 100, offset: int = 0, db: str = "default") -> list[dict]:
    cols = ", ".join(_CHUNK_COLS)
    with db_connection(db) as conn:
        rows = conn.execute(
            f"SELECT {cols} FROM chunks ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def update_chunk(chunk_id: int, fields: dict, db: str = "default") -> Optional[dict]:
    allowed = {"title", "source", "tags", "content"}
    to_set  = {key: value for key, value in fields.items() if key in allowed}
    if not to_set:
        return get_chunk(chunk_id, include_content=False, db=db)

    plain_content: Optional[str] = None
    if "content" in to_set:
        plain_content        = to_set["content"]
        to_set["content"]    = _compress(plain_content)
        to_set["word_count"] = _compute_word_count(plain_content)

    fts_affected = bool({"title", "source", "tags", "content"} & to_set.keys())
    assignments  = ", ".join(f"{key} = ?" for key in to_set)
    values       = list(to_set.values())

    with db_connection(db) as conn:
        if fts_affected:
            current_row = conn.execute(
                "SELECT title, source, tags, content FROM chunks WHERE id = ?",
                (chunk_id,),
            ).fetchone()
            if current_row:
                _fts_delete(
                    conn,
                    chunk_id,
                    current_row["title"]  or "",
                    current_row["source"] or "",
                    current_row["tags"]   or "",
                    _decompress(current_row["content"]) or "",
                )
        conn.execute(
            f"UPDATE chunks SET {assignments} WHERE id = ?",
            values + [chunk_id],
        )
        if fts_affected:
            new_row = conn.execute(
                "SELECT title, source, tags, content FROM chunks WHERE id = ?",
                (chunk_id,),
            ).fetchone()
            if new_row:
                resolved_content = (
                    plain_content
                    if plain_content is not None
                    else _decompress(new_row["content"]) or ""
                )
                _fts_insert(
                    conn,
                    chunk_id,
                    new_row["title"]  or "",
                    new_row["source"] or "",
                    new_row["tags"]   or "",
                    resolved_content,
                )
    return get_chunk(chunk_id, include_content=False, db=db)


def delete_chunk(chunk_id: int, db: str = "default") -> bool:
    with db_connection(db) as conn:
        row = conn.execute(
            "SELECT title, source, tags, content FROM chunks WHERE id = ?",
            (chunk_id,),
        ).fetchone()
        if not row:
            return False
        _fts_delete(
            conn,
            chunk_id,
            row["title"]  or "",
            row["source"] or "",
            row["tags"]   or "",
            _decompress(row["content"]) or "",
        )
        conn.execute("DELETE FROM chunks WHERE id = ?", (chunk_id,))
    return True


def search_chunks(
    q: str,
    limit: int = 20,
    source: Optional[str] = None,
    tags: Optional[str] = None,
    db: str = "default",
) -> list[dict]:
    cols        = ", ".join(f"c.{col}" for col in _CHUNK_COLS)
    snippet_col = "snippet(chunks_fts, 3, '[', ']', '...', 32) AS snippet"

    sql = f"""
        SELECT {cols}, {snippet_col}
        FROM chunks_fts
        JOIN chunks c ON c.id = chunks_fts.rowid
        WHERE chunks_fts MATCH ?
    """
    fts_q = fts_build_query(q)
    if not fts_q:
        return []

    params: list = [fts_q]
    if source:
        sql += " AND c.source LIKE ?"
        params.append(f"%{source}%")
    if tags:
        sql += " AND c.tags LIKE ?"
        params.append(f"%{tags}%")

    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)

    with db_connection(db) as conn:
        rows = conn.execute(sql, params).fetchall()

    results = []
    for row in rows:
        item            = {col: row[col] for col in _CHUNK_COLS}
        item["snippet"] = row["snippet"]
        results.append(item)
    return results


def get_status(db: str = "default") -> dict:
    with db_connection(db) as conn:
        total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    db_path = _registry_get_db_path(db)
    db_size = db_path.stat().st_size if db_path.exists() else 0
    return {
        "total_chunks":  total,
        "db_size_bytes": db_size,
    }


def search_all_dbs(
    q: str,
    limit: int = 20,
    source: Optional[str] = None,
    tags: Optional[str] = None,
) -> list[dict]:
    from app.registry import list_database_ids

    fts_q = fts_build_query(q)
    if not fts_q:
        return []

    all_results: list[dict] = []
    for db_id in list_database_ids():
        try:
            results = search_chunks(q, limit=limit, source=source, tags=tags, db=db_id)
            for result in results:
                result["db"] = db_id
            all_results.extend(results)
        except Exception:
            pass
    return all_results[:limit]
