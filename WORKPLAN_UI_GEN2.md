# UI Gen2 Workplan

## Background - The Problems Being Solved

A series of design discussions (May 2026) identified recurring friction in the KoreStack UI:

1. **Height inconsistency.** `kcui-tag` (small, ~18–20px), `btn-sm`, `kg-nav-btn` (1.75rem), and `input` all have different heights. Placing them in the same row requires manual fudging per page.

2. **Semantic overloading of `kcui-tag`.** The tag component was designed as a label/badge but has been used as an action button (Import Another, View Articles, Search ↗). Once a label doubles as a button, sizing and hierarchy fights start.

3. **No distinction between consequential controls and view modifiers.** In KoreAgent's panel headers, `sandbox / web / LLM-DIRECT` (mode selectors - major functional impact) and `wrap / live` (display tweaks - trivial, incidental) are styled identically. There is no visual grammar to separate them.

4. **Ad-hoc panel header composition.** Each page assembles its own header row independently, with no shared pattern for title / mode group / view tweaks.

5. **No canonical layout test.** Problems are discovered page-by-page rather than caught in a single reference layout.

6. **Python vs. Jinja inconsistency.** Some pages are built in pure Python (string concatenation), others in Jinja2 templates. There is no enforced standard.

---

## Conclusions from Discussion

### `kcui-tag` is v1 — Element2 replaces it
`kcui-tag` was the first-generation annotation component. In Element2 it is replaced by two things depending on how it was being used:
- **annotation/badge** (read-only label, status, count): use `e2-label`. Same slot height as every other control.
- **action button** (Import Another, View Articles, mode toggle): use `e2-btn` or `e2-seg`.

There is no sub-slot tag component in Element2. All controls share `--e2-h`. All `kcui-tag` uses are migration targets — none are permanent.

### View-modifier controls belong on their target, not in the header
Controls that modify *how a panel displays its content* (`wrap`, `live`) should be **corner overlays on the panel body**, not header buttons. This creates unambiguous spatial association and frees the panel header for identity + mode controls only. A reusable `.panel-overlay-controls` pattern will be defined in UIElements.

### Grouping, not height, resolves the mode vs. tweak problem
Mutually exclusive mode selectors (`sandbox / web / LLM-DIRECT`) should be visually grouped as a unit (a segmented control idiom: tight gap, shared boundary, radio-button behaviour). This communicates their relationship without adding row height.

### One panel layout — KoreAgent-style full-page split panes
All pages use a single layout model: full-page, with movable divider bars creating resizable panes. Two legacy patterns are retired:

- **Fully fitted** (KoreCode): viewport-locked, no scroll — replaced by a clamped split-pane layout
- **Organised sections** (KoreFeeds/KoreData): stacked scrolling sections — replaced by split panes where each pane scrolls independently

Within the KoreAgent model, two variants are permitted:
- **Clamped**: the outer container is locked to the viewport height. Used when a persistent bar (chat input, command bar) must stay at the bottom.
- **Free**: individual panes may grow beyond viewport height and scroll. Used for content-heavy read panels.

The divider position may be persisted per-page in `sessionStorage`. No page invents a third model.

### One canonical control height — `--e2-h`
All interactive form controls (input, button, select, kg-nav-btn) should reference a single CSS variable `--e2-h: 1.75rem`. Tags remain smaller. This eliminates row-alignment fudging.

---

## Element2 — Concept

**Element2** is a second-generation UI element specification. The core idea: every control, label, or interactive element occupies a **standard-height slot** with defined internal padding. All components derive from this slot.

### The slot
```
height:       var(--e2-h)          /* single canonical height, default 1.75rem */
padding:      var(--e2-pad-v) var(--e2-pad-h)
font-size:    var(--e2-font)
line-height:  1
border-radius: var(--e2-radius)    /* theme-level variable; shape is NOT per-component */
box-sizing:   border-box
```

Corner shape (`--e2-radius`) is a theme-level decision, set once, applied uniformly. Component names encode **what a thing is** - not how it is drawn. An `e2-btn` is an interactive action trigger; an `e2-label` is a non-interactive descriptor. Whether any of them has square, softened, or pill corners is a style detail that can change without touching HTML.

### Components derived from the slot
| Component | Element | Semantic role |
|---|---|---|
| `e2-btn` | `<button>` | Interactive action trigger; full slot height, variable width |
| `e2-iconbtn` | `<button>` | Interactive action trigger; square slot (width = height), glyph only |
| `e2-input` | `<input>` | Text entry; full slot height, flex-grow in row |
| `e2-select` | `<select>` | Option selector; full slot height |
| `e2-number` | `<input type=number>` | Numeric entry; full slot height, fixed width |
| `e2-slider` | `<input type=range>` | Range selector; vertically centred in slot |
| `e2-label` | `<span>` or `<label>` | Non-interactive descriptor; inline, vertically centred |
| `e2-seg` | `<div>` + `<button>` children | Segmented control; mutually exclusive option group; all children share slot height |

### What stays outside Element2
- `.panel-overlay-controls` - corner-overlay view tweaks, positioned absolutely
- **Navigation chrome** (Topbar 1, Topbar 2, Topbar 3) - defined below; share a single `--bar-h` variable, not `--e2-h`

---

## Navigation Chrome

Three stacked bars appear at the top of every page. All three share the same height (`--bar-h`) and are defined entirely in UIElements - no per-page overrides.

```
┌─────────────────────────────────────────────────────────────────┐
│ Topbar 1   [icon] KoreData    [icon] KoreAgent  ···      v1.2.3 │  cross-service
├─────────────────────────────────────────────────────────────────┤
│ Topbar 2   [icon] Feeds       [icon] Library    ···             │  within-service
├─────────────────────────────────────────────────────────────────┤
│ Topbar 3     Sources    Categories    Tags       ···            │  within-section
└─────────────────────────────────────────────────────────────────┘
```

Not every page has all three bars - only the bars that apply to its depth are rendered.

### Topbar 1 - cross-service
Identical across every page in every service. Primary trust signal that the user is in the KoreStack family.

| Slot | Element | Role |
|---|---|---|
| `.topbar1` | `<nav>` | Full-width fixed bar; height `--bar-h`; consistent background across all services |
| `.topbar1-btn` | `<a>` | Service entry: icon + name + subtitle tagline; service color token on active underline; `is-active` state |
| `.topbar1-version` | `<span>` | Right-aligned version string; non-interactive |

- Each service registers a color token (e.g. `--svc-color-data`, `--svc-color-agent`). The active underline uses that service's token.
- Topbar 1 **never** contains page-level controls. Identity and navigation only.

### Topbar 2 - within-service
Shows the major sections within the current service. Uses the current service's color scheme.

| Slot | Element | Role |
|---|---|---|
| `.topbar2` | `<nav>` | Full-width fixed bar; height `--bar-h`; service color background or tint |
| `.topbar2-btn` | `<a>` | Section entry: icon + name + subtitle tagline; `is-active` = underline |

### Topbar 3 - within-section
Sub-section navigation. Minimal - names and active underline only.

| Slot | Element | Role |
|---|---|---|
| `.topbar3` | `<nav>` | Full-width fixed bar; height `--bar-h` |
| `.topbar3-btn` | `<a>` | Sub-section entry: name only, no icon, no subtitle; `is-active` = underline |

---



## Common rules

### Row composition rule
An `e2-row` is a flex row with `gap: var(--e2-gap)` and `align-items: stretch`. Every direct child is either an Element2 component (same height) or a spacer. No mixing of `kcui-tag` buttons and `e2-btn` in the same row.

### Colour / state vocabulary
Element2 buttons adopt the existing colour modifier vocabulary (`--accent`, `--muted`, `--dim`, `--info`, `--warning`, `--danger`) so the colour system is reused, not replaced.

We need buttons to be a consistent color when in a diabled state, multicolored in helping highlight their individuality, and variations on that color to support transparent fill and hover/click changes.

---

## Workplan

### Phase 0 - Fork UIElements and build test page

**Step 1 - Fork**
- [ ] Copy `UIElements/` to `UIElements2/` — full copy of all CSS, fonts, assets, and docs
- [ ] `UIElements/` is **permanently frozen**. No edits, ever. It continues serving existing pages unchanged throughout the entire transition.
- [ ] Update the `UIElements2/` served path to `/ui-elements-2/` (or equivalent) so test pages can reference it independently

**Step 2 - Define variables** (in `UIElements2/assets/css/tokens.css`)
- [ ] Add `--e2-h`, `--e2-pad-v`, `--e2-pad-h`, `--e2-font`, `--e2-gap`, `--e2-radius`, `--bar-h` alongside existing `kcui-` tokens

**Step 3 - Test page**
- [ ] Build `UIElements2/docs/element2.html` - a standalone test/reference page showing:
  - All Element2 components in isolation (button, icon-button, input, select, number, slider, label, segmented control)
  - Mixed rows: input + button, input + icon-button + button
  - Panel with split-pane layout (clamped variant and free variant)
  - Panel overlay controls example
  - Topbar 1/2/3 stack example
  - Dark theme only
- [ ] Debug all alignment, font, and spacing issues in the test page before touching live pages

### Phase 1 - UIElements2 library additions
- [ ] Add `navbar.css` to `UIElements2/assets/css/`: `--bar-h`, `.topbar1`, `.topbar1-btn`, `.topbar1-version`, `.topbar2`, `.topbar2-btn`, `.topbar3`, `.topbar3-btn`, per-service color tokens
- [ ] Build `UIElements2/docs/navbar.html` - reference page showing Topbar 1/2/3, active states, version label
- [ ] Add `element2.css` to `UIElements2/assets/css/` (all controls at `--e2-h`; no sub-slot components)
- [ ] Update `UIElements2/README.md`: Element2 usage rules; `kcui-tag` migration note (`kcui-tag` → `e2-label` or `e2-btn`); navbar usage rules

### Phase 2 - KoreGraph (lowest complexity, already partially modernised)
- [ ] Replace `kg-nav-btn` definition with `--e2-h`-based reference
- [ ] Replace `btn-sm` uses with `e2-btn` or `e2-iconbtn`
- [ ] Move any view-modifier tags to panel overlay pattern

### Phase 3 - KoreDataGateway
- [ ] Search row: `input + SEARCH + e2-btn` - align to `--e2-h`
- [ ] `kcui-tag` → `e2-label` for all status/count annotations; `kcui-tag` → `e2-btn` for all action uses
- [ ] Verify `decodeHtml` + `escHtml` rendering covers all result card fields

### Phase 4 - KoreAgent
- [ ] Panel headers: title + segmented mode group + (move wrap/live to overlay)
- [ ] Input bar: textarea + send controls - align to Element2 row rules
- [ ] Log panel: overlay controls (live, wrap, ↑ ↓)

### Phase 5 - Remaining services
- KoreChat, KoreDocs, KoreCode, KoreComms, KoreData sub-pages
- Apply Element2 row pattern to all form rows and panel headers

### Phase 6 - Python / Jinja consistency pass
- [ ] Audit which pages are pure Python string output vs. Jinja2 templates
- [ ] Establish rule: all pages with dynamic data use Jinja2; utility/config pages may use Python
- [ ] Extract shared macros (panel header, e2-row, result card) into Jinja2 macro files where reused across more than one template
- [ ] Ensure all services import from `UIElements2/` with a consistent relative path (no inline copies)

### Phase 7 - UIElements retirement
- [ ] Verify zero remaining references to `/ui-elements/` across all services
- [ ] Delete `UIElements/` directory
- [ ] Consider whether `UIElements2/` should be renamed (e.g. back to `UIElements/`) — decision deferred until this point

---

## Known Debt to Address During Rework

> All `kcui-tag` uses are v1 debt. The list below tracks them by page/phase.

**Layout migrations (Phase 5)**
- KoreCode: fully-fitted viewport layout — migrate to clamped split-pane
- KoreFeeds / KoreData sub-pages: organised scrolling sections — migrate to free split-pane
- All pages: add `sessionStorage` divider-position persistence where absent

**Control migrations**
- `reference_import.html`: `Import Another` / `View Articles` use `kcui-tag` as action buttons — migrate to `e2-btn` during Phase 3
- `connections.html`: state filter links use `kcui-tag` toggle-state — migrate to `e2-btn is-active` during Phase 2
- `btn-sm`: ad-hoc small button class used across KoreGraph — migrate to `e2-btn` or `e2-iconbtn` during Phase 2
- KoreAgent `log-btn-up/down`: currently `kcui-tag` scroll buttons - migrate to `e2-iconbtn` overlay during Phase 4
- KoreAgent mode selectors: `kcui-tag` as `<button>` - migrate to `e2-seg` during Phase 4
- Home search row (`sq-domain` checkboxes, date filters, limit input): inconsistent sizing - full Element2 row pass during Phase 3

---

## Open Questions

- Should `e2-label` replace the current `<label>` styling in base.html, or coexist?
- Corner overlay: should `live` (toggled frequently) have a slightly larger hit target than `wrap` (set-and-forget)?
- Slider component: does any current page use `<input type=range>`? If not, deprioritise.
