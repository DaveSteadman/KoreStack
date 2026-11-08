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
#   - KoreCommon/compress.py  -- article body compression (if enabled)
# MARK: FUNCTIONS
# Primary types: FeedDatabaseError.
# Function inventory:
# - _parse_published: Implements the  parse published operation for this module.
# - _sanitize_domain: Implements the  sanitize domain operation for this module.
# - get_db_path: Returns db path for this module.
# - db_connection: Implements the db connection operation for this module.
# - init_db: Implements the init db operation for this module.
# - _normalise_published: Implements the  normalise published operation for this module.
# - insert_entry: Implements the insert entry operation for this module.
# - get_entries: Returns entries for this module.
# - get_entry: Returns entry for this module.
# - update_entry_page_text: Updates entry page text for this module.
# - search_entries_detailed: Implements the search entries detailed operation for this module.
# - search_entries: Implements the search entries operation for this module.
# - list_domains: Lists domains for this module.
# - _tombstone: Implements the  tombstone operation for this module.
# - delete_entry: Deletes entry for this module.
# - delete_entries_by_feed: Deletes entries by feed for this module.
# - delete_entries_older_than: Deletes entries older than for this module.
# - delete_entries_by_ids: Deletes entries by ids for this module.
# - delete_entries_over_limit: Deletes entries exceeding a domain limit for this module.
# - get_entry_count: Returns entry count for this module.
# - get_feed_counts: Returns feed counts for this module.
# - get_domain_age_settings: Returns domain limit settings for this module.
# - set_domain_age_settings: Sets domain limit settings for this module.
# - delete_entries_outside_calendar: Deletes entries outside calendar for this module.
# - apply_age_rule: Implements the apply age rule operation for this module.
# - delete_domain_db: Deletes domain db for this module.
# - rename_domain_db: Implements the rename domain db operation for this module.
# - rename_feed_entries: Implements the rename feed entries operation for this module.
# ====================================================================================================
import sqlite3
import json
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate as _rfc_parsedate
from pathlib import Path
from typing import Any, Optional

from app.config import cfg
from dbutil import fts_build_query

DATA_DIR = Path(cfg["data_dir"])

@dataclass
class _ConnectionEntry:
    connection: sqlite3.Connection
    lock:       threading.RLock


_connections: dict[Path, _ConnectionEntry] = {}
_connections_lock = threading.RLock()
_domains_ready: set[str] = set()
_domains_lock = threading.Lock()


class FeedDatabaseError(RuntimeError):
    pass


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
    # Older versions allowed an empty domain.  SQLite then created a literal
    # ``.db`` file, which was displayed as a domain but could not be deleted:
    # sanitising the displayed name produced ``_db.db`` instead.  Keep this
    # exact mapping solely so the legacy artifact remains removable.
    name = str(domain or "").strip()
    if name == ".db":
        return DATA_DIR / name
    if name == "_db":
        return DATA_DIR / f"{name}.db"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
        raise FeedDatabaseError("Domain names may contain only letters, digits, underscores, and hyphens")
    return DATA_DIR / f"{name}.db"


@contextmanager
def db_connection(domain: str):
    db_path = get_db_path(domain)
    with _connections_lock:
        entry = _connections.get(db_path)
        if entry is None:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 5000")
            entry = _ConnectionEntry(connection=conn, lock=threading.RLock())
            _connections[db_path] = entry
    # SQLite permits different databases to proceed independently, but one shared
    # connection still needs serial access.  Do not turn an operation on one domain
    # into a global stop-the-world lock for the entire service.
    with entry.lock:
        try:
            yield entry.connection
            entry.connection.commit()
        except Exception:
            entry.connection.rollback()
            raise


def _close_cached_connection(path: Path) -> None:
    """Close one cached connection after any in-flight operation using it has finished."""
    with _connections_lock:
        entry = _connections.pop(path, None)
    if entry is not None:
        with entry.lock:
            entry.connection.close()


def release_all_cached_connections() -> None:
    """Close cached SQLite handles during controlled service shutdown."""
    with _connections_lock:
        entries = list(_connections.values())
        _connections.clear()
    for entry in entries:
        with entry.lock:
            entry.connection.close()


def init_db(domain: str) -> None:
    with db_connection(domain) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
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
            CREATE TABLE IF NOT EXISTS domain_settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # migrate existing databases that pre-date the deleted column
        cols = {row[1] for row in conn.execute("PRAGMA table_info(entries)").fetchall()}
        if "deleted" not in cols:
            conn.execute("ALTER TABLE entries ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
        # normalise any published values not yet in UTC YYYY-MM-DD HH:MM:SS
        _normalise_published(conn)

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
        if cur.rowcount > 0 and cur.lastrowid:
            conn.execute(
                "INSERT INTO entries_fts(rowid, headline, page_text) VALUES (?, ?, ?)",
                (cur.lastrowid, headline or "", page_text or ""),
            )
            inserted_entry_id = int(cur.lastrowid)
    if inserted_entry_id is None:
        return False
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


def update_entry_page_text(domain: str, entry_id: int, page_text: str) -> dict:
    normalized_text = str(page_text or "")
    with _domains_lock:
        if domain not in _domains_ready:
            init_db(domain)
            _domains_ready.add(domain)

    headline_text = ""

    with db_connection(domain) as conn:
        entry = conn.execute(
            "SELECT id, headline FROM entries WHERE id = ? AND deleted = 0",
            (entry_id,),
        ).fetchone()
        if not entry:
            raise FeedDatabaseError(f"Entry {entry_id} not found in domain '{domain}'.")

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
    return {
        "domain":        domain,
        "entry_id":      entry_id,
        "page_text_len": len(normalized_text),
    }


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


def list_domains() -> list[str]:
    DATA_DIR.mkdir(exist_ok=True)
    domains: list[str] = []
    for path in sorted(DATA_DIR.glob("*.db")):
        domain = ".db" if path.name == ".db" else path.stem
        if domain in {".db", "_db"} or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", domain):
            domains.append(domain)
    return domains


def _tombstone(domain: str, conn: sqlite3.Connection, where: str, params: list) -> int:
    """Soft-delete: blank content fields and set deleted=1. URL is preserved for dedup."""
    # Capture IDs before the update so we can remove them from the FTS index
    ids = [
        r[0] for r in conn.execute(
            f"SELECT id FROM entries WHERE deleted=0 AND {where}", params
        ).fetchall()
    ]
    cur = conn.execute(
        f"UPDATE entries SET headline=NULL, page_text=NULL, metadata=NULL, deleted=1"
        f" WHERE deleted=0 AND {where}",
        params,
    )
    for id_ in ids:
        conn.execute("DELETE FROM entries_fts WHERE rowid=?", (id_,))
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


def delete_entries_over_limit(domain: str, maximum_entries: int) -> int:
    """Soft-delete entries beyond the newest ``maximum_entries`` entries."""
    if maximum_entries < 1:
        raise ValueError("maximum_entries must be at least 1")
    try:
        with db_connection(domain) as conn:
            return _tombstone(
                domain,
                conn,
                """id IN (
                    SELECT id FROM entries
                    WHERE deleted = 0
                    ORDER BY published DESC, id DESC
                    LIMIT -1 OFFSET ?
                )""",
                [maximum_entries],
            )
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
    """Return entry limit settings for a domain.

    Returns a dict with keys: mode ('none'|'days_previous'|'calendar_period'),
    days (int|None), start_date (str|None), end_date (str|None),
    max_entries_enabled (bool), and max_entries (int|None).
    """
    try:
        with db_connection(domain) as conn:
            rows = conn.execute(
                "SELECT key, value FROM domain_settings "
                "WHERE key IN ("
                "'age_mode','age_days','age_start','age_end','max_age_days',"
                "'max_entries_enabled','max_entries'"
                ")"
            ).fetchall()
            s = {r["key"]: r["value"] for r in rows}
            # backwards compat: migrate legacy max_age_days â†’ days_previous
            if "age_mode" not in s and s.get("max_age_days"):
                return {
                    "mode": "days_previous",
                    "days": int(s["max_age_days"]),
                    "start_date": None,
                    "end_date": None,
                    "max_entries_enabled": False,
                    "max_entries": None,
                }
            mode = s.get("age_mode", "none") or "none"
            days = int(s["age_days"]) if s.get("age_days") else None
            max_entries = int(s["max_entries"]) if s.get("max_entries") else None
            return {
                "mode": mode,
                "days": days,
                "start_date": s.get("age_start"),
                "end_date": s.get("age_end"),
                "max_entries_enabled": s.get("max_entries_enabled") == "1",
                "max_entries": max_entries,
            }
    except Exception:
        return {
            "mode": "none",
            "days": None,
            "start_date": None,
            "end_date": None,
            "max_entries_enabled": False,
            "max_entries": None,
        }


def set_domain_age_settings(
    domain: str,
    mode: str,
    days: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    max_entries_enabled: bool = False,
    max_entries: Optional[int] = None,
) -> None:
    """Persist age-gating and maximum-entry settings for a domain."""
    if max_entries_enabled and (max_entries is None or max_entries < 1):
        raise ValueError("max_entries must be at least 1 when enabled")
    init_db(domain)
    with db_connection(domain) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO domain_settings (key, value) VALUES (?, ?)",
            [
                ("age_mode", mode),
                ("age_days", str(days) if days else None),
                ("age_start", start_date),
                ("age_end", end_date),
                ("max_entries_enabled", "1" if max_entries_enabled else "0"),
                ("max_entries", str(max_entries) if max_entries_enabled else None),
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
        deleted = 0
        if age["mode"] == "days_previous" and age["days"]:
            deleted = delete_entries_older_than(domain, float(age["days"]))
        elif age["mode"] == "calendar_period":
            start = age.get("start_date") or "1970-01-01"
            end   = age.get("end_date")   or "9999-12-31"
            deleted = delete_entries_outside_calendar(domain, start, end)
        if age["max_entries_enabled"] and age["max_entries"]:
            deleted += delete_entries_over_limit(domain, age["max_entries"])
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
        _close_cached_connection(path)
        path.unlink()
        deleted_db = True
    for sidecar_path in (Path(f"{path}-wal"), Path(f"{path}-shm")):
        if sidecar_path.exists():
            sidecar_path.unlink()
    with _domains_lock:
        _domains_ready.discard(domain)
        if domain == ".db":
            _domains_ready.discard("")
    return deleted_db


def rename_domain_db(old: str, new: str) -> bool:
    """Rename the SQLite database file for a domain. Returns False if old didn't exist."""
    old_path = get_db_path(old)
    renamed_db = False
    if old_path.exists():
        new_path = get_db_path(new)
        if new_path.exists():
            raise FileExistsError(f"Domain database '{new}' already exists")
        _close_cached_connection(old_path)
        old_path.rename(new_path)
        renamed_db = True
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
