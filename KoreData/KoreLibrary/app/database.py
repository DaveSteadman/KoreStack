# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# SQLite database layer for KoreLibrary.
#
# Schema:
#   books  -- book catalog entries with FTS5 full-text search across title and author
#
# Supports catalog:id format (e.g. "openlibrary:OL12345W") for cross-catalog deduplication.
# Completeness checking flags entries missing key metadata fields.
#
# Related modules:
#   - app/server.py    -- catalog read/write and search operations
#   - CommonCode/dbutil.py  -- fts_build_query
# ====================================================================================================
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import cfg
from compress import compress as _compress, decompress as _decompress
from dbutil import fts_build_query, compute_word_count as _compute_word_count


DATA_DIR = Path(cfg["data_dir"])
_DB_PATH = DATA_DIR / "library.db"
_BUNDLED_CATALOGS_DIR = Path(__file__).resolve().parents[1] / "catalogs"
_DEFAULT_CATALOG = str(cfg.get("default_catalog", "local") or "local").strip() or "local"
_CATALOG_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Fields that are checked for completeness (NULL or empty = incomplete)
COMPLETENESS_FIELDS = ("author", "year", "language", "genre")

_SENTENCE_SCHEMA_COLUMNS = (
    "id",
    "book_id",
    "sentence_index",
    "source_field",
    "char_start",
    "char_end",
    "chroma_indexed_at",
    "deleted",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _split_sentences(text: str) -> list[tuple[int, int, str]]:
    text = str(text or "")
    if not text:
        return []

    sentences: list[tuple[int, int, str]] = []
    start = 0
    i     = 0
    n     = len(text)

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
                i     = end
                continue
        i += 1

    if start < n:
        sentence = text[start:].strip()
        if sentence:
            sentences.append((start, n, sentence))
    return sentences


def _normalize_catalog_id(catalog: Optional[str]) -> str:
    value = (catalog or _DEFAULT_CATALOG).strip().lower() or _DEFAULT_CATALOG
    if not _CATALOG_ID_RE.fullmatch(value):
        raise ValueError(f"Invalid catalog id: {catalog!r}")
    return value


def make_book_ref(catalog: str, book_id: int) -> str:
    return f"{_normalize_catalog_id(catalog)}:{int(book_id)}"


def parse_book_ref(book_ref: str | int, catalog: Optional[str] = None) -> tuple[str, int]:
    if isinstance(book_ref, int):
        return _normalize_catalog_id(catalog), int(book_ref)
    text = str(book_ref).strip()
    if ":" in text and catalog is None:
        cat, local_id = text.split(":", 1)
        return _normalize_catalog_id(cat), int(local_id)
    return _normalize_catalog_id(catalog), int(text)


def _bundled_catalog_map() -> dict[str, Path]:
    if not _BUNDLED_CATALOGS_DIR.exists():
        return {}
    result: dict[str, Path] = {}
    for path in _BUNDLED_CATALOGS_DIR.glob("*.db"):
        catalog = _normalize_catalog_id(path.stem)
        result[catalog] = path.resolve()
    return result


def _user_catalog_map() -> dict[str, Path]:
    if not DATA_DIR.exists():
        return {}
    _default_filename = _DB_PATH.name.lower()
    result: dict[str, Path] = {}
    for path in DATA_DIR.glob("*.db"):
        if path.name.lower() == _default_filename:
            continue
        if not _CATALOG_ID_RE.fullmatch(path.stem.lower()):
            continue  # skip files with invalid catalog-id names (dots, spaces, etc.)
        catalog = _normalize_catalog_id(path.stem)
        result[catalog] = path.resolve()
    return result


def list_catalogs() -> list[dict]:
    catalogs: list[dict] = [
        {
            "id": _DEFAULT_CATALOG,
            "label": "Local Library",
            "path": str(_DB_PATH.resolve()),
            "read_only": False,
            "source": "local",
            "enabled": True,
        }
    ]
    user_catalogs = _user_catalog_map()
    bundled_catalogs = _bundled_catalog_map()
    for catalog, path in sorted(user_catalogs.items()):
        if catalog == _DEFAULT_CATALOG:
            continue
        catalogs.append({
            "id": catalog,
            "label": catalog.replace("_", " ").replace("-", " ").title(),
            "path": str(path),
            "read_only": False,
            "source": "user",
            "enabled": True,
        })
    for catalog, path in sorted(bundled_catalogs.items()):
        if catalog == _DEFAULT_CATALOG or catalog in user_catalogs:
            continue
        catalogs.append({
            "id": catalog,
            "label": catalog.replace("_", " ").replace("-", " ").title(),
            "path": str(path),
            "read_only": True,
            "source": "bundled",
            "enabled": True,
        })
    return catalogs


def _catalog_info(catalog: Optional[str], create: bool = False) -> dict:
    catalog_id = _normalize_catalog_id(catalog)
    if catalog_id == _DEFAULT_CATALOG:
        return {
            "id": catalog_id,
            "path": _DB_PATH.resolve(),
            "read_only": False,
            "source": "local",
        }

    user_catalogs = _user_catalog_map()
    if catalog_id in user_catalogs:
        return {
            "id": catalog_id,
            "path": user_catalogs[catalog_id],
            "read_only": False,
            "source": "user",
        }

    bundled_catalogs = _bundled_catalog_map()
    if catalog_id in bundled_catalogs:
        return {
            "id": catalog_id,
            "path": bundled_catalogs[catalog_id],
            "read_only": True,
            "source": "bundled",
        }

    if create:
        return {
            "id": catalog_id,
            "path": (DATA_DIR / f"{catalog_id}.db").resolve(),
            "read_only": False,
            "source": "user",
        }

    raise ValueError(f"Unknown catalog: {catalog_id}")


def _selected_catalog_ids(catalog: Optional[str] = None, catalogs: Optional[list[str]] = None) -> list[str]:
    if catalogs:
        seen: set[str] = set()
        ordered: list[str] = []
        for value in catalogs:
            normalized = _normalize_catalog_id(value)
            if normalized not in seen:
                _catalog_info(normalized)
                seen.add(normalized)
                ordered.append(normalized)
        return ordered
    if catalog:
        normalized = _normalize_catalog_id(catalog)
        _catalog_info(normalized)
        return [normalized]
    return [item["id"] for item in list_catalogs() if item.get("enabled", True)]


def get_db_path(catalog: Optional[str] = None, create: bool = False) -> Path:
    info = _catalog_info(catalog, create=create)
    path = Path(info["path"])
    if not info["read_only"]:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def db_connection(catalog: Optional[str] = None, create: bool = False):
    conn = sqlite3.connect(str(get_db_path(catalog, create=create)), check_same_thread=False)
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


def _index_book_sentences(
    conn: sqlite3.Connection,
    book_id: int,
    title: str,
    body: str,
) -> None:
    rows: list[tuple[int, int, str, int, int]] = []
    sentence_index = 0
    for source_field, raw_text in (("title", title), ("body", body)):
        for char_start, char_end, _sentence_text in _split_sentences(raw_text):
            rows.append((book_id, sentence_index, source_field, char_start, char_end))
            sentence_index += 1
    if rows:
        conn.executemany(
            """
            INSERT INTO sentences
                (book_id, sentence_index, source_field, char_start, char_end)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )


def _backfill_book_sentences(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT b.id, b.title, b.body
        FROM books b
        WHERE NOT EXISTS (
            SELECT 1
            FROM sentences s
            WHERE s.book_id = b.id AND s.deleted = 0
        )
        """
    ).fetchall()
    for row in rows:
        _index_book_sentences(
            conn,
            int(row["id"]),
            row["title"] or "",
            _decompress(row["body"]) or "",
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
            book_id           INTEGER NOT NULL,
            sentence_index    INTEGER NOT NULL,
            source_field      TEXT NOT NULL,
            char_start        INTEGER NOT NULL,
            char_end          INTEGER NOT NULL,
            chroma_indexed_at TEXT,
            deleted           INTEGER NOT NULL DEFAULT 0,
            UNIQUE(book_id, sentence_index)
        )
    """)

    if compatible:
        conn.execute("""
            INSERT INTO sentences_new
                (id, book_id, sentence_index, source_field, char_start, char_end, chroma_indexed_at, deleted)
            SELECT
                id,
                book_id,
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sentences_book_id ON sentences(book_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sentences_chroma_indexed_at ON sentences(chroma_indexed_at)")

    if not compatible:
        _backfill_book_sentences(conn)


def _extract_sentence_text(book_row: sqlite3.Row | dict, sentence_row: sqlite3.Row | dict) -> str:
    source_field = str(sentence_row["source_field"] or "")
    if isinstance(book_row, dict):
        source_value = book_row.get(source_field, "")
        source_text  = _decompress(source_value) if source_field == "body" else str(source_value or "")
    else:
        source_value = book_row[source_field]
        source_text  = _decompress(source_value) if source_field == "body" else str(source_value or "")
    char_start = max(0, int(sentence_row["char_start"]))
    char_end   = max(char_start, int(sentence_row["char_end"]))
    return source_text[char_start:char_end].strip()


def _sentence_locator(catalog: str, sentence_id: int) -> str:
    return f"library/{_normalize_catalog_id(catalog)}/{int(sentence_id)}"


def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS books (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT    NOT NULL,
                author      TEXT,
                year        INTEGER,
                language    TEXT,
                genre       TEXT,
                notes       TEXT,
                source      TEXT,
                source_id   TEXT,
                word_count  INTEGER,
                body        TEXT,
                added_at    TEXT,
                updated_at  TEXT
            )
        """)
        existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(books)")}
        for name, definition in (
            ("source", "TEXT"),
            ("source_id", "TEXT"),
            ("added_at", "TEXT"),
            ("updated_at", "TEXT"),
        ):
            if name not in existing_cols:
                conn.execute(f"ALTER TABLE books ADD COLUMN {name} {definition}")
        conn.execute("UPDATE books SET added_at = COALESCE(added_at, ?) WHERE added_at IS NULL OR added_at = ''", (_now(),))
        conn.execute("UPDATE books SET updated_at = COALESCE(updated_at, added_at, ?) WHERE updated_at IS NULL OR updated_at = ''", (_now(),))
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_books_source_id ON books(source, source_id) WHERE source_id IS NOT NULL AND source_id != ''")
        # Detect old content-table schema (triggers existed before this migration)
        _has_old_triggers = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name='books_ai'"
        ).fetchone()[0] > 0

        # Drop old triggers regardless (no-op if already gone)
        for _trg in ("books_ai", "books_ad", "books_au"):
            conn.execute(f"DROP TRIGGER IF EXISTS {_trg}")

        if _has_old_triggers:
            # Migrating from content FTS: rebuild as contentless and compress body
            conn.execute("DROP TABLE IF EXISTS books_fts")

        # FTS: contentless — body stored compressed so triggers can't index it.
        # Python CRUD code manages FTS explicitly with plain text.
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5(
                title, author, body,
                tokenize='unicode61 remove_diacritics 1',
                content=''
            )
        """)

        if _has_old_triggers:
            # Compress all text bodies and populate FTS with plain text
            _rows = conn.execute(
                "SELECT id, title, author, body FROM books WHERE body IS NOT NULL"
            ).fetchall()
            for _row in _rows:
                conn.execute("UPDATE books SET body=? WHERE id=?",
                             (_compress(_row["body"]), _row["id"]))
                conn.execute(
                    "INSERT INTO books_fts(rowid, title, author, body) VALUES (?,?,?,?)",
                    (_row["id"], _row["title"] or "", _row["author"] or "", _row["body"] or ""),
                )
            # Index rows with NULL body (title/author still searchable)
            conn.execute("""
                INSERT INTO books_fts(rowid, title, author, body)
                SELECT id, COALESCE(title,''), COALESCE(author,''), ''
                FROM books WHERE body IS NULL
            """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS sentences (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id           INTEGER NOT NULL,
                sentence_index    INTEGER NOT NULL,
                source_field      TEXT NOT NULL,
                char_start        INTEGER NOT NULL,
                char_end          INTEGER NOT NULL,
                chroma_indexed_at TEXT,
                deleted           INTEGER NOT NULL DEFAULT 0,
                UNIQUE(book_id, sentence_index)
            )
        """)
        sentence_cols = {row[1] for row in conn.execute("PRAGMA table_info(sentences)").fetchall()}
        if "deleted" not in sentence_cols:
            conn.execute("ALTER TABLE sentences ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
        if "chroma_indexed_at" not in sentence_cols:
            conn.execute("ALTER TABLE sentences ADD COLUMN chroma_indexed_at TEXT")
        if _sentence_schema_needs_normalization(conn):
            _normalize_sentence_schema(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sentences_book_id ON sentences(book_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sentences_chroma_indexed_at ON sentences(chroma_indexed_at)")
        if _sentence_index_needs_rebuild(conn):
            conn.execute("DELETE FROM sentences")
            _backfill_book_sentences(conn)
        else:
            _backfill_book_sentences(conn)


def init_db() -> None:
    initialized: set[str] = set()
    for catalog in _selected_catalog_ids():
        info = _catalog_info(catalog, create=(catalog == _DEFAULT_CATALOG))
        if info["read_only"]:
            continue
        if catalog in initialized:
            continue
        with db_connection(catalog, create=True) as conn:
            _ensure_schema(conn)
        initialized.add(catalog)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_page_markers(text: str) -> str:
    """Remove Gutenberg page-break markers like {1}, {vii}, {ix} etc."""
    return re.sub(r"\{[ivxlcdmIVXLCDM\d]+\}", "", text)


def _make_body_snippet(body: str, q: str, context_chars: int = 300) -> Optional[str]:
    """Find first occurrence of any query word in *body* and return context around it.

    Used when FTS5 contentless snippet() returns NULL.  Marks the matched word
    with square brackets, e.g. ``...some text [AT&T] more text...``.
    """
    # Extract bare words from the raw query string (strip FTS quote wrappers)
    words = [w.strip('"').strip() for w in q.split() if w.strip('"').strip()]
    body_lower = body.lower()
    best_pos = -1
    best_word = ""
    for word in words:
        pos = body_lower.find(word.lower())
        if pos != -1 and (best_pos == -1 or pos < best_pos):
            best_pos = pos
            best_word = word
    if best_pos == -1:
        return None
    half = context_chars // 2
    start = max(0, best_pos - half)
    end   = min(len(body), best_pos + len(best_word) + half)
    text  = body[start:end].strip()
    # Bracket the first match for visibility
    if best_word:
        text = re.sub(re.escape(best_word), f"[{best_word}]", text, count=1, flags=re.IGNORECASE)
    return ("..." if start > 0 else "") + text + ("..." if end < len(body) else "")


def _fts_delete(conn: sqlite3.Connection, book_id: int, title: str, author: str, body: str) -> None:
    """Remove a book from the FTS index."""
    conn.execute(
        "INSERT INTO books_fts(books_fts, rowid, title, author, body) VALUES ('delete', ?, ?, ?, ?)",
        (book_id, title, author, body),
    )


def _fts_insert(conn: sqlite3.Connection, book_id: int, title: str, author: str, body: str) -> None:
    """Add or re-add a book to the FTS index."""
    conn.execute(
        "INSERT INTO books_fts(rowid, title, author, body) VALUES (?, ?, ?, ?)",
        (book_id, title, author, body),
    )


_BOOK_COLS = (
    "id", "title", "author", "year", "language", "genre",
    "notes", "source", "source_id", "word_count", "added_at", "updated_at",
)

_BOOK_COLS_WITH_BODY = _BOOK_COLS + ("body",)


def _row_to_dict(row: sqlite3.Row, include_body: bool = False, catalog: Optional[str] = None) -> dict:
    cols = _BOOK_COLS_WITH_BODY if include_body else _BOOK_COLS
    d = {c: row[c] for c in cols}
    if include_body:
        d["body"] = _decompress(d.get("body"))
    if catalog is not None:
        d["catalog"] = catalog
        d["route_id"] = make_book_ref(catalog, d["id"])
    return d


def list_writable_catalogs() -> list[str]:
    return [item["id"] for item in list_catalogs() if not item.get("read_only")]


def _assert_catalog_writable(catalog: Optional[str]) -> str:
    info = _catalog_info(catalog, create=True)
    if info["read_only"]:
        raise ValueError(f"Catalog '{info['id']}' is read-only")
    return info["id"]


def _merge_catalog_rows(row_sets: list[list[dict]], limit: int, offset: int) -> list[dict]:
    merged: list[dict] = []
    index = 0
    while True:
        added = False
        for rows in row_sets:
            if index < len(rows):
                merged.append(rows[index])
                added = True
        if not added:
            break
        index += 1
    return merged[offset: offset + limit]


def get_book_sentences(book_id: str | int, include_deleted: bool = False, catalog: Optional[str] = None) -> list[dict]:
    catalog_id, local_id = parse_book_ref(book_id, catalog=catalog)
    cols = "s.id, s.book_id, s.sentence_index, s.source_field, s.char_start, s.char_end, s.chroma_indexed_at, s.deleted, b.title, b.body"
    where = "WHERE s.book_id = ?"
    if not include_deleted:
        where += " AND s.deleted = 0"
    with db_connection(catalog_id) as conn:
        rows = conn.execute(
            f"""
            SELECT {cols}
            FROM sentences s
            JOIN books b ON b.id = s.book_id
            {where}
            ORDER BY s.sentence_index ASC
            """,
            (local_id,),
        ).fetchall()
    results: list[dict] = []
    for row in rows:
        item = dict(row)
        item["sentence_text"] = _extract_sentence_text(row, row)
        item["catalog"]       = catalog_id
        item["route_id"]      = make_book_ref(catalog_id, int(item["book_id"]))
        item["locator"]       = _sentence_locator(catalog_id, int(item["id"]))
        item.pop("body", None)
        results.append(item)
    return results


def get_sentence(catalog: str, sentence_id: int) -> Optional[dict]:
    catalog_id = _normalize_catalog_id(catalog)
    with db_connection(catalog_id) as conn:
        row = conn.execute(
            """
            SELECT s.id, s.book_id, s.sentence_index, s.source_field, s.char_start, s.char_end,
                   s.chroma_indexed_at, s.deleted, b.title, b.author, b.year, b.language,
                   b.genre, b.body
            FROM sentences s
            JOIN books b ON b.id = s.book_id
            WHERE s.id = ?
            """,
            (int(sentence_id),),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["sentence_text"] = _extract_sentence_text(row, row)
    item["catalog"]       = catalog_id
    item["route_id"]      = make_book_ref(catalog_id, int(item["book_id"]))
    item["locator"]       = _sentence_locator(catalog_id, int(item["id"]))
    item.pop("body", None)
    return item


def backfill_sentence_index(catalog: str) -> dict:
    catalog_id = _assert_catalog_writable(catalog)
    with db_connection(catalog_id, create=True) as conn:
        _ensure_schema(conn)
        before = int(conn.execute("SELECT COUNT(*) FROM sentences WHERE deleted = 0").fetchone()[0])
        _backfill_book_sentences(conn)
        after  = int(conn.execute("SELECT COUNT(*) FROM sentences WHERE deleted = 0").fetchone()[0])
    return {
        "catalog":         catalog_id,
        "sentences_added": max(0, after - before),
        "sentence_count":  after,
    }


def rebuild_sentence_index(catalog: str, book_id: Optional[str | int] = None) -> dict:
    catalog_id = _assert_catalog_writable(catalog)
    local_id: Optional[int] = None
    if book_id is not None:
        _, local_id = parse_book_ref(book_id, catalog=catalog_id)

    deleted_sentence_ids: list[int] = []
    rebuilt_sentences:    int        = 0

    with db_connection(catalog_id, create=True) as conn:
        _ensure_schema(conn)
        if local_id is None:
            deleted_sentence_ids = [
                int(row[0]) for row in conn.execute(
                    "SELECT id FROM sentences WHERE deleted = 0 ORDER BY id"
                ).fetchall()
            ]
            conn.execute("DELETE FROM sentences")
            _backfill_book_sentences(conn)
            rebuilt_sentences = int(conn.execute(
                "SELECT COUNT(*) FROM sentences WHERE deleted = 0"
            ).fetchone()[0])
        else:
            book_row = conn.execute(
                "SELECT id, title, body FROM books WHERE id = ?",
                (local_id,),
            ).fetchone()
            if book_row is None:
                raise ValueError(f"Book not found: {make_book_ref(catalog_id, local_id)}")
            deleted_sentence_ids = [
                int(row[0]) for row in conn.execute(
                    "SELECT id FROM sentences WHERE book_id = ? AND deleted = 0 ORDER BY id",
                    (local_id,),
                ).fetchall()
            ]
            conn.execute("DELETE FROM sentences WHERE book_id = ?", (local_id,))
            _index_book_sentences(conn, local_id, book_row["title"] or "", _decompress(book_row["body"]) or "")
            rebuilt_sentences = int(conn.execute(
                "SELECT COUNT(*) FROM sentences WHERE book_id = ? AND deleted = 0",
                (local_id,),
            ).fetchone()[0])

    if deleted_sentence_ids:
        try:
            from app.chroma_index import delete_sentence_ids
            delete_sentence_ids(catalog_id, deleted_sentence_ids)
        except Exception:
            pass

    try:
        from app.chroma_index import sync_book_sentences, sync_pending_sentences
        if local_id is None:
            sync_pending_sentences(catalog_id, batch_size=250)
        else:
            sync_book_sentences(catalog_id, local_id)
    except Exception:
        pass

    return {
        "catalog":              catalog_id,
        "book_id":              local_id,
        "rebuilt_sentences":    rebuilt_sentences,
        "deleted_sentence_ids": len(deleted_sentence_ids),
    }


def get_sentences_for_chroma(
    catalog: str,
    limit: int = 250,
    only_unindexed: bool = False,
    sentence_ids: Optional[list[int]] = None,
) -> list[dict]:
    catalog_id = _normalize_catalog_id(catalog)
    with db_connection(catalog_id) as conn:
        clauses = ["s.deleted = 0"]
        params: list[object] = []
        if sentence_ids:
            validated = [int(item) for item in sentence_ids]
            placeholders = ",".join("?" for _ in validated)
            clauses.append(f"s.id IN ({placeholders})")
            params.extend(validated)
        if only_unindexed:
            clauses.append("(s.chroma_indexed_at IS NULL OR s.chroma_indexed_at = '')")
        params.append(max(1, int(limit)))
        rows = conn.execute(
            f"""
            SELECT s.id, s.book_id, s.sentence_index, s.source_field, s.char_start, s.char_end,
                   s.chroma_indexed_at, b.title, b.author, b.year, b.language, b.genre, b.body
            FROM sentences s
            JOIN books b ON b.id = s.book_id
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
        item["catalog"]       = catalog_id
        item["route_id"]      = make_book_ref(catalog_id, int(item["book_id"]))
        item["locator"]       = _sentence_locator(catalog_id, int(item["id"]))
        item.pop("body", None)
        results.append(item)
    return results


def mark_sentences_chroma_indexed(catalog: str, sentence_ids: list[int]) -> int:
    if not sentence_ids:
        return 0
    catalog_id = _normalize_catalog_id(catalog)
    validated  = [int(item) for item in sentence_ids]
    placeholders = ",".join("?" for _ in validated)
    with db_connection(catalog_id, create=True) as conn:
        cur = conn.execute(
            f"UPDATE sentences SET chroma_indexed_at = ? WHERE id IN ({placeholders})",
            [_now(), *validated],
        )
    return int(cur.rowcount or 0)


def reset_sentence_chroma_index(catalog: str, book_id: Optional[str | int] = None) -> int:
    catalog_id = _normalize_catalog_id(catalog)
    local_id: Optional[int] = None
    if book_id is not None:
        _, local_id = parse_book_ref(book_id, catalog=catalog_id)
    with db_connection(catalog_id, create=True) as conn:
        if local_id is None:
            cur = conn.execute("UPDATE sentences SET chroma_indexed_at = NULL")
        else:
            cur = conn.execute("UPDATE sentences SET chroma_indexed_at = NULL WHERE book_id = ?", (local_id,))
    return int(cur.rowcount or 0)


def set_sentence_deleted(catalog: str, sentence_id: int, deleted: bool) -> dict:
    catalog_id = _assert_catalog_writable(catalog)
    entry_book_id: Optional[int] = None
    with db_connection(catalog_id, create=True) as conn:
        row = conn.execute(
            "SELECT id, book_id, deleted FROM sentences WHERE id = ?",
            (int(sentence_id),),
        ).fetchone()
        if row is None:
            raise ValueError(f"Sentence {sentence_id} not found in catalog '{catalog_id}'.")
        entry_book_id = int(row["book_id"])
        conn.execute(
            "UPDATE sentences SET deleted = ?, chroma_indexed_at = CASE WHEN ? = 0 THEN NULL ELSE chroma_indexed_at END WHERE id = ?",
            (1 if deleted else 0, 1 if deleted else 0, int(sentence_id)),
        )
    try:
        from app.chroma_index import delete_sentence_ids, sync_book_sentences
        if deleted:
            delete_sentence_ids(catalog_id, [int(sentence_id)])
        elif entry_book_id is not None:
            sync_book_sentences(catalog_id, entry_book_id)
    except Exception:
        pass
    sentence = get_sentence(catalog_id, int(sentence_id))
    return {
        "catalog":     catalog_id,
        "sentence_id": int(sentence_id),
        "deleted":     bool(deleted),
        "sentence":    sentence,
    }


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def add_book(
    title: str,
    body: Optional[str],
    author: Optional[str] = None,
    year: Optional[int] = None,
    language: Optional[str] = None,
    genre: Optional[str] = None,
    notes: Optional[str] = None,
    source: Optional[str] = None,
    source_id: Optional[str] = None,
    catalog: Optional[str] = None,
    **_ignored,
) -> dict:
    catalog_id = _assert_catalog_writable(catalog)
    cleaned_body = _strip_page_markers(body) if body else None
    word_count = _compute_word_count(cleaned_body)
    compressed = _compress(cleaned_body)
    now = _now()
    with db_connection(catalog_id, create=True) as conn:
        _ensure_schema(conn)
        cur = conn.execute("""
            INSERT INTO books (title, author, year, language, genre, notes, source, source_id,
                               word_count, body, added_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (title, author, year, language, genre, notes, source, source_id,
              word_count, compressed, now, now))
        book_id = cur.lastrowid
        conn.execute(
            "INSERT INTO books_fts(rowid, title, author, body) VALUES (?, ?, ?, ?)",
            (book_id, title or "", author or "", cleaned_body or ""),
        )
        _index_book_sentences(conn, int(book_id), title or "", cleaned_body or "")
        cols = ", ".join(_BOOK_COLS)
        row = conn.execute(f"SELECT {cols} FROM books WHERE id = ?", (book_id,)).fetchone()
    try:
        from app.chroma_index import sync_book_sentences
        sync_book_sentences(catalog_id, int(book_id))
    except Exception:
        pass
    return _row_to_dict(row, include_body=False, catalog=catalog_id)


def get_book(book_id: str | int, include_body: bool = True, catalog: Optional[str] = None) -> Optional[dict]:
    catalog_id, local_id = parse_book_ref(book_id, catalog=catalog)
    cols = ", ".join(_BOOK_COLS_WITH_BODY if include_body else _BOOK_COLS)
    with db_connection(catalog_id) as conn:
        row = conn.execute(
            f"SELECT {cols} FROM books WHERE id = ?", (local_id,)
        ).fetchone()
    return _row_to_dict(row, include_body=include_body, catalog=catalog_id) if row else None


def get_book_chunk(
    book_id: str | int,
    offset_chars: int = 0,
    length_chars: int = 4096,
    catalog: Optional[str] = None,
) -> Optional[dict]:
    """Return a character slice of a book body. Only the slice travels over the wire."""
    catalog_id, local_id = parse_book_ref(book_id, catalog=catalog)
    with db_connection(catalog_id) as conn:
        row = conn.execute(
            "SELECT id, title, author, genre, word_count, body FROM books WHERE id = ?",
            (local_id,),
        ).fetchone()
    if row is None:
        return None
    body         = _decompress(row["body"]) or ""
    offset_chars = max(0, offset_chars)
    length_chars = max(1, length_chars)
    chunk        = body[offset_chars: offset_chars + length_chars]
    next_offset  = offset_chars + length_chars
    total_chars  = len(body)
    return {
        "route_id":     make_book_ref(catalog_id, local_id),
        "title":        row["title"],
        "author":       row["author"],
        "genre":        row["genre"],
        "word_count":   row["word_count"],
        "chunk":        chunk,
        "offset_chars": offset_chars,
        "next_offset":  next_offset if next_offset < total_chars else None,
        "total_chars":  total_chars,
        "has_more":     next_offset < total_chars,
    }


def update_book_body(book_id: str | int, body: str, catalog: Optional[str] = None) -> Optional[dict]:
    catalog_id, local_id = parse_book_ref(book_id, catalog=catalog)
    _assert_catalog_writable(catalog_id)
    cleaned = _strip_page_markers(body) if body else None
    word_count = _compute_word_count(cleaned)
    compressed = _compress(cleaned)
    previous_sentence_ids: list[int] = []
    title_text:             str       = ""
    with db_connection(catalog_id, create=True) as conn:
        _ensure_schema(conn)
        cur_row = conn.execute(
            "SELECT title, author, body FROM books WHERE id = ?", (local_id,)
        ).fetchone()
        title_text = str(cur_row["title"] or "") if cur_row else ""
        if cur_row:
            _fts_delete(conn, local_id, cur_row["title"] or "", cur_row["author"] or "",
                        _decompress(cur_row["body"]) or "")
            previous_sentence_ids = [
                int(row[0]) for row in conn.execute(
                    "SELECT id FROM sentences WHERE book_id = ? ORDER BY id",
                    (local_id,),
                ).fetchall()
            ]
        conn.execute(
            "UPDATE books SET body = ?, word_count = ?, updated_at = ? WHERE id = ?",
            (compressed, word_count, _now(), local_id),
        )
        if cur_row:
            _fts_insert(conn, local_id, cur_row["title"] or "", cur_row["author"] or "", cleaned or "")
            conn.execute("DELETE FROM sentences WHERE book_id = ?", (local_id,))
            _index_book_sentences(conn, local_id, title_text, cleaned or "")
    if previous_sentence_ids:
        try:
            from app.chroma_index import delete_sentence_ids
            delete_sentence_ids(catalog_id, previous_sentence_ids)
        except Exception:
            pass
    try:
        from app.chroma_index import sync_book_sentences
        sync_book_sentences(catalog_id, local_id)
    except Exception:
        pass
    return get_book(local_id, include_body=False, catalog=catalog_id)


def list_books(
    limit: int = 100,
    offset: int = 0,
    catalog: Optional[str] = None,
    catalogs: Optional[list[str]] = None,
) -> list[dict]:
    cols = ", ".join(_BOOK_COLS)
    result_sets: list[list[dict]] = []
    for catalog_id in _selected_catalog_ids(catalog=catalog, catalogs=catalogs):
        with db_connection(catalog_id) as conn:
            rows = conn.execute(
                f"SELECT {cols} FROM books ORDER BY title LIMIT ? OFFSET 0",
                (limit + offset,),
            ).fetchall()
        result_sets.append([_row_to_dict(r, catalog=catalog_id) for r in rows])
    if len(result_sets) == 1:
        return result_sets[0][offset: offset + limit]
    merged = [item for rows in result_sets for item in rows]
    merged.sort(key=lambda item: ((item.get("title") or "").lower(), item.get("catalog") or "", item.get("id") or 0))
    return merged[offset: offset + limit]


def update_book(book_id: str | int, fields: dict, catalog: Optional[str] = None) -> Optional[dict]:
    """Partial update. Only provided keys are written."""
    catalog_id, local_id = parse_book_ref(book_id, catalog=catalog)
    _assert_catalog_writable(catalog_id)
    allowed = {
        "title", "author", "year", "language", "genre",
        "notes", "body", "source", "source_id",
    }
    to_set = {k: v for k, v in fields.items() if k in allowed}
    if not to_set:
        return get_book(local_id, include_body=False, catalog=catalog_id)

    if "body" in to_set:
        cleaned_body: Optional[str] = _strip_page_markers(to_set["body"]) if to_set["body"] else None
        to_set["body"] = _compress(cleaned_body)
        to_set["word_count"] = _compute_word_count(cleaned_body)

    fts_affected = bool({"title", "author", "body"} & to_set.keys())
    to_set["updated_at"] = _now()
    assignments = ", ".join(f"{k} = ?" for k in to_set)
    values = list(to_set.values())
    values.append(local_id)

    previous_sentence_ids: list[int] = []
    with db_connection(catalog_id, create=True) as conn:
        _ensure_schema(conn)
        if fts_affected:
            cur_row = conn.execute(
                "SELECT title, author, body FROM books WHERE id = ?", (local_id,)
            ).fetchone()
            if cur_row:
                _fts_delete(conn, local_id, cur_row["title"] or "", cur_row["author"] or "",
                            _decompress(cur_row["body"]) or "")
                previous_sentence_ids = [
                    int(row[0]) for row in conn.execute(
                        "SELECT id FROM sentences WHERE book_id = ? ORDER BY id",
                        (local_id,),
                    ).fetchall()
                ]
        conn.execute(f"UPDATE books SET {assignments} WHERE id = ?", values)
        if fts_affected:
            upd_row = conn.execute(
                "SELECT title, author, body FROM books WHERE id = ?", (local_id,)
            ).fetchone()
            if upd_row:
                _fts_insert(conn, local_id, upd_row["title"] or "", upd_row["author"] or "",
                            _decompress(upd_row["body"]) or "")
                conn.execute("DELETE FROM sentences WHERE book_id = ?", (local_id,))
                _index_book_sentences(conn, local_id, upd_row["title"] or "", _decompress(upd_row["body"]) or "")
    if fts_affected and previous_sentence_ids:
        try:
            from app.chroma_index import delete_sentence_ids
            delete_sentence_ids(catalog_id, previous_sentence_ids)
        except Exception:
            pass
    if fts_affected:
        try:
            from app.chroma_index import sync_book_sentences
            sync_book_sentences(catalog_id, local_id)
        except Exception:
            pass
    return get_book(local_id, include_body=False, catalog=catalog_id)


def delete_book(book_id: str | int, catalog: Optional[str] = None) -> bool:
    catalog_id, local_id = parse_book_ref(book_id, catalog=catalog)
    _assert_catalog_writable(catalog_id)
    sentence_ids: list[int] = []
    with db_connection(catalog_id, create=True) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            "SELECT title, author, body FROM books WHERE id = ?", (local_id,)
        ).fetchone()
        if not row:
            return False
        sentence_ids = [
            int(item[0]) for item in conn.execute(
                "SELECT id FROM sentences WHERE book_id = ?",
                (local_id,),
            ).fetchall()
        ]
        _fts_delete(conn, local_id, row["title"] or "", row["author"] or "",
                    _decompress(row["body"]) or "")
        conn.execute("DELETE FROM sentences WHERE book_id = ?", (local_id,))
        conn.execute("DELETE FROM books WHERE id = ?", (local_id,))
    if sentence_ids:
        try:
            from app.chroma_index import delete_sentence_ids
            delete_sentence_ids(catalog_id, sentence_ids)
        except Exception:
            pass
    return True


def move_book(book_id: str | int, dest_catalog: str, src_catalog: Optional[str] = None) -> Optional[dict]:
    """Copy a book to dest_catalog, then delete from src_catalog.
    Returns the new book dict with updated catalog/id, or None if source not found."""
    src_catalog_id, local_id = parse_book_ref(book_id, catalog=src_catalog)
    dest_catalog_id = _normalize_catalog_id(dest_catalog)
    if src_catalog_id == dest_catalog_id:
        return get_book(local_id, include_body=False, catalog=src_catalog_id)

    _assert_catalog_writable(src_catalog_id)
    _assert_catalog_writable(dest_catalog_id)

    cols = ", ".join(_BOOK_COLS_WITH_BODY)
    with db_connection(src_catalog_id) as conn:
        row = conn.execute(
            f"SELECT {cols} FROM books WHERE id = ?", (local_id,)
        ).fetchone()
    if not row:
        return None

    # Re-use add_book so FTS, compression, schema creation all go through the same path
    new_book = add_book(
        title=row["title"],
        body=_decompress(row["body"]) if row["body"] else None,
        author=row["author"],
        year=row["year"],
        language=row["language"],
        genre=row["genre"],
        notes=row["notes"],
        source=row["source"],
        source_id=row["source_id"],
        catalog=dest_catalog_id,
    )
    delete_book(local_id, catalog=src_catalog_id)
    return new_book


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_books(
    q: Optional[str] = None,
    author: Optional[str] = None,
    title: Optional[str] = None,
    year: Optional[int] = None,
    language: Optional[str] = None,
    genre: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    catalog: Optional[str] = None,
    catalogs: Optional[list[str]] = None,
    fts_scope: str = "all",  # "all": title+author+body; "metadata": title+author only
) -> list[dict]:
    cols = ", ".join(f"b.{c}" for c in _BOOK_COLS)
    result_sets: list[list[dict]] = []

    for catalog_id in _selected_catalog_ids(catalog=catalog, catalogs=catalogs):
        params: list = []
        if q:
            # metadata scope: restrict FTS5 to title/author columns to avoid body false-positives
            if fts_scope == "metadata":
                snippet_col = "NULL AS snippet"
            else:
                snippet_col = "snippet(books_fts, 2, '[', ']', '...', 32) AS snippet"
            sql = f"""
                SELECT {cols}, {snippet_col}
                FROM books_fts
                JOIN books b ON b.id = books_fts.rowid
                WHERE books_fts MATCH ?
            """
            fts_q = fts_build_query(q)
            if not fts_q:
                continue
            if fts_scope == "metadata":
                fts_q = "{title author}: " + fts_q
            params.append(fts_q)
            filters, filter_params = _build_meta_filters(
                author, title, year, language, genre, table_prefix="b"
            )
            if filters:
                sql += " AND " + " AND ".join(filters)
                params.extend(filter_params)
            sql += " ORDER BY rank LIMIT ? OFFSET 0"
            params += [limit + offset]
        else:
            snippet_col = "NULL AS snippet"
            sql = f"SELECT {cols}, {snippet_col} FROM books b WHERE 1=1"
            filters, filter_params = _build_meta_filters(
                author, title, year, language, genre, table_prefix="b"
            )
            if filters:
                sql += " AND " + " AND ".join(filters)
                params.extend(filter_params)
            sql += " ORDER BY b.title LIMIT ? OFFSET 0"
            params += [limit + offset]

        result: list[dict] = []
        with db_connection(catalog_id) as conn:
            rows = conn.execute(sql, params).fetchall()
            for row in rows:
                d = _row_to_dict(row, include_body=False, catalog=catalog_id)
                snippet = row["snippet"]
                if q and snippet is None:
                    # Contentless FTS5: snippet() returns NULL; extract manually
                    body_row = conn.execute(
                        "SELECT body FROM books WHERE id = ?", (d["id"],)
                    ).fetchone()
                    if body_row and body_row[0]:
                        body_text = _decompress(body_row[0]) or ""
                        snippet = _make_body_snippet(body_text, q)
                d["snippet"] = snippet
                result.append(d)
        result_sets.append(result)

    if not result_sets:
        return []
    if len(result_sets) == 1:
        return result_sets[0][offset: offset + limit]
    if q:
        return _merge_catalog_rows(result_sets, limit=limit, offset=offset)
    merged = [item for rows in result_sets for item in rows]
    merged.sort(key=lambda item: ((item.get("title") or "").lower(), item.get("catalog") or "", item.get("id") or 0))
    return merged[offset: offset + limit]


def _build_meta_filters(
    author, title, year, language, genre, table_prefix: str = ""
) -> tuple[list[str], list]:
    prefix = f"{table_prefix}." if table_prefix else ""
    filters: list[str] = []
    params: list = []
    if author:
        filters.append(f"{prefix}author LIKE ?")
        params.append(f"%{author}%")
    if title:
        filters.append(f"{prefix}title LIKE ?")
        params.append(f"%{title}%")
    if year is not None:
        filters.append(f"{prefix}year = ?")
        params.append(year)
    if language:
        filters.append(f"{prefix}language = ?")
        params.append(language)
    if genre:
        filters.append(f"{prefix}genre LIKE ?")
        params.append(f"%{genre}%")
    return filters, params


# ---------------------------------------------------------------------------
# Incomplete records
# ---------------------------------------------------------------------------

def list_incomplete(fields: Optional[list[str]] = None, catalog: Optional[str] = None, catalogs: Optional[list[str]] = None) -> list[dict]:
    """Return books with NULL/empty values in completeness fields."""
    check = [f for f in (fields or list(COMPLETENESS_FIELDS))
             if f in COMPLETENESS_FIELDS]
    if not check:
        check = list(COMPLETENESS_FIELDS)

    conditions = " OR ".join(
        f"({f} IS NULL OR {f} = '')" for f in check
    )
    cols = ", ".join(_BOOK_COLS)
    result = []
    for catalog_id in _selected_catalog_ids(catalog=catalog, catalogs=catalogs):
        with db_connection(catalog_id) as conn:
            rows = conn.execute(
                f"SELECT {cols} FROM books WHERE {conditions} ORDER BY title"
            ).fetchall()
        for row in rows:
            d = _row_to_dict(row, include_body=False, catalog=catalog_id)
            d["missing_fields"] = [
                f for f in COMPLETENESS_FIELDS
                if not row[f]
            ]
            result.append(d)
    result.sort(key=lambda item: ((item.get("title") or "").lower(), item.get("catalog") or "", item.get("id") or 0))
    return result


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def get_status(catalog: Optional[str] = None, catalogs: Optional[list[str]] = None) -> dict:
    total = 0
    incomplete = 0
    no_body = 0
    db_size = 0
    catalog_stats: list[dict] = []
    for catalog_id in _selected_catalog_ids(catalog=catalog, catalogs=catalogs):
        with db_connection(catalog_id) as conn:
            cat_total = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
            cat_incomplete = conn.execute(
                "SELECT COUNT(*) FROM books WHERE "
                "author IS NULL OR author = '' OR "
                "year IS NULL OR "
                "language IS NULL OR language = '' OR "
                "genre IS NULL OR genre = ''"
            ).fetchone()[0]
            cat_no_body = conn.execute(
                "SELECT COUNT(*) FROM books WHERE body IS NULL OR body = ''"
            ).fetchone()[0]
        cat_path = get_db_path(catalog_id)
        cat_size = cat_path.stat().st_size if cat_path.exists() else 0
        catalog_stats.append({
            "id": catalog_id,
            "db_size_bytes": cat_size,
            "total_books": cat_total,
            "incomplete_records": cat_incomplete,
            "books_without_body": cat_no_body,
        })
        total += cat_total
        incomplete += cat_incomplete
        no_body += cat_no_body
        db_size += cat_size
    return {
        "total_books": total,
        "incomplete_records": incomplete,
        "books_without_body": no_body,
        "db_size_bytes": db_size,
        "catalogs": catalog_stats,
    }


def title_exists(title: str, catalog: Optional[str] = None, catalogs: Optional[list[str]] = None) -> bool:
    for catalog_id in _selected_catalog_ids(catalog=catalog, catalogs=catalogs):
        with db_connection(catalog_id) as conn:
            row = conn.execute(
                "SELECT 1 FROM books WHERE title = ? LIMIT 1",
                (title,),
            ).fetchone()
        if row is not None:
            return True
    return False
