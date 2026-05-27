# KoreDocs API Design

> Status: Active
> Date: 2026-05-01

---

## 1. Scope

This document covers the programmatic HTTP API exposed by KoreDocs for use by KoreAgent, KoreComms, and other suite services. It does not cover the MCP tool layer (see `koredocs_mcp.py`) or the browser UI (see `DESIGN_UI.md`).

Base URL: `http://127.0.0.1:<port>` (suite default port 8615 from top-level config; standalone fallback default is 5500).

All request and response bodies are JSON unless noted. Successful mutation responses always include `{"ok": true}` or the updated resource object.

---

## 2. Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/status` | Returns `{"status":"ok","service":"koredocs"}`. Used by KoreStack as the health probe. |

---

## 3. File Types

KoreDocs stores three document types in its KoreFile database. All file content is serialized as a string in the `content` field.

### 3.1 `.koredoc`

Plain Markdown text. Optional YAML frontmatter between `---` delimiters.

```
---
title: My Document
tags: notes, project
---

# My Document

Markdown content here.
```

### 3.2 `.koresheet`

JSON object serialized as a string. Sparse cell store — only non-default cells are present.

```json
{
  "version": 1,
  "meta": { "title": "My Sheet", "created": "2026-05-01T00:00:00Z" },
  "cols": 26,
  "rows": 100,
  "cells": {
    "A1": { "value": "Name" },
    "B1": { "value": "Score" },
    "A2": { "value": "Alice", "style": {} },
    "B2": { "value": 95 }
  }
}
```

Cell fields:
- `value` — string, number, or boolean
- `formula` — formula string beginning with `=`; `computed` holds last evaluated result
- `style` — optional style object

Cell addresses use A1 notation (`A1`, `B3`, `AA10`).

### 3.3 `.kodiag`

JSON object serialized as a string. Diagram with nodes and directed edges.

```json
{
  "koreDiag": "1.0",
  "id": "<uuid>",
  "title": "My Diagram",
  "created": "2026-05-01T00:00:00Z",
  "modified": "2026-05-01T00:00:00Z",
  "settings": {},
  "nodes": [
    { "id": "n1", "type": "rect", "label": "Start", "x": 100, "y": 100, "width": 120, "height": 40 },
    { "id": "n2", "type": "ellipse", "label": "End",   "x": 300, "y": 100, "width": 120, "height": 40 }
  ],
  "edges": [
    { "id": "e1", "from": "n1", "to": "n2", "fromPort": "e", "toPort": "w", "label": "" }
  ]
}
```

Node types: `rect`, `ellipse`, `waypoint`.  
Edge ports: compass directions `n`, `e`, `s`, `w`.

---

## 4. Format Discovery

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/schema` | Return format info for all supported types |
| `GET` | `/api/schema?type=<type>` | Return format info for one type (`koredoc`, `koresheet`, `kodiag`) |

Response fields: `type`, `extension`, `content_type`, `notes`, `schema`, `example`.

---

## 5. KoreFile — Folders

The folder system is a virtual tree backed by a SQLite database. The root folder always exists with `id=1` and `path="/"`.

### Common folder fields

| Field | Type | Description |
|---|---|---|
| `id` | int | Stable numeric identifier |
| `name` | string | Folder name segment |
| `parent_id` | int\|null | Parent folder id; null for root |
| `path` | string | Full path e.g. `/Projects/Q1` |
| `revision` | int | Incremented on every mutation |
| `created_at` | string | ISO 8601 UTC |
| `modified_at` | string | ISO 8601 UTC |

### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/folders` | List all folders, flat, ordered by path |
| `POST` | `/api/folders` | Create a folder |
| `PATCH` | `/api/folders/{folder_id}` | Rename and/or move a folder |
| `DELETE` | `/api/folders/{folder_id}` | Delete a folder; pass `recursive=true` to remove nested files and sub-folders |

**POST /api/folders**

```json
{ "name": "Q1 2026", "parent_id": 2 }
```

`parent_id` defaults to `1` (root).

**PATCH /api/folders/{folder_id}**

```json
{ "name": "Q1 2026 Final", "parent_id": 3, "expected_revision": 4 }
```

All fields optional. `expected_revision` enables optimistic concurrency — returns `409` if the folder has been modified.

**DELETE /api/folders/{folder_id}**

Query param `?expected_revision=<n>` optional.  
Query param `?recursive=true` deletes all nested files and sub-folders after confirmation.
Returns `409` if the folder is not empty and `recursive` is not enabled.

---

## 6. KoreFile — Files

### Common file fields

| Field | Type | Description |
|---|---|---|
| `id` | int | Stable numeric identifier |
| `name` | string | Filename including extension (e.g. `notes.koredoc`) |
| `ext` | string | Extension without dot (`koredoc`, `koresheet`, `kodiag`) |
| `folder_id` | int | Containing folder id |
| `folder_path` | string | Containing folder path |
| `content` | string | Full serialized file content (only present when explicitly requested) |
| `metadata` | object\|null | Arbitrary JSON metadata |
| `revision` | int | Incremented on every mutation |
| `created_at` | string | ISO 8601 UTC |
| `modified_at` | string | ISO 8601 UTC |

### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/files` | List files (metadata only, no content) |
| `GET` | `/api/files/{file_id}` | Get a file, optionally with content |
| `POST` | `/api/files` | Create a file |
| `PUT` | `/api/files/{file_id}` | Update a file's content and/or metadata |
| `PATCH` | `/api/files/{file_id}` | Rename and/or move a file |
| `DELETE` | `/api/files/{file_id}` | Delete a file |

**GET /api/files** query params:

| Param | Type | Description |
|---|---|---|
| `folder_id` | int | Filter by folder id |
| `folder_path` | string | Filter by folder path (e.g. `/Projects`) |
| `type` | string | Filter by extension (`koredoc`, `koresheet`, `kodiag`) |
| `name` | string | Filter by exact filename |
| `limit` | int 1–500 | Maximum results |

**GET /api/files/{file_id}** query params:

| Param | Default | Description |
|---|---|---|
| `include_content` | `true` | Set to `false` to omit the `content` field |

**POST /api/files**

```json
{
  "folder_id": 3,
  "name": "report.koredoc",
  "content": "# Report\n\nContent here.",
  "metadata": {}
}
```

Returns `201` with the created file record. Returns `409` if a file with the same name already exists in the folder.

**PUT /api/files/{file_id}**

```json
{
  "content": "# Report\n\nUpdated content.",
  "metadata": null,
  "expected_revision": 2
}
```

All fields optional. `expected_revision` enables optimistic concurrency.

**PATCH /api/files/{file_id}**

```json
{ "name": "report-final.koredoc", "folder_id": 5, "expected_revision": 3 }
```

Provide `name`, `folder_id`, or both.

**DELETE /api/files/{file_id}**

Query param `?expected_revision=<n>` optional.

---

## 7. KoreFile — Search

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/search` | Full-text search across all KoreFile documents |

Query params:

| Param | Required | Description |
|---|---|---|
| `q` | yes | Search query; supports words and quoted phrases |
| `type` | no | Restrict to `koredoc`, `koresheet`, or `kodiag` |
| `folder_path` | no | Restrict to a folder path |
| `limit` | no | Maximum results (1–200, default 20) |

Returns a list of file metadata records with a `snippet` field.

---

## 8. KoreSheet — Spreadsheet Operations

These routes operate on `.koresheet` files already stored in KoreFile. All require a `file_id` from the file listing.

### Concurrency

All mutating sheet endpoints accept `expected_revision` in the request body. When provided and the sheet has been updated since that revision, the server returns `409`. Omit it to perform an unconditional write.

### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/sheets/{file_id}` | Get sheet metadata and optionally all cells |
| `GET` | `/api/sheets/{file_id}/range` | Read an A1-style range |
| `GET` | `/api/sheets/{file_id}/table` | Read a range as header-keyed row objects |
| `POST` | `/api/sheets/{file_id}/cells` | Write sparse cell updates by A1 address |
| `POST` | `/api/sheets/{file_id}/rows/append` | Append rows below existing data |
| `POST` | `/api/sheets/{file_id}/rows/upsert` | Update or append rows matched by key columns |
| `POST` | `/api/sheets/{file_id}/range/clear` | Clear all cells in an A1 range |

---

**GET /api/sheets/{file_id}**

Query param `?include_cells=true` to include the full sparse cell map.

Returns: `{ id, name, revision, cols, rows, title, cells? }`

---

**GET /api/sheets/{file_id}/range**

Query params:

| Param | Required | Description |
|---|---|---|
| `range` | yes | A1-style range e.g. `A1:D10`, `A1:A`, `B2` |
| `values_only` | no | When `true`, return bare values instead of cell objects |

Returns: `{ range, rows: [[cell, ...], ...] }` where each cell is `{ value, formula?, computed?, style? }` or a bare value if `values_only=true`.

---

**GET /api/sheets/{file_id}/table**

Query params:

| Param | Default | Description |
|---|---|---|
| `header_row` | `1` | Row number containing column headers |
| `range` | entire sheet | Optional A1 range to constrain the read |

Returns: `{ header_row, headers: [...], rows: [{col: value, ...}, ...] }` — rows as dicts keyed by column header.

---

**POST /api/sheets/{file_id}/cells**

```json
{
  "cells": {
    "A1": { "value": "Name" },
    "B1": { "value": "Score" },
    "B2": { "formula": "=A2*2" }
  },
  "expected_revision": 1
}
```

Merges the provided cells into the existing sparse store. To clear a cell, set its value to `null`.

---

**POST /api/sheets/{file_id}/rows/append**

```json
{
  "rows": [["Alice", 95], ["Bob", 88]],
  "start_col": "A",
  "header_row": 1,
  "expected_revision": 2
}
```

`rows` is a list of row arrays. Rows are appended below the last occupied row in the sheet (or below the header row if the sheet is otherwise empty).

---

**POST /api/sheets/{file_id}/rows/upsert**

```json
{
  "rows": [
    { "Name": "Alice", "Score": 97 },
    { "Name": "Carol", "Score": 91 }
  ],
  "key_columns": ["Name"],
  "header_row": 1,
  "create_missing_columns": false,
  "expected_revision": 3
}
```

`rows` is a list of dicts keyed by column header. Each row is matched against existing rows by `key_columns`. Matched rows are updated in place; unmatched rows are appended.

---

**POST /api/sheets/{file_id}/range/clear**

```json
{ "range": "B2:D10", "expected_revision": 4 }
```

Sets all cells in the given range to empty (removes them from the sparse store).

---

## 9. Legacy Flat-FS API

These routes predate KoreFile and operate on raw files in the `KOREDOCS_DATA_DIR` flat directory. Prefer the `/api/` routes for new work.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/legacy/files` | List flat-FS files; optional `?type=koredoc` filter |
| `GET` | `/api/legacy/files/{name}` | Read raw file content (plain text) |
| `PUT` | `/api/legacy/files/{name}` | Overwrite or create a file |
| `POST` | `/api/legacy/files` | Create a new file (409 if exists) |
| `DELETE` | `/api/legacy/files/{name}` | Delete a file |
| `POST` | `/api/import-fs` | Import all flat-FS files into the KoreFile DB |

---

## 10. Error Responses

| Status | Meaning |
|---|---|
| `400` | Invalid request — bad filename, unsupported type, missing required field |
| `404` | Resource not found |
| `409` | Conflict — file/folder already exists, or `expected_revision` mismatch |

All error bodies: `{ "detail": "<message>" }`.
