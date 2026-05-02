# KoreDocs — Design Document

> **Status**: Draft — Phase 1 in progress
> **Date**: 2026-04-25

---

## 1. Vision

The purpose of KoreDocs is to provide a document hosting and editing environment to 
combine agent functionalty with user purpose and value.

KoreDocs is a minimal, locally-hosted document and data editing suite. Each editor is a
single-page, zero-dependency vanilla-JS front end served by a single Python FastAPI back
end. Files may be stored on the native file system or in a searchable SQLite database
(**KoreFile**). The suite exposes its content as an **MCP server** so that LLM agents can
search, retrieve, and edit documents directly.

The suite is designed to be:

- **Hackable** — no build step, no bundler, open files in any text editor
- **LLM-readable** — formats stay as close to plain text as possible; where structure is
  needed it stays markdown-compatible or is self-describing JSON
- **Composable** — editors share the same service and can reference each other's files
  (e.g. a `.kodiag` embedded in a `.koredoc`)
- **MCP-ready** — the KoreFile database is exposed as an MCP server so agents can search,
  retrieve, create, and edit documents without a browser

---

## 2. Suite Overview

| App | File ext | Status | One-liner |
|---|---|---|---|
| **KoreDoc** | `.koredoc` | Phase 1 | Source-styled markdown document editor |
| **KoreSheet** | `.koresheet` | Phase 1 | Grid spreadsheet with aggregate formulas |
| **KoreDiag** | `.kodiag` | Phase 1 | Canvas-based node/edge diagramming |
| **KoreSlide** | `.koreslide` | Phase 2 | Full-screen slide presentations |
| **KoreBase** | `.korebase` | Phase 2 | Custom-schema record database |
---

## 3. Architecture

### 3.0 UI Terminology

KoreDocs uses the following chrome terminology consistently:

- **Top bar** — the very top line of the page containing the KoreDocs branding, the open
  tabs, and the `+` button
- **Application menu bar** — the line directly below the top bar containing app-specific
  menus such as File / Edit / View

The **top bar** must remain **common code** across all applications. It is a shared UI
surface provided once and reused by KoreDoc, KoreSheet, and KoreDiag.

Implementation: shared top bar is referenced via `static/shared/js/topbar.js` and
`static/shared/css/topbar.css`. The application menu bar renders from the shared
`static/shared/js/appMenu.js` (`renderAppMenu` + `initAppMenuEvents`) but is populated
with application-specific menus and handlers by each app's `main.js`.

### 3.1 Service

A single **FastAPI monolith** (`server.py`) serves all front ends.

```
GET  /                        -> redirect to /doc
GET  /doc                     -> KoreDoc SPA
GET  /sheet                   -> KoreSheet SPA
GET  /diag                    -> KoreDiag SPA

# Flat file-system storage (Phase 1)
GET    /api/files             -> list files in watched folder (all types)
GET    /api/files/{name}      -> read file contents
PUT    /api/files/{name}      -> write file contents
DELETE /api/files/{name}      -> delete file
POST   /api/files             -> create new file  { name, content }

# KoreFile database storage (Phase 2)
GET    /api/folders                    -> list folder tree
POST   /api/folders                    -> create folder  { name, parent_id }
DELETE /api/folders/{folder_id}        -> delete folder (must be empty)

GET    /api/files                      -> list files (metadata only, ?folder_id=)
GET    /api/files/{file_id}            -> get file with content
POST   /api/files                      -> create/import file  { folder_id, name, content }
PUT    /api/files/{file_id}            -> save file content + update metadata
DELETE /api/files/{file_id}            -> delete file

GET    /api/search?q=&type=&folder_id= -> FTS5 full-text search
```

The watched flat-folder path and database path are configured via `.env`:

```
KOREDOCS_DATA_DIR=C:\Util\Data\KoreFiles
KOREDOCS_DB_PATH=C:\Util\Data\KoreFiles\korefile.db
```

### 3.2 Front Ends

Each editor is a folder under `static/<app>/`:

```
static/
  shared/
    css/  variables.css  topbar.css  app-menu.css  tabs.css
    js/   topbar.js  appMenu.js  tabs.js  fileapi.js  draft.js
  doc/    index.html  style.css  js/
  sheet/  index.html  style.css  js/
  diag/   index.html  style.css  js/
```

All editors follow the same conventions: ES modules, no framework, no build step.

### 3.3 Design Tokens

All shared colours and dimensions live in `static/shared/css/variables.css` which is
imported by `topbar.css`. Per-app stylesheets contain only app-specific tokens.

```css
/* current palette — desaturated dark blue */
--bg: #0f1520;   --bg-2: #131d2e;
--surface: #1a2640;   --surface-2: #1f2f4a;
--border: #2a3f5a;    --border-2: #344e6e;
--text: #c8d8ec;  --text-2: #8fa8c8;  --text-dim: #4a5c70;
--accent: #4a6fa5;  --accent-2: #6b8cba;
```

---

## 4. KoreFile — Virtual File System

### 4.1 Concept

KoreFile is a **virtual file system stored in SQLite**. It provides:

- A **folder hierarchy** for organising documents (independent of the OS file system)
- **Full-text search** (SQLite FTS5) across all document content
- A single location for MCP tools and agents to read/write documents
- A **dual-storage bridge**: apps can open/save files from either the flat OS file system
  or the KoreFile DB, without changing the document format

KoreFile is inspired by the patterns in KoreData/KoreRAG:
- WAL journal mode for concurrent reads
- Contentless FTS5 table kept in sync with explicit INSERT/delete operations
- zlib compression on content blobs
- A `db_connection()` context-manager that auto-commits or rolls back

### 4.2 Database Schema

```sql
-- Folder hierarchy (adjacency-list model)
CREATE TABLE folders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id   INTEGER REFERENCES folders(id) ON DELETE RESTRICT,
    name        TEXT    NOT NULL,
    path        TEXT    NOT NULL UNIQUE,  -- materialised path e.g. "/Projects/KoreDocs"
    created_at  TEXT    DEFAULT (datetime('now','utc'))
);

-- Files
CREATE TABLE files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id   INTEGER REFERENCES folders(id) ON DELETE RESTRICT,
    name        TEXT    NOT NULL,         -- e.g. "design.koredoc"
    ext         TEXT    NOT NULL,         -- "koredoc" | "koresheet" | "kodiag" | ...
    content     BLOB,                     -- zlib-compressed UTF-8 text
    metadata    TEXT,                     -- JSON: title, tags, frontmatter, etc.
    word_count  INTEGER,
    created_at  TEXT    DEFAULT (datetime('now','utc')),
    modified_at TEXT    DEFAULT (datetime('now','utc')),
    UNIQUE (folder_id, name)
);

-- Full-text search index (contentless, managed explicitly like KoreRAG)
CREATE VIRTUAL TABLE files_fts USING fts5(
    name, metadata, content,
    tokenize = 'unicode61 remove_diacritics 1',
    content  = ''
);
```

**Folder path** is a materialised `/`-separated string (`/Root/Projects/KoreDocs`).
It is kept in sync on rename/reparent. This avoids recursive CTEs for breadcrumb
resolution while remaining trivially searchable with `path LIKE '/Root/Projects/%'`.

**Metadata** is a JSON column carrying whatever frontmatter the format exposes:
- `.koredoc`: `{ "title": "...", "tags": [...], "created": "..." }`
- `.koresheet`: `{ "title": "...", "cols": 26, "rows": 100 }`
- `.kodiag`: `{ "title": "...", "nodeCount": 12 }`

### 4.3 Folder Hierarchy Rules

- The root is the single row with `parent_id IS NULL`.
- A folder cannot be deleted if it has children or files (FK `ON DELETE RESTRICT`).
- Renaming a folder triggers `UPDATE folders SET path = replace(path, old, new)
  WHERE path LIKE old || '%'` to keep all descendant paths consistent.
- Maximum nesting depth: no enforced limit; UI limits the picker to 5 levels in Phase 2.

### 4.4 Dual Storage Strategy

Both storage backends coexist. The front end chooses which one to use at open/save time
via a **source tag** on the URL:

| Source | URL query param | API used |
|---|---|---|
| Flat file system | `?src=fs&file=notes.koredoc` | `/api/files/*` |
| KoreFile database | `?src=kf&id=42` | `/api/files/*` |

When no source tag is present, the app defaults to the flat file system (Phase 1
backward compatibility).

**`fileio.js` changes per app (Phase 2):**

- `autoOpenFromUrl()` reads `?src=` and dispatches to `_openFromFs()` or `_openFromKf()`
- `save()` saves back to whichever source was opened from
- `saveAs()` always prompts for storage type (FS or KoreFile) and location

The document format itself is **unchanged** — `.koredoc` is still plain markdown whether
it came from disk or the database. Only the transport layer differs.

**Import from flat FS into KoreFile DB:**

A one-shot `/api/import-fs` endpoint walks `KOREDOCS_DATA_DIR`, reads every `*.kore*`
file, and inserts each into the DB under a folder matching its relative OS path. This is
the migration path; after import the user can work entirely in KoreFile.

### 4.5 Python Implementation Notes

Following the KoreRAG pattern (see `KoreData/KoreRAG/app/database.py`):

```python
@contextmanager
def db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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
```

Content is stored compressed:

```python
import zlib

def _compress(text: str) -> bytes:
    return zlib.compress(text.encode("utf-8"), level=6)

def _decompress(blob: bytes) -> str:
    return zlib.decompress(blob).decode("utf-8")
```

FTS is maintained with explicit `'delete'` + re-insert on update, identical to KoreRAG.

---

## 5. MCP Server

### 5.1 Concept

KoreDocs exposes an **MCP (Model Context Protocol) server** so that LLM agents
(e.g. GitHub Copilot, Claude Desktop, custom agents) can:

- Search all documents by keyword, type, or folder
- Retrieve full document content
- Create and edit documents
- List the folder hierarchy

The MCP layer sits on top of the KoreFile database — it is the agent-facing API for the
same data the browser UI manipulates.

### 5.2 MCP Tools

| Tool | Description |
|---|---|
| `search_files` | Full-text search. Args: `query`, `type?` (ext), `folder_path?`, `limit?` |
| `get_file` | Retrieve a file by id. Returns metadata + full content. |
| `list_files` | List files in a folder (metadata only). Args: `folder_path?` |
| `list_folders` | Return the full folder tree as a nested structure. |
| `create_file` | Create a new file. Args: `folder_path`, `name`, `content` |
| `update_file` | Overwrite a file's content. Args: `id`, `content` |
| `delete_file` | Delete a file. Args: `id` |

### 5.3 Implementation

The MCP server is implemented using the **`fastmcp`** library, mounted on the same
`server.py` process under `/mcp`, or run as a separate transport for local agent use:

```python
from fastmcp import FastMCP

mcp = FastMCP("KoreDocs")

@mcp.tool()
def search_files(query: str, type: str | None = None,
                 folder_path: str | None = None, limit: int = 20) -> list[dict]:
    """Full-text search across all KoreFile documents."""
    return kf_search(query, ext=type, folder_path=folder_path, limit=limit)

@mcp.tool()
def get_file(id: int) -> dict:
    """Retrieve a document's metadata and full content by id."""
    f = kf_get_file(id, include_content=True)
    if f is None:
        raise ValueError(f"File {id} not found")
    return f

# ... list_files, list_folders, create_file, update_file, delete_file
```

**Startup model — one process, always both:**

The web UI and the MCP server are **co-launched in a single process**. There is no
separate "start the MCP server" step — starting KoreDocs starts everything.

```
python server.py          # HTTP mode: web UI + MCP SSE/HTTP on port 5500
python server.py --mcp-stdio  # stdio mode: web UI on port 5500 in background thread;
                              #             MCP protocol on stdin/stdout
```

The `--mcp-stdio` flag changes only the MCP *transport*. The web UI (HTTP on port 5500)
always starts regardless of transport mode, so a user can have their browser open while
an agent is also connected over stdio.

**Transport options:**

| Mode | How to connect | Use-case |
|---|---|---|
| `stdio` | `python server.py --mcp-stdio` | Local agents (Claude Desktop, Copilot) |
| `SSE` | `GET http://localhost:5500/mcp/sse` | Remote or multi-client agents |
| HTTP | `POST http://localhost:5500/mcp/messages` | Stateless REST-style agent calls |

SSE and HTTP transports are available in all startup modes. The MCP server requires the
KoreFile database to be initialised. MCP tools always go through KoreFile — they do not
operate on the flat file system.

---

## 6. KoreDoc

### 6.1 Concept

A **source-styled markdown editor**. The user types raw markdown source and sees live
visual treatment applied inline — headings stay as `# Heading` but render larger and
bolder; `**bold**` shows bold text while keeping the asterisks visible. Think Typora's
"source mode with decoration" rather than a split-pane preview.

This keeps the file 100% valid markdown at all times.

### 6.2 File Format

`.koredoc` files are **plain markdown**. No special wrapper. A YAML frontmatter block is
optional but recommended for metadata:

```markdown
---
title: My Document
created: 2026-04-23
tags: [design, koredocs]
---

# Heading

Body text here. Embed a diagram with a reference:

!kodiag[Label](./my-diagram.kodiag)
```

The `!kodiag[label](path)` syntax is a KoreDoc extension to the standard image syntax.
The viewer renders a live embedded preview of the `.kodiag` file.

### 6.3 Editor Canvas

- `<textarea>` overlaid on a `<div>` render mirror. Textarea text is `color: transparent`;
  `caret-color` makes the cursor visible. No contenteditable.
- Inline styling applied via CSS classes injected by a lightweight markdown tokeniser
  running on every keystroke.
- Toolbar: heading levels, bold, italic, inline code, bullet list, numbered list,
  horizontal rule.

### 6.4 Inline Styling Rules

| Markdown element | Visual treatment |
|---|---|
| `# H1` | 2em, bold — `#` shown in muted colour |
| `## H2` | 1.5em, bold |
| `### H3` | 1.17em, bold |
| `**bold**` | bold; `**` muted |
| `*italic*` | italic; `*` muted |
| `` `code` `` | monospace, code background; backticks muted |
| `- item` | bullet rendered; `-` muted |
| `1. item` | number shown, slight indent |
| `---` | rendered as a horizontal rule |
| `[text](url)` | text in link colour; `[]()` muted |

### 6.5 Properties Panel

When nothing is focused: document metadata (title, tags from frontmatter).
When text is selected: character/word count, selected text style.

---

## 7. KoreSheet

### 7.1 Concept

A lightweight grid spreadsheet. Cells hold plain text, numbers, or a formula. Formatting
is stored per-cell. The file format is human-readable JSON.

### 7.2 File Format

`.koresheet` is a JSON file:

```json
{
  "version": 1,
  "meta": { "title": "My Sheet", "created": "2026-04-23" },
  "cols": 26,
  "rows": 100,
  "cells": {
    "A1": { "value": "Revenue", "style": { "bold": true, "fillColor": "#e8f0fe" } },
    "B1": { "value": 42000 },
    "B2": { "formula": "SUM(B1:B10)", "computed": 42000 }
  }
}
```

Only non-default cells are stored. Empty cells are absent from the `cells` map.

### 7.3 Formula Engine

Phase 1 formula support: **selection-range aggregates only**.

| Formula | Description |
|---|---|
| `SUM(A1:D4)` | Sum all numeric values in the range |
| `AVERAGE(A1:D4)` | Average of numeric values |
| `COUNT(A1:D4)` | Count of cells with a numeric value |
| `MIN(A1:D4)` | Minimum numeric value |
| `MAX(A1:D4)` | Maximum numeric value |

No cross-cell references in Phase 1.

### 7.4 Cell Formatting

```json
{ "bold": false, "italic": false, "fontSize": 13,
  "fillColor": null, "textColor": null, "align": "left" }
```

### 7.5 Grid Canvas

- Rendered on an HTML5 canvas
- Click to select cell; Shift+click / Shift+Arrow or click-drag to select range
- Double-click or F2 to enter edit mode (overlaid `<input>`)
- Tab/Enter/Arrow to commit and move

### 7.6 Properties Panel

- Single cell: value, formula, style controls
- Range: live aggregates (sum, avg, count, min, max) + one-click insert
- Nothing selected: sheet metadata, default cell style

---

## 8. KoreDiag

### 8.1 Concept

A canvas-based node/edge diagramming editor. Nodes are rectangles or ellipses; edges
connect named anchor ports. Diagrams are stored as JSON and may be embedded inside
KoreDoc documents via `!kodiag[label](path.kodiag)`.

### 8.2 Technology Constraints

| Constraint | Decision |
|---|---|
| UI runtime | Plain HTML5 + CSS3 + vanilla ES-module JS |
| Canvas rendering | HTML5 `<canvas>` |
| Dependencies | Browser built-ins only |

### 8.3 File Format — `.kodiag`

Files are UTF-8 JSON:

```jsonc
{
  "koreDiag": "1.0",
  "id": "<uuid>",
  "title": "My Diagram",
  "created": "2026-04-22T00:00:00Z",
  "modified": "2026-04-22T00:00:00Z",
  "settings": {
    "gridSize": 20,
    "defaultArrow": "forward",
    "defaultNodeStyle": { "fillColor": "#ffffff", "strokeColor": "#5a5a8a",
                          "strokeWidth": 1.5, "fontSize": 13 },
    "defaultEdgeStyle": { "strokeColor": "#4a6fa5", "strokeWidth": 1.5 }
  },
  "nodes": [
    {
      "id": "node-001", "type": "rect", "label": "Service A",
      "x": 0, "y": 0, "width": 6, "height": 3,
      "style": {}, "meta": {}, "children": []
    }
  ],
  "edges": [
    {
      "id": "edge-001", "from": "node-001", "to": "node-002",
      "via": [], "fromPort": "s", "toPort": "n",
      "label": "calls", "arrow": "forward", "routing": "straight",
      "style": {}, "meta": {}
    }
  ]
}
```

**LLM readability notes:**
- `label` fields carry semantic meaning.
- `children` array makes containment explicit.
- `from`/`to`/`label` on edges describes directed relationships clearly.
- `meta` bag allows domain-specific context an LLM can reason about.

### 8.4 Canvas & Coordinate System

- Infinite virtual canvas; origin `(0,0)` at initial view centre.
- Coordinates are **logical grid units**: `screenX = worldX x gridSize x zoom + pan.x`
- Negative coordinates are fully supported.

### 8.5 Grid & Snap

- Always-visible subtle grid background.
- All node positions and sizes clamp to the grid — no free positioning in v1.
- Default `gridSize`: 20 px per unit at 100% zoom.

### 8.6 Node Types

| Type | Description |
|---|---|
| `rect` | Rectangle |
| `ellipse` | Circle / ellipse |
| `waypoint` | Invisible routing handle; not exported to PNG |

### 8.7 Node Properties

| Property | Type | Notes |
|---|---|---|
| `id` | string | UUID, immutable |
| `type` | enum | Shape type |
| `label` | string | Displayed inside shape |
| `x`, `y` | integer | Grid units (relative to parent if nested, else world) |
| `width`, `height` | integer | Grid units |
| `style` | object | Fill, stroke, font overrides |
| `meta` | object | Arbitrary key/value pairs |
| `children` | array | Nested child nodes |

### 8.8 Containment & Hierarchy

- Child positions are **relative to parent's top-left**; world coords computed at render.
- Moving a parent moves all children.
- Parent bounding box wraps children on drop with 1 grid-unit padding (no live
  auto-expand during drag in v1).
- **Drag-promotion**: dropping a node so its centre lands inside another node makes it a
  child of that node.
- **Drag-demotion**: dropping outside parent bounds promotes it to the grandparent (or
  root level).

### 8.9 Edges & Ports

Each node exposes **8 fixed ports**: N, NE, E, SE, S, SW, W, NW. Port positions are
computed at render time, not stored.

| Node type | Endpoint behaviour |
|---|---|
| `rect` | Stored `fromPort`/`toPort` key position; falls back to centre |
| `ellipse` | Boundary intersection recomputed at render (port key ignored) |
| `waypoint` | Centre of the 1x1 node |

Edge routing: `straight` (v1), `orthogonal` (v2).

### 8.10 Interactions

**Selection:**
- Click to select; click-drag on empty canvas for rubber-band multi-select.
- `Ctrl+A` selects all; `Escape` clears.
- Dragging a selected group moves all; edges with both endpoints in group move rigidly.

**Editing:**
- Double-click a node or edge label to enter inline text edit mode.
- Drag a node to move (snaps to grid); drag resize handle to resize.
- Drag from a port to start a new edge.

**Pan & Zoom:**

| Action | Effect |
|---|---|
| Mouse wheel | Zoom (centred on cursor) |
| Middle-mouse drag / Space+drag | Pan canvas |
| `Ctrl+Shift+H` | Reset to home view |

**Keyboard shortcuts:**

| Key | Action |
|---|---|
| `Ctrl+Z` / `Ctrl+Y` | Undo / Redo |
| `Delete` / `Backspace` | Delete selected |
| `Ctrl+S` / `Ctrl+O` | Save / Open |
| `Ctrl+Shift+E` | Export PNG |

### 8.11 Undo / Redo

Command-pattern stack. Every mutating action (add/remove node or edge, move, resize,
label change, meta change, reparent) is a reversible `Command` object. Upper bound:
200 steps.

### 8.12 UI Layout

```
+--------------------------------------------------------------+
|  [Top bar -- tabs]                                           |
+--------------------------------------------------------------+
|  [Application menu bar -- File  Edit  View]                  |
+----------+-----------------------------------+---------------+
|  Tool    |                                   |  Properties   |
|  Sidebar |          Canvas                   |  Panel        |
|          |                                   +---------------+
|          |                                   |  Hierarchy    |
|          |                                   |  Panel        |
+----------+-----------------------------------+---------------+
```

**Tool sidebar:**

| Tool | Function |
|---|---|
| Select / Move | Select, move, resize nodes |
| Rectangle | Draw a new rect node |
| Ellipse | Draw a new ellipse node |
| Connect | Draw an edge |
| Text | Add/edit label on selection |
| Pan | Drag canvas |

---

## 9. LLM Readability Strategy

| App | Format | LLM-readable? | Notes |
|---|---|---|---|
| KoreDoc | `.koredoc` (markdown) | Yes — Native | Plain markdown is ideal LLM input |
| KoreSheet | `.koresheet` (JSON) | Yes — Good | Sparse cell map is self-describing |
| KoreDiag | `.kodiag` (JSON) | Yes — Good | Node/edge structure clear in JSON |
| KoreSlide | `.koreslide` (TBD) | Target Yes | Slide-per-object, markdown content |
| KoreBase | `.korebase` (TBD) | Target Yes | Schema + records JSON |

Where plain-text readability degrades (e.g. a dense KoreSheet), a lightweight LLM
summary block may be appended as a comment or a `metadata` JSON column value — deferred
to Phase 2.

---

## 10. KoreSlide — Placeholder

Full-screen presentation editor. Each slide is a canvas with positioned text, shapes,
and embedded KoreDiag diagrams. File format: JSON array of slide objects. **Phase 3.**

---

## 11. KoreBase — Placeholder

Custom-schema record database. User defines fields (text, number, date, reference).
Records stored as row objects. UI: filterable, sortable table with an edit form.
**Phase 3.**

---

## 12. Phase Roadmap

| Phase | Deliverables |
|---|---|
| **Phase 1a** | FastAPI monolith skeleton; flat file API; all three Phase 1 editors scaffolded |
| **Phase 1b** | KoreDiag migrated into monolith; file I/O via flat API |
| **Phase 1c** | KoreDoc editor canvas, inline styling, save/load via file API |
| **Phase 1d** | KoreSheet grid canvas, cell editing, aggregate formulas, save/load |
| **Phase 2a** | KoreFile SQLite schema, `/api/*` routes, import-from-FS tool |
| **Phase 2b** | Front-end dual-storage (`?src=kf`), file picker with KoreFile browser |
| **Phase 2c** | MCP server (`fastmcp`), stdio + SSE transports, all seven tools |
| **Phase 3** | KoreSlide; KoreBase; KoreDoc diagram embeds |

---

## 13. Decisions

| # | Question | Decision |
|---|---|---|
| 1 | KoreDoc editor mechanism | **Textarea + render mirror.** Textarea holds clean source; a `<div>` mirror behind it re-renders on every keystroke. `color: transparent` on textarea; `caret-color` shows the cursor. No contenteditable. |
| 2 | `/api/files` listing | Returns all supported types mixed. Optional `?type=koredoc` for per-type filtering. |
| 3 | KoreDoc image embeds | Standard `![alt](path)` for raster images; supported Phase 3. |
| 4 | KoreSheet grid limits | **26 cols x 100 rows** Phase 1. Canvas at natural size; container scrolls via `overflow: auto`. |
| 5 | Auth | Localhost-only Phase 1. No auth. |
| 6 | Dual-storage URL scheme | `?src=fs&file=<name>` (flat FS) vs `?src=kf&id=<id>` (KoreFile DB). No param = flat FS (backward compat). |
| 7 | KoreFile folder model | Adjacency-list with materialised path string. Simple to query, trivial to sync on rename. |
| 8 | MCP transport | `stdio` as primary (local agent use); SSE endpoint for remote. Same `FastMCP` instance, mounted on `/mcp`. |
| 9 | Shared component library | **Shared API client only** (Phase 1). `fileapi.js` extended to support dual storage in Phase 2. UI components written per-editor. |
