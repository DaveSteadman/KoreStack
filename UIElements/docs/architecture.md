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

## Shared Typography Roles

UIElements owns shell typography roles for structure and controls.
Page-level domain content typography remains service-local.

Primary shell family:
- `Roboto Mono` for shell labels, control text, and compact UI chrome

Secondary shell family:
- service fallback stack for long-form readability when needed in shell metadata

The suite should define and maintain the following semantic typography roles.
This is the core baseline set of 20 roles:

### Top Bar Fonts

Role: Suite Bar Title
- Purpose: top-most suite identity text for KoreStack
- Typical usage: global top bar brand label

Role: Suite Bar Subtitle
- Purpose: optional secondary line next to suite identity
- Typical usage: environment or mode text in top bar

### Application Bar Fonts

Role: Application Bar Overline
- Purpose: small context kicker above app title
- Typical usage: app type, workspace mode, or section hint

Role: Application Bar Title
- Purpose: primary identity for the current application
- Typical usage: `KoreAgent`, `KoreDocs`, `KoreData`, etc

Role: Tab Title
- Purpose: tab text in the shared tab strip
- Typical usage: route labels and page sections

Role: Tab Title Active
- Purpose: emphasized version of tab text for selected tab
- Typical usage: selected route label

### Panels

Role: Panel Title
- Purpose: heading for shared panel chrome
- Typical usage: dashboard and split-panel headers

Role: Panel Subtitle
- Purpose: supporting line under or beside panel title
- Typical usage: panel context and scope text

Role: Section Heading
- Purpose: intermediate heading inside shared structural regions
- Typical usage: grouped shell controls in sidebars and drawers


### Minor UI Elements 

Role: Button Text
- Purpose: label for standard action buttons
- Typical usage: primary, secondary, tertiary shell buttons

### Generic Fonts

Role: Menu Item Label
- Purpose: text for app and overflow menu entries
- Typical usage: dropdown and context menu rows

Role: Input Label
- Purpose: label text for shell-level form controls
- Typical usage: filter bars, search rows, dialog forms

Role: Input Value
- Purpose: editable text and selected values in controls
- Typical usage: text field content, selected option text

Role: Comment
- Purpose: annotation or helper note text in shell-managed areas
- Typical usage: inline hints, short explanatory notes

Role: Helper Text
- Purpose: neutral guidance text tied to controls
- Typical usage: validation guidance and usage hints

Role: Status Text
- Purpose: state messaging for shell components
- Typical usage: ready, syncing, warning, offline indicators

Role: Dialog Title
- Purpose: heading for modal and drawer shells
- Typical usage: confirmation or settings dialogs

Role: Dialog Body
- Purpose: explanatory text in modal shell containers
- Typical usage: confirmation details and action context

Role: Tooltip Text
- Purpose: short explanatory hover/focus text
- Typical usage: icon affordance descriptions

Role: Footer Meta
- Purpose: low-emphasis shell footer information
- Typical usage: version, environment, legal/system note

Naming convention for future tokenization:
- `--font-role-suite-title`
- `--font-role-app-overline`
- `--font-role-app-title`
- `--font-role-tab-title`
- `--font-role-button-text`
- `--font-role-panel-title`
- `--font-role-comment`

Implementation rule:
- Roles are semantic, not component-specific size aliases.
- Components must consume role tokens rather than hardcoded font sizes and weights.
- UIElements is the source of truth for these role definitions.

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
