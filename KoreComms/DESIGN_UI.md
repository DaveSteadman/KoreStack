# KoreComms UI Design

> Status: Active
> Date: 2026-05-01

---

## 1. Scope

This document defines the layout and UI structure of KoreComms.

KoreComms is the communication hub — it manages incoming and outgoing messages across multiple interfaces (Gmail, manual injection, etc.) and exposes them for agent interaction.

It is server-rendered via FastAPI + Jinja2. There is no SPA layer; each page is a full HTML response.

Runtime architecture, interface connectors, and delivery contracts remain in [DESIGN.md](DESIGN.md).

---

## 2. Shell

KoreComms uses the shared UIElements shell, initialized in `base.html`.

```
┌─────────────────────────────────────────────────────────────────┐
│  Suite Top Bar  (#topbar)                                       │
├─────────────────────────────────────────────────────────────────┤
│  Application Bar  (#app-bar)                                    │
│  KoreComms brand  │  Conversations Compose Connections Activity │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   <main>  {% block content %}                                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**Top bar** — Initialized by `initTopbar({ currentService: 'korecomms' })`.

**Application bar** — Initialized by `initAppBar(...)` and provides app identity + primary tabs.

**Local nav/header styles** — Used within page content areas, not as a replacement for the suite shell.

All pages extend `base.html` via `{% block content %}`.

---

## 3. Page Index

| URL | Template | Purpose |
|---|---|---|
| `/` | `home.html` | Conversation list |
| `/conversation/{id}` | `chat.html` | Active thread + reply |
| `/compose` | `compose.html` | Inject test message |
| `/connections` | `connections.html` | Interface management |
| `/connections/new?type={t}` | `connection_edit.html` | Add/edit interface |
| `/activity` | `activity_log.html` | Agent action log |

---

## 4. Home — Conversation List (`/`)

```
┌──────────────────────────────────────────────────────────────────┐
│  KoreChat ID  │  Interface  │  Name  │  Started  │  Act  │
├──────────────────────────────────────────────────────────────────┤
│  conv_abc123          │  [gmail]    │  Alice  │  Apr 30   │  →   │
│  conv_def456          │  [manual]   │  Test   │  Apr 29   │  →   │
│  …                                                               │
├──────────────────────────────────────────────────────────────────┤
│                                    [← Prev page]  [Next page →]  │
└──────────────────────────────────────────────────────────────────┘
```

Standard `<table>` with header row. Each row links to the conversation thread via the ID and an `→` action button.

**Interface badge** — `kcui-tag` color-coded by interface type: `gmail` → `--info`, `manual` → `--dim`.

**Pagination** — `?offset=N` links render when more than 50 conversations exist.

---

## 5. Chat — Conversation Thread (`/conversation/{id}`)

Full-screen layout (`min-height: 100dvh`). Three zones stacked vertically.

```
┌──────────────────────────────────────────────────────────────┐
│  ← Messages  │  conv_abc123  [gmail]  [Delete]               │  .chat-header
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  You                                                         │
│  ┌──────────────────────────────────┐                        │
│  │  Hi, can you check the report?   │   .bubble-row.outbound │
│  └──────────────────────────────────┘                        │
│  alice@example.com · Apr 30 14:30 · [read]                   │
│                                                              │
│                             Agent                            │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Sure, I'll review it now…                             │  │  .bubble-row.inbound
│  └────────────────────────────────────────────────────────┘  │
│  KoreAgent · Apr 30 14:31 · [sent]                           │
│                                                              │
│  .chat-messages (scrollable, auto-scrolls to bottom)         │
├──────────────────────────────────────────────────────────────┤
│  Agent: idle                                                 │  .chat-statusbar / #agent-status
├──────────────────────────────────────────────────────────────┤
│  ┌──────────────────────────────────────────────────────┐    │
│  │  Type a reply…                                       │    │  .chat-compose
│  └──────────────────────────────────────────────────────┘    │
│                                               [Send]         │
└──────────────────────────────────────────────────────────────┘
```

**Sub-header** (`.chat-header`):
- Back link (`← Messages`) to `/`
- Conversation ID (`.chat-subj`)
- Interface `kcui-tag` (type badge)
- Delete button (POST to `/conversation/{id}/delete`)

**Messages** (`.chat-messages`) — Scrollable; auto-scrolls to bottom on load.

**Bubble rows**:
- `.bubble-row.outbound` — Messages from external contacts; left-aligned
- `.bubble-row.inbound` — Messages from KoreAgent; right-aligned, amber-tinted background

**Bubble anatomy:**
```
.bubble  (max-width 65%, border)
  .bubble-content   ← message text
  .bubble-meta
    .bm-sender      ← sender name or address
    timestamp
    kcui-tag        ← message status (`--accent` replied/sent, `--warning` queued, `--info` processing, `--dim` ignored)
```

**Status bar** (`.chat-statusbar`) — `#agent-status` reflects live delivery/agent state from the event stream.

**Composer** (`.chat-compose`, sticky bottom) — `<textarea>` + Submit button. POSTs to the conversation reply endpoint.

---

## 6. Compose — Inject Test Message (`/compose`)

Simple form page for injecting synthetic inbound messages into the queue for testing.

```
┌─────────────────────────────────────────────┐
│  Sender                                     │
│  [Alice <alice@example.com>_____________]   │
│                                             │
│  KoreChat ID                        │
│  [conv_abc123____________________________]  │
│                                             │
│  Message                                    │
│  [                                       ]  │
│  [                                       ]  │
│  [                                       ]  │
│                                             │
│  [→ Inject to Queue]  [Cancel]              │
└─────────────────────────────────────────────┘
```

POST to `/compose`. Required fields: `sender`, `korechat_id`, `content`.

---

## 7. Connections — Interface Management (`/connections`)

```
┌────────────────────────────────────────────────────────────────────┐
│  ID      │ Type     │ Name          │ On │ Created    │ Actions    │
├────────────────────────────────────────────────────────────────────┤
│  iface-1 │ [gmail]  │ My Gmail      │  ● │ 2026-04-01 │ Edit  Del  │
│  iface-2 │ [manual] │ Test channel  │    │ 2026-04-15 │ Edit  Del  │
├────────────────────────────────────────────────────────────────────┤
│  [+ Gmail]  [+ Manual]                                             │
└────────────────────────────────────────────────────────────────────┘
```

Standard `<table>`. Enabled status shown as `●` in `--green` when active.

Add-interface buttons at the bottom: `+ Gmail`, `+ Manual` link to `/connections/new?type={type}`.

---

## 8. Connection Edit — Add/Configure Interface (`/connections/new`)

Form page. Fields vary by interface type.

**Common fields:**
- Connection Name (text)
- Poll Interval in seconds (number)
- Enabled checkbox

**Gmail-specific additional fields:**
- Google OAuth Client ID
- Google OAuth Client Secret
- Redirect URI hint (read-only)
- Authorize Gmail button (POST to `/connections/{id}/gmail-authorize`)

---

## 9. Activity Log (`/activity`)

Read-only table of the last 200 agent actions, newest first.

```
┌──────────────────────────────────────────────────────────────┐
│  Timestamp          │  Action          │  Detail             │
├──────────────────────────────────────────────────────────────┤
│  2026-04-30 14:31   │  [forwarded]     │  conv_abc           │
│  2026-04-30 14:30   │  [polled]        │  gmail              │
└──────────────────────────────────────────────────────────────┘
```

No pagination control — fixed at 200 rows.

**Action tags** (`kcui-tag kcui-tag--pill`):

| Action | `kcui-tag` color modifier |
|---|---|
| `forwarded` | `--info` |
| `routed` | `--accent` |
| `fetched` | `--info` |
| `replied` | `--accent` |
| `completed` | `--accent` |
| `sent` / `send_new` | `--warning` |
| `injected` | `--warning` |
| `deleted` | `--danger` |
| `polled` | `--dim` |
| *(any other)* | `--dim` |

---

## 10. Shared Components

All pages in `base.html` share these component classes:

| Class | Renders as |
|---|---|
| `.panel` | Card with border |
| `.panel-header` | Sticky section heading within a card |
| `.panel-body` | Scrollable card content area |
| `.btn` | Standard action button |
| `.btn.btn-primary` | Accent-colored primary action |
| `.btn.btn-danger` | Red destructive action |
| `.btn.btn-sm` | Compact button for table rows |
| `kcui-tag` | Color-coded type/status/action tag (from UIElements `chrome.css`) |

---

## 11. CSS Ownership

| File | Owns |
|---|---|
| `/ui-elements/assets/css/chrome.css` | Shared design tokens (colors, fonts, spacing), `kcui-tag` component |
| `base.html` inline `<style>` | Header, nav, panel, table, form, button — all shared components |
| `chat.html` inline `<style>` | Chat-specific: `.chat-header`, `.chat-messages`, `.bubble-row`, `.bubble`, `.chat-statusbar`, `.chat-compose` |
