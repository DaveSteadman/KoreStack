# KoreCode UI Design

> Status: Active
> Date: 2026-05-01

---

## 1. Scope

This document defines the layout and UI structure of KoreCode.

It covers the single page that KoreCode exposes: the main code editor workspace.

Runtime architecture, API boundaries, and LLM feature scope remain in [DESIGN.md](DESIGN.md).

---

## 2. Shell

KoreCode uses the shared KoreStack shell from UIElements.

```
┌─────────────────────────────────────────────────────────┐
│  Suite Top Bar  (#topbar)                               │
│  KoreStack wordmark · service links · suite nav         │
├─────────────────────────────────────────────────────────┤
│  Application Bar  (#app-bar)                            │
│  KoreCode brand  │  open-file tabs  │  (flex space)     │
├─────────────────────────────────────────────────────────┤
│                                                         │
│   Main workspace  (#code-app)                           │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**Top bar** — Suite identity. Rendered by `initTopbar({ currentService: 'korecode' })`.

**Application bar** — Application identity + open-file tabs. Rendered by `initAppBar` with `editorTabsSlot: 'kc-editor-tabs'`. The tab slot (`#kc-editor-tabs`) is created dynamically by `initAppBar` and populated by `editor.js`.

Shell styling is sourced from `UIElements/assets/css/chrome.css`. Per-service accent color is defined in `UIElements/assets/js/theme.js`.

---

## 3. Main Layout

The main workspace below the shell uses a horizontal split layout.

```
┌──────────────────────┬──┬──────────────────────────────┐
│                      │  │                              │
│  Explorer panel      │▓▓│  Editor panel                │
│  (#code-sidebar)     │  │  (#code-main)                │
│  .kcui-panel-left    │  │  .kcui-panel-right           │
│                      │  │                              │
│                      │  │                              │
└──────────────────────┴──┴──────────────────────────────┘
                       ▲
              Drag handle (#code-splitter)
              .kcui-splitter
```

Container: `#code-app .kcui-panels`

Layout is provided by `UIElements/assets/css/panels.css` (`.kcui-panels`, `.kcui-panel-left`, `.kcui-splitter`, `.kcui-panel-right`). The drag-to-resize behavior is provided by `UIElements/assets/js/panels.js` (`initPanels`). Sidebar width is persisted to localStorage under `korecode-sidebar-w`.

---

## 4. Explorer Panel

```
┌──────────────────────────────────┐
│  Explorer                        │  ← .panel-kicker
│  KoreStack                       │  ← #root-label (.panel-title)
│  C:\Util\GithubRepos\KoreStack   │  ← #root-path (.panel-path)
│                             [↻]  │  ← #btn-refresh-tree
├──────────────────────────────────┤
│  Loading workspace…              │  ← #tree-status (.panel-status)
├──────────────────────────────────┤
│  ▸ KoreAgent/                    │
│  ▸ KoreCode/                     │  ← #code-tree
│    ▾ static/                     │
│      ▾ code/                     │
│        › main.py                 │
│  ...                             │
└──────────────────────────────────┘
```

**Panel header** (`.panel-header`) — Contains the kicker label, the workspace root name, the resolved absolute path, and the refresh button.

- `#root-label` — Short name of the active workspace root (e.g. `KoreStack`). Set by `explorer.js` when the tree loads.
- `#root-path` — Full absolute path of the workspace root. Updated by `explorer.js` alongside `#root-label`. Truncates with ellipsis if too wide.
- `#btn-refresh-tree` — Re-fetches the full directory tree from the server.

**Tree status** (`#tree-status .panel-status`) — Shows loading, error, or completion messages.

**File tree** (`#code-tree`) — Rendered by `explorer.js`. Directories are collapsible buttons with depth-indented rows. Files are buttons that call `openFile`. Active file ancestors are highlighted with `.is-active`.

---

## 5. Application Bar Tabs

Open-file tabs live in the application bar, in the slot created by `editorTabsSlot`.

```
│  KoreCode ▸  │  🐍 main.py ✕  │  {} config.json ✕  │  …  │
```

Each tab is a `<button class="kappbar-editortab">`. Active tab carries `.is-active`.

Tab anatomy:

```
[ file-icon ][ filename ][ dirty-dot? ][ × ]
```

- **File icon** (`.kappbar-editortab-icon`) — SVG icon matched to extension by `fileIconForPath` from `UIElements/assets/js/icons.js`.
- **Filename** (`.kappbar-editortab-name`) — Short filename only (not full path). Full path is in the `title` attribute.
- **Dirty indicator** (`.kappbar-editortab-dirty`) — `●` shown when the file has unsaved changes.
- **Close button** (`.kappbar-editortab-close`) — `×` removes the tab. Does not confirm.

Tab styles live in `UIElements/assets/css/appbar.css` under `.kappbar-editortabs` / `.kappbar-editortab*`. Active state uses `--kappbar-accent`.

---

## 6. Editor Panel

The editor panel is only active when a file tab is open. When no file is open, the panel remains visible in an empty state and the editor toolbar actions are greyed out and disabled.

```
┌──────────────────────────────────────────────────────┐
│  [ Find ]  [ Save ]                                  │  ← #editor-toolbar / #editor-actions
├──────────────────────────────────────────────────────┤
│  static/code/js/main.js          read/write editor   │  ← #editor-meta
├──────────────────────────────────────────────────────┤
│  ┌────────────────────────────────────────────────┐  │
│  │ Find: [_____________] [Prev] [Next] [Close]    │  │  ← #editor-findbar (hidden by default)
│  └────────────────────────────────────────────────┘  │
│                                                      │
│   1 │ import { initAppBar } from …                   │
│   2 │ import { initPanels } from …                   │  ← #editor-host (CodeMirror)
│   3 │ …                                              │
│                                                      │
└──────────────────────────────────────────────────────┘
```

### 6.1 Toolbar (`#editor-toolbar`)

Contains only the action buttons. No title or path — those moved to the appbar and explorer panel respectively.

When no file tab is active, all toolbar buttons are disabled.

- **Find** (`#btn-find`) — Toggles `#editor-findbar`.
- **Save** (`#btn-save`) — Saves the active file. Disabled when no file is open or no unsaved changes.

### 6.2 Meta bar (`#editor-meta`)

Two-column row immediately below the toolbar:

- Left: `#file-breadcrumb` — Relative path of the active file within the workspace root, or `No file open` when nothing is active.
- Right: `#file-state` — Read/write state label, or `Editor unavailable` when nothing is active.

### 6.3 Find bar (`#editor-findbar`)

Toggled by the Find button or `Ctrl+F` keybinding. Sits above the editor content and pushes it down. Closed with the Close button or `Escape`.

Controls: text input (`#find-input`), Prev (`#btn-find-prev`), Next (`#btn-find-next`), Close (`#btn-find-close`).

### 6.4 Editor surface (`#editor-surface`)

Fills remaining vertical space.

- `#editor-empty` — Shown when no file is open. Contains the `No file open` empty-state message.
- `#editor-host` — CodeMirror 6 editor mount. Visible when a file is loaded.

---

## 7. CSS File Ownership

| File | Owns |
|---|---|
| `UIElements/assets/css/chrome.css` | Top bar, application bar, shared shell tokens |
| `UIElements/assets/css/panels.css` | `.kcui-panels` split layout primitives |
| `static/code/css/base.css` | Page frame, panel header/kicker/title/path, shared button classes |
| `static/code/css/explorer.css` | File tree rows, depth indentation, caret, active state |
| `static/code/css/find.css` | Find bar layout and inputs |
| `static/code/css/editor.css` | Editor toolbar, meta bar, editor surface, empty state |

---

## 8. JS Module Ownership

| Module | Owns |
|---|---|
| `main.js` | Entry point; wires all modules together; `boot()` sequence |
| `state.js` | Shared in-memory state (`openTabs`, `activePath`, `tree`, `root`); `api()` fetch utility |
| `editor.js` | CodeMirror instance; tab state; `renderTabs`, `renderMeta`, `openFile`, `restoreTabs` |
| `explorer.js` | File tree fetch and render; `initExplorer`, `refreshTree`, `renderTree`, `expandAncestors` |
| `find.js` | Find bar logic; `initFind`, `runFind`, `runFindNext`, `runFindPrevious`, `closeFindBar` |
| `UIElements/…/chrome.js` | `initTopbar`, `initAppBar` — shell rendering |
| `UIElements/…/panels.js` | `initPanels` — drag-to-resize splitter |
| `UIElements/…/icons.js` | `fileIconForPath` — per-extension SVG file icons |
