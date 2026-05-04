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
┌──────────────────────┬──┬──────────────────────────────────────────────────────────┐
│                      │  │                                                          │
│  Explorer panel      │▓▓│  Editor panel  (#code-main)                              │
│  (#code-sidebar)     │  │                                                          │
│  .kcui-panel-left    │  │  ┌─ #editor-toolbar ─────────────────────────────────┐  │
│                      │  │  │  breadcrumb · state · [Find] [Save] [AI]          │  │
│                      │  │  ├─ #editor-body ─────────────────────────────────── │  │
│                      │  │  │  ┌─ #editor-surface ──────┬──┬─ #chat-panel ──┐  │  │
│                      │  │  │  │  findbar (optional)    │▓▓│  #chat-thread  │  │  │
│                      │  │  │  │  editor host           │  │  #chat-composer│  │  │
│                      │  │  │  └────────────────────────┴──┴────────────────┘  │  │
│                      │  │  └───────────────────────────────────────────────────┘  │
│                      │  │                                                          │
└──────────────────────┴──┴──────────────────────────────────────────────────────────┘
         ▲                                                    ▲
  #code-splitter                                      #chat-splitter
  .kcui-splitter                                   (visual divider)
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
│  breadcrumb · state · [Find] [Save] [AI]             │  ← #editor-toolbar (single bar)
├──────────────────────────────────────────────────────┤
│  ┌─ #editor-body ─────────────────────────────────┐  │
│  │  ┌─ #editor-surface ──────┬──┬─ #chat-panel ─┐ │  │
│  │  │  [Find bar, optional]  │▓▓│  #chat-thread  │ │  │
│  │  │  1 │ import …          │  │  (exchanges)   │ │  │
│  │  │  2 │ …                 │  ├───────────────┤ │  │
│  │  │                        │  │  #chat-composer│ │  │
│  │  └────────────────────────┴──┴────────────────┘ │  │
│  └─────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

### 6.1 Toolbar (`#editor-toolbar`)

Single-row bar spanning the full width of `#code-main`. Left side shows `#file-breadcrumb` and `#file-state`; right side has the action buttons.

- **Find** (`#btn-find`) — Toggles `#editor-findbar`.
- **Save** (`#btn-save`) — Saves the active file. Disabled when no file is open or no unsaved changes.
- **AI** (`#btn-ai`) — Toggles `#chat-panel`. Active state uses `.is-active` on the button. Keyboard shortcut: `Alt+A`.

### 6.2 Editor body (`#editor-body`)

Flex row below the toolbar that fills all remaining vertical space. Contains `#editor-surface`, `#chat-splitter`, and `#chat-panel`.

### 6.3 Editor surface (`#editor-surface`)

Fills the left portion of `#editor-body` (`flex: 1`). Contains the find bar and CodeMirror instance.

- `#editor-findbar` — Toggled by Find button or `Ctrl+F`. Closed with Close button or `Escape`.
- `#editor-empty` — Shown when no file is open.
- `#editor-host` — CodeMirror 6 editor mount.

---

## 7. Chat Panel

```
┌─────────────────────────────┐
│                             │  ← #chat-thread (flex: 1, scrollable)
│  ┌───────────────────────┐  │
│  │ Kore                  │  │  ← .chat-msg--assistant
│  │ Here is the answer …  │  │
│  └───────────────────────┘  │
│  ─────────────────────────  │  ← .chat-divider
│             [ user prompt ] │  ← .chat-msg--user .bubble
│                             │
├─────────────────────────────┤
│  [textarea ___________][Snd]│  ← #chat-composer / #chat-input / #btn-chat-send
└─────────────────────────────┘
```

`#chat-panel` is the right column inside `#editor-body`. Width: 340 px by default. Hidden (`hidden` attribute) until the **AI** button is toggled on.

One chat thread exists per open file. When the active tab changes, `chat.js` re-renders the thread for the new path. All threads are held in memory (a `Map<path, exchanges[]>`) for the lifetime of the page.

Each submission is a new exchange in a KoreChat conversation:

1. User types a prompt and presses **Enter** (or clicks **Send**).
2. `chat.js` derives a stable `session_id` from the file path (e.g. `kc_KoreCode__app__server_py`).
3. `POST {koreagent}/sessions/{session_id}/prompt` — sends `{ prompt }` → receives `{ run_id }`.
4. `GET  {koreagent}/runs/{run_id}/stream` — SSE stream. Events with `type: "response"` accumulate the reply text; `type: "done"` closes the stream.
5. The completed assistant reply is appended to the thread and persisted in the in-memory store.

KoreAgent base URL is taken from `window.__koreSuiteUrls.koreagent`, falling back to `http://127.0.0.1:8605`.

### 7.1 Thread (`#chat-thread`)

Scrollable flex column. Messages are rendered in submission order.

- `.chat-msg--user` — Right-aligned bubble (`.bubble`). Multi-line text preserved with `white-space: pre-wrap`.
- `.chat-msg--assistant` — Avatar label (`Kore`) above body text (`.body`). Fenced code blocks rendered as `<pre>`.
- `.chat-thinking` — Animated dots shown while the stream is pending.
- `.chat-divider` — 1 px horizontal rule inserted after each completed assistant exchange.

### 7.2 Composer (`#chat-composer`)

Pinned to the bottom of `#chat-panel`.

- `#chat-input` — Auto-sizing `<textarea>` (min 34 px, max 100 px). `Enter` submits; `Shift+Enter` inserts a newline.
- `#btn-chat-send` — Disabled while a stream is active.

---

## 8. CSS File Ownership

| File | Owns |
|---|---|
| `UIElements/assets/css/chrome.css` | Top bar, application bar, shared shell tokens |
| `UIElements/assets/css/panels.css` | `.kcui-panels` split layout primitives |
| `static/code/css/base.css` | Page frame, panel header/kicker/title/path, shared button classes |
| `static/code/css/explorer.css` | File tree rows, depth indentation, caret, active state |
| `static/code/css/find.css` | Find bar layout and inputs |
| `static/code/css/editor.css` | Editor toolbar, `#editor-body`, editor surface, empty state |
| `static/code/css/chat.css` | Chat panel, thread, message bubbles, composer |

---

## 9. JS Module Ownership

| Module | Owns |
|---|---|
| `main.js` | Entry point; wires all modules together; `boot()` sequence |
| `state.js` | Shared in-memory state (`openTabs`, `activePath`, `tree`, `root`); `api()` fetch utility |
| `editor.js` | CodeMirror instance; tab state; `renderTabs`, `renderMeta`, `openFile`, `restoreTabs` |
| `explorer.js` | File tree fetch and render; `initExplorer`, `refreshTree`, `renderTree`, `expandAncestors` |
| `find.js` | Find bar logic; `initFind`, `runFind`, `runFindNext`, `runFindPrevious`, `closeFindBar` |
| `chat.js` | Chat panel toggle, prompt submit, SSE streaming, per-file thread store; `initChat` |
| `UIElements/…/chrome.js` | `initTopbar`, `initAppBar` — shell rendering |
| `UIElements/…/panels.js` | `initPanels` — drag-to-resize splitter |
| `UIElements/…/icons.js` | `fileIconForPath` — per-extension SVG file icons |
