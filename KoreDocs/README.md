# KoreDocs

A minimal, locally-hosted document and data editing suite. Zero dependencies,
no build step, no bundler — plain HTML + CSS + vanilla ES-module JavaScript
served by a single Python FastAPI back end.

| App | URL | File ext | Status |
|---|---|---|---|
| **KoreDoc** | `/doc` | `.koredoc` | Phase 1 — ProseMirror WYSIWYG markdown editor |
| **KoreSheet** | `/sheet` | `.koresheet` | Phase 1 — grid spreadsheet with formulas |
| **KoreDiag** | `/diag` | `.korediag` | Phase 1 — canvas node/edge diagramming |

See [design.md](design.md) for the full architecture and design decisions.

---

## Quick start

KoreDocs now consumes shared shell assets from `../UIElements/assets`. You can override that location with `KORE_UIELEMENTS_ASSETS_DIR`. If the shared asset bundle is absent, KoreDocs falls back to its local legacy shell assets.

### 1. Install dependencies

```
pip install -r requirements.txt
```

### 2. Configure

KoreDocs is expected to run against the suite-level shared storage layout.

Important paths:

```
# Shared suite root override (optional)
KORE_SUITE_ROOT=C:\Util\Data\GitRepos\KoreStack

# Shared user-data root used by FileAccess and KoreDocs (optional override)
KORE_SUITE_DATAUSER=C:\Util\Dropbox\Misc\KoreStackData\datauser

# KoreDocs can still override its root directly, but this should normally point at the same shared datauser tree.
KOREDOCS_DATA_DIR=C:\Util\Dropbox\Misc\KoreStackData\datauser
```

Legacy `korefile.db` is no longer a live storage backend. If present under `datacontrol/koredocs/`, it is treated only as a one-time migration source into the real filesystem.

### 3. Start

One command starts everything — the browser UI **and** the MCP server run in the same
process:

```
python .\main.py
```

Open the UI at **http://localhost:8615** (redirects to `/ui`).

The MCP server is available immediately on the same port:
- **Streamable HTTP** — `http://localhost:8615/mcp`

For local agent use (Claude Desktop, GitHub Copilot), use **stdio transport** instead.
The web UI still starts in a background thread on port 8615:

```
python .\main.py --mcp-stdio
```

Add to your agent's MCP config (e.g. `mcp_servers.json`):

```json
{
  "koredocs": {
    "command": "python",
    "args": ["C:/Util/GithubRepos/KoreStack/KoreDocs/main.py", "--mcp-stdio"]
  }
}
```

> **Note:** `uvicorn server:app --reload` also works for development (hot-reload), but
> does not support the `--mcp-stdio` flag. Use it when you only need the web UI.

KoreDocs now consumes the shared shell assets from `/ui-elements/assets/`; legacy `/static/commonui/*` paths are no longer part of the supported UI contract.

### Storage architecture note

- The source of truth is the real filesystem rooted at the shared `datauser/` directory.
- FileAccess is the canonical generic mechanism for navigating, reading, and writing that tree.
- KoreDocs lives on top of the same files and folders. Its browser, editors, and MCP tools add document-aware and sheet-aware behavior, but they do not represent a separate storage backend.
- Legacy `korefile.db` is migration-only. After startup migration, the live system reads and writes real files.

---

## KoreFile — Filesystem-backed document index

`app/korefile.py` preserves the older file/folder API shape used by the browser and MCP layers, but it is now a filesystem-backed adapter over the shared `datauser/` tree.

That means:

- folder ids and file ids are stable derived ids built from filesystem-relative paths
- file metadata, revision numbers, and search results are computed from real files on disk
- generic navigation semantics come from the shared `datauser` path rules used by FileAccess
- KoreDocs-specific APIs exist to add typed editing and rich metadata on top of those files

KoreDocs accepts compatibility prefixes like `KoreDocs/...` for folder-path inputs, but these resolve into the same shared `datauser/` tree rather than a separate subtree.

### KoreFile REST API

```
GET    /api/folders                     list folder tree
POST   /api/folders                     create folder  { name, parent_id }
DELETE /api/folders/{id}                delete (must be empty)

GET    /api/files?folder_id=&type=      list files (metadata only)
GET    /api/files/{id}                  get file with content
POST   /api/files                       create  { folder_id, name, content }
PUT    /api/files/{id}                  save content
DELETE /api/files/{id}                  delete

GET    /api/sheets/{id}                 sheet metadata / optional sparse cells
GET    /api/sheets/{id}/range           read A1-style range (?range=A1:C10&values_only=1)
GET    /api/sheets/{id}/table           read header-keyed rows (?header_row=1&range=A1:C10)
POST   /api/sheets/{id}/cells           sparse cell writes { cells }
POST   /api/sheets/{id}/rows/append     append list-style or header-mapped rows
POST   /api/sheets/{id}/rows/upsert     update-or-append rows by key columns
POST   /api/sheets/{id}/range/clear     clear a range { range }

GET    /api/search?q=&type=&folder_id=  FTS5 full-text search
POST   /api/import-fs                   import from flat data directory
GET    /api/schema?type=                   file type schema / examples
```

The `/api/legacy/files/*` and `/api/textedit/*` routes also operate on the same shared filesystem root. They are compatibility/raw-edit surfaces over `datauser`, not a second storage backend.

---

## MCP Tools

The MCP server exposes typed tools that operate on the shared `datauser` tree through the KoreDocs layer. The canonical public names use the `koredocs_` prefix with a `service_object_verb` pattern.

| Tool | Description |
|---|---|
| `koredocs_files_search` | Full-text search. Args: `query`, `type?`, `folder_path?`, `limit?` |
| `koredocs_file_get` | Retrieve a file by id — returns metadata + full content including `revision` |
| `koredocs_files_list` | List files in a folder (metadata only). Args: `folder_path?`, `type?` |
| `koredocs_folders_list` | Return the full folder tree |
| `koredocs_folder_structure_get` | Navigation starting point: return the folder tree with document summaries |
| `koredocs_folder_create` | Create a KoreFile folder so sheet/document workflows stay inside KoreDocs |
| `koredocs_types_list` | Return every supported KoreDocs file type with schema summary, notes, and example content |
| `koredocs_file_format_get` | Return canonical schema, example content, and notes for a file type |
| `koredocs_file_create` | Create a new file. Args: `folder_path`, `name`, `content` |
| `koredocs_doc_create` | Create a `.koredoc` from Markdown |
| `koredocs_sheet_create` | Create a `.koresheet` from a sparse cell map |
| `koredocs_sheet_table_create` | Create a `.koresheet` from headers plus initial rows |
| `koredocs_sheet_compounding_schedule_create` | Create a labelled compounding model with formulas and yearly rows |
| `koredocs_diag_create` | Create a `.korediag` from a diagram object, filling safe defaults |
| `koredocs_doc_outline_get` | Return the heading outline for a `.koredoc` file |
| `koredocs_doc_section_read` | Read a full document, one heading section, or an explicit line range |
| `koredocs_doc_section_replace` | Replace one heading section using optimistic concurrency via `expected_revision?` |
| `koredocs_doc_section_insert` | Insert a new markdown block relative to a section anchor |
| `koredocs_doc_markdown_append` | Append markdown to the end of a document |
| `koredocs_sheet_get` | Return sheet metadata, dimensions, used range, and optionally the sparse cell map |
| `koredocs_sheet_describe` | Return a structural summary with guessed headers, sample rows, labels, and formulas |
| `koredocs_sheet_headers_get` | Return detected headers, guessing the header row when omitted |
| `koredocs_sheet_column_find` | Locate a sheet column by header name |
| `koredocs_sheet_preview` | Return a compact table preview without dumping the entire sparse cell map |
| `koredocs_sheet_range_read` | Read `A1`-style ranges such as `A1:C10`, `A:A`, or `2:4` |
| `koredocs_sheet_table_read` | Read a range as header-keyed row objects |
| `koredocs_sheet_rows_find` | Find table rows using header-keyed filters such as exact match, contains, or numeric comparisons |
| `koredocs_sheet_rows_update` | Update all matched table rows using header-keyed values |
| `koredocs_sheet_headers_set` | Write or replace a contiguous header row |
| `koredocs_sheet_table_rows_append` | Append object-style or list-style rows using table semantics |
| `koredocs_sheet_labels_find` | Find label cells and return likely adjacent values for navigation |
| `koredocs_sheet_named_value_get` | Read a value next to a label such as `Annual Rate` or `Starting Balance` |
| `koredocs_sheet_named_value_set` | Write a value next to a label, with revision checks when needed |
| `koredocs_sheet_cells_write` | Apply sparse `A1`-addressed cell updates without rewriting the whole file |
| `koredocs_sheet_rows_append` | Append list-based or header-mapped rows to an existing sheet |
| `koredocs_sheet_rows_upsert` | Update matching rows by key columns or append when no match exists |
| `koredocs_sheet_range_clear` | Clear all cells in a range |
| `koredocs_file_update` | Overwrite content. Supports `expected_revision?` |
| `koredocs_file_delete` | Delete a file. Supports `expected_revision?` |

Every mutating tool that changes an existing file accepts an optional `expected_revision` argument. Use it to avoid silent overwrite when the document was changed by the UI or another agent between read and write.

For agent workflows, prefer the semantic sheet tools first:

- Use `docs_sheet_describe`, `docs_sheet_headers_get`, `docs_sheet_preview`, and `docs_sheet_column_find` to understand sheet structure.
- Use `docs_sheet_rows_find`, `docs_sheet_rows_update`, `docs_sheet_table_rows_append`, and `docs_sheet_headers_set` for table-shaped data.
- Use `docs_sheet_labels_find`, `docs_sheet_named_value_get`, and `docs_sheet_named_value_set` for model-style sheets that store inputs and outputs as labels plus adjacent values.
- Drop to `docs_sheet_range_read` or `docs_sheet_cells_write` only when a task genuinely requires raw cell addressing.

---

## File formats

| Format | Description |
|---|---|
| `.koredoc` | Plain markdown with optional YAML frontmatter. Always valid markdown. |
| `.koresheet` | JSON sparse cell map, per-cell style, aggregate formulas (`SUM`, `AVERAGE`, `COUNT`, `MIN`, `MAX`). |
| `.korediag` | JSON node/edge graph with hierarchy, port-anchored edges. |

All formats are human-readable and LLM-friendly by design. They are identical whether
stored on the flat file system or in KoreFile.

For programmatic discovery, use:

- REST: `GET /api/schema` or `GET /api/schema?type=koredoc`
- MCP: `list_supported_types()` or `get_file_format_info(type)`

---

## Keyboard shortcuts

### KoreDoc

| Shortcut | Action |
|---|---|
| `Ctrl+B` | Bold |
| `Ctrl+I` | Italic |

### KoreSheet

| Shortcut | Action |
|---|---|
| `Arrows` | Move selection |
| `Shift+Arrow` | Extend range |
| `Click-drag` | Select range |
| `F2` / double-click | Enter edit mode |
| `Delete` | Clear cell |
| `Enter` | Commit and move down |
| `Tab` | Commit and move right |

### KoreDiag

| Shortcut | Action |
|---|---|
| `V` | Select / Move tool |
| `R` | Rectangle tool |
| `E` | Ellipse tool |
| `C` | Connect tool |
| `W` | Waypoint tool |
| `H` / Space+drag | Pan |
| Scroll wheel | Zoom |
| Middle-mouse drag | Pan |
| Double-click | Edit label |
| `Ctrl+Z` / `Ctrl+Y` | Undo / Redo |
| `Ctrl+Shift+E` | Export PNG |
