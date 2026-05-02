# KoreLibrary

Requirements and top-level design document for KoreLibrary, a long-form text storage and retrieval service.

---

## Purpose

Store, manage, and search a collection of ebooks and other long-form text documents for presentation to LLM agents and the KoreDataGateway.

Content is:
- **Static** — not time-sensitive or source-dependent.
- **Standalone** — no inter-document links are required (unlike KoreReference).
- **Long-form** — full book or document text, not excerpts.

---

## Content Sources

- **Primary**: Import from a local [Kiwix](https://www.kiwix.org/) server, which hosts Project Gutenberg ZIM content.
- **Secondary**: Manual additions via REST API (e.g. POST of text/metadata).
- Future sources may be added without breaking the core schema.

---

## Data Model

KoreLibrary keeps a writable default catalog in `library.db` and may expose additional catalogs from `datauser/catalogs/*.db` or bundled read-only `catalogs/*.db` files. Each catalog uses the same `books` table schema.

| Column       | Type    | Notes                                                      |
|--------------|---------|------------------------------------------------------------|
| `id`         | INTEGER | Primary key, auto-increment                                |
| `title`      | TEXT    | Book or document title                                     |
| `author`     | TEXT    | Author name(s), comma-separated if multiple                |
| `year`       | INTEGER | Publication year                                           |
| `language`   | TEXT    | e.g. `en`, `fr`                                            |
| `genre`      | TEXT    | Subject or genre classification                            |
| `notes`      | TEXT    | Free-form description, editorial notes                     |
| `source`     | TEXT    | Origin (e.g. `kiwix`, `manual`, source URL)                |
| `source_id`  | TEXT    | Unique ID at the source (e.g. Gutenberg ID); used to deduplicate imports |
| `word_count` | INTEGER | Word count of `body`; computed at import time              |
| `body`       | TEXT    | Full text of the book or document; page markers `{n}` stripped at import |
| `added_at`   | TEXT    | UTC timestamp of when the record was inserted              |
| `updated_at` | TEXT    | UTC timestamp of last edit                                 |

- FTS5 virtual table indexes `title`, `author`, and `body` for full-text search with BM25 ranking.
- Metadata search (title, author, year, language, genre) is supported via standard SQL queries.
- API responses include `catalog` and `route_id` so callers can address books uniquely across catalogs (`local:42`, `gutenberg:15`, etc.).

---

## API

KoreLibrary exposes a REST API (FastAPI). There is **no local web UI** — all user interaction goes through KoreDataGateway.

### Books

| Method   | Path               | Description                              |
|----------|--------------------|------------------------------------------|
| `GET`    | `/catalogs`        | List available catalogs and their capabilities |
| `GET`    | `/books`           | List books (metadata only, no body), optionally scoped by `catalog` |
| `GET`    | `/books/{id}`      | Get a single book (metadata + body). `id` may be catalog-aware (`local:42`) |
| `POST`   | `/books`           | Add a new book to the specified writable catalog |
| `PATCH`  | `/books/{id}`      | Update metadata or body (for corrections) |
| `DELETE` | `/books/{id}`      | Remove a book from a writable catalog |

### Search

| Method | Path      | Description                                            |
|--------|-----------|--------------------------------------------------------|
| `GET`  | `/search` | Search by query string (FTS) and/or metadata filters  |

Query parameters for `/search`:
- `q` — full-text query (searches title, author, body)
- `author`, `title`, `year`, `language`, `genre` — metadata filters
- `catalog` or `catalogs` — scope search to one or more catalogs
- `limit`, `offset` — pagination

### Incomplete Records

| Method  | Path                    | Description                                          |
|---------|-------------------------|------------------------------------------------------|
| `GET`   | `/incomplete`           | List books with one or more missing metadata fields  |
| `PATCH` | `/books/{id}`           | Fill in missing fields (same endpoint as corrections)|

`GET /incomplete` returns books where any of the following are NULL or empty: `author`, `year`, `language`, `genre`. Response is metadata only (no body). Optional query parameter `fields` to filter by specific missing field(s), e.g. `?fields=author,year`.

This is the primary tool for post-import manual review.

### Admin

| Method | Path      | Description             |
|--------|-----------|-------------------------|
| `GET`  | `/status` | Server status and stats |

---

## Content Management

### Import behaviour

During import, a book is always created if `title` and `body` can be extracted. Missing metadata fields are left NULL rather than blocking the import. The book will then appear in `GET /incomplete` for manual follow-up.

Fields that may commonly be absent after automated import:
- `author` — not always parseable from page content; may need manual entry.
- `year` — sometimes only in preface prose, not in a structured field.
- `genre` — never auto-populated; always requires manual classification.
- `language` — usually available from Kiwix OPDS metadata; rarely missing.

### Manual correction

Books can be edited after import to correct:
- Missing or wrong metadata (use `PATCH /books/{id}`).
- Formatting errors introduced during scanning / OCR.
- Typos in body text.

`updated_at` is set on every edit.

Bundled catalogs are treated as read-only. Write operations against them return an error instead of mutating shipped content.

---

## Configuration

Behaviour is controlled by a JSON config file (`config/default.json`). No command-line flags.

| Key        | Default     | Description                  |
|------------|-------------|------------------------------|
| `port`     | `8100`      | HTTP port                    |
| `host`     | `0.0.0.0`   | Bind address                 |
| `data_dir` | `data`      | Directory for `library.db`   |
| `log_level`| `info`      | Uvicorn log level            |

---

## Application

- Console application, run with `python main.py`.
- Single worker, single SQLite file — no concurrency concerns at this scale.
- Follows the same versioning scheme as KoreFeed: `[NNNN / X.Y+dev]`.
