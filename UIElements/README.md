# UIElements

## Overview

UIElements is the shared UI shell layer for the Kore suite. It is not a runnable process — it is a static asset
library served by the KoreStack host and consumed by every Kore microservice.

Each app loads the shell via two includes:

```html
<link rel="stylesheet" href="/ui-elements/assets/css/chrome.css">
<script type="module" src="/ui-elements/assets/js/chrome.js?v=20260501a"></script>
```

`chrome.css` and `chrome.js` are the single entry points. They re-export everything below so apps never need
to import individual files.

**Rule:** No UIElement component should be duplicated inside an application's own CSS or JS. UIElements takes
precedence. If an app needs a variant, it should be added here.

---

## Asset Structure

```
UIElements/assets/
  css/
    chrome.css        ← bundle: @imports all CSS below
    tokens.css        ← design tokens (colors, typography, spacing, radii)
    topbar.css        ← top bar shell
    appbar.css        ← application bar shell
    app-menu.css      ← slide-out or dropdown app menu
    panels.css        ← kcui-panel / kcui-panel-header / kcui-panel-body
    tabs.css          ← kcui-tab bar
    workspace.css     ← kcui-workspace split-pane layout
    tags.css          ← kcui-tag component
  js/
    chrome.js         ← bundle: re-exports all JS below
    suiteMeta.js      ← suite version constant
    icons.js          ← SUITE_ICONS SVG map + resolveIcon()
    theme.js          ← per-service accent color map + applyTheme()
    topbar.js         ← initTopbar()
    appbar.js         ← initAppBar()
    appMenu.js        ← app menu open/close
    tabs.js           ← renderTabs() + tab routing
    panels.js         ← panel resize helpers
    workspace.js      ← splitter drag behavior
    tags.js           ← createTag() + updateTag()
  fonts/
    RobotoMono-*.ttf  ← suite typeface (Regular, Medium, SemiBold, Bold)
```

---

## Design Tokens (`tokens.css`)

All layout measurements, colors, and typography are defined as CSS custom properties on `:root`.

| Category | Key tokens |
|---|---|
| Heights | `--kcui-tab-h` (38px), `--kcui-appbar-h` (42px) |
| Radii | `--kcui-radius-sm` (0), `--kcui-radius-md` (2px) |
| Surfaces | `--bg`, `--bg-2`, `--panel`, `--surface`, `--surface-2` |
| Borders | `--border` (#1e2233), `--border-2` (#2b3148) |
| Text | `--text`, `--text-2`, `--text-dim` |
| Semantic colors | `--accent` (#4af77a), `--success`, `--warning` (#f0c060), `--danger` (#ff5f5f), `--info` (#6eb5ff) |
| Typography | `--kcui-text-body` (13px), `--kcui-text-label` (11px), `--kcui-text-caption` (10px) |
| Font stacks | `--kcui-font-ui`, `--kcui-font-code`, `--kcui-font-topbar` (all Roboto Mono → Cascadia Code → monospace) |

---

## Top Bar (`topbar.css` / `topbar.js`)

The top bar is the primary cross-suite navigation strip. It is fixed at the top of every page.

```
┌──────────────────────────────────────────────────────────────────────┐
│  [KoreStack] [KoreAgent] [KoreChat] [KoreData] … [KoreComms] │
└──────────────────────────────────────────────────────────────────────┘
```

Each entry is a `.ktopbar-item` link. The active service gets `.is-active` which draws a 2px accent underline
colored by that service's theme accent (`--topbar-accent`).

**Initialization:**

```js
import { initTopbar } from '/ui-elements/assets/js/chrome.js';

initTopbar({ currentService: 'koreagent' });
```

**Options:**

| Option | Default | Description |
|---|---|---|
| `mountId` | `'topbar'` | ID of the `<div>` to render into |
| `currentService` | `null` | Service key for active highlight |
| `urls` | `{}` | Override URL per service key |
| `iconSize` | `14` | SVG icon size in px |

---

## Application Bar (`appbar.css` / `appbar.js`)

The application bar sits directly below the top bar. It contains:
- **Left — brand block** (`.kappbar-brand`): service icon + overline label + title. Accent-tinted background.
- **Right — content area**: status values, page/tab navigation, or action controls — defined per app.

```
┌──────────────────────────────────────────────────────────────────────┐
│  [▣ KORE / KoreAgent]  │  [tab: Chat]  [tab: Tasks]  [tab: Log]     │
└──────────────────────────────────────────────────────────────────────┘
```

**Initialization:**

```js
import { initAppBar } from '/ui-elements/assets/js/chrome.js';

initAppBar({
  mountId:        'appbar',
  currentService: 'koreagent',
  tabs: [
    { label: 'Chat',  href: '#chat'  },
    { label: 'Tasks', href: '#tasks' },
  ],
});
```

---

## Tags (`tags.css` / `tags.js`)

The `kcui-tag` component is a small bordered label or toggle button used suite-wide to show status, type, or
mode. It replaces all legacy `.badge`, `.pill`, and `.kc-tag` patterns.

**HTML-only usage (primary):**

```html
<span  class="kcui-tag kcui-tag--accent">active</span>
<span  class="kcui-tag kcui-tag--warning kcui-tag--pill">pending</span>
<button class="kcui-tag kcui-tag--dim is-on" type="button">wrap</button>
```

**JS helper (optional):**

```js
import { createTag, updateTag } from '/ui-elements/assets/js/chrome.js';

const tag = createTag({ text: 'live', color: 'accent', interactive: true, active: true });
updateTag(tag, { color: 'dim', active: false });
```

**Modifiers:**

| Class | Effect |
|---|---|
| `kcui-tag--accent` | Green (#4af77a) |
| `kcui-tag--success` | Green (#4af77a) |
| `kcui-tag--warning` | Yellow (#f0c060) |
| `kcui-tag--danger` | Red (#ff5f5f) |
| `kcui-tag--info` | Blue (#6eb5ff) |
| `kcui-tag--dim` | Muted (#4e5466) |
| `kcui-tag--pill` | Rounded ends (border-radius: 999px) |
| `kcui-tag--filled` | 12% background tint from current color |
| `is-on` | 14% background tint — active state for button tags |
| `is-off` | Resets to dim + transparent |

**Shape choice by app:**

| App | Shape |
|---|---|
| KoreChat | Pill (`--pill`) |
| KoreComms | Square (no modifier) |
| KoreAgent | Square (no modifier) |

---

## Other Components

### Panels (`panels.css`)
`.kcui-panel` + `.kcui-panel-header` + `.kcui-panel-body` — standard panel frame used in KoreAgent and
KoreChat split layouts.

### Tabs (`tabs.css` / `tabs.js`)
`.kcui-tab-bar` with `renderTabs()` — used in the application bar and in multi-page apps such as KoreData.

### Workspace (`workspace.css` / `workspace.js`)
`.kcui-workspace` split-pane grid with draggable `.kcui-workspace__splitter` dividers. Used in KoreAgent's
three-panel log / chat / input layout.

### App Menu (`app-menu.css` / `appMenu.js`)
Slide-out or dropdown menu anchored to the top bar. Provides cross-service navigation on smaller viewports.
