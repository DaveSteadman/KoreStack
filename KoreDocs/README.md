# KoreDocs

A minimal, locally-hosted document and data editing suite. Zero dependencies,
no build step, no bundler — plain HTML + CSS + vanilla ES-module JavaScript
served by a single Python FastAPI back end.

| App | URL | File ext | Status |
|---|---|---|---|
| **KoreDoc** | `/doc` | `.koredoc` | Phase 1 — ProseMirror WYSIWYG markdown editor |
| **KoreSheet** | `/sheet` | `.koresheet` | Phase 1 — grid spreadsheet with formulas |
| **KoreDiag** | `/diag` | `.kodiag` | Phase 1 — canvas node/edge diagramming |

See [design.md](design.md) for the full architecture and design decisions.

---

## Quick start

KoreDocs now consumes shared shell assets from `../UIElements/assets`. You can override that location with `KORE_UIELEMENTS_ASSETS_DIR`. If the shared asset bundle is absent, KoreDocs falls back to its local legacy shell assets.

### 1. Install dependencies

```
pip install -r requirements.txt
```

### 2. Configure

Copy `.env.example` to `.env` and edit as needed:

```
# Directory scanned for .koredoc / .koresheet / .kodiag files (flat FS storage)
KOREDOCS_DATA_DIR=C:\Util\Data\KoreFiles

# Path to the KoreFile SQLite database (Phase 2 virtual file system)
KOREDOCS_DB_PATH=C:\Util\Data\KoreFiles\korefile.db
```

Both settings have sensible defaults (`./data` and `./data/korefile.db`) if you skip
this step.

### 3. Start

One command starts everything — the browser UI **and** the MCP server run in the same
process:

```
python .\main.py
```

Open the UI at **http://localhost:5500** (redirects to `/kf`).

The MCP server is available immediately on the same port:
- **SSE transport** — `GET http://localhost:5500/mcp/sse`
- **HTTP transport** — `POST http://localhost:5500/mcp/messages`

For local agent use (Claude Desktop, GitHub Copilot), use **stdio transport** instead.
The web UI still starts in a background thread on port 5500:

```
python .\main.py --mcp-stdio
```

Add to your agent's MCP config (e.g. `mcp_servers.json`):

```json
{
  "koredocs": {
    "command": "python",
    "args": ["C:/Util/GithubRepos/KoreDocs/main.py", "--mcp-stdio"]
  }
}
```

> **Note:** `uvicorn server:app --reload` also works for development (hot-reload), but
> does not support the `--mcp-stdio` flag. Use it when you only need the web UI.

**SSE message endpoint:**

```
POST http://localhost:5500/mcp/messages
```

### Storage architecture note

- File creation, opening, renaming, moving, and deletion are handled in **KoreFiles** (`/kf`).
- KoreDoc, KoreSheet, and KoreDiag are editor surfaces for files that already exist in KoreFile.
- When a file is opened from KoreFiles, edits are autosaved back to the KoreFile DB with a 1 second debounce. Navigation/page-hide triggers an immediate keepalive flush of any pending save.
- The MCP server operates on **KoreFile**, so agent-visible files and user-edited files now share the same storage backend.

---

## KoreFile — Virtual File System

KoreFile is a SQLite database that provides a **virtual folder hierarchy** for all
KoreDocs documents. It is the primary storage backend for Phase 2 and the only storage
backend the MCP server operates on.

### Why KoreFile?

The flat file system (`KOREDOCS_DATA_DIR`) works for Phase 1 but has no search, no
organisation, and no agent access. KoreFile adds:

- **Folder hierarchy** — organise files into a tree of named folders (independent of the
  OS file system)
- **Full-text search** — SQLite FTS5 index across all document content, titles, tags, and
  metadata; supports phrase and keyword queries
- **MCP access** — agents read, write, and search documents through the KoreFile API

### Folder hierarchy

Folders use an adjacency-list model with a materialised path string
(`/Root/Projects/KoreDocs`). The root folder has no parent. Folders cannot be deleted
while they contain files or sub-folders.

### Database schema (summary)

```sql
folders (id, parent_id, name, path, created_at)
files   (id, folder_id, name, ext, content BLOB, metadata JSON,
         word_count, created_at, modified_at)
files_fts  -- FTS5 virtual table: name + metadata + content
```

Content blobs are zlib-compressed. The FTS index is a contentless FTS5 table kept in
sync on every write, following the same pattern as KoreData/KoreRAG.

## Dual storage — flat FS and KoreFile DB

Both backends coexist. The front end tracks which one a file came from via a URL query
parameter:

| Source | URL | API prefix |
|---|---|---|
| Flat file system | `?src=fs&file=notes.koredoc` | `/api/files/` |
| KoreFile DB | `?id=42&file=notes.koredoc` | `/api/kf/files/` |

No source param → defaults to flat FS (Phase 1 compatibility).

The current recommended workflow is KoreFiles-first: create and open files from `/kf`, then edit them in the app-specific surface.

### Importing existing files

A one-shot endpoint walks `KOREDOCS_DATA_DIR` and imports every `*.kore*` file into the
DB, creating matching folders for the relative OS path:

```
POST /api/kf/import-fs
```

After import you can work entirely inside KoreFile and stop using the flat directory.

### KoreFile REST API

```
GET    /api/kf/folders                     list folder tree
POST   /api/kf/folders                     create folder  { name, parent_id }
DELETE /api/kf/folders/{id}                delete (must be empty)

GET    /api/kf/files?folder_id=&type=      list files (metadata only)
GET    /api/kf/files/{id}                  get file with content
POST   /api/kf/files                       create  { folder_id, name, content }
PUT    /api/kf/files/{id}                  save content
DELETE /api/kf/files/{id}                  delete

GET    /api/kf/sheets/{id}                 sheet metadata / optional sparse cells
GET    /api/kf/sheets/{id}/range           read A1-style range (?range=A1:C10&values_only=1)
GET    /api/kf/sheets/{id}/table           read header-keyed rows (?header_row=1&range=A1:C10)
POST   /api/kf/sheets/{id}/cells           sparse cell writes { cells }
POST   /api/kf/sheets/{id}/rows/append     append list-style or header-mapped rows
POST   /api/kf/sheets/{id}/rows/upsert     update-or-append rows by key columns
POST   /api/kf/sheets/{id}/range/clear     clear a range { range }

GET    /api/kf/search?q=&type=&folder_id=  FTS5 full-text search
POST   /api/kf/import-fs                   import from flat data directory
GET    /api/schema?type=                   file type schema / examples
```

---

## MCP Tools

The MCP server exposes tools that operate on KoreFile. The canonical public names use the `koredocs_` prefix.

| Tool | Description |
|---|---|
| `koredocs_search_files` | Full-text search. Args: `query`, `type?`, `folder_path?`, `limit?` |
| `koredocs_get_file` | Retrieve a file by id — returns metadata + full content including `revision` |
| `koredocs_list_files` | List files in a folder (metadata only). Args: `folder_path?`, `type?` |
| `koredocs_list_folders` | Return the full folder tree |
| `koredocs_get_folder_structure` | Navigation starting point: return the folder tree with document summaries |
| `koredocs_create_folder` | Create a KoreFile folder so sheet/document workflows stay inside KoreDocs |
| `koredocs_list_supported_types` | Return every supported KoreDocs file type with schema summary, notes, and example content |
| `koredocs_get_file_format_info` | Return canonical schema, example content, and notes for a file type |
| `koredocs_create_file` | Create a new file. Args: `folder_path`, `name`, `content` |
| `koredocs_create_koredoc` | Create a `.koredoc` from Markdown |
| `koredocs_create_koresheet` | Create a `.koresheet` from a sparse cell map |
| `koredocs_create_sheet_table` | Create a `.koresheet` from headers plus initial rows |
| `koredocs_create_compounding_schedule` | Create a labelled compounding model with formulas and yearly rows |
| `koredocs_create_kodiag` | Create a `.kodiag` from a diagram object, filling safe defaults |
| `koredocs_get_koredoc_outline` | Return the heading outline for a `.koredoc` file |
| `koredocs_read_koredoc_section` | Read a full document, one heading section, or an explicit line range |
| `koredocs_replace_koredoc_section` | Replace one heading section using optimistic concurrency via `expected_revision?` |
| `koredocs_insert_koredoc_section` | Insert a new markdown block relative to a section anchor |
| `koredocs_append_koredoc_markdown` | Append markdown to the end of a document |
| `koredocs_get_sheet` | Return sheet metadata, dimensions, used range, and optionally the sparse cell map |
| `koredocs_describe_sheet` | Return a structural summary with guessed headers, sample rows, labels, and formulas |
| `koredocs_get_sheet_headers` | Return detected headers, guessing the header row when omitted |
| `koredocs_find_sheet_column` | Locate a sheet column by header name |
| `koredocs_preview_sheet` | Return a compact table preview without dumping the entire sparse cell map |
| `koredocs_read_sheet_range` | Read `A1`-style ranges such as `A1:C10`, `A:A`, or `2:4` |
| `koredocs_read_sheet_table` | Read a range as header-keyed row objects |
| `koredocs_find_sheet_rows` | Find table rows using header-keyed filters such as exact match, contains, or numeric comparisons |
| `koredocs_update_sheet_rows` | Update all matched table rows using header-keyed values |
| `koredocs_set_sheet_headers` | Write or replace a contiguous header row |
| `koredocs_append_sheet_table_rows` | Append object-style or list-style rows using table semantics |
| `koredocs_find_labelled_cells` | Find label cells and return likely adjacent values for navigation |
| `koredocs_get_named_value` | Read a value next to a label such as `Annual Rate` or `Starting Balance` |
| `koredocs_set_named_value` | Write a value next to a label, with revision checks when needed |
| `koredocs_write_sheet_cells` | Apply sparse `A1`-addressed cell updates without rewriting the whole file |
| `koredocs_append_sheet_rows` | Append list-based or header-mapped rows to an existing sheet |
| `koredocs_upsert_sheet_rows` | Update matching rows by key columns or append when no match exists |
| `koredocs_clear_sheet_range` | Clear all cells in a range |
| `koredocs_update_file` | Overwrite content. Supports `expected_revision?` |
| `koredocs_delete_file` | Delete a file. Supports `expected_revision?` |

Every mutating tool that changes an existing file accepts an optional `expected_revision` argument. Use it to avoid silent overwrite when the document was changed by the UI or another agent between read and write.

For agent workflows, prefer the semantic sheet tools first:

- Use `koredocs_describe_sheet`, `koredocs_get_sheet_headers`, `koredocs_preview_sheet`, and `koredocs_find_sheet_column` to understand sheet structure.
- Use `koredocs_find_sheet_rows`, `koredocs_update_sheet_rows`, `koredocs_append_sheet_table_rows`, and `koredocs_set_sheet_headers` for table-shaped data.
- Use `koredocs_find_labelled_cells`, `koredocs_get_named_value`, and `koredocs_set_named_value` for model-style sheets that store inputs and outputs as labels plus adjacent values.
- Drop to `koredocs_read_sheet_range` or `koredocs_write_sheet_cells` only when a task genuinely requires raw cell addressing.

---

## File formats

| Format | Description |
|---|---|
| `.koredoc` | Plain markdown with optional YAML frontmatter. Always valid markdown. |
| `.koresheet` | JSON sparse cell map, per-cell style, aggregate formulas (`SUM`, `AVERAGE`, `COUNT`, `MIN`, `MAX`). |
| `.kodiag` | JSON node/edge graph with hierarchy, port-anchored edges. |

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
