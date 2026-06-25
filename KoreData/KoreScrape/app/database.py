# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Database helpers for KoreData/KoreScrape/app.
# Owns persistence access patterns, schema-facing helpers, and storage utilities for this component.
# ====================================================================================================

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from app.config import cfg
from dbutil import compute_word_count as _compute_word_count

DATA_DIR = Path(cfg["data_dir"])
DB_PATH  = DATA_DIR / "scrape_index.db"


def _fallback_snippet(text: str, q: str, context_chars: int = 300) -> str:
    body = (text or "").strip()
    if not body:
        return ""
    terms = [part for part in q.replace('"', " ").split() if part.upper() not in {"AND", "OR", "NOT"}]
    lower = body.lower()
    pos = -1
    for term in terms:
        pos = lower.find(term.lower())
        if pos >= 0:
            break
    if pos < 0:
        return body[:context_chars].strip()
    start = max(0, pos - (context_chars // 3))
    end   = min(len(body), start + context_chars)
    return body[start:end].strip()


def _count_substring(text: str, q: str) -> int:
    haystack = (text or "").lower()
    needle   = (q or "").strip().lower()
    if not needle:
        return 0
    return haystack.count(needle)


@contextmanager
def db_connection():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scrape_chunks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                capture_id    TEXT NOT NULL,
                page_url      TEXT NOT NULL,
                page_path     TEXT NOT NULL,
                page_title    TEXT,
                captured_at   TEXT,
                chunk_index   INTEGER NOT NULL,
                content       TEXT NOT NULL,
                word_count    INTEGER,
                created_at    TEXT DEFAULT (datetime('now','utc')),
                UNIQUE(capture_id, page_url, chunk_index)
            )
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS scrape_chunks_fts USING fts5(
                page_title, page_url, content,
                tokenize='unicode61 remove_diacritics 1',
                content=''
            )
        """)


def replace_capture_chunks(capture_id: str, rows: list[dict]) -> int:
    with db_connection() as conn:
        old_rows = conn.execute(
            "SELECT id, page_title, page_url, content FROM scrape_chunks WHERE capture_id = ?",
            (capture_id,),
        ).fetchall()
        for row in old_rows:
            conn.execute(
                "INSERT INTO scrape_chunks_fts(scrape_chunks_fts, rowid, page_title, page_url, content) VALUES ('delete', ?, ?, ?, ?)",
                (row["id"], row["page_title"] or "", row["page_url"] or "", row["content"] or ""),
            )
        conn.execute("DELETE FROM scrape_chunks WHERE capture_id = ?", (capture_id,))

        inserted = 0
        for row in rows:
            cur = conn.execute(
                """
                INSERT INTO scrape_chunks (
                    capture_id, page_url, page_path, page_title, captured_at,
                    chunk_index, content, word_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    capture_id,
                    row["page_url"],
                    row["page_path"],
                    row.get("page_title"),
                    row.get("captured_at"),
                    row["chunk_index"],
                    row["content"],
                    row.get("word_count"),
                ),
            )
            row_id = cur.lastrowid
            conn.execute(
                "INSERT INTO scrape_chunks_fts(rowid, page_title, page_url, content) VALUES (?, ?, ?, ?)",
                (row_id, row.get("page_title") or "", row["page_url"], row["content"]),
            )
            inserted += 1
    return inserted


def get_chunk(chunk_id: int) -> Optional[dict]:
    with db_connection() as conn:
        row = conn.execute(
            """
            SELECT id, capture_id, page_url, page_path, page_title, captured_at,
                   chunk_index, content, word_count, created_at
              FROM scrape_chunks
             WHERE id = ?
            """,
            (chunk_id,),
        ).fetchone()
    return dict(row) if row else None


def delete_chunk(chunk_id: int) -> bool:
    with db_connection() as conn:
        row = conn.execute(
            "SELECT id, page_title, page_url, content FROM scrape_chunks WHERE id = ?",
            (chunk_id,),
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            "INSERT INTO scrape_chunks_fts(scrape_chunks_fts, rowid, page_title, page_url, content) VALUES ('delete', ?, ?, ?, ?)",
            (row["id"], row["page_title"] or "", row["page_url"] or "", row["content"] or ""),
        )
        conn.execute("DELETE FROM scrape_chunks WHERE id = ?", (chunk_id,))
    return True


def list_chunks(limit: int = 100, offset: int = 0, capture_id: Optional[str] = None) -> list[dict]:
    sql = """
        SELECT id, capture_id, page_url, page_path, page_title, captured_at,
               chunk_index, word_count, created_at, content
          FROM scrape_chunks
         WHERE 1 = 1
    """
    params: list = []
    if capture_id:
        sql += " AND capture_id = ?"
        params.append(capture_id)
    sql += " ORDER BY captured_at DESC, page_url ASC, chunk_index ASC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with db_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    results: list[dict] = []
    for row in rows:
        item = dict(row)
        content = (item.pop("content", "") or "").strip()
        item["preview"] = content[:180].strip()
        results.append(item)
    return results


def search_chunks(q: str, limit: int = 20, capture_id: Optional[str] = None) -> list[dict]:
    needle = (q or "").strip().lower()
    if not needle:
        return []

    sql = """
        SELECT id, capture_id, page_url, page_path, page_title, captured_at,
               chunk_index, word_count, content
          FROM scrape_chunks
         WHERE (
               instr(lower(coalesce(page_title, '')), ?) > 0
            OR instr(lower(coalesce(page_url,   '')), ?) > 0
            OR instr(lower(coalesce(content,    '')), ?) > 0
         )
    """
    params: list = [needle, needle, needle]
    if capture_id:
        sql += " AND capture_id = ?"
        params.append(capture_id)
    sql += " ORDER BY captured_at DESC, page_url ASC, chunk_index ASC LIMIT ?"
    params.append(limit)

    with db_connection() as conn:
        rows = conn.execute(sql, params).fetchall()

    results: list[dict] = []
    for row in rows:
        item = dict(row)
        content = row["content"] or ""
        title   = row["page_title"] or ""
        page    = row["page_url"] or ""
        item["snippet"] = _fallback_snippet(content or f"{title} {page}", q)
        item["match_count"] = (
            _count_substring(title, q)
            + _count_substring(page, q)
            + _count_substring(content, q)
        )
        item.pop("content", None)
        results.append(item)
    return results


def get_status() -> dict:
    init_db()
    with db_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total_chunks, COUNT(DISTINCT page_url) AS indexed_pages FROM scrape_chunks"
        ).fetchone()
    db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    return {
        "indexed_chunks": int(row["total_chunks"] if row else 0),
        "indexed_pages":  int(row["indexed_pages"] if row else 0),
        "db_size_bytes":  db_size,
    }


def make_chunk_row(
    capture_id: str,
    page_url: str,
    page_path: str,
    page_title: str,
    captured_at: str,
    chunk_index: int,
    content: str,
) -> dict:
    return {
        "capture_id":  capture_id,
        "page_url":    page_url,
        "page_path":   page_path,
        "page_title":  page_title,
        "captured_at": captured_at,
        "chunk_index": chunk_index,
        "content":     content,
        "word_count":  _compute_word_count(content),
    }
