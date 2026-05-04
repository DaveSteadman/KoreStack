# KoreData UI Design

> Status: Active
> Date: 2026-05-01

---

## 1. Scope

This document defines the layout and UI structure of KoreData.

KoreData is the data service suite — it aggregates RSS/web feeds, library books, reference articles, and operator-curated RAG chunks, and exposes them through a single gateway interface.

The UI is server-rendered via FastAPI + Jinja2 templates. All pages extend a shared `base.html`.

The active UI surfaces are:

| Section | URL prefix | Purpose |
|---|---|---|
| Home | `/ui` | Service health overview + unified search |
| Feeds | `/ui/feeds` | RSS/web feed domains and entry browser |
| Library | `/ui/library` | Long-form book browser |
| Reference | `/ui/reference` | Encyclopedia/wiki article browser |
| RAG | `/ui/rag` | Operator-curated RAG chunk management |
| Agent search API | `/api/search` | POST — unified cross-service search for agents/MCP |
| RAG JSON API | `/api/rag/...` | CRUD REST API for RAG chunks |
| Feed rate API | `/api/feeds/...` | PATCH — update feed refresh rate |
| MCP server | `/mcp` | Streamable HTTP MCP transport |

Runtime architecture, service boundaries, and API contracts remain in [DESIGN.md](DESIGN.md).

---

## 2. Shell

KoreData uses the shared KoreStack shell from UIElements. The shell is initialized in every page via `base.html`.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Suite Top Bar  (#topbar)                                           │
│  KoreStack wordmark · service links · suite nav                     │
├─────────────────────────────────────────────────────────────────────┤
│  Application Bar  (#app-bar)                                        │
│  KoreData brand  │  ● status dot  │  Role: Gateway                  │
│  [Home] [Feeds] [Library] [Reference] [RAG] [API Docs ↗]           │
└─────────────────────────────────────────────────────────────────────┘
```

**Top bar** — `initTopbar({ currentService: 'koredata' })`

**Application bar** — `initAppBar` with:
- `overline: 'Data Service'`, `brandLabel: 'KoreData'`, `brandIcon: 'koredata'`
- `statusDot: { id: 'hdr-dot', className: 'dot-live', title: 'checking...' }` — pulsed green when `/status` returns 200, red when offline; polled every 15 seconds
- `chips: [{ label: 'Role', value: 'Gateway' }]` — static role label
- `tabs` — navigation tabs for each section; active tab driven by `request.url.path` in Jinja2

Shell styling from `UIElements/assets/css/chrome.css`. All other styling is inline in `base.html`.

---

## 3. Shared Page Frame

All pages use the same frame from `base.html`:

```
┌──────────────────────────────────────────────────────────────┐
│  #topbar                                                     │
├──────────────────────────────────────────────────────────────┤
│  #app-bar                                                    │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  <main>  {% block content %}                                 │
│  max-width: 1320px; margin: 0 auto; padding: 1.5rem 2rem     │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

Pages that need full-width layout (no max-width) add `class="kcui-main"` to `<main>`, which strips the default padding and width constraint.

---

## 4. Home Page (`/ui`)

The landing surface. Two zones: service health cards at the top, unified search below.

```
┌────────────────────────────────────────────────────────────────┐
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ Feeds    │  │ Library  │  │Reference │  │  RAG     │       │  .grid / .domain-card
│  │ ● online │  │ ● online │  │ ○ offline│  │ ● online │       │
│  │ 12 dom   │  │ 84 books │  │ 312 arts │  │ 48 chunks│       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
├────────────────────────────────────────────────────────────────┤
│  Search  ┌──────────────────────────────────────────────────┐  │
│          │ query                                            │  │
│          ├──────────────────────────────────────────────────┤  │
│          │ Domain filter  │ Since [date]  │ Until [date]    │  │
│          │ Limit [50 ±]                                     │  │
│          └──────────────────────────────────────────────────┘  │
│                                                   [Search]     │
├────────────────────────────────────────────────────────────────┤
│  Results                                                       │
│  [Cards ▸]  [JSON ▸]   ← tab switcher                         │
│                                                                │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Headline                                                │  │
│  │  domain · feed · Apr 30                                  │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

**Service health grid** (`.grid`) — Responsive auto-fill, min 180 px per card. Each `.domain-card` shows service name, live status, and entry count.

**Search form** — Query text, domain filter, date range (`.date-pick` widgets), result limit (`.num-stepper`).

**Results** — Tab-switched between card view and raw JSON view.

---

## 5. Feeds (`/ui/feeds`)

### Feed Index

```
┌──────────────────────────────────────────────────────────────────┐
│  Domains                                              [+ Domain] │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                        │
│  │ news     │  │ tech     │  │ finance  │  …                     │  .grid / .domain-card
│  │ 4 feeds  │  │ 7 feeds  │  │ 3 feeds  │                        │
│  └──────────┘  └──────────┘  └──────────┘                        │
├──────────────────────────────────────────────────────────────────┤
│  Feed  │  Domain  │  URL  │  Rate (min)  │  Last fetch  │  State │
│  ─────────────────────────────────────────────────────────────── │
│  BBC   │  news    │  …    │  [30 ±]      │  Apr 30 12:00│  ● ok  │
│  …                                                               │
└──────────────────────────────────────────────────────────────────┘
```

Domain cards link into per-domain views. Feed table shows all feeds with inline rate steppers (`.rate-stepper` / `.num-stepper`) for editing poll interval in-place.

### Feed Search

```
┌──────────────────────────────────────────────────────────────────┐
│  Query [___________]  Since [date]  Until [date]  Limit [50 ±]  │
│                                                       [Search]   │
├──────────────────────────────────────────────────────────────────┤
│  □  │  ID   │  Headline  │  Domain  │  Feed  │  Published        │
│  □  │  1234 │  …         │  news    │  BBC   │  Apr 30           │
│  …                                                               │
│  [Delete selected]                                               │
└──────────────────────────────────────────────────────────────────┘
```

Bulk-delete via row checkboxes. Results table with sortable columns.

---

## 6. Library (`/ui/library`)

### Library Index

The Library index opens with a catalog overview row, then the book search and listing below it. This gives the user an immediate picture of how the collection is organised before they start filtering.

```
┌──────────────────────────────────────────────────────────────────┐
│  Catalogs                                                        │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐                 │
│  │ ancient    │  │ shakespeare│  │ dickens    │  …              │  .grid / .domain-card
│  │ 84 books   │  │ 42 books   │  │ 18 books   │                 │
│  └────────────┘  └────────────┘  └────────────┘                 │
│  Clicking a card sets the Catalog filter below.                  │
├──────────────────────────────────────────────────────────────────┤
│  Title [___]  Author [___]  Year [___]  Language [___]           │
│  Genre [___]  Catalog [all ▾]  Limit [50 ±]         [Search]    │
├──────────────────────────────────────────────────────────────────┤
│  Catalog    │  ID  │  Title              │  Author     │  Year   │
│  local      │  1   │  The Great Gatsby   │  Fitzgerald │  1925   │
│  ancient    │  42  │  The Iliad          │  Homer      │  -750   │
│  …                                                               │
│  [← Prev]  Page 1 of 8  [Next →]                                 │
└──────────────────────────────────────────────────────────────────┘
```

- **Catalog cards** (`.domain-card`) — one card per catalog returned by `GET /catalogs`, showing the catalog ID and book count. Clicking a card populates the Catalog filter and submits the search.
- **Catalog filter** — `<select>` populated from `GET /catalogs`; defaults to `all` (search across all catalogs). The selected value is carried through pagination.
- **Catalog column** — every row in the results table shows the source catalog so books with the same title in different catalogs are not ambiguous.
- **Creating a catalog** — there is no separate create-catalog action. A new catalog is created automatically the first time a book is added to it (via `POST /books` with a new `catalog` value). Until that first book is added, the catalog does not exist and will not appear in the Catalog filter.
- Incomplete records shown with `.missing` (amber) badges on empty metadata fields.

### Book Detail

```
┌──────────────────────────────────────────────────────────────────┐
│  The Great Gatsby                          Catalog: local  [▸]  │
│  F. Scott Fitzgerald · 1925 · English · Fiction · 47,094 words  │
│  Notes: …                                                        │
│                                              [Move to catalog ▾] │
├──────────────────────────────────────────────────────────────────┤
│  .book-md (markdown-rendered body via marked.js)                 │
│  Chapter 1 heading                                               │
│  Body text…                                                      │
└──────────────────────────────────────────────────────────────────┘
```

- **Catalog badge** — top-right of the header; shows which catalog the book lives in. Clicking it sets the catalog filter on the index and navigates back, so the user can browse siblings in the same catalog.
- **Move to catalog** — dropdown button populated with all other known catalogs; selecting one calls `POST /books/{id}/move` and redirects to the book's new URL (`/{new_catalog}:{new_id}`).

Book body rendered from markdown using `marked.js`. Table of contents anchor links scroll the page. Styled via `.book-md` (monospace, generous line height, green headings).

---

## 7. Reference (`/ui/reference`)

### Reference Index

```
┌──────────────────────────────────────────────────────────────────┐
│  Search [_______________]                    [Search] [+ Article] │
├──────────────────────────────────────────────────────────────────┤
│  ID  │  Title            │  Words                                 │
│  12  │  Python (lang)    │  2,840                                 │
│  …                                                               │
│  [← Prev]  Page 1 of 3  [Next →]                                 │
└──────────────────────────────────────────────────────────────────┘
```

### Article Detail

```
┌──────────────────────────────┬───────────────────────────────────┐
│  Article title               │  Facts                            │
│                              │  Founded: 1991 · Type: language   │
│  Summary paragraph…          ├───────────────────────────────────┤
│                              │  Categories                       │
│  ## Section heading          │  Programming · Open Source        │
│  Body text…                  ├───────────────────────────────────┤
│                              │  Backlinks  (3)                   │
│  (wikilinks [[Name]] are     │  · Django                         │
│   rendered as links)         │  · NumPy                          │
│                              ├───────────────────────────────────┤
│                              │  Links  (lazy-loaded)             │
│                              │  · python.org                     │
└──────────────────────────────┴───────────────────────────────────┘
```

Two-column layout (`.two-col`, collapses at 900 px). Main column: title, summary, sections. Right sidebar: facts table, categories, backlinks, outbound links (fetched lazily via `fetch`).

---

## 8. RAG (`/ui/rag`)

### RAG Index

```
┌──────────────────────────────────────────────────────────────────┐
│  RAG Chunks                          [+ Insert]  [Search]        │
├──────────────────────────────────────────────────────────────────┤
│  ID  │  Title          │  Source  │  Tags  │  Words  │  Created  │
│  1   │  Pricing guide  │  manual  │  sales │  312    │  Apr 28   │
│  …                                                               │
│  [← Prev]  Page 1 of 2  [Next →]                                 │
└──────────────────────────────────────────────────────────────────┘
```

### RAG Insert

```
┌──────────────────────────────────────────────────────────────────┐
│  Title  [______________________________]                         │
│  Source [______________________________]                         │
│  Tags   [______________________________]                         │
│  Content                                                         │
│  [                                                            ]  │
│  [                                                            ]  │
│                                                  [Insert Chunk]  │
├──────────────────────────────────────────────────────────────────┤
│  Bulk API                                                        │
│  POST /api/rag/chunks                                            │
│  { "title": "…", "source": "…", "tags": ["…"], "content": "…" } │
└──────────────────────────────────────────────────────────────────┘
```

---

## 9. Shared UI Components

All pages inherit these components from `base.html` inline styles:

| Component | Class(es) | Usage |
|---|---|---|
| Panel / card | `.panel`, `.panel-header`, `.panel-body` | Section groupings |
| Domain card | `.domain-card`, `.dc-name`, `.dc-count` | Service health + domain grids |
| Two-column grid | `.two-col` | Reference article detail |
| Auto-fill grid | `.grid` | Service health cards, domain lists |
| Table | `table`, `th`, `td` | All data listings |
| Badge | `.badge`, `.badge-dim`, `.missing` | Status, type labels, missing fields |
| Button | `button`, `.btn`, `.btn-danger`, `.btn-sm`, `.btn-secondary` | Actions |
| Form controls | `input`, `select`, `textarea` | All filter and edit forms |
| Number stepper | `.num-stepper`, `.stepper-btns` | Numeric input with ± buttons |
| Date picker | `.date-pick`, `.cal-icon` | Date range filters |
| Pagination | `.pagination` | Page navigation (prev/next/info) |
| Status dot | `.dot-live`, `.dot-live.online` | Live connection indicator |
| Metadata row | `.kv`, `.kv-key`, `.kv-val` | Key-value detail display |
| Pre / scrollable body | `pre`, `.scrollable-body` | Formatted text blocks |
| Markdown body | `.book-md` | Rendered library book content |

---

## 10. CSS Ownership

| Source | Owns |
|---|---|
| `UIElements/assets/css/chrome.css` | Top bar, application bar, shared shell tokens |
| `base.html` inline `<style>` | All layout, components, typography, form controls, colors |

There are no separate CSS files for KoreData pages — all styles are consolidated in the `base.html` `<style>` block and extended per page via Jinja2 `{% block %}` overrides where needed.
