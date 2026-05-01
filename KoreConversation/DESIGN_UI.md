# KoreConversation UI Design

> Status: Active
> Date: 2026-05-01

---

## 1. Scope

This document defines the layout and UI structure of KoreConversation.

KoreConversation is the conversation-state management service. Its UI is a developer/operator debug console — it exposes a direct view into conversation records, messages, events, and state for inspection and manual intervention.

It exposes a single page: the conversation debug view.

Runtime architecture, database schema, agent read-process-write cycle, and API contracts remain in [DESIGN.md](DESIGN.md).

---

## 2. Shell

KoreConversation uses the shared KoreStack shell from UIElements.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Suite Top Bar  (#topbar)                                           │
│  KoreStack wordmark · service links · suite nav                     │
├─────────────────────────────────────────────────────────────────────┤
│  Application Bar  (#app-bar)                                        │
│  KoreConversation brand  │  status dot  │  API chip  │  filters     │
│                          │  [New] [Refresh] [Auto □]                │
└─────────────────────────────────────────────────────────────────────┘
```

**Top bar** — `initTopbar({ currentService: 'koreconversation' })`

**Application bar** — `initAppBar` with:
- `overline: 'Debug Console'`, `brandLabel: 'KoreConversation'`, `brandIcon: 'koreconversation'`
- `statusDot: { id: 'status-dot', className: 'off', title: 'API status' }` — live connection indicator, updated by JS
- `chips: [{ label: 'API', value: 'connecting...', valueId: 'status-label' }]` — API health text
- `actionsHtml` — inline HTML injected into the appbar actions zone:
  - Status filter dropdown (`#filter-status`) — all / awaiting_inbound / waiting_agent / agent_processing / archived / deleted
  - Channel filter dropdown (`#filter-channel`) — all / webchat / gmail / sms / whatsapp / manual
  - `#btn-new-conv` — Create new conversation
  - `#btn-refresh` — Reload conversation list
  - Auto-refresh checkbox (`#chk-auto`) — Polling toggle

Shell styling from `UIElements/assets/css/chrome.css`.

---

## 3. Main Layout

A horizontal two-panel layout with a draggable splitter.

```
┌──────────────────────┬──┬────────────────────────────────────────┐
│                      │  │                                        │
│  Sidebar             │▓▓│  Detail pane                           │
│  Conversation list   │  │  #detail-wrap                          │
│  #sidebar            │  │                                        │
│                      │  │  (empty state or conversation detail)  │
│                      │  │                                        │
└──────────────────────┴──┴────────────────────────────────────────┘
```

Container: `#main-grid`

CSS: `grid-template-columns: var(--sidebar-w) var(--splitter-w) 1fr`

Splitter (`#splitter`) — Draggable; JS updates `--sidebar-w` on drag and persists to localStorage.

---

## 4. Sidebar — Conversation List (`#sidebar`)

```
┌──────────────────────────────────┐
│  Conversations  [12]  [new]      │  ← .panel-header / #conv-count
├──────────────────────────────────┤
│  ● alice – webchat               │
│    awaiting_inbound · Apr 30     │  ← #conv-list items
│                                  │
│  ○ test – manual                 │
│    archived · Apr 29             │
└──────────────────────────────────┘
```

`#conv-list` — Rendered by JS. Each item shows:
- Conversation name / channel
- Status tag (color-coded by state) — rendered as `kcui-tag kcui-tag--pill`
- Timestamp
- Clicking an item loads the detail pane

**Status tags** (`kcui-tag kcui-tag--pill`):

| Status | `kcui-tag` color modifier | Example |
|---|---|---|
| `awaiting_inbound` | `--accent` | `<span class="kcui-tag kcui-tag--pill kcui-tag--accent">awaiting_inbound</span>` |
| `waiting_agent` | `--warning` | `<span class="kcui-tag kcui-tag--pill kcui-tag--warning">waiting_agent</span>` |
| `agent_processing` | `--info` | `<span class="kcui-tag kcui-tag--pill kcui-tag--info">agent_processing</span>` |
| `archived` | `--dim` | `<span class="kcui-tag kcui-tag--pill kcui-tag--dim">archived</span>` |
| `deleted` | `--danger` | `<span class="kcui-tag kcui-tag--pill kcui-tag--danger">deleted</span>` |

Message direction tags (also `kcui-tag kcui-tag--pill`):

| Direction/Role | `kcui-tag` color modifier |
|---|---|
| `inbound` | `--warning` |
| `outbound` | `--info` |
| `admin` | `--warning` |
| `external` | `--dim` |

Event/message status tags:

| Status | `kcui-tag` color modifier |
|---|---|
| `completed` | `--accent` |
| `pending` | `--warning` |
| `claimed` | `--info` |
| `failed` | `--danger` |

---

## 5. Detail Pane (`#detail-wrap`)

### Empty state (`#detail-empty`)

```
┌──────────────────────────────────────────────────┐
│                                                  │
│        Select a conversation to inspect it.      │
│                                                  │
└──────────────────────────────────────────────────┘
```

Shown until a conversation is selected.

### Conversation detail (`#detail`)

A vertically stacked list of collapsible sections. Each section has a `.section-header` with an icon badge, title, count/empty indicator, and optional action buttons.

```
┌──────────────────────────────────────────────────────────┐
│  [i] Metadata                    [rename] [delete]       │  #sec-meta
│  conv_abc123 · webchat · active · created Apr 30…        │  #meta-table .meta-grid
├──────────────────────────────────────────────────────────┤
│  [B] Background Context          (empty)                 │  #sec-bg
│  <pre>…</pre>                                            │  #bg-text .text-block
├──────────────────────────────────────────────────────────┤
│  [S] Thread Summary              (empty)                 │  #sec-summary
│  <pre>…</pre>                                            │  #summary-text .text-block
├──────────────────────────────────────────────────────────┤
│  [P] Scratchpad                  (empty)                 │  #sec-scratchpad
│  key │ value                                             │  #scratchpad-table .kv-table
├──────────────────────────────────────────────────────────┤
│  [H] Input History       [3]     (empty)                 │  #sec-history
│  1. "Hello"                                              │  #history-list .history-list
│  2. "Follow up"                                          │
├──────────────────────────────────────────────────────────┤
│  [+] Send Inbound Message                                │  #sec-compose
│  [inbound ▾]  [Type a message…_______]  [send]          │  #compose-row
├──────────────────────────────────────────────────────────┤
│  [M] Messages  [5]               □ show summarised       │  #sec-messages
│  …message rows…                                          │  #messages-body
├──────────────────────────────────────────────────────────┤
│  [E] Events    [12]                                      │  #sec-events
│  …event rows…                                            │  #events-body
└──────────────────────────────────────────────────────────┘
```

**Section anatomy:**

```
.section
  .section-header
    .section-icon       ← single-char icon badge
    section title
    .count-badge        ← item count (when non-zero)
    .empty-badge        ← "(empty)" hint (when zero)
    .flex-gap           ← pushes action buttons right
    .section-btn        ← text action buttons (rename, delete, etc.)
  section body content
```

**Section action buttons** (`.section-btn`):
- Default: subtle border, lowercase monospace
- `.section-btn-danger` — red border for destructive actions (delete)

**Compose row** (`#compose-row`):
- Direction select (`#compose-direction`) — inbound / outbound
- Message input (`#compose-text`) — Enter or Send button triggers POST
- `#compose-btn` — Send

**Messages toggle** — `#chk-summarised` checkbox shows/hides summarised messages on change.

---

## 6. CSS File Ownership

| File | Owns |
|---|---|
| `UIElements/assets/css/chrome.css` | Top bar, application bar, shared shell tokens, `kcui-tag` component |
| `app/ui/conversations.css` | Full page layout, sidebar, splitter, detail pane, section styles, compose row, message/event rows |

---

## 7. JS File Ownership

| File | Owns |
|---|---|
| `app/ui/conversations.js` | All client logic: conversation list fetch, detail render, message/event rendering, compose, SSE push, splitter drag, auto-refresh, localStorage persistence |
| Inline `<script type="module">` in HTML | `initTopbar`, `initAppBar` — shell initialization |
