# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# SQLite database layer for KoreFeed.
#
# Schema:
#   entries  -- feed article metadata and content, organised by domain
#
# Supports RFC 2822 and ISO 8601 date parsing so diverse feed formats are handled
# uniformly.  WAL mode enabled.  Age-based retention is enforced by the ingest scheduler.
#
# Related modules:
#   - app/server.py      -- article read operations
#   - app/ingest.py      -- background ingest scheduler writes new entries
#   - CommonCode/compress.py  -- article body compression (if enabled)
# ====================================================================================================
import sqlite3
import json
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate as _rfc_parsedate
from pathlib import Path
from typing import Any, Optional

from app.config import cfg
from dbutil import fts_build_query

DATA_DIR = Path(cfg["data_dir"])

_domains_ready: set[str] = set()
_domains_lock = threading.Lock()


class FeedDatabaseError(RuntimeError):
    pass


_SENTENCE_SCHEMA_COLUMNS = (
    "id",
    "entry_id",
    "sentence_index",
    "source_field",
    "char_start",
    "char_end",
    "chroma_indexed_at",
    "deleted",
)


def _split_sentences(text: str) -> list[tuple[int, int, str]]:
    """Split text into sentence-like spans with stable offsets into the original text."""
    text = str(text or "")
    if not text:
        return []

    sentences: list[tuple[int, int, str]] = []
    start = 0
    i = 0
    n = len(text)
    while start < n and text[start].isspace():
        start += 1
    i = start
    while i < n:
        if text[i] in ".!?":
            end = i + 1
            while end < n and text[end] in "\"')]":
                end += 1
            if end == n or text[end].isspace():
                sentence = text[start:end].strip()
                if sentence:
                    sentences.append((start, end, sentence))
                while end < n and text[end].isspace():
                    end += 1
                start = end
                i = end
                continue
        i += 1

    if start < n:
        sentence = text[start:].strip()
        if sentence:
            sentences.append((start, n, sentence))
    return sentences


def _index_entry_sentences(
    conn: sqlite3.Connection,
    entry_id: int,
    headline: str,
    page_text: str,
) -> None:
    rows: list[tuple[int, int, str, int, int]] = []
    sentence_index = 0
    for source_field, raw_text in (("headline", headline), ("page_text", page_text)):
        for char_start, char_end, _sentence_text in _split_sentences(raw_text):
            rows.append(
                (entry_id, sentence_index, source_field, char_start, char_end)
            )
            sentence_index += 1
    if rows:
        conn.executemany(
            """
            INSERT INTO sentences
                (entry_id, sentence_index, source_field, char_start, char_end)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )


def _rebuild_sentence_index(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM sentences")
    _backfill_entry_sentences(conn)


def _backfill_entry_sentences(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT e.id, e.headline, e.page_text
        FROM entries e
        WHERE e.deleted = 0
          AND NOT EXISTS (
              SELECT 1
              FROM sentences s
              WHERE s.entry_id = e.id AND s.deleted = 0
          )
        """
    ).fetchall()
    for row in rows:
        _index_entry_sentences(
            conn,
            int(row["id"]),
            row["headline"] or "",
            row["page_text"] or "",
        )


def _sentence_index_needs_rebuild(conn: sqlite3.Connection) -> bool:
    sentence_cols = {row[1] for row in conn.execute("PRAGMA table_info(sentences)").fetchall()}
    if "sentence_text" in sentence_cols:
        row = conn.execute(
            "SELECT 1 FROM sentences WHERE sentence_text IS NOT NULL AND sentence_text != '' LIMIT 1"
        ).fetchone()
        if row:
            return True
    return False


def _sentence_schema_needs_normalization(conn: sqlite3.Connection) -> bool:
    current_cols = tuple(row[1] for row in conn.execute("PRAGMA table_info(sentences)").fetchall())
    if not current_cols:
        return False
    return current_cols != _SENTENCE_SCHEMA_COLUMNS


def _normalize_sentence_schema(conn: sqlite3.Connection) -> None:
    current_cols = tuple(row[1] for row in conn.execute("PRAGMA table_info(sentences)").fetchall())
    if not current_cols or current_cols == _SENTENCE_SCHEMA_COLUMNS:
        return

    current_set  = set(current_cols)
    required_set = set(_SENTENCE_SCHEMA_COLUMNS)
    compatible   = required_set.issubset(current_set)

    conn.execute("DROP TABLE IF EXISTS sentences_new")
    conn.execute("""
        CREATE TABLE sentences_new (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id          INTEGER NOT NULL,
            sentence_index    INTEGER NOT NULL,
            source_field      TEXT NOT NULL,
            char_start        INTEGER NOT NULL,
            char_end          INTEGER NOT NULL,
            chroma_indexed_at TEXT,
            deleted           INTEGER NOT NULL DEFAULT 0,
            UNIQUE(entry_id, sentence_index)
        )
    """)

    if compatible:
        conn.execute("""
            INSERT INTO sentences_new
                (id, entry_id, sentence_index, source_field, char_start, char_end, chroma_indexed_at, deleted)
            SELECT
                id,
                entry_id,
                sentence_index,
                source_field,
                char_start,
                char_end,
                chroma_indexed_at,
                deleted
            FROM sentences
        """)

    conn.execute("DROP TABLE sentences")
    conn.execute("ALTER TABLE sentences_new RENAME TO sentences")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sentences_entry_id ON sentences(entry_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sentences_chroma_indexed_at ON sentences(chroma_indexed_at)")

    if not compatible:
        _backfill_entry_sentences(conn)


def _extract_sentence_text(entry_row: sqlite3.Row | dict, sentence_row: sqlite3.Row | dict) -> str:
    source_field = str(sentence_row["source_field"] or "")
    source_text = str(entry_row.get(source_field, "") if isinstance(entry_row, dict) else entry_row[source_field] or "")
    char_start = max(0, int(sentence_row["char_start"]))
    char_end = max(char_start, int(sentence_row["char_end"]))
    return source_text[char_start:char_end].strip()


def _sentence_locator(domain: str, sentence_id: int) -> str:
    return f"feeds/{domain}/{sentence_id}"


def _parse_published(s: str) -> Optional[datetime]:
    """Parse an RSS date string to a naive UTC datetime. Returns None on failure."""
    if not s:
        return None
    # RFC 2822 (most common in RSS feeds)
    try:
        t = _rfc_parsedate(s)
        if t:
            return datetime(*t[:6])
    except Exception:
        pass
    # ISO 8601 / Atom  (e.g. "2026-01-09T12:00:00Z" or stored by newer ingest)
    try:
        return datetime.fromisoformat(s.rstrip("Z").replace("T", " ")[:19])
    except Exception:
        pass
    return None


def _sanitize_domain(domain: str) -> str:
    """Strip path traversal characters; allow only word chars and hyphens."""
    return re.sub(r"[^\w\-]", "_", domain)


def get_db_path(domain: str) -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    return DATA_DIR / f"{_sanitize_domain(domain)}.db"


@contextmanager
def db_connection(domain: str):
    conn = sqlite3.connect(str(get_db_path(domain)))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(domain: str) -> None:
    with db_connection(domain) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                feed_name    TEXT NOT NULL,
                headline     TEXT,
                url          TEXT UNIQUE,
                published    TEXT,
                metadata     TEXT,
                page_text    TEXT,
                ingested_at  TEXT DEFAULT (datetime('now')),
                deleted      INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sentences (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id       INTEGER NOT NULL,
                sentence_index INTEGER NOT NULL,
                source_field   TEXT NOT NULL,
                char_start     INTEGER NOT NULL,
                char_end       INTEGER NOT NULL,
                chroma_indexed_at TEXT,
                deleted        INTEGER NOT NULL DEFAULT 0,
                UNIQUE(entry_id, sentence_index)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS domain_settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # migrate existing databases that pre-date the deleted column
        cols = {row[1] for row in conn.execute("PRAGMA table_info(entries)").fetchall()}
        if "deleted" not in cols:
            conn.execute("ALTER TABLE entries ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
        sentence_cols = {row[1] for row in conn.execute("PRAGMA table_info(sentences)").fetchall()}
        if "deleted" not in sentence_cols:
            conn.execute("ALTER TABLE sentences ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
        if "chroma_indexed_at" not in sentence_cols:
            conn.execute("ALTER TABLE sentences ADD COLUMN chroma_indexed_at TEXT")
        if _sentence_schema_needs_normalization(conn):
            _normalize_sentence_schema(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sentences_entry_id ON sentences(entry_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sentences_chroma_indexed_at ON sentences(chroma_indexed_at)"
        )
        # normalise any published values not yet in UTC YYYY-MM-DD HH:MM:SS
        _normalise_published(conn)
        if _sentence_index_needs_rebuild(conn):
            _rebuild_sentence_index(conn)
        else:
            _backfill_entry_sentences(conn)

        # FTS5 virtual table for word-boundary search + BM25 ranking
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
                headline, page_text,
                tokenize='unicode61 remove_diacritics 1'
            )
        """)
        # Back-fill any existing entries not yet in the FTS index
        conn.execute("""
            INSERT INTO entries_fts(rowid, headline, page_text)
            SELECT e.id, COALESCE(e.headline, ''), COALESCE(e.page_text, '')
            FROM entries e
            WHERE e.deleted = 0
              AND e.id NOT IN (SELECT rowid FROM entries_fts)
        """)


def backfill_sentence_index(domain: str) -> dict:
    with _domains_lock:
        if domain not in _domains_ready:
            init_db(domain)
            _domains_ready.add(domain)
    with db_connection(domain) as conn:
        before = int(conn.execute("SELECT COUNT(*) FROM sentences WHERE deleted = 0").fetchone()[0])
        _backfill_entry_sentences(conn)
        after = int(conn.execute("SELECT COUNT(*) FROM sentences WHERE deleted = 0").fetchone()[0])
    return {
        "domain":          domain,
        "mode":            "backfill",
        "sentences_added": max(0, after - before),
        "sentence_count":  after,
    }


def rebuild_sentence_index(domain: str, entry_id: Optional[int] = None) -> dict:
    with _domains_lock:
        if domain not in _domains_ready:
            init_db(domain)
            _domains_ready.add(domain)

    deleted_sentence_ids: list[int] = []
    rebuilt_entries:      int        = 0
    rebuilt_sentences:    int        = 0

    with db_connection(domain) as conn:
        if entry_id is None:
            deleted_sentence_ids = [
                int(row[0]) for row in conn.execute(
                    "SELECT id FROM sentences WHERE deleted = 0 ORDER BY id"
                ).fetchall()
            ]
            conn.execute("DELETE FROM sentences")
            _backfill_entry_sentences(conn)
            rebuilt_entries = int(conn.execute(
                "SELECT COUNT(*) FROM entries WHERE deleted = 0"
            ).fetchone()[0])
            rebuilt_sentences = int(conn.execute(
                "SELECT COUNT(*) FROM sentences WHERE deleted = 0"
            ).fetchone()[0])
        else:
            entry = conn.execute(
                "SELECT id, headline, page_text FROM entries WHERE id = ? AND deleted = 0",
                (entry_id,),
            ).fetchone()
            if not entry:
                raise FeedDatabaseError(f"Entry {entry_id} not found in domain '{domain}'.")
            deleted_sentence_ids = [
                int(row[0]) for row in conn.execute(
                    "SELECT id FROM sentences WHERE entry_id = ? AND deleted = 0 ORDER BY id",
                    (entry_id,),
                ).fetchall()
            ]
            conn.execute("DELETE FROM sentences WHERE entry_id = ?", (entry_id,))
            _index_entry_sentences(
                conn,
                int(entry["id"]),
                str(entry["headline"] or ""),
                str(entry["page_text"] or ""),
            )
            rebuilt_entries = 1
            rebuilt_sentences = int(conn.execute(
                "SELECT COUNT(*) FROM sentences WHERE entry_id = ? AND deleted = 0",
                (entry_id,),
            ).fetchone()[0])

    if deleted_sentence_ids:
        try:
            from app.chroma_index import delete_sentence_ids
            delete_sentence_ids(domain, deleted_sentence_ids)
        except Exception:
            pass

    try:
        from app.chroma_index import sync_entry_sentences, sync_pending_sentences
        if entry_id is None:
            sync_pending_sentences(domain, batch_size=250)
        else:
            sync_entry_sentences(domain, int(entry_id))
    except Exception:
        pass

    return {
        "domain":               domain,
        "mode":                 "rebuild",
        "entry_id":             entry_id,
        "rebuilt_entries":      rebuilt_entries,
        "rebuilt_sentences":    rebuilt_sentences,
        "deleted_sentence_ids": len(deleted_sentence_ids),
    }


def _normalise_published(conn: sqlite3.Connection) -> None:
    """Rewrite existing published values to UTC 'YYYY-MM-DD HH:MM:SS' for consistent sorting."""
    rows = conn.execute(
        "SELECT id, published FROM entries WHERE published IS NOT NULL AND published != ''"
    ).fetchall()
    updates = []
    for row in rows:
        raw = row["published"]
        # already canonical: starts with YYYY-MM-DD and has a space at position 10
        if len(raw) >= 19 and raw[4] == "-" and raw[7] == "-" and raw[10] == " ":
            continue
        dt = _parse_published(raw)
        if dt:
            updates.append((dt.strftime("%Y-%m-%d %H:%M:%S"), row["id"]))
    if updates:
        conn.executemany("UPDATE entries SET published = ? WHERE id = ?", updates)


def insert_entry(
    domain: str,
    feed_name: str,
    headline: str,
    url: str,
    published: str,
    metadata: dict,
    page_text: str,
) -> bool:
    inserted_entry_id: int | None = None
    with _domains_lock:
        if domain not in _domains_ready:
            init_db(domain)
            _domains_ready.add(domain)
    with db_connection(domain) as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO entries
                (feed_name, headline, url, published, metadata, page_text)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (feed_name, headline, url, published, json.dumps(metadata), page_text),
        )
        # Only index rows that were actually inserted (not ignored duplicates)
        if cur.rowcount > 0 and cur.lastrowid:
            _index_entry_sentences(conn, cur.lastrowid, headline or "", page_text or "")
            conn.execute(
                "INSERT INTO entries_fts(rowid, headline, page_text) VALUES (?, ?, ?)",
                (cur.lastrowid, headline or "", page_text or ""),
            )
            inserted_entry_id = int(cur.lastrowid)
    if inserted_entry_id is None:
        return False
    try:
        from app.chroma_index import sync_entry_sentences

        sync_entry_sentences(domain, inserted_entry_id)
    except Exception:
        pass
    return True


def get_entries(domain: str, limit: int = 50, offset: int = 0) -> list[dict]:
    try:
        with db_connection(domain) as conn:
            rows = conn.execute(
                "SELECT * FROM entries WHERE deleted = 0 ORDER BY published DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        raise FeedDatabaseError(f"Could not load entries for domain '{domain}': {exc}") from exc


def get_entry(domain: str, entry_id: int) -> Optional[dict]:
    try:
        with db_connection(domain) as conn:
            row = conn.execute(
                "SELECT * FROM entries WHERE id = ? AND deleted = 0", (entry_id,)
            ).fetchone()
            return dict(row) if row else None
    except Exception as exc:
        raise FeedDatabaseError(f"Could not load entry {entry_id} for domain '{domain}': {exc}") from exc


def get_entry_sentences(domain: str, entry_id: int, include_deleted: bool = False) -> list[dict]:
    try:
        with db_connection(domain) as conn:
            rows = conn.execute(
                """
                SELECT s.id, s.entry_id, s.sentence_index, s.source_field,
                       s.deleted,
                       s.char_start, s.char_end,
                       e.feed_name, e.headline, e.page_text, e.url, e.published, e.ingested_at
                FROM sentences s
                JOIN entries e ON e.id = s.entry_id
                WHERE s.entry_id = ? AND e.deleted = 0
                  AND (? = 1 OR s.deleted = 0)
                ORDER BY s.sentence_index ASC
                """,
                (entry_id, 1 if include_deleted else 0),
            ).fetchall()
            results: list[dict] = []
            for row in rows:
                item = dict(row)
                item["sentence_text"] = _extract_sentence_text(item, item)
                item.pop("page_text", None)
                results.append(item)
            return results
    except Exception as exc:
        raise FeedDatabaseError(
            f"Could not load sentences for entry {entry_id} in domain '{domain}': {exc}"
        ) from exc


def get_sentence(domain: str, sentence_id: int) -> Optional[dict]:
    try:
        with db_connection(domain) as conn:
            row = conn.execute(
                """
                SELECT s.id, s.entry_id, s.sentence_index, s.source_field,
                       s.deleted,
                       s.char_start, s.char_end,
                       e.feed_name, e.headline, e.page_text, e.url, e.published, e.ingested_at
                FROM sentences s
                JOIN entries e ON e.id = s.entry_id
                WHERE s.id = ? AND s.deleted = 0 AND e.deleted = 0
                """,
                (sentence_id,),
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            item["sentence_text"] = _extract_sentence_text(item, item)
            item.pop("page_text", None)
            return item
    except Exception as exc:
        raise FeedDatabaseError(
            f"Could not load sentence {sentence_id} for domain '{domain}': {exc}"
        ) from exc


def update_entry_page_text(domain: str, entry_id: int, page_text: str) -> dict:
    normalized_text = str(page_text or "")
    with _domains_lock:
        if domain not in _domains_ready:
            init_db(domain)
            _domains_ready.add(domain)

    previous_sentence_ids: list[int] = []
    headline_text = ""

    with db_connection(domain) as conn:
        entry = conn.execute(
            "SELECT id, headline FROM entries WHERE id = ? AND deleted = 0",
            (entry_id,),
        ).fetchone()
        if not entry:
            raise FeedDatabaseError(f"Entry {entry_id} not found in domain '{domain}'.")

        previous_sentence_ids = [
            int(row[0]) for row in conn.execute(
                "SELECT id FROM sentences WHERE entry_id = ? ORDER BY id",
                (entry_id,),
            ).fetchall()
        ]
        headline_text = str(entry["headline"] or "")

        conn.execute(
            "UPDATE entries SET page_text = ? WHERE id = ?",
            (normalized_text, entry_id),
        )
        conn.execute(
            "DELETE FROM entries_fts WHERE rowid = ?",
            (entry_id,),
        )
        conn.execute(
            "INSERT INTO entries_fts(rowid, headline, page_text) VALUES (?, ?, ?)",
            (entry_id, headline_text, normalized_text),
        )
        conn.execute("DELETE FROM sentences WHERE entry_id = ?", (entry_id,))
        _index_entry_sentences(conn, entry_id, headline_text, normalized_text)

        sentence_count = int(conn.execute(
            "SELECT COUNT(*) FROM sentences WHERE entry_id = ? AND deleted = 0",
            (entry_id,),
        ).fetchone()[0])

    if previous_sentence_ids:
        try:
            from app.chroma_index import delete_sentence_ids
            delete_sentence_ids(domain, previous_sentence_ids)
        except Exception:
            pass

    try:
        from app.chroma_index import sync_entry_sentences
        sync_entry_sentences(domain, entry_id)
    except Exception:
        pass

    return {
        "domain":         domain,
        "entry_id":       entry_id,
        "sentence_count": sentence_count,
        "page_text_len":  len(normalized_text),
    }


def set_sentence_deleted(domain: str, sentence_id: int, deleted: bool) -> dict:
    with _domains_lock:
        if domain not in _domains_ready:
            init_db(domain)
            _domains_ready.add(domain)

    entry_id: int | None = None

    with db_connection(domain) as conn:
        row = conn.execute(
            "SELECT id, entry_id, deleted FROM sentences WHERE id = ?",
            (sentence_id,),
        ).fetchone()
        if not row:
            raise FeedDatabaseError(f"Sentence {sentence_id} not found in domain '{domain}'.")
        entry_id = int(row["entry_id"])
        conn.execute(
            "UPDATE sentences SET deleted = ?, chroma_indexed_at = CASE WHEN ? = 0 THEN NULL ELSE chroma_indexed_at END WHERE id = ?",
            (1 if deleted else 0, 1 if deleted else 0, sentence_id),
        )

    try:
        from app.chroma_index import delete_sentence_ids, sync_entry_sentences
        if deleted:
            delete_sentence_ids(domain, [sentence_id])
        elif entry_id is not None:
            sync_entry_sentences(domain, entry_id)
    except Exception:
        pass

    sentence = get_sentence(domain, sentence_id)
    if sentence is None and deleted:
        entry = get_entry(domain, entry_id) if entry_id is not None else None
        if entry is not None:
            sentence_rows = get_entry_sentences(domain, entry_id, include_deleted=True)
            sentence = next((row for row in sentence_rows if int(row["id"]) == int(sentence_id)), None)

    return {
        "domain":      domain,
        "sentence_id": sentence_id,
        "entry_id":    entry_id,
        "deleted":     deleted,
        "sentence":    sentence,
    }


def get_sentences_for_chroma(
    domain: str,
    limit: int = 250,
    sentence_ids: Optional[list[int]] = None,
    only_unindexed: bool = False,
) -> list[dict]:
    try:
        with db_connection(domain) as conn:
            clauses = ["s.deleted = 0", "e.deleted = 0"]
            params: list[object] = []
            if sentence_ids:
                validated = [int(i) for i in sentence_ids]
                placeholders = ",".join("?" * len(validated))
                clauses.append(f"s.id IN ({placeholders})")
                params.extend(validated)
            if only_unindexed:
                clauses.append("(s.chroma_indexed_at IS NULL OR s.chroma_indexed_at = '')")
            params.append(max(1, int(limit)))
            rows = conn.execute(
                f"""
                SELECT s.id, s.entry_id, s.sentence_index, s.source_field,
                       s.char_start, s.char_end, s.chroma_indexed_at,
                       e.feed_name, e.headline, e.page_text, e.url, e.published, e.ingested_at
                FROM sentences s
                JOIN entries e ON e.id = s.entry_id
                WHERE {' AND '.join(clauses)}
                ORDER BY s.id ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
            results: list[dict] = []
            for row in rows:
                item = dict(row)
                item["sentence_text"] = _extract_sentence_text(item, item)
                item["locator"] = _sentence_locator(domain, int(item["id"]))
                item.pop("page_text", None)
                results.append(item)
            return results
    except Exception as exc:
        raise FeedDatabaseError(
            f"Could not load sentences for Chroma sync in domain '{domain}': {exc}"
        ) from exc


def mark_sentences_chroma_indexed(domain: str, sentence_ids: list[int]) -> int:
    if not sentence_ids:
        return 0
    validated = [int(i) for i in sentence_ids]
    placeholders = ",".join("?" * len(validated))
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with db_connection(domain) as conn:
            cur = conn.execute(
                f"""
                UPDATE sentences
                SET chroma_indexed_at = ?
                WHERE deleted = 0 AND id IN ({placeholders})
                """,
                [timestamp, *validated],
            )
            return cur.rowcount
    except Exception:
        return 0


def reset_sentence_chroma_index(domain: str, entry_id: Optional[int] = None) -> int:
    try:
        with db_connection(domain) as conn:
            if entry_id is None:
                cur = conn.execute(
                    """
                    UPDATE sentences
                    SET chroma_indexed_at = NULL
                    WHERE deleted = 0
                    """
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE sentences
                    SET chroma_indexed_at = NULL
                    WHERE deleted = 0 AND entry_id = ?
                    """,
                    (int(entry_id),),
                )
            return cur.rowcount
    except Exception:
        return 0


def search_entries_detailed(
    domain: Optional[str],
    query: str,
    limit: int = 50,
    include_body: bool = False,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> tuple[list[dict], list[dict[str, str]]]:
    body_col    = ", e.page_text" if include_body else ""
    domains     = [domain] if domain else list_domains()
    fts_query   = fts_build_query(query)
    if not fts_query:
        return [], []
    per_domain_cap = max(limit, 20)

    date_clauses = ""
    date_params: list = []
    if since:
        date_clauses += " AND e.published >= ?"
        date_params.append(since)
    if until:
        date_clauses += " AND e.published <= ?"
        date_params.append(until)

    results:        list[dict]              = []
    failed_domains: list[dict[str, str]]    = []
    for d in domains:
        try:
            with db_connection(d) as conn:
                rows = conn.execute(
                    f"""
                    SELECT e.id, e.feed_name, e.headline, e.url, e.published,
                           e.ingested_at{body_col}, ? AS domain
                         , bm25(entries_fts) AS score
                    FROM entries_fts f
                    JOIN entries e ON e.id = f.rowid
                    WHERE entries_fts MATCH ?
                      AND e.deleted = 0
                      {date_clauses}
                    ORDER BY score, e.published DESC
                    LIMIT ?
                    """,
                    (d, fts_query, *date_params, per_domain_cap),
                ).fetchall()
                results.extend([dict(r) for r in rows])
        except Exception as exc:
            failed_domains.append({
                "domain": d,
                "error":  str(exc),
            })
    results.sort(key=lambda r: r.get("published") or "", reverse=True)
    return results[:limit], failed_domains


def search_entries(
    domain: Optional[str],
    query: str,
    limit: int = 50,
    include_body: bool = False,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> list[dict]:
    results, _failed_domains = search_entries_detailed(
        domain=domain,
        query=query,
        limit=limit,
        include_body=include_body,
        since=since,
        until=until,
    )
    return results


def get_recent_entries(
    domain: Optional[str],
    hours: float = 24.0,
    limit: int = 50,
) -> list[dict]:
    modifier       = f"-{hours} hours"
    domains        = [domain] if domain else list_domains()
    per_domain_cap = max(limit, 20)
    results: list[dict] = []
    for d in domains:
        try:
            with db_connection(d) as conn:
                rows = conn.execute(
                    """
                    SELECT id, feed_name, headline, url, published, ingested_at,
                           ? AS domain
                    FROM entries
                    WHERE deleted = 0 AND ingested_at >= datetime('now', ?)
                    ORDER BY published DESC LIMIT ?
                    """,
                    (d, modifier, per_domain_cap),
                ).fetchall()
                results.extend([dict(r) for r in rows])
        except Exception:
            pass
    results.sort(key=lambda r: r.get("published") or "", reverse=True)
    return results[:limit]


def list_domains() -> list[str]:
    DATA_DIR.mkdir(exist_ok=True)
    return [f.stem for f in sorted(DATA_DIR.glob("*.db"))]


def _tombstone(domain: str, conn: sqlite3.Connection, where: str, params: list) -> int:
    """Soft-delete: blank content fields and set deleted=1. URL is preserved for dedup."""
    # Capture IDs before the update so we can remove them from the FTS index
    ids = [
        r[0] for r in conn.execute(
            f"SELECT id FROM entries WHERE deleted=0 AND {where}", params
        ).fetchall()
    ]
    sentence_ids: list[int] = []
    if ids:
        placeholders = ",".join("?" * len(ids))
        sentence_ids = [
            int(r[0]) for r in conn.execute(
                f"SELECT id FROM sentences WHERE deleted=0 AND entry_id IN ({placeholders})",
                ids,
            ).fetchall()
        ]
    cur = conn.execute(
        f"UPDATE entries SET headline=NULL, page_text=NULL, metadata=NULL, deleted=1"
        f" WHERE deleted=0 AND {where}",
        params,
    )
    if ids:
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE sentences SET deleted=1 WHERE deleted=0 AND entry_id IN ({placeholders})",
            ids,
        )
    for id_ in ids:
        conn.execute("DELETE FROM entries_fts WHERE rowid=?", (id_,))
    if sentence_ids:
        try:
            from app.chroma_index import delete_sentence_ids

            delete_sentence_ids(domain, sentence_ids)
        except Exception:
            pass
    return cur.rowcount


def delete_entry(domain: str, entry_id: int) -> bool:
    """Soft-delete a single entry. Returns True if the row was tombstoned."""
    try:
        with db_connection(domain) as conn:
            return _tombstone(domain, conn, "id = ?", [entry_id]) > 0
    except Exception:
        return False


def delete_entries_by_feed(domain: str, feed_name: str) -> int:
    """Soft-delete all entries from a specific feed. Returns count tombstoned."""
    try:
        with db_connection(domain) as conn:
            return _tombstone(domain, conn, "feed_name = ?", [feed_name])
    except Exception:
        return 0


def delete_entries_older_than(domain: str, days: float) -> int:
    """Soft-delete entries whose *published* date is more than `days` days ago."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with db_connection(domain) as conn:
            return _tombstone(
                domain,
                conn,
                "published IS NOT NULL AND published != '' AND published < ?",
                [cutoff],
            )
    except Exception:
        return 0


def delete_entries_by_ids(domain: str, ids: list[int]) -> int:
    """Soft-delete multiple entries by ID list. Returns count tombstoned."""
    if not ids:
        return 0
    validated = [int(i) for i in ids]
    placeholders = ",".join("?" * len(validated))
    try:
        with db_connection(domain) as conn:
            return _tombstone(domain, conn, f"id IN ({placeholders})", validated)
    except Exception:
        return 0


def get_entry_count(domain: str) -> int:
    try:
        with db_connection(domain) as conn:
            row = conn.execute("SELECT COUNT(*) FROM entries WHERE deleted = 0").fetchone()
            return row[0]
    except Exception:
        return 0


def get_feed_counts(domain: str) -> dict[str, int]:
    """Return {feed_name: entry_count} for all non-deleted entries in a domain."""
    try:
        with db_connection(domain) as conn:
            rows = conn.execute(
                "SELECT feed_name, COUNT(*) AS cnt FROM entries WHERE deleted = 0 GROUP BY feed_name"
            ).fetchall()
            return {r["feed_name"]: r["cnt"] for r in rows}
    except Exception:
        return {}


def get_domain_age_settings(domain: str) -> dict:
    """Return age-gating settings for a domain.

    Returns a dict with keys: mode ('none'|'days_previous'|'calendar_period'),
    days (int|None), start_date (str|None), end_date (str|None).
    """
    try:
        with db_connection(domain) as conn:
            rows = conn.execute(
                "SELECT key, value FROM domain_settings "
                "WHERE key IN ('age_mode','age_days','age_start','age_end','max_age_days')"
            ).fetchall()
            s = {r["key"]: r["value"] for r in rows}
            # backwards compat: migrate legacy max_age_days → days_previous
            if "age_mode" not in s and s.get("max_age_days"):
                return {
                    "mode": "days_previous",
                    "days": int(s["max_age_days"]),
                    "start_date": None,
                    "end_date": None,
                }
            mode = s.get("age_mode", "none") or "none"
            days = int(s["age_days"]) if s.get("age_days") else None
            return {
                "mode": mode,
                "days": days,
                "start_date": s.get("age_start"),
                "end_date": s.get("age_end"),
            }
    except Exception:
        return {"mode": "none", "days": None, "start_date": None, "end_date": None}


def set_domain_age_settings(
    domain: str,
    mode: str,
    days: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> None:
    """Persist age-gating settings for a domain."""
    init_db(domain)
    with db_connection(domain) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO domain_settings (key, value) VALUES (?, ?)",
            [
                ("age_mode", mode),
                ("age_days", str(days) if days else None),
                ("age_start", start_date),
                ("age_end", end_date),
                # clear legacy key so it doesn't cause confused fallback
                ("max_age_days", None),
            ],
        )


def delete_entries_outside_calendar(domain: str, start_date: str, end_date: str) -> int:
    """Soft-delete entries whose published date falls outside [start_date, end_date]."""
    start_str = f"{start_date} 00:00:00"
    end_str   = f"{end_date} 23:59:59"
    try:
        with db_connection(domain) as conn:
            return _tombstone(
                domain,
                conn,
                "(published IS NULL OR published = '' OR published < ? OR published > ?)",
                [start_str, end_str],
            )
    except Exception:
        return 0


def apply_age_rule(domain: str) -> int:
    """Apply the domain age gate if it hasn't already run today. Returns count deleted.

    Reads `age_last_pruned` from domain_settings; skips if the stored date equals
    today (UTC). Otherwise runs the appropriate purge and records today's date.
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")
    try:
        with db_connection(domain) as conn:
            row = conn.execute(
                "SELECT value FROM domain_settings WHERE key = 'age_last_pruned'"
            ).fetchone()
            last_pruned = row["value"] if row else None
        if last_pruned == today:
            return 0
        age = get_domain_age_settings(domain)
        if age["mode"] == "none":
            return 0
        deleted = 0
        if age["mode"] == "days_previous" and age["days"]:
            deleted = delete_entries_older_than(domain, float(age["days"]))
        elif age["mode"] == "calendar_period":
            start = age.get("start_date") or "1970-01-01"
            end   = age.get("end_date")   or "9999-12-31"
            deleted = delete_entries_outside_calendar(domain, start, end)
        with db_connection(domain) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO domain_settings (key, value) VALUES ('age_last_pruned', ?)",
                (today,),
            )
        return deleted
    except Exception:
        return 0


def delete_domain_db(domain: str) -> bool:
    """Delete the SQLite database for a domain. Returns False if it didn't exist."""
    path = get_db_path(domain)
    deleted_db = False
    if path.exists():
        path.unlink()
        deleted_db = True
    try:
        from app.chroma_index import delete_domain_store

        delete_domain_store(domain)
    except Exception:
        pass
    return deleted_db


def rename_domain_db(old: str, new: str) -> bool:
    """Rename the SQLite database file for a domain. Returns False if old didn't exist."""
    old_path = get_db_path(old)
    renamed_db = False
    if old_path.exists():
        new_path = get_db_path(new)
        old_path.rename(new_path)
        renamed_db = True
    try:
        from app.chroma_index import rename_domain_store

        rename_domain_store(old, new)
    except Exception:
        pass
    return renamed_db


def rename_feed_entries(domain: str, old_name: str, new_name: str) -> int:
    """Update feed_name on all entries (including deleted) for a renamed feed. Returns row count."""
    try:
        with db_connection(domain) as conn:
            cur = conn.execute(
                "UPDATE entries SET feed_name = ? WHERE feed_name = ?",
                [new_name, old_name],
            )
            return cur.rowcount
    except Exception:
        return 0
