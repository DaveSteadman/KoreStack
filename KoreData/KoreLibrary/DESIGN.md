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

## Catalogs (Multi-Database Support)

KoreLibrary organises its content into **catalogs** — separate SQLite databases that all share the same schema. This allows different content collections to be kept isolated, segmented by purpose, access pattern, or provenance, without breaking search across them.

### Catalog types

| Type | Location | Writable | Notes |
|------|----------|----------|---------|
| **Default (local)** | `{data_dir}/library.db` | Yes | The primary catalog; always present |
| **User-added** | `{data_dir}/catalogs/{id}.db` | Yes | Created automatically on first write |

`{data_dir}` is the resolved `korelibrary.data_dir` config value (default: `datacontrol/koredata/Library`).

The service looks for additional catalogs in exactly one folder: **`{data_dir}/catalogs/`**. Any `.db` file present there at startup is registered as a catalog. There is no recursive scan and no other location is checked.

### Creating a catalog

There is no separate "create catalog" step. A catalog is created automatically the first time a book is written to it:

```
POST /books
{ "catalog": "dickens", "title": "A Tale of Two Cities", "author": "Charles Dickens", ... }
```

If `{data_dir}/catalogs/dickens.db` does not yet exist, the service creates it, applies the schema, and inserts the book. Subsequent writes to `catalog: "dickens"` open the same file. The new catalog appears in `GET /catalogs` immediately.

To create an empty catalog before adding any books is not directly supported via the API — simply add the first book and the catalog comes into existence.

### Catalog IDs

A catalog ID is the filename stem of its `.db` file (e.g. `ancient`, `shakespeare`, `dickens`). IDs must match `[A-Za-z0-9_-]+`. The default catalog ID defaults to `local` and is configurable via `korelibrary.default_catalog`.

### Book references

Books can be addressed globally using the form `{catalog}:{local_id}`, e.g. `shakespeare:42`, `ancient:7`. All API responses include a `catalog` field and a `ref` field (`catalog:id`) so callers can address books unambiguously across catalogs.

### Search across catalogs

Search (`GET /search`) queries **all enabled catalogs** by default and merges results ranked by BM25 score. Callers can narrow the scope using the `catalog` (single) or `catalogs` (comma-separated list) query parameters. The search implementation opens each catalog database independently, executes the FTS query, and merges before applying pagination.

### Write semantics

Write operations (`POST /books`, `PATCH /books/{id}`, `DELETE /books/{id}`) target the specified catalog. If no catalog is specified, the default catalog is used.

### Use cases

- **By author**: a `dickens` catalog for Dickens novels, `shakespeare` for plays and sonnets.
- **By era**: an `ancient` catalog for classical texts (Homer, Virgil, Plato), a `victorian` catalog for 19th-century works.
- **By genre**: a `poetry` or `drama` catalog segmented from prose collections.
- **Catch-all default**: `local` receives anything that doesn't fit a named catalog.

---

## Data Model

Each catalog is an independent SQLite database. Every catalog uses the same `books` table schema.

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
- API responses include `catalog` and `route_id` so callers can address books uniquely across catalogs (`ancient:7`, `shakespeare:42`, etc.).

---

## API

KoreLibrary exposes a REST API (FastAPI). There is **no local web UI** — all user interaction goes through KoreDataGateway.

### Books

| Method   | Path               | Description                              |
|----------|--------------------|------------------------------------------|
| `GET`    | `/catalogs`        | List available catalogs and their capabilities |
| `GET`    | `/books`           | List books (metadata only, no body), optionally scoped by `catalog` |
| `GET`    | `/books/{id}`      | Get a single book (metadata + body). `id` may be catalog-aware (`local:42`) |
| `POST`   | `/books`           | Add a new book. Body field `catalog` selects the target catalog (defaults to `local`) |
| `PATCH`  | `/books/{id}`      | Update metadata or body (for corrections) |
| `DELETE` | `/books/{id}`      | Remove a book permanently from its catalog |
| `POST`   | `/books/{id}/move` | Move a book to a different catalog |

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

### Importing to a specific catalog

When calling `POST /books` (or triggering a Kiwix import), include a `catalog` field in the request body to direct the book into a named catalog. If `catalog` is omitted the default catalog (`local`) is used.

Example: `POST /books` with body `{ "catalog": "dickens", "title": "...", "body": "..." }` creates the book in the `dickens` catalog, which must already exist as `{data_dir}/catalogs/dickens.db`.

### Deleting a book

`DELETE /books/{id}` permanently removes the book and its FTS index entry from the catalog. The `id` must be catalog-aware if the book is not in the default catalog (e.g. `shakespeare:42`). There is no soft-delete; the operation is immediate and irreversible.

### Moving a book between catalogs

`POST /books/{id}/move` copies the book record (all metadata and body) into the destination catalog, inserts it into that catalog's FTS index, then deletes the original. The request body must include `{ "catalog": "destination_id" }`. The response returns the new catalog-aware `ref` (e.g. `dickens:99`). The move is not atomic across two SQLite files — if the delete step fails after a successful insert, both copies will exist; the duplicate can be removed with a subsequent `DELETE`.

### Manual correction

Books can be edited after import to correct:
- Missing or wrong metadata (use `PATCH /books/{id}`).
- Formatting errors introduced during scanning / OCR.
- Typos in body text.

`updated_at` is set on every edit.

---

## Configuration

Behaviour is controlled by a JSON config file (`config/default.json`). No command-line flags.

| Key        | Default     | Description                  |
|------------|-------------|------------------------------|
| `port`     | `8802`      | HTTP port (standalone default; suite mode derives from gateway base port) |
| `host`     | `0.0.0.0`   | Bind address                 |
| `data_dir` | `data`      | Directory for `library.db`   |
| `log_level`| `info`      | Uvicorn log level            |

---

## Application

- Console application, run with `python main.py`.
- Single worker, single SQLite file — no concurrency concerns at this scale.
- Follows the same versioning scheme as KoreFeed: `[NNNN / X.Y+dev]`.
