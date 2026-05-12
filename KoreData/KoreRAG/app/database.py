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
import re
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT,
                source      TEXT,
                tags        TEXT,
                content     BLOB,
                word_count  INTEGER,
                created_at  TEXT DEFAULT (datetime('now','utc'))
            )
        """)
        # Contentless FTS5 — Python CRUD manages FTS explicitly with plain text.
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                title, source, tags, content,
                tokenize='unicode61 remove_diacritics 1',
                content=''
            )
        """)
        # Drop any legacy content-table triggers (old schema used auto-triggers; these are
        # incompatible with compressed storage and must be removed if present).
        for _trg in ("chunks_ai", "chunks_ad", "chunks_au"):
            conn.execute(f"DROP TRIGGER IF EXISTS {_trg}")
        # Migrate: compress any uncompressed text rows and rebuild FTS clean.
        _uncompressed = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE typeof(content)='text' AND content IS NOT NULL"
        ).fetchone()[0]
        if _uncompressed:
            _rows = conn.execute(
                "SELECT id, title, source, tags, content FROM chunks WHERE typeof(content)='text'"
            ).fetchall()
            conn.execute("DELETE FROM chunks_fts")
            for _row in _rows:
                _plain = _row["content"]
                conn.execute(
                    "UPDATE chunks SET content=?, word_count=? WHERE id=?",
                    (_compress(_plain), _compute_word_count(_plain), _row["id"]),
                )
                conn.execute(
                    "INSERT INTO chunks_fts(rowid, title, source, tags, content) VALUES (?,?,?,?,?)",
                    (_row["id"], _row["title"] or "", _row["source"] or "",
                     _row["tags"] or "", _plain),
                )


_CHUNK_COLS = ("id", "title", "source", "tags", "word_count", "created_at")
_CHUNK_COLS_WITH_CONTENT = _CHUNK_COLS + ("content",)


def _fts_delete(conn: sqlite3.Connection, chunk_id: int,
                title: str, source: str, tags: str, content: str) -> None:
    """Remove a chunk from the FTS index."""
    conn.execute(
        "INSERT INTO chunks_fts(chunks_fts, rowid, title, source, tags, content) "
        "VALUES ('delete', ?, ?, ?, ?, ?)",
        (chunk_id, title, source, tags, content),
    )


def _fts_insert(conn: sqlite3.Connection, chunk_id: int,
                title: str, source: str, tags: str, content: str) -> None:
    """Add or re-add a chunk to the FTS index."""
    conn.execute(
        "INSERT INTO chunks_fts(rowid, title, source, tags, content) VALUES (?, ?, ?, ?, ?)",
        (chunk_id, title, source, tags, content),
    )


def _row_to_dict(row: sqlite3.Row, include_content: bool = False) -> dict:
    cols = _CHUNK_COLS_WITH_CONTENT if include_content else _CHUNK_COLS
    d = {c: row[c] for c in cols}
    if include_content:
        d["content"] = _decompress(d.get("content"))
    return d


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
            f"SELECT {cols} FROM chunks WHERE id = ?", (chunk_id,)
        ).fetchone()
    return _row_to_dict(row, include_content=include_content) if row else None


def list_chunks(limit: int = 100, offset: int = 0, db: str = "default") -> list[dict]:
    cols = ", ".join(_CHUNK_COLS)
    with db_connection(db) as conn:
        rows = conn.execute(
            f"SELECT {cols} FROM chunks ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_chunk(chunk_id: int, fields: dict, db: str = "default") -> Optional[dict]:
    """Partial update. Only provided keys are written."""
    allowed = {"title", "source", "tags", "content"}
    to_set = {k: v for k, v in fields.items() if k in allowed}
    if not to_set:
        return get_chunk(chunk_id, include_content=False, db=db)

    plain_content: Optional[str] = None
    if "content" in to_set:
        plain_content = to_set["content"]
        to_set["content"] = _compress(plain_content)
        to_set["word_count"] = _compute_word_count(plain_content)

    fts_affected = bool({"title", "source", "tags", "content"} & to_set.keys())
    assignments = ", ".join(f"{k} = ?" for k in to_set)
    values = list(to_set.values())

    with db_connection(db) as conn:
        if fts_affected:
            cur_row = conn.execute(
                "SELECT title, source, tags, content FROM chunks WHERE id = ?", (chunk_id,)
            ).fetchone()
            if cur_row:
                _fts_delete(conn, chunk_id,
                            cur_row["title"] or "", cur_row["source"] or "",
                            cur_row["tags"] or "", _decompress(cur_row["content"]) or "")
        conn.execute(
            f"UPDATE chunks SET {assignments} WHERE id = ?", values + [chunk_id]
        )
        if fts_affected:
            new_row = conn.execute(
                "SELECT title, source, tags, content FROM chunks WHERE id = ?", (chunk_id,)
            ).fetchone()
            if new_row:
                resolved_content = (
                    plain_content if plain_content is not None
                    else _decompress(new_row["content"]) or ""
                )
                _fts_insert(conn, chunk_id,
                            new_row["title"] or "", new_row["source"] or "",
                            new_row["tags"] or "", resolved_content)
    return get_chunk(chunk_id, include_content=False, db=db)


def delete_chunk(chunk_id: int, db: str = "default") -> bool:
    with db_connection(db) as conn:
        row = conn.execute(
            "SELECT title, source, tags, content FROM chunks WHERE id = ?", (chunk_id,)
        ).fetchone()
        if not row:
            return False
        _fts_delete(conn, chunk_id,
                    row["title"] or "", row["source"] or "",
                    row["tags"] or "", _decompress(row["content"]) or "")
        conn.execute("DELETE FROM chunks WHERE id = ?", (chunk_id,))
    return True


def search_chunks(
    q: str,
    limit: int = 20,
    source: Optional[str] = None,
    tags: Optional[str] = None,
    db: str = "default",
) -> list[dict]:
    """FTS search returning metadata + snippet from the best-matching content."""
    cols = ", ".join(f"c.{col}" for col in _CHUNK_COLS)
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
        d = {c: row[c] for c in _CHUNK_COLS}
        d["snippet"] = row["snippet"]
        results.append(d)
    return results


def get_status(db: str = "default") -> dict:
    with db_connection(db) as conn:
        total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    db_path = _registry_get_db_path(db)
    db_size = db_path.stat().st_size if db_path.exists() else 0
    return {
        "total_chunks": total,
        "db_size_bytes": db_size,
    }


def search_all_dbs(
    q: str,
    limit: int = 20,
    source: Optional[str] = None,
    tags: Optional[str] = None,
) -> list[dict]:
    """Search all registered databases and merge results (up to limit total)."""
    from app.registry import list_database_ids
    fts_q = fts_build_query(q)
    if not fts_q:
        return []
    all_results: list[dict] = []
    for db_id in list_database_ids():
        try:
            results = search_chunks(q, limit=limit, source=source, tags=tags, db=db_id)
            for r in results:
                r["db"] = db_id
            all_results.extend(results)
        except Exception:
            pass
    return all_results[:limit]


# ---------------------------------------------------------------------------
# Navigation tables (Hansard-specific layer 2)
# ---------------------------------------------------------------------------

def has_hansard_tables(db: str = "default") -> bool:
    """Return True if this database has Hansard navigation tables."""
    with db_connection(db) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='h_sittings'"
        ).fetchone()
    return row is not None


def get_sittings(db: str = "default") -> list[dict]:
    """List sitting dates with debate counts, newest first."""
    with db_connection(db) as conn:
        rows = conn.execute(
            "SELECT sitting_date, house, debate_count FROM h_sittings ORDER BY sitting_date DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_sitting_debates(date: str, db: str = "default") -> list[dict]:
    """List debates for a sitting date with speech counts."""
    with db_connection(db) as conn:
        rows = conn.execute("""
            SELECT d.uuid, d.title, d.item_number, d.url,
                   COUNT(s.chunk_id) AS speech_count
            FROM h_debates d
            LEFT JOIN h_speeches s ON s.debate_uuid = d.uuid
            WHERE d.sitting_date = ?
            GROUP BY d.uuid
            ORDER BY d.item_number
        """, (date,)).fetchall()
    return [dict(r) for r in rows]


def get_members(db: str = "default") -> list[dict]:
    """List members ordered by speech count descending."""
    with db_connection(db) as conn:
        rows = conn.execute("""
            SELECT m.member_id, m.display_name, m.party, m.constituency, m.chunk_id,
                   COUNT(s.chunk_id) AS speech_count
            FROM h_members m
            LEFT JOIN h_speeches s ON s.member_id = m.member_id
            GROUP BY m.member_id
            ORDER BY speech_count DESC, m.display_name
        """).fetchall()
    return [dict(r) for r in rows]


def get_debate(debate_uuid: str, db: str = "default") -> Optional[dict]:
    """Return debate metadata for a single UUID."""
    with db_connection(db) as conn:
        row = conn.execute(
            "SELECT uuid, title, item_number, url, sitting_date FROM h_debates WHERE uuid = ?",
            (debate_uuid,),
        ).fetchone()
    return dict(row) if row else None


def get_debate_speeches(debate_uuid: str, db: str = "default") -> list[dict]:
    """List speeches for a debate in order, with speaker and decompressed content."""
    with db_connection(db) as conn:
        rows = conn.execute("""
            SELECT s.chunk_id, s.speech_order, s.speaker_raw,
                   m.member_id, m.display_name AS member_name, m.party, m.constituency,
                   c.title, c.word_count, c.content
            FROM h_speeches s
            JOIN chunks c ON c.id = s.chunk_id
            LEFT JOIN h_members m ON m.member_id = s.member_id
            WHERE s.debate_uuid = ?
            ORDER BY s.speech_order
        """, (debate_uuid,)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["content"] = _decompress(d.get("content"))
        result.append(d)
    return result


_HONORIFICS = re.compile(
    r'^(Mr|Mrs|Ms|Miss|Dame|Sir|Lord|Baroness|Dr|The\s+\S+)\s+',
    flags=re.IGNORECASE,
)


def _bare_name(display_name: str) -> str:
    """Strip honorific prefix so 'Ms Diane Abbott' → 'Diane Abbott'."""
    return _HONORIFICS.sub("", display_name).strip()


def get_member_by_id(member_id: int, db: str = "default") -> Optional[dict]:
    """Return member metadata plus decompressed bio content (may be None)."""
    with db_connection(db) as conn:
        row = conn.execute(
            "SELECT member_id, display_name, house, party, constituency, chunk_id "
            "FROM h_members WHERE member_id = ?",
            (member_id,),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        if d["chunk_id"]:
            c = conn.execute(
                "SELECT content, word_count FROM chunks WHERE id = ?", (d["chunk_id"],)
            ).fetchone()
            if c:
                d["bio"] = _decompress(c["content"])
                d["bio_word_count"] = c["word_count"]
    return d


def get_member_speeches(member_id: int, db: str = "default") -> list[dict]:
    """List speeches by a member with content, matched by name against speaker_raw."""
    with db_connection(db) as conn:
        m = conn.execute(
            "SELECT display_name FROM h_members WHERE member_id = ?", (member_id,)
        ).fetchone()
        if m is None:
            return []
        name = _bare_name(m["display_name"])
        rows = conn.execute("""
            SELECT s.chunk_id, s.speech_order, s.speaker_raw,
                   d.uuid AS debate_uuid, d.title AS debate_title, d.sitting_date,
                   c.word_count, c.content
            FROM h_speeches s
            JOIN h_debates d ON d.uuid = s.debate_uuid
            JOIN chunks c ON c.id = s.chunk_id
            WHERE s.speaker_raw = ? OR s.speaker_raw LIKE ?
            ORDER BY d.sitting_date DESC, s.speech_order
        """, (name, name + " (%")).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["content"] = _decompress(d.get("content"))
        result.append(d)
    return result
