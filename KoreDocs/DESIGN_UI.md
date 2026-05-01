# KoreDocs UI Design

> Status: Active
> Date: 2026-05-01

---

## 1. Scope

This document defines the layout and UI structure of KoreDocs.

KoreDocs is the document and file-management suite. It exposes two active surfaces:

- **KoreFile** (`/kf`) — database-backed file browser and organizer (Phase 1, complete)
- **KoreDoc** (`/doc`) — markdown editor with source-styled inline rendering (Phase 1, complete)

Planned but not yet built: KoreSheet, KoreDiag.

Runtime architecture, file formats, and API contracts remain in [design.md](design.md).

---

## 2. Shell

KoreDocs uses a shared shell via `/static/commonui/` (a static mount over the shared UIElements assets).

```
┌─────────────────────────────────────────────────────────┐
│  Suite Top Bar  (#topbar)                               │
├─────────────────────────────────────────────────────────┤
│  Application Tab Bar  (#tab-bar)                        │
│  [KoreFile]  [KoreDoc]  [KoreSheet]  [KoreDiag]         │
├─────────────────────────────────────────────────────────┤
│  Application Menu Bar  (#app-menu-host)  [KoreDoc only] │
├─────────────────────────────────────────────────────────┤
│                                                         │
│   Page content                                          │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**Top bar** — Initialized by `initChrome()` in each page's inline module script.

**Tab bar** (`#tab-bar`) — Application-level tabs navigating between KoreFile, KoreDoc, KoreSheet, KoreDiag. Managed by `/static/commonui/js/appbar.js`. Tab state is tracked across navigations via `kd:before-navigate`.

**App menu bar** (`#app-menu-host`) — Present on KoreDoc only. Provides File / Edit / View / Insert menu actions. Managed by `/static/commonui/js/appMenu.js`.

---

## 3. KoreFile (`/kf`) — File Browser

KoreFile is the default landing surface and primary file-management interface.

```
┌────────────────────────────────────────────────────────────────┐
│  Top Bar + Tab Bar                                             │
├──────────────────┬──┬─────────────────────────────────────────┤
│                  │  │  [ ＋ New File ]  [ ↓ Import FS ]       │
│  Folders         │▓▓│  Search: [________________]             │
│  #kf-tree-panel  │  ├─────────────────────────────────────────┤
│                  │  │  Name ↕  │ Type ↕ │ Words ↕ │ Modified ↕│
│  ▸ Inbox         │  │  ──────────────────────────────────────  │
│  ▸ Projects      │  │  📄 notes.koredoc   doc    312   Apr 30  │
│  ▸ Archive       │  │  📊 budget.koresheet sheet  —    Apr 28  │
│                  │  │  …                                       │
│  [＋ New Folder] │  │                                          │
│                  │  │  (kf-empty when no files)                │
└──────────────────┴──┴──────────────────────────────────────────┘
```

### 3.1 Folder Tree (`#kf-tree-panel`)

```
┌──────────────────────────────────┐
│  Folders                  [＋]   │  ← #kf-tree-header
├──────────────────────────────────┤
│  ▸ Inbox                         │
│  ▸ Projects                      │  ← #kf-tree
│    ▾ Q1 2026                     │
│      › Report                    │
└──────────────────────────────────┘
```

`#btn-new-folder` — Creates a new folder under the currently selected parent.

Resize handle (`#kf-resize-handle`) — Draggable divider between tree and file list.

### 3.2 File List (`#kf-main-panel`)

**Breadcrumb** (`#kf-breadcrumb`) — Shows current folder path.

**Toolbar** (`#kf-toolbar`):
- `#btn-new-file` — Create a new file in the current folder
- `#btn-import-fs` — Import existing flat-storage files into the database
- `#kf-search` — Live search filter over the current folder

**File table** (`#kf-file-list`):

| Column | Description |
|---|---|
| `.col-icon` | File-type icon |
| `.col-name` | Filename; sortable |
| `.col-type` | Extension / type; sortable |
| `.col-words` | Word count (for `.koredoc`); sortable |
| `.col-modified` | Last modified timestamp; sortable |
| `.col-actions` | Open / Delete row actions |

Sortable columns carry `data-sort` attributes; active sort shown via `.sort-arrow`.

`#kf-empty` — Shown when the current folder contains no files.

---

## 4. KoreDoc (`/doc`) — Markdown Editor

KoreDoc is a source-styled markdown editor. The document is visible as live-rendered source — markdown syntax remains visible but styled to be readable.

```
┌──────────────────────────────────────────────────────────────────┐
│  Top Bar + Tab Bar + App Menu Bar                                │
├──────────────────────────────────────────────────────────────────┤
│  Toolbar  [H1][H2][H3]│[B][I][`c`]│[⇥][⇤]│[• –][1.][```][—]    │
├─────────────────────────────────────┬────────────────────────────┤
│                                     │  Properties                │
│                                     │  Title: …                  │
│  # My Document                      │  Tags: …                   │
│                                     │  Word count: 312           │
│  This is a paragraph with **bold**  ├────────────────────────────┤
│  and *italic* and `code` inline.    │  Document Map              │
│                                     │  · Section 1               │
│  ## Section 1                       │  · Section 2               │
│                                     │                            │
│  #editor-host                       │  #right-sidebar            │
├─────────────────────────────────────┴────────────────────────────┤
│  Untitled.koredoc        Ln 12, Col 4        312 words  1,840 ch │
│  #status-bar                                                     │
└──────────────────────────────────────────────────────────────────┘
```

### 4.1 Formatting Toolbar (`#toolbar`)

Button groups with `data-insert` attributes drive formatting insertion:

| Buttons | Group |
|---|---|
| `H1` `H2` `H3` | Heading levels |
| `B` `I` `` `c` `` | Bold, italic, inline code |
| `⇥` `⇤` | Indent / outdent |
| `• –` `1.` ` ``` ` `—` | Bullet list, numbered list, code block, horizontal rule |

Each button triggers an `editor.applyInsert(type)` operation at the current cursor.

### 4.2 Editor (`#editor-host`)

A source-styled overlay editor: a transparent `<textarea>` over a styled render mirror. Markdown syntax characters are visible but visually muted, giving a readable source appearance.

Managed by `editor.js`.

### 4.3 Right Sidebar (`#right-sidebar`)

Two sections:

**Properties** (`#properties-panel` / `#props-content`):
- When unfocused: document metadata (title, tags, created date)
- When text is selected: character and word count for selection

**Document Map** (`#map-panel` / `#map-content`):
- Heading outline for the active document
- Clicking a heading item scrolls the editor to that position

### 4.4 Status Bar (`#status-bar`)

```
Untitled.koredoc        Ln 12, Col 4        312 words  1,840 ch
   #status-file          #status-pos             #status-counts
```

Always visible at the bottom of the editor surface.

---

## 5. CSS File Ownership

| File | Owns |
|---|---|
| `/static/commonui/css/chrome.css` | Top bar, tab bar, shared shell tokens |
| `/static/shared/css/variables.css` | Design tokens (colors, fonts, spacing) |
| `/static/doc/style.css` | KoreDoc editor layout, toolbar, sidebar, status bar |
| `/static/kf/style.css` | KoreFile tree panel, file list table, toolbar |

---

## 6. JS Module Ownership

| Module | Owns |
|---|---|
| `doc/js/main.js` | KoreDoc entry point; wires editor, toolbar, properties, file I/O, menu |
| `doc/js/editor.js` | Source-styled textarea+mirror editor; `init`, `getValue`, `setValue` |
| `doc/js/toolbar.js` | Formatting button handlers |
| `doc/js/properties.js` | Properties panel and document map rendering |
| `doc/js/fileio.js` | File open/save/autosave, dirty tracking, URL param auto-open |
| `doc/js/chrome.js` | Shell initialization (`initChrome`) |
| `kf/js/main.js` | KoreFile entry point; folder tree, file list, search, new/import |
| `/static/commonui/js/appbar.js` | Tab bar management, `kd:before-navigate` event |
| `/static/commonui/js/appMenu.js` | Application menu bar rendering and event dispatch |
| `/static/shared/js/draft.js` | Autosave draft management (localStorage) |
| `/static/shared/js/fileapi.js` | File I/O abstraction over the server API |
