# KoreAgent UI Design

> Status: Active
> Date: 2026-05-01

---

## 1. Scope

This document defines the layout and UI structure of KoreAgent.

KoreAgent exposes a single-page workspace: the agent control interface where operators monitor scheduled tasks, stream live logs, and interact with the agent via chat.

Runtime architecture, skill system, and API contracts remain in [DESIGN.md](DESIGN.md).

---

## 2. Shell

KoreAgent uses the shared KoreStack shell from UIElements.

```
┌─────────────────────────────────────────────────────────┐
│  Suite Top Bar  (#topbar)                               │
│  KoreStack wordmark · service links · suite nav         │
├─────────────────────────────────────────────────────────┤
│  Application Bar  (#app-bar)                            │
│  KoreAgent brand  │  Agent Control overline             │
├─────────────────────────────────────────────────────────┤
│                                                         │
│   Main workspace  (#main-grid .kcui-workspace)          │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**Top bar** — `initTopbar({ currentService: 'koreagent' })`

**Application bar** — `initAppBar({ currentService: 'koreagent', overline: 'Agent Control', brandLabel: 'KoreAgent', brandIcon: 'koreagent' })`

Shell styling from `UIElements/assets/css/chrome.css`. Workspace layout from `UIElements/assets/css/workspace.css`. Resize behavior from `UIElements/assets/js/workspace.js` (`initWorkspaceLayouts`).

---

## 3. Main Layout

The workspace is a resizable three-column, two-row grid driven by `kcui-workspace`.

```
┌──────────────────┬──┬──────────────────┬──┬──────────────────┐
│                  │  │                  │  │                  │
│  Timeline        │▓▓│  Log Stream      │▓▓│  Chat            │
│  #panel-timeline │  │  #panel-log      │  │  #panel-chat     │
│                  │  │                  │  │                  │
├──────────────────┴──┴──────────────────┴──┴──────────────────┤
│                                                               │
│  Input bar  (#panel-input)                                    │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

Container: `#main-grid .kcui-workspace`

Data attributes on `#main-grid`:
- `data-kcui-layout-key="koreagent-main-v1"` — persists column/row sizes to localStorage
- `data-kcui-columns="0,2,4"` — three column tracks with splitters at indices 1 and 3
- `data-kcui-rows="0,2"` — two row tracks with splitter at index 1
- `data-kcui-disable-below="900"` — collapses to single-column below 900 px

Splitters:
- `#splitter-v1` — between Timeline and Log (`.kcui-workspace__splitter--v`, before=0, after=2)
- `#splitter-v2` — between Log and Chat (`.kcui-workspace__splitter--v`, before=2, after=4)
- `#splitter-h1` — between main panels and Input bar (`.kcui-workspace__splitter--h`, before=0, after=2)

All panels carry `.kcui-panel .kcui-workspace__region`. Panel bodies carry `.kcui-panel-body`.

---

## 4. Timeline Panel (`#panel-timeline`)

```
┌──────────────────────────────────┐
│  Schedule                        │  ← .panel-header
├──────────────────────────────────┤
│  12:00 ● task_pulse              │
│  12:15   task_AINews             │  ← #timeline-ticker
│  12:30   task_EUNews             │
├──────────────────────────────────┤
│  Queue: 2 pending                │  ← #timeline-queue
└──────────────────────────────────┘
```

**`#timeline-ticker`** — Renders the scheduled-task timeline. Each minute slot shows upcoming or in-progress tasks. Current minute is highlighted.

**`#timeline-queue`** — Shows the count and summary of tasks currently queued for execution.

---

## 5. Log Stream Panel (`#panel-log`)

```
┌──────────────────────────────────────────────────┐
│  Log  [↑] [↓] [live▶] [wrap]                     │  ← .panel-header
├──────────────────────────────────────────────────┤
│  ── task_pulse ──────────────────────────────     │  .log-sep / .log-title
│  [round 1] Thinking…                             │  .log-tool-round
│  ✓ api_call completed in 0.4s                    │  .log-progress
│  Error: connection refused                       │  .error
│  Done.                                           │  .success
└──────────────────────────────────────────────────┘
```

**Header controls** — all four are `button.kcui-tag.kcui-tag--dim` (square ends); active state adds `is-on`:
- `#log-btn-up` / `#log-btn-down` — Jump to previous/next log section separator; always white (`--text-hi`)
- `#log-btn-live` — Auto-scroll to follow new lines; green (`--green`) when `is-on`, dim when off
- `#wrap-btn-log` — Toggle line-wrap on `#log-body`; yellow (`--yellow`) when `is-on`, dim when off

**Log line classes:**

| Class | Meaning |
|---|---|
| `.log-sep` | Section separator line |
| `.log-title` | Task or run title |
| `.log-tool-round` | Tool execution round marker |
| `.log-progress` | Progress / timing message |
| `.log-thinking` | Agent thinking markers |
| `.error` | Error text (red) |
| `.success` | Success text (green) |

Log body (`#log-body`) uses `.nowrap` by default; toggle via wrap button.

---

## 6. Chat Panel (`#panel-chat`)

```
┌──────────────────────────────────────────────────┐
│  Chat: session-abc123  [sandbox] [web] [wrap]     │  ← .panel-header
├──────────────────────────────────────────────────┤
│                                                   │
│  You                                              │  .chat-msg.user
│  ┌────────────────────────────────────┐           │
│  │ Summarise the last log run         │           │  .msg-text
│  └────────────────────────────────────┘           │
│  12:03 · 12 tok                                   │  .msg-meta
│                                                   │
│                               Agent               │  .chat-msg.agent
│           ┌────────────────────────────────────┐  │
│           │ The last run completed at 12:01…   │  │  .msg-text
│           └────────────────────────────────────┘  │
│           12:04 · 148 tok                         │  .msg-meta
│                                                   │
└──────────────────────────────────────────────────┘
```

**Header controls** — all three are `button.kcui-tag.kcui-tag--dim` (square ends):
- `#chat-panel-title` — Current session ID
- `#sandbox-btn` (`sandbox-on` green / `sandbox-off` red) — Toggle sandbox execution mode
- `#webskills-btn` (`webskills-on` cyan / `webskills-off` dim) — Toggle web-access skill
- `#wrap-btn-chat` (`is-on` yellow) — Toggle line-wrap on `#chat-body`

**Message anatomy:**

```
.chat-msg.user / .chat-msg.agent
  .msg-role     ← "You" or "Agent"
  .msg-text     ← message content bubble
  .msg-meta     ← timestamp · token count
```

---

## 7. Input Bar (`#panel-input`)

```
┌───────────────────────────────────────────────────────┐
│  [ Type a prompt… (Enter to send, Shift+Enter newline) │  #chat-input
│                                                      ] │
│                                               [Send]   │  #send-btn
└───────────────────────────────────────────────────────┘
```

Spans the full width of the workspace (below all three column panels).

**Keyboard shortcuts:**
- `Enter` — Submit prompt
- `Shift+Enter` — Insert newline
- `↑` / `↓` — Navigate input history
- `Tab` — Open slash-command suggestion dropdown
- `Escape` — Close dropdown

---

## 8. Slash-Command Dropdown (`#slash-suggest`)

Fixed-position overlay, hidden until `/` is typed or `Tab` is pressed.

```
┌──────────────────────────────┐
│  /run                        │  ← suggestion item (active)
│  /status                     │
│  /clear                      │
└──────────────────────────────┘
```

Rendered by `app.js` (`_renderSuggest`). Dismissed on `Escape` or after selection.

---

## 9. CSS File Ownership

| File | Owns |
|---|---|
| `UIElements/assets/css/chrome.css` | Top bar, application bar, shared shell tokens |
| `UIElements/assets/css/workspace.css` | `.kcui-workspace` grid layout, splitters, regions |
| `static/style.css` | Panel headers, log-line classes, chat bubbles, input bar, wrap/sandbox button states |

---

## 10. JS Module Ownership

| Module | Owns |
|---|---|
| `app.js` | All client-side logic: SSE log stream, chat messages, timeline, input handling, slash commands, scroll controllers |
| `UIElements/…/chrome.js` | `initTopbar`, `initAppBar` — shell rendering |
| `UIElements/…/workspace.js` | `initWorkspaceLayouts` — drag-to-resize splitters, layout persistence |
