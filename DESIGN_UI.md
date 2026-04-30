# KoreStack UI Design

## 1. Scope

This document defines the operator-facing UI model for the Kore suite.

It covers:

- the shared shell
- application identity and accent behavior
- shared layout primitives
- shared page workspace layouts
- the KoreStack landing page
- operator-facing version surfaces
- near-term UI adoption direction

System architecture, runtime boundaries, and suite topology remain in [DESIGN.md](DESIGN.md).

---

## 2. Shared Shell

All user-facing applications use the same shell model:

- suite top bar
- application bar directly beneath it
- shared accent theme per application
- shared page-width and panel-spacing rules
- shared tab-height and chrome behavior

The top bar communicates suite context.

The application bar communicates application-level navigation and status.

Legacy breadcrumb or title rows that repeat the same context are not part of the current design language.

Canonical shell ownership lives in UIElements:

- `UIElements/assets/js/topbar.js` owns the suite top bar
- `UIElements/assets/js/appbar.js` owns the application bar and tabbed application variant
- `UIElements/assets/js/icons.js` owns shared service and app-family icons

Shared shell styling and visual tokens also belong in UIElements:

- `UIElements/assets/js/theme.js` is the source of truth for per-service accent colors
- `UIElements/assets/css/tokens.css` owns semantic typography and shared UI tokens
- `UIElements/assets/css/panels.css` owns shared role classes and panel presentation rules

Legacy names such as `tabs.js`, `tabs.css`, and older `suitebar` references are compatibility wrappers only. They are not the design source of truth and should not be used to introduce new shell behavior.

KoreDocs may still expose compatibility static mounts such as `/static/commonui` and `/static/shared`, but those mounts are adapters over the same shared shell assets rather than a separate shell implementation.

---

## 3. Application Identity

Each application has a distinct accent color defined in UIElements and applied through shared theme utilities.

Current suite identities are:

- KoreStack
- KoreAgent
- KoreConversation
- KoreData
- KoreDocs
- KoreComms

Accent ownership lives in shared UIElements code rather than being redefined independently inside each app.

Shared iconography also belongs to UIElements. Tab-bar and app-bar identity should be derived from the shared shell runtime rather than page-local icon markup.

---

## 4. Layout Primitives

UIElements provides the baseline page and panel primitives used across the suite, including:

- page wrappers
- stack and grid spacing
- panel headers and bodies
- shared card layouts

Applications remain free to build domain-specific internals, but their outer shell and spacing system are shared.

---

## 5. Shared Workspace Layout

The suite should use one common page-workspace model in UIElements rather than treating panel pages and divider pages as separate layout systems.

The shared model is a workspace layout composed from:

- a shell-wrapped page frame
- one or more workspace regions
- panels as the default content container
- optional dividers between adjacent regions
- shared sizing, overflow, and collapse behavior

The rule is simple: divider-based pages are still panel pages. They are panels arranged in a split workspace rather than cards stacked in a dashboard.

### 5.1 Layout Variants

UIElements should expose one workspace system with a small set of variants:

- dashboard workspace for panel-based pages such as KoreData and the KoreStack landing page
- split workspace for divider-based pages such as KoreAgent and KoreConversation
- hybrid workspace for pages that need a dashboard summary area plus a persistent split work surface

These are variants of one layout language, not separate design systems.

### 5.2 Split Workspace

The KoreAgent layout should become the canonical split-workspace pattern in UIElements.

That pattern is essentially:

- maximized panels
- direct divider boundaries between working regions
- persistent side-by-side or top-bottom work areas
- restrained outer padding compared with dashboard pages
- shared panel chrome, headers, and scrolling rules

KoreConversation should use the same pattern when it presents a conversation list beside an active thread or detail surface.

### 5.3 Dashboard Workspace

Dashboard pages still use panels, but with more page breathing room and less emphasis on divider resizing.

Typical traits are:

- panel groups arranged in stacks or grids
- stronger page-level spacing between groups
- summary and status cards
- operational controls that do not require persistent split resizing

KoreData and the KoreStack landing page are the current reference cases.

### 5.4 Shared Behavior Ownership

UIElements should own the common behavior for these layouts:

- region sizing tokens
- divider appearance
- drag-to-resize behavior when enabled
- collapsed and minimum panel states when allowed
- scroll containment rules
- mobile and narrow-width fallback rules

Applications should instantiate the layout and provide domain content, but they should not reinvent the layout mechanics locally.

---

## 6. KoreStack Landing Page

The KoreStack landing page is the suite dashboard.

It presents:

- system paths
- live service rows
- inline service controls
- status and reachability information

The landing page intentionally avoids repeating information already carried by the shared shell. Its job is operational control, not duplicated branding.

The landing page should become the first navigation surface, not just a process list.

Over time it should provide:

- service health and reachability
- a topology diagram of the active services
- configured IP and port assignments for each service
- direct links into each product area
- suite-level status summary
- key runtime metrics from the active services
- the configured shared data-folder layout from top-level config
- possibly recent activity and operator shortcuts

The landing page should not replace subsystem UIs. It should connect them.

The near-term constraint remains the same: it should stay a launcher and health dashboard rather than become a full portal immediately.

---

## 7. Operator-Facing Version Surfaces

The suite no longer presents independent product-version chips across the subsystems.

The operator-facing policy is:

- KoreAgent retains the suite-visible version identity
- other services expose health, role, and state without separate version surfaces
- documentation should not describe deprecated per-service version banners as active behavior

This keeps the operator-facing system identity singular instead of fragmented.

---

## 8. UI Adoption Direction

The current direction is:

- move each user-facing app toward UIElements shell adoption
- move page layouts onto the shared workspace variants instead of maintaining separate local page models
- keep shared shell behavior in UIElements rather than subsystem-local CSS or JavaScript
- keep shared divider, region, and panel behavior in UIElements rather than subsystem-local layout code
- preserve compatibility shims only where they protect existing launch or static-asset paths
- keep the KoreStack landing page focused on control and navigation rather than duplicating subsystem workflows

The specific next layout move is to extract KoreAgent's maximized-panel style into a reusable UIElements split-workspace pattern and instantiate that pattern across other work-surface pages.

---

## 9. Open UI Question

One UI planning question remains explicitly open:

1. How much suite-level cross-navigation should appear inside each subsystem beyond basic app switching?