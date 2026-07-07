# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# SQLite database layer for KoreReference.
#
# Schema:
#   articles  -- wiki article content with zlib-compressed body, FTS5 full-text index,
#                backlink resolution, and extracted table data
#
# FTS5 content is kept in sync with every write.  WAL mode is enabled.
# Body content is compressed via CommonCode/compress.py.
#
# Related modules:
#   - app/server.py                  -- all read/write operations
#   - app/importers/kiwix.py         -- bulk article import
#   - CommonCode/compress.py         -- body storage compression
#   - CommonCode/dbutil.py           -- fts_build_query
# ====================================================================================================
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from KoreCommon.sentence_index import extract_sentence_text as _extract_sentence_text_common
from KoreCommon.sentence_index import mark_sentences_indexed
from KoreCommon.sentence_index import normalize_sentence_schema as _normalize_sentence_schema_common
from KoreCommon.sentence_index import reset_sentence_indexed_at
from KoreCommon.sentence_index import sentence_index_needs_rebuild as _sentence_index_needs_rebuild_common
from KoreCommon.sentence_index import sentence_schema_columns
from KoreCommon.sentence_index import sentence_schema_needs_normalization as _sentence_schema_needs_normalization_common
from KoreCommon.sentence_index import split_sentences
from app.importers.shared import TABLE_OPEN, TABLE_CLOSE, table_to_fts_text
from app.config import cfg
from compress import compress as _compress, decompress as _decompress
from dbutil import fts_build_query

_TABLE_MARKER_RE = re.compile(rf'{re.escape(TABLE_OPEN)}(.*?){re.escape(TABLE_CLOSE)}', re.DOTALL)
_SIMPLE_Q_RE      = re.compile(r"^[^\s()\",|]+$")
_HEADING_RE       = re.compile(r'^== (.+?) ==$')

_SENTENCE_SCHEMA_COLUMNS = sentence_schema_columns("article_id")

_EXCLUDED_SECTION_TITLES = {
    "see also",
    "notes",
    "references",
    "citations",
    "footnotes",
    "works cited",
    "sources",
    "external links",
    "further reading",
    "bibliography",
}


def _body_for_fts(body: Optional[str]) -> str:
    """Replace <<<TABLE>>>...<<<ENDTABLE>>> blocks with plain cell text for FTS indexing."""
    if not body:
        return ""
    return _TABLE_MARKER_RE.sub(lambda m: table_to_fts_text(m.group(1)), body)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_heading_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(title or "").strip().lower()).strip()


def _iter_body_blocks(text: str) -> list[tuple[int, int, str]]:
    value = str(text or "")
    if not value:
        return []

    blocks: list[tuple[int, int, str]] = []
    start = 0
    n     = len(value)
    while start < n:
        split_at = value.find("\n\n", start)
        if split_at == -1:
            end = n
        else:
            end = split_at
        block = value[start:end]
        if block.strip():
            blocks.append((start, end, block))
        if split_at == -1:
            break
        start = split_at + 2
        while start < n and value[start] == "\n":
            start += 1
    return blocks


def _block_is_table(block_text: str) -> bool:
    stripped = str(block_text or "").strip()
    return stripped.startswith(TABLE_OPEN) and stripped.endswith(TABLE_CLOSE)


def _block_is_list(block_text: str) -> bool:
    lines = [line.strip() for line in str(block_text or "").splitlines() if line.strip()]
    return bool(lines) and all(line.startswith("* ") or line.startswith("# ") for line in lines)


def _iter_indexable_body_spans(body: Optional[str]) -> list[tuple[int, int]]:
    text             = str(body or "")
    excluded_section = False
    spans: list[tuple[int, int]] = []

    for block_start, block_end, block_text in _iter_body_blocks(text):
        stripped = block_text.strip()
        heading  = _HEADING_RE.fullmatch(stripped)
        if heading:
            excluded_section = _normalize_heading_title(heading.group(1)) in _EXCLUDED_SECTION_TITLES
            continue
        if excluded_section:
            continue
        if _block_is_table(stripped) or _block_is_list(stripped):
            continue
        if not any(ch.isalpha() for ch in stripped):
            continue

        leading_ws = len(block_text) - len(block_text.lstrip())
        trailing_ws = len(block_text) - len(block_text.rstrip())
        span_start = block_start + leading_ws
        span_end   = block_end - trailing_ws
        if span_start < span_end:
            spans.append((span_start, span_end))
    return spans


def _index_article_sentences(
    conn: sqlite3.Connection,
    article_id: int,
    summary: Optional[str],
    body: Optional[str],
) -> None:
    rows: list[tuple[int, int, str, int, int]] = []
    sentence_index = 0

    for char_start, char_end, _sentence_text in split_sentences(summary or ""):
        rows.append((article_id, sentence_index, "summary", char_start, char_end))
        sentence_index += 1

    body_text = str(body or "")
    for block_start, block_end in _iter_indexable_body_spans(body_text):
        block_text = body_text[block_start:block_end]
        for local_start, local_end, _sentence_text in split_sentences(block_text):
            rows.append(
                (
                    article_id,
                    sentence_index,
                    "body",
                    block_start + local_start,
                    block_start + local_end,
                )
            )
            sentence_index += 1

    if rows:
        conn.executemany(
            """
            INSERT INTO sentences
                (article_id, sentence_index, source_field, char_start, char_end)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )


def _backfill_article_sentences(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT a.id, a.summary, a.body
        FROM articles a
        WHERE a.redirect_to IS NULL
          AND NOT EXISTS (
              SELECT 1
              FROM sentences s
              WHERE s.article_id = a.id AND s.deleted = 0
          )
        """
    ).fetchall()
    for row in rows:
        _index_article_sentences(
            conn,
            int(row["id"]),
            row["summary"] or "",
            _decompress(row["body"]) or "",
        )


def _sentence_index_needs_rebuild(conn: sqlite3.Connection) -> bool:
    return _sentence_index_needs_rebuild_common(conn)


def _sentence_schema_needs_normalization(conn: sqlite3.Connection) -> bool:
    return _sentence_schema_needs_normalization_common(conn, _SENTENCE_SCHEMA_COLUMNS)


def _normalize_sentence_schema(conn: sqlite3.Connection) -> None:
    _normalize_sentence_schema_common(
        conn,
        owner_column      = "article_id",
        expected_cols     = _SENTENCE_SCHEMA_COLUMNS,
        backfill_callback = _backfill_article_sentences,
    )


def _sentence_locator(sentence_id: int) -> str:
    return f"reference/main/{int(sentence_id)}"


DATA_DIR = Path(cfg["data_dir"])
_DB_PATH = DATA_DIR / "reference.db"


def get_db_path() -> Path:
    DATA_DIR.mkdir(exist_ok=True)
    return _DB_PATH


@contextmanager
def db_connection():
    conn = sqlite3.connect(str(get_db_path()), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def init_db() -> None:
    with db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT    NOT NULL,
                redirect_to TEXT,
                summary     TEXT,
                body        TEXT,
                word_count  INTEGER,
                facts       TEXT
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_title
            ON articles (title)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS links (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                from_id  INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
                to_title TEXT    NOT NULL,
                to_id    INTEGER REFERENCES articles(id) ON DELETE SET NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_links_from ON links (from_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_links_to   ON links (to_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_links_to_title ON links (to_title)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_title_lower ON articles (lower(title))")
        # FTS: contentless — body is stored compressed so triggers can't index it.
        # Python code in upsert/delete manages FTS explicitly with plain text.
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
                title, body,
                tokenize='unicode61 remove_diacritics 1',
                content=''
            )
        """)
        # Drop old content-table triggers if they exist from a previous schema
        for _trg in ("articles_ai", "articles_ad", "articles_au"):
            conn.execute(f"DROP TRIGGER IF EXISTS {_trg}")
        # Migrate: compress body for existing uncompressed rows and rebuild FTS
        _need_compress = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE typeof(body)='text' AND body IS NOT NULL"
        ).fetchone()[0]
        if _need_compress:
            rows = conn.execute("SELECT id, title, body FROM articles WHERE typeof(body)='text'").fetchall()
            # Rebuild FTS clean
            conn.execute("DELETE FROM articles_fts")
            for _row in rows:
                _blob = _compress(_row["body"])
                conn.execute("UPDATE articles SET body=? WHERE id=?", (_blob, _row["id"]))
                conn.execute(
                    "INSERT INTO articles_fts(rowid, title, body) VALUES (?,?,?)",
                    (_row["id"], _row["title"] or "", _body_for_fts(_row["body"] or "")),
                )
        # Migrate: add facts column if not present (for databases created before this feature)
        _cols = {row[1] for row in conn.execute("PRAGMA table_info(articles)")}
        if "facts" not in _cols:
            conn.execute("ALTER TABLE articles ADD COLUMN facts TEXT")
        # Migrate: drop sections column (data is now derived from body at read time)
        if "sections" in _cols:
            conn.execute("ALTER TABLE articles DROP COLUMN sections")
        # Migrate: drop legacy metadata columns if present
        # SQLite refuses DROP COLUMN when an index references that column, so
        # we first detect and drop any such indexes.
        for _col in ("source", "source_id", "source_hash", "added_at", "updated_at"):
            if _col in _cols:
                for _idx in conn.execute("PRAGMA index_list(articles)").fetchall():
                    _idx_name = _idx[1]
                    _idx_cols = {r[2] for r in conn.execute(f"PRAGMA index_info({_idx_name})")}
                    if _col in _idx_cols:
                        conn.execute(f"DROP INDEX IF EXISTS [{_idx_name}]")
                conn.execute(f"ALTER TABLE articles DROP COLUMN {_col}")
        # Migrate: drop categories column and tables
        if "categories" in _cols:
            conn.execute("ALTER TABLE articles DROP COLUMN categories")
        _tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "article_categories" in _tables:
            conn.execute("DROP TABLE article_categories")
        if "categories" in _tables:
            conn.execute("DROP TABLE categories")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sentences (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id        INTEGER NOT NULL,
                sentence_index    INTEGER NOT NULL,
                source_field      TEXT NOT NULL,
                char_start        INTEGER NOT NULL,
                char_end          INTEGER NOT NULL,
                chroma_indexed_at TEXT,
                deleted           INTEGER NOT NULL DEFAULT 0,
                UNIQUE(article_id, sentence_index)
            )
        """)
        sentence_cols = {row[1] for row in conn.execute("PRAGMA table_info(sentences)").fetchall()}
        if "deleted" not in sentence_cols:
            conn.execute("ALTER TABLE sentences ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
        if "chroma_indexed_at" not in sentence_cols:
            conn.execute("ALTER TABLE sentences ADD COLUMN chroma_indexed_at TEXT")
        if _sentence_schema_needs_normalization(conn):
            _normalize_sentence_schema(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sentences_article_id ON sentences(article_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sentences_chroma_indexed_at ON sentences(chroma_indexed_at)")
        if _sentence_index_needs_rebuild(conn):
            conn.execute("DELETE FROM sentences")
            _backfill_article_sentences(conn)
        else:
            _backfill_article_sentences(conn)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _word_count(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    return len(text.split())


def _parse_json_list(value: Optional[str]) -> list:
    if not value:
        return []
    try:
        return json.loads(value)
    except Exception:
        return []


_ARTICLE_META_COLS = (
    "id", "title", "redirect_to", "summary", "word_count",
)
_ARTICLE_FULL_COLS = _ARTICLE_META_COLS + ("body", "facts")


def body_to_sections(body: Optional[str]) -> list[dict]:
    """Derive [{title, content}] sections from the inline == Heading == markers in body."""
    if not body:
        return []
    sections: list[dict] = []
    current_title: Optional[str] = None
    current_parts: list[str] = []
    for line in body.split("\n\n"):
        m = _HEADING_RE.match(line.strip())
        if m:
            if current_title is not None:
                sections.append({"title": current_title,
                                  "content": "\n\n".join(current_parts).strip()})
            current_title = m.group(1)
            current_parts = []
        else:
            if line.strip():
                current_parts.append(line)
    if current_title is not None:
        sections.append({"title": current_title,
                          "content": "\n\n".join(current_parts).strip()})
    return sections


def _row_to_dict(row: sqlite3.Row, full: bool = False) -> dict:
    cols = _ARTICLE_FULL_COLS if full else _ARTICLE_META_COLS
    d = {c: row[c] for c in cols}
    if full:
        d["body"]     = _decompress(d.get("body"))
        d["sections"] = body_to_sections(d.get("body"))
        d["facts"]    = _parse_json_list(d.get("facts"))
    return d


def _extract_sentence_text(article_row: sqlite3.Row | dict, sentence_row: sqlite3.Row | dict) -> str:
    return _extract_sentence_text_common(
        article_row,
        sentence_row,
        value_transform = lambda source_field, source_text: _decompress(source_text) if source_field == "body" else source_text,
    )


# ---------------------------------------------------------------------------
# Article CRUD
# ---------------------------------------------------------------------------

def get_article_by_title(title: str, full: bool = True) -> Optional[dict]:
    cols = ", ".join(_ARTICLE_FULL_COLS if full else _ARTICLE_META_COLS)
    with db_connection() as conn:
        row = conn.execute(
            f"SELECT {cols} FROM articles WHERE title = ?", (title,)
        ).fetchone()
    return _row_to_dict(row, full=full) if row else None


def get_article_by_id(article_id: int, full: bool = True) -> Optional[dict]:
    cols = ", ".join(_ARTICLE_FULL_COLS if full else _ARTICLE_META_COLS)
    with db_connection() as conn:
        row = conn.execute(
            f"SELECT {cols} FROM articles WHERE id = ?", (article_id,)
        ).fetchone()
    return _row_to_dict(row, full=full) if row else None


def resolve_article(title: str) -> Optional[dict]:
    """Fetch article, following up to 5 levels of redirect."""
    seen: set[str] = set()
    redirected_from: Optional[str] = None
    current = title
    while current and current not in seen:
        seen.add(current)
        article = get_article_by_title(current, full=True)
        if article is None:
            return None
        if not article["redirect_to"]:
            if redirected_from:
                article["redirected_from"] = redirected_from
            return article
        redirected_from = redirected_from or current
        current = article["redirect_to"]
    return None


def list_articles(limit: int = 100, offset: int = 0) -> list[dict]:
    cols = ", ".join(_ARTICLE_META_COLS)
    with db_connection() as conn:
        rows = conn.execute(
            f"SELECT {cols} FROM articles WHERE redirect_to IS NULL "
            f"ORDER BY title LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def upsert_article(
    title: str,
    body: Optional[str],
    summary: Optional[str] = None,
    facts: Optional[list] = None,
    redirect_to: Optional[str] = None,
    link_titles: Optional[list[str]] = None,
    conn: Optional[sqlite3.Connection] = None,
    pending_deleted_sentence_ids: Optional[set[int]] = None,
    pending_sync_article_ids: Optional[set[int]] = None,
    **_ignored,
) -> dict:
    """Insert or update an article."""
    title      = title.strip()
    wc         = _word_count(body)
    facts_json = json.dumps(facts or [])

    def _upsert(active_conn: sqlite3.Connection) -> tuple[int, list[int]]:
        existing = active_conn.execute(
            "SELECT id FROM articles WHERE title = ?", (title,)
        ).fetchone()

        fts_body        = _body_for_fts(body)
        compressed_body = _compress(body)
        previous_sentence_ids: list[int] = []

        if existing:
            article_id = existing["id"]
            previous_sentence_ids = [
                int(row[0]) for row in active_conn.execute(
                    "SELECT id FROM sentences WHERE article_id = ? ORDER BY id",
                    (article_id,),
                ).fetchall()
            ]
            # Update FTS with tag-stripped text before storing compressed
            active_conn.execute(
                "INSERT INTO articles_fts(articles_fts, rowid, title, body) VALUES('delete',?,?,?)",
                (article_id, title, fts_body),
            )
            active_conn.execute(
                "INSERT INTO articles_fts(rowid, title, body) VALUES(?,?,?)",
                (article_id, title, fts_body),
            )
            active_conn.execute("""
                UPDATE articles
                SET redirect_to=?, summary=?, body=?,
                    facts=?, word_count=?
                WHERE id=?
            """, (redirect_to, summary, compressed_body,
                  facts_json, wc, article_id))
            active_conn.execute("DELETE FROM links WHERE from_id=?", (article_id,))
        else:
            cur = active_conn.execute("""
                INSERT INTO articles
                    (title, redirect_to, summary, body,
                     facts, word_count)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (title, redirect_to, summary, compressed_body,
                  facts_json, wc))
            article_id = cur.lastrowid
            # Sync FTS with tag-stripped text after insert
            active_conn.execute(
                "INSERT INTO articles_fts(rowid, title, body) VALUES(?,?,?)",
                (article_id, title, fts_body),
            )

        active_conn.execute("DELETE FROM sentences WHERE article_id = ?", (article_id,))
        if not redirect_to:
            _index_article_sentences(active_conn, article_id, summary, body)

        # Insert links (to_id resolved later)
        for lt in (link_titles or []):
            active_conn.execute(
                "INSERT INTO links (from_id, to_title) VALUES (?, ?)",
                (article_id, lt),
            )
        return article_id, previous_sentence_ids

    if conn is None:
        with db_connection() as owned_conn:
            article_id, previous_sentence_ids = _upsert(owned_conn)
        if previous_sentence_ids:
            try:
                from app.chroma_index import delete_sentence_ids
                delete_sentence_ids(previous_sentence_ids)
            except Exception:
                pass
        try:
            from app.chroma_index import sync_article_sentences
            sync_article_sentences(int(article_id))
        except Exception:
            pass
        return get_article_by_id(article_id, full=False)

    article_id, previous_sentence_ids = _upsert(conn)
    if pending_deleted_sentence_ids is not None:
        pending_deleted_sentence_ids.update(previous_sentence_ids)
    if pending_sync_article_ids is not None and not redirect_to:
        pending_sync_article_ids.add(int(article_id))
    return {"id": article_id, "title": title}


def delete_article(title: str) -> bool:
    with db_connection() as conn:
        row = conn.execute("SELECT id FROM articles WHERE title=?", (title,)).fetchone()
        if not row:
            return False
        sentence_ids = [
            int(item[0]) for item in conn.execute(
                "SELECT id FROM sentences WHERE article_id = ?",
                (int(row["id"]),),
            ).fetchall()
        ]
        conn.execute(
            "INSERT INTO articles_fts(articles_fts, rowid, title, body) VALUES('delete',?,?,'')",
            (row["id"], title),
        )
        conn.execute("DELETE FROM sentences WHERE article_id = ?", (int(row["id"]),))
        conn.execute("DELETE FROM articles WHERE id=?", (row["id"],))
    if sentence_ids:
        try:
            from app.chroma_index import delete_sentence_ids
            delete_sentence_ids(sentence_ids)
        except Exception:
            pass
    return True


def delete_all_articles() -> int:
    """Delete all articles, links, and FTS data, then vacuum. Returns number of article rows deleted."""
    with db_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        conn.execute("DELETE FROM links")
        conn.execute("DELETE FROM sentences")
        conn.execute("DELETE FROM articles")
        conn.execute("DELETE FROM articles_fts")
    try:
        from app.chroma_index import delete_store
        delete_store()
    except Exception:
        pass
    # VACUUM must run outside any transaction (autocommit mode)
    conn = sqlite3.connect(str(get_db_path()), isolation_level=None)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()
    return count


def get_random_article() -> Optional[dict]:
    cols = ", ".join(_ARTICLE_META_COLS)
    with db_connection() as conn:
        row = conn.execute(
            f"SELECT {cols} FROM articles WHERE redirect_to IS NULL "
            f"ORDER BY RANDOM() LIMIT 1"
        ).fetchone()
    return _row_to_dict(row) if row else None


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------

def resolve_links(batch_size: int = 500) -> int:
    """Fill in to_id for unresolved links. Runs in batches to avoid a single long write lock."""
    total_resolved = 0
    while True:
        with db_connection() as conn:
            cur = conn.execute("""
                UPDATE links SET to_id = (
                    SELECT id FROM articles WHERE lower(title) = lower(links.to_title)
                )
                WHERE to_id IS NULL
                  AND rowid IN (
                      SELECT rowid FROM links WHERE to_id IS NULL LIMIT ?
                  )
            """, (batch_size,))
            resolved = cur.rowcount
        total_resolved += resolved
        if resolved == 0:
            break
    return total_resolved


def get_unresolved_link_titles(limit: int = 10_000) -> list[str]:
    """Return distinct to_title values in links that have no matching articles row.

    These are the titles that were linked to but never imported — likely redirects
    or articles just outside the crawl boundary.
    """
    with db_connection() as conn:
        rows = conn.execute("""
            SELECT DISTINCT l.to_title
            FROM links l
            WHERE l.to_id IS NULL
              AND l.to_title IS NOT NULL
            ORDER BY l.to_title
            LIMIT ?
        """, (limit,)).fetchall()
    return [r["to_title"] for r in rows]


def get_links(title: str) -> list[dict]:
    """Outbound links from an article."""
    with db_connection() as conn:
        rows = conn.execute("""
            SELECT l.to_title,
                   COALESCE(l.to_id, late.id)          AS to_id,
                   COALESCE(a.summary, late.summary)    AS summary
            FROM links l
            JOIN articles src ON src.title=? AND src.id=l.from_id
            LEFT JOIN articles a    ON a.id=l.to_id
            LEFT JOIN articles late ON late.title=l.to_title AND l.to_id IS NULL
            ORDER BY l.to_title
        """, (title,)).fetchall()
    return [{"to_title": r["to_title"], "to_id": r["to_id"], "summary": r["summary"]} for r in rows]


def get_backlinks(title: str, limit: int = 50, offset: int = 0) -> list[dict]:
    """Articles that link to the given article title."""
    with db_connection() as conn:
        rows = conn.execute("""
            SELECT src.id, src.title, src.summary
            FROM links l
            JOIN articles target ON target.title=? AND target.id=l.to_id
            JOIN articles src    ON src.id=l.from_id
            ORDER BY src.title
            LIMIT ? OFFSET ?
        """, (title, limit, offset)).fetchall()
    return [{"id": r["id"], "title": r["title"], "summary": r["summary"]} for r in rows]


def get_article_sentences(article_id: int, include_deleted: bool = False) -> list[dict]:
    cols = "s.id, s.article_id, s.sentence_index, s.source_field, s.char_start, s.char_end, s.chroma_indexed_at, s.deleted, a.title, a.summary, a.body"
    where = "WHERE s.article_id = ?"
    if not include_deleted:
        where += " AND s.deleted = 0"
    with db_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT {cols}
            FROM sentences s
            JOIN articles a ON a.id = s.article_id
            {where}
            ORDER BY s.sentence_index ASC
            """,
            (int(article_id),),
        ).fetchall()
    results: list[dict] = []
    for row in rows:
        item = dict(row)
        item["sentence_text"] = _extract_sentence_text(row, row)
        item["locator"]       = _sentence_locator(int(item["id"]))
        item.pop("body", None)
        results.append(item)
    return results


def get_sentence(sentence_id: int) -> Optional[dict]:
    with db_connection() as conn:
        row = conn.execute(
            """
            SELECT s.id, s.article_id, s.sentence_index, s.source_field, s.char_start, s.char_end,
                   s.chroma_indexed_at, s.deleted, a.title, a.summary, a.body, a.word_count
            FROM sentences s
            JOIN articles a ON a.id = s.article_id
            WHERE s.id = ?
            """,
            (int(sentence_id),),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["sentence_text"] = _extract_sentence_text(row, row)
    item["locator"]       = _sentence_locator(int(item["id"]))
    item.pop("body", None)
    return item


def backfill_sentence_index() -> dict:
    with db_connection() as conn:
        before = int(conn.execute("SELECT COUNT(*) FROM sentences WHERE deleted = 0").fetchone()[0])
        _backfill_article_sentences(conn)
        after  = int(conn.execute("SELECT COUNT(*) FROM sentences WHERE deleted = 0").fetchone()[0])
    return {
        "sentences_added": max(0, after - before),
        "sentence_count":  after,
    }


def rebuild_sentence_index(article_id: Optional[int] = None) -> dict:
    deleted_sentence_ids: list[int] = []
    rebuilt_sentences:    int       = 0

    with db_connection() as conn:
        if article_id is None:
            deleted_sentence_ids = [
                int(row[0]) for row in conn.execute(
                    "SELECT id FROM sentences WHERE deleted = 0 ORDER BY id"
                ).fetchall()
            ]
            conn.execute("DELETE FROM sentences")
            _backfill_article_sentences(conn)
            rebuilt_sentences = int(conn.execute(
                "SELECT COUNT(*) FROM sentences WHERE deleted = 0"
            ).fetchone()[0])
        else:
            article_row = conn.execute(
                "SELECT id, summary, body FROM articles WHERE id = ? AND redirect_to IS NULL",
                (int(article_id),),
            ).fetchone()
            if article_row is None:
                raise ValueError(f"Article not found: {article_id}")
            deleted_sentence_ids = [
                int(row[0]) for row in conn.execute(
                    "SELECT id FROM sentences WHERE article_id = ? AND deleted = 0 ORDER BY id",
                    (int(article_id),),
                ).fetchall()
            ]
            conn.execute("DELETE FROM sentences WHERE article_id = ?", (int(article_id),))
            _index_article_sentences(
                conn,
                int(article_id),
                article_row["summary"] or "",
                _decompress(article_row["body"]) or "",
            )
            rebuilt_sentences = int(conn.execute(
                "SELECT COUNT(*) FROM sentences WHERE article_id = ? AND deleted = 0",
                (int(article_id),),
            ).fetchone()[0])

    if deleted_sentence_ids:
        try:
            from app.chroma_index import delete_sentence_ids
            delete_sentence_ids(deleted_sentence_ids)
        except Exception:
            pass

    try:
        from app.chroma_index import sync_article_sentences, sync_pending_sentences
        if article_id is None:
            sync_pending_sentences(batch_size=250)
        else:
            sync_article_sentences(int(article_id))
    except Exception:
        pass

    return {
        "article_id":           int(article_id) if article_id is not None else None,
        "rebuilt_sentences":    rebuilt_sentences,
        "deleted_sentence_ids": len(deleted_sentence_ids),
    }


def get_sentences_for_chroma(
    limit: int = 250,
    only_unindexed: bool = False,
    sentence_ids: Optional[list[int]] = None,
) -> list[dict]:
    with db_connection() as conn:
        clauses = ["s.deleted = 0"]
        params: list[object] = []
        if sentence_ids:
            validated    = [int(item) for item in sentence_ids]
            placeholders = ",".join("?" for _ in validated)
            clauses.append(f"s.id IN ({placeholders})")
            params.extend(validated)
        if only_unindexed:
            clauses.append("(s.chroma_indexed_at IS NULL OR s.chroma_indexed_at = '')")
        params.append(max(1, int(limit)))
        rows = conn.execute(
            f"""
            SELECT s.id, s.article_id, s.sentence_index, s.source_field, s.char_start, s.char_end,
                   s.chroma_indexed_at, a.title, a.summary, a.body, a.word_count
            FROM sentences s
            JOIN articles a ON a.id = s.article_id
            WHERE {" AND ".join(clauses)}
            ORDER BY s.id ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
    results: list[dict] = []
    for row in rows:
        item = dict(row)
        item["sentence_text"] = _extract_sentence_text(row, row)
        item["locator"]       = _sentence_locator(int(item["id"]))
        item.pop("body", None)
        results.append(item)
    return results


def mark_sentences_chroma_indexed(sentence_ids: list[int]) -> int:
    if not sentence_ids:
        return 0
    with db_connection() as conn:
        return mark_sentences_indexed(
            conn,
            sentence_ids   = sentence_ids,
            indexed_at     = _now(),
            deleted_filter = False,
        )


def reset_sentence_chroma_index(article_id: Optional[int] = None) -> int:
    with db_connection() as conn:
        return reset_sentence_indexed_at(
            conn,
            owner_column   = "article_id",
            owner_id       = article_id,
            deleted_filter = False,
        )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_articles(
    q: Optional[str] = None,
    title: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    meta_cols = ", ".join(f"a.{c}" for c in _ARTICLE_META_COLS)

    if q:
        q_text = q.strip()
        simple_q = bool(_SIMPLE_Q_RE.fullmatch(q_text))
        prefix_results: list[dict] = []
        seen_ids: set[int] = set()

        if simple_q:
            prefix_results = search_articles(title=q_text, limit=limit, offset=offset)
            seen_ids       = {int(r["id"]) for r in prefix_results if r.get("id") is not None}
            if len(prefix_results) >= limit:
                return prefix_results

        # FTS path
        with db_connection() as conn:
            fts_q = fts_build_query(q)
            if not fts_q:
                return prefix_results
            rows = conn.execute(f"""
                SELECT {meta_cols},
                       bm25(articles_fts) AS score
                FROM articles_fts
                JOIN articles a ON a.id=articles_fts.rowid
                WHERE articles_fts MATCH :q
                  AND a.redirect_to IS NULL
                ORDER BY score
                LIMIT :lim OFFSET :off
            """, {"q": fts_q, "lim": limit, "off": offset}).fetchall()
        results = list(prefix_results)
        for r in rows:
            if r["id"] in seen_ids:
                continue
            d = _row_to_dict(r)
            d["score"] = r["score"]
            results.append(d)
            if len(results) >= limit:
                break
        return results

    # Non-FTS: title prefix filter
    clauses = ["a.redirect_to IS NULL"]
    params: list = []
    if title:
        clauses.append("a.title LIKE ? ESCAPE '\\'")
        params.append(title.replace("%", "\\%").replace("_", "\\_") + "%")
    where = " AND ".join(clauses)
    params += [limit, offset]
    with db_connection() as conn:
        rows = conn.execute(
            f"SELECT {meta_cols} FROM articles a WHERE {where} "
            f"ORDER BY a.title LIMIT ? OFFSET ?",
            params,
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def get_status() -> dict:
    with db_connection() as conn:
        row = conn.execute("""
            SELECT
                SUM(redirect_to IS NULL)     AS total_articles,
                SUM(redirect_to IS NOT NULL) AS total_redirects,
                (SELECT COUNT(*) FROM links)                     AS total_links,
                (SELECT COUNT(*) FROM links WHERE to_id IS NULL)  AS unresolved_links
            FROM articles
        """).fetchone()
    return {
        "total_articles":   row["total_articles"]   or 0,
        "total_redirects":  row["total_redirects"]  or 0,
        "total_links":      row["total_links"]       or 0,
        "unresolved_links": row["unresolved_links"]  or 0,
    }
