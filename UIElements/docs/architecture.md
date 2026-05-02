# UIElements Architecture

## Scope

UIElements exists to unify the outer shell across the Kore system.

Shared:
- font choices
- shell colors
- tabs and top bar
- app menu bar
- frame and panel primitives
- shared workspace layouts and divider behavior
- button, input, and dialog accents where adopted

Not shared:
- editor surfaces
- grid and canvas widgets
- domain-specific result rendering

## Rollout Strategy

1. Keep the shared shell assets under UIElements.
2. Generalize JS entrypoints so each app can supply routes, labels, and storage keys.
3. Mount UIElements from each repo as shared static assets.
4. Move other repos onto the same shell tokens and panel primitives.
5. Only after adoption, add shared button, form, and table components as separate layers.

## Current Generalized Pieces

- `tabs.js` supports app-level routing, branding, storage, and URL parameter configuration.
- `appMenu.js` shares the same icon registry model, so adopters do not need to redefine shell icons separately from tab chrome.
- `icons.js` is the central registry for shell-level app icons and should be extended before adding icon definitions ad hoc in other modules.

## Design Rule

Same shell, same tokens, same panel language.
Shared workspace mechanics, different product internals.

## Workspace Layout Rule

UIElements should provide one common workspace layout system with variants rather than separate page models.

The core variants are:

- dashboard workspace for panel-and-card pages
- split workspace for maximized panels separated by dividers
- hybrid workspace for pages that combine summary panels with a persistent split work area

The shell, spacing tokens, panel chrome, divider styling, and resize behavior belong in UIElements.
Apps provide their own workflow content inside those shared regions.

## KoreAgent Implication

KoreChat under the KoreAgent implementation tree is an early adopter for the shared outer shell.
Its detailed conversation surface should remain local, but the top-level shell, menu framing,
neutral panel primitives, and split-workspace mechanics should come from UIElements.
