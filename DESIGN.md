# KoreStack - Top-Level Design

## 1. Purpose

Kore is no longer a set of loosely related repos started independently. It is becoming a
single local-first application suite with one root entrypoint, one shared UI language,
and one clear system layout, while still preserving useful service boundaries.

The merged workspace should behave as a cohesive system with these properties:

- one top-level launch path for the operator
- one suite-level navigation surface for moving between products
- one shared UI shell and interaction language across products
- one top-level configuration area for network and path settings
- one top-level location for operator data and control-state folders
- clear service boundaries where those boundaries are still valuable

This document defines the intended shape of the consolidated system.

---

## 2. System Positioning

Kore is a suite of cooperating local services rather than a single monolith.

That distinction matters. The goal is not to collapse everything into one process. 
The goal is to make the system feel unified to the user while preserving
the architectural separations that are already useful.

In particular:

- **KoreData** remains a distinct knowledge system and MCP server.
- **KoreDocs** remains a distinct document system and MCP server.
- **KoreAgent** is the main orchestration and agent runtime.
- **KoreComms** remains the communications hub.
- **KoreConversation** is an always-on suite service and part of the normal suite startup contract.
- **UIElements** is the shared suite shell layer used across all user-facing apps.

The top-level suite provides orchestration, navigation, config, and operator-facing
cohesion. It does not erase the subsystem boundaries.

---

## 3. Core Design Principles

### 3.1 One Suite, Several Services

The operator starts Kore from the workspace root. KoreStack is responsible for bringing
up the selected services and exposing the system landing page.

The operator should not need to remember four different startup commands just to use the
system. Internally, however, those services may still run as separate processes.

For naming consistency in the consolidated repo:

- `KoreX` names identify runnable process-level services
- non-process shared libraries and support areas should not use the `Kore` prefix

### 3.2 Same Shell, Different Work Surfaces

All user-facing surfaces should share the same outer shell language:

- brand framing
- tabs and global navigation
- top bar
- application menu bar
- panel primitives
- token palette, typography, spacing, and icon rules

Product-specific work surfaces remain local to each subsystem. KoreDocs editors,
conversation panes, data-search layouts, and agent panels should not be forced into a
fake shared component model when their internals are domain-specific.

This is the responsibility of **UIElements**.

### 3.3 Shared Configuration at the Right Level

Configuration that describes the overall suite should live at the suite level.

Examples:

- host/IP bindings
- public service URLs
- default ports
- root data paths
- storage locations
- cross-service connection addresses
- MCP endpoint registration used by the agent runtime

Configuration that only matters inside one subsystem should remain local to that
subsystem.

Examples:

- KoreFeed ingest settings
- KoreDocs editor-specific defaults
- service-internal import options
- subsystem-specific maintenance settings

This is the responsibility of a new top-level **config** area.

### 3.4 Top-Level Data Ownership

The suite-level operator data folders should be visible at the suite root, not buried
inside one subsystem that happens to have owned them first.

`datacontrol` and `datauser` are now suite assets, not KoreAgent-only assets.

### 3.5 MCP as a First-Class Integration Boundary

KoreData and KoreDocs should be treated as first-class MCP providers, not merely internal
implementation details.

That gives the suite a clean boundary:

- the main agent can consume them through MCP
- they remain separately useful and separately testable
- future tools or external agent runtimes can consume them too
- their internal evolution is decoupled from the main agent UI/runtime

---

## 4. Intended Top-Level Structure

The suite root should become the primary place from which a developer or operator
understands the system.

Target structure:

```text
Kore74/
  main.py                 # root wrapper into KoreStack
  README.md               # suite-level operator guide
  DESIGN.md               # suite-level architecture and design
  KoreStack/              # coordinating service, landing page, and control plane
  config/                 # suite-level path + network + service connection config
  UIElements/             # shared shell assets and UI conventions
  KoreAgent/              # agent runtime and suite orchestration entrypoint
  KoreConversation/       # shared conversation-state service
  KoreComms/              # communications hub
  KoreData/               # knowledge system + MCP server
  KoreDocs/               # docs system + MCP server
  datacontrol/            # suite control/state data - Can be configured to be elsewhere in production
  datauser/               # suite user-owned content and working data - Can be configured to be elsewhere in production
  progress/               # suite-wide progress and screenshots where useful - disposable
```

### 4.1 Promoted Top-Level Folders

The following folders should be treated as suite-owned root folders:

- `datacontrol`
- `datauser`

These currently live under KoreAgent's current implementation area and already act like cross-cutting system
stores. They should be documented and eventually referenced as top-level suite paths.

Expected ownership:

- `datacontrol/` holds logs, schedules, queue state, test prompts/results, conversation
  snapshots, and other operational control data.
- `datauser/` holds user-facing content, notes, spreadsheets, working files, prompt
  material, imported text, and other operator-managed data.

### 4.2 config Area

The suite needs a dedicated configuration area rather than relying on one subsystem's
root `default.json` as the de facto global truth.

`config/` should become the suite-level home for:

- root path definitions
- host and IP bindings
- port allocations
- cross-service URLs
- MCP endpoint definitions
- environment-specific overrides if needed later

Initial design intent:

```text
config/
  default.json           # canonical suite defaults
  local.json             # optional machine-local overrides, ignored by git if needed
  README.md              # config conventions and precedence
```

The canonical suite config should be a single `config/default.json` file. If a
machine-specific override layer is needed, it should live in `config/local.json`.

Possible logical sections inside `default.json`:

```json
{
  "paths": {
    "datacontrol": "datacontrol",
    "datauser": "datauser",
    "conversation_data": "datacontrol/conversations",
    "comms_data": "datacontrol/korecomms",
    "docs_data": "datauser/KoreFiles",
    "docs_db": "datauser/KoreFiles/korefile.db"
  },
  "network": {
    "host": "127.0.0.1"
  },
  "services": {
    "agent": { "port": 8000 },
    "koreconversation": { "port": 8700 },
    "data": { "port": 8800 },
    "comms": { "port": 8900 },
    "docs": { "port": 5500 },
    "korestack": { "port": 8600 }
  },
  "connections": {
    "korecomms": "http://127.0.0.1:8900",
    "koreconversation": "http://127.0.0.1:8700"
  },
  "mcp": {
    "connections": []
  }
}
```

This does **not** mean every setting in the system belongs here. It means suite-level
settings belong here, and subsystems should consume them from here when they need a
shared address or shared path.

---

## 5. Runtime Architecture

### 5.1 Top-Level Runtime Roles

The consolidated system currently has these suite-level roles:

| Area | Role |
|---|---|
| **KoreStack** | Starts, stops, monitors, and presents the landing page for the selected services |
| **KoreAgent** | Main agent runtime, orchestration UI, scheduler, MCP consumer |
| **KoreConversation** | Shared conversation state and event coordination |
| **KoreComms** | External communication interfaces and queueing |
| **KoreData** | Knowledge service family and MCP provider |
| **KoreDocs** | Document suite and MCP provider |
| **UIElements** | Shared shell assets for all user-facing apps |
| **config** | Shared suite-level configuration source |

### 5.2 Relationship Diagram

```text
Operator
   |
   v
KoreStack
   |
  +--> KoreAgent ---------------------------+
   |        |                                |
   |        +--> consumes MCP from KoreData -+
   |        +--> consumes MCP from KoreDocs -+
   |        +--> calls KoreComms via REST ---+
   |        +--> calls KoreConversation -----+
   |
   +--> KoreComms ---------------------------> external channels
   |
   +--> KoreConversation --------------------> shared conversation state
   |
   +--> KoreData ----------------------------> knowledge services + MCP
   |
   +--> KoreDocs ----------------------------> document services + MCP
   |
  +--> UIElements --------------------------> shared shell assets
   |
  +--> config ------------------------------> shared path/IP/URL settings
```

### 5.3 Service Boundaries to Preserve

The following boundaries are intentional and should remain unless there is a concrete
reason to collapse them:

- **KoreData** is a domain service, not a library inside the agent.
- **KoreDocs** is a domain service, not a panel inside the agent.
- **KoreConversation** is a suite service, not just an internal helper hidden inside the agent runtime.
- **MCP** is a stable boundary between the agent runtime and those systems.
- **UIElements** shares shell and chrome, not internal domain widgets.
- **config** supplies shared settings, not every internal subsystem option.

---

## 6. UI Consolidation Model

### 6.1 Suite-Level UI Goal

From the operator's point of view, Kore should feel like one application suite with
multiple work areas, not four unrelated localhost pages.

That means:

- a KoreStack landing page
- consistent visual framing
- consistent navigation labels
- consistent app-switching behavior
- predictable panel and menu behavior
- shared terminology for shell elements

### 6.2 UIElements Responsibilities

UIElements is the system-wide UI shell layer.

It is **not** a standalone runtime service. It is a shared library of assets, templates,
functions, and UI primitives that each service uses to present a unified shell.

It should own:

- top bar
- tabs
- app menu bar shell
- tokens and typography
- panel/frame primitives
- shared icon registry
- shared shell behavior for cross-app navigation

It should not own:

- document editors
- sheet grid behavior
- diagram canvas logic
- agent orchestration panels
- conversation rendering details
- data result semantics

### 6.3 Visual Direction

The common UI direction for Kore should follow a deliberately technical, operator-first
visual language, closer to a local systems console than a generic SaaS dashboard.

`KoreData` should be treated as the current best visual reference for that direction.
Its live UI already captures the tone the suite should converge toward: dark matte
surfaces, thin border framing, restrained use of color, tight monospace typography,
compact information density, and utility-first panels instead of decorative cards.

The reference direction for `UIElements` and the other service UIs is therefore:

- dark-mode-first shell surfaces
- nerdy, technical typography with a monospace-forward voice
- dense but readable operator information layout
- topology and systems-view presentation for service relationships
- restrained chrome with strong contrast and precise spacing, minimal rounded corners
- signature accent colors per service so each system is identifiable at a glance

That means the shared shell should prefer:

- dark neutral backgrounds
- mono or mono-paired typography for navigation, metrics, labels, and diagnostics
- thin-line borders and panel separators rather than soft, inflated container treatments
- compact headers, status strips, and operator controls with minimal ornamental spacing
- service-colored highlights applied consistently to cards, pills, outlines, icons, and topology nodes
- dashboards that feel like a local control room rather than a marketing page

The intended accent-color behavior is service-specific rather than random:

- `KoreAgent` should have its own identifying accent
- `KoreData` and its child services should share a related data-family accent system
- `KoreDocs`, `KoreComms`, and `KoreConversation` should each have their own signature
  color identity
- `KoreStack` should use a neutral control-plane identity that frames the whole system
  without visually overpowering the service accents

This visual direction belongs in `UIElements`, so the same dark-mode shell, typography
rules, and accent-color language appear across the landing page and all service UIs.

In practical terms, `UIElements` should gradually extract and standardize the parts of
the `KoreData` aesthetic that are shell-level concerns:

- typography stack
- dark surface and border tokens
- panel/header treatment
- button, tab, and status-badge language
- spacing rhythm for dense operator pages

### 6.4 Adoption Direction

Adoption should proceed from outer shell inward:

1. use `KoreData` as the immediate visual benchmark for shell tone and density
2. move shared shell tokens and chrome patterns into `UIElements`
3. update `KoreStack` to align more closely with the `KoreData` look and feel
4. progressively align `KoreDocs`, `KoreComms`, `KoreConversation`, and `KoreAgent`
  to the same shell language without flattening their domain-specific workflows
5. only then decide whether any buttons, forms, tables, or dialogs deserve to become
   shared suite components

This keeps the consolidation disciplined and avoids over-sharing the wrong UI layers.

### 6.5 Mounting Model

Each user-facing service should consume UIElements as a shared library layer rather than
treating it as a separately running service.

That means:

- the service owns its own UI runtime
- the service imports or mounts shared UIElements assets/templates as needed
- the suite does not add a separate "UIElements server" process

The result should be one visual language without inventing another deployable runtime.

---

## 7. Configuration Model

### 7.1 Config Layers

The system should use a layered config model.

1. **Suite defaults** from `config/default.json`
2. **Machine-local overrides** from `config/local.json` if present
3. **KoreStack resolution** that passes shared settings into launched services
4. **Subsystem-local config** from the subsystem's own config area
5. **Explicit CLI/runtime overrides** from the current process launch

Precedence should flow from broad to specific.

### 7.2 What Belongs in config

Belongs in `config/`:

- canonical root paths
- canonical host/IP bindings
- canonical port map
- canonical service URLs
- MCP connection registration used across the suite
- suite-level feature switches when they affect cross-service behavior

Does not belong in `config/`:

- feed scrape rules
- editor hotkeys
- import tuning knobs
- local UI experiments inside one subsystem
- maintenance-only settings that no other subsystem needs to understand

### 7.3 Migration Intent

The former `MiniAgentFramework/default.json`, now housed at `KoreAgent/default.json`, is
effectively acting as the suite's first global config file.

That should be treated as a transitional state.

The long-term direction is:

- promote suite-wide settings into `config/`
- keep subsystem-specific settings local
- make KoreStack the first resolver of shared suite config
- pass resolved shared values into services via environment variables, CLI flags, or both
- preserve compatibility shims during migration so existing startup paths do not break

---

## 8. Data Ownership Model

### 8.1 datacontrol

`datacontrol/` is the suite's operational state area.

It should hold things like:

- logs
- schedules
- queue state
- test prompts and test results
- conversation state snapshots
- runtime artifacts produced by the suite

The design expectation is that multiple subsystems may write into structured subfolders
inside `datacontrol/`, but the folder itself is owned by the suite.

### 8.2 datauser

`datauser/` is the suite's operator-facing working data area.

It should hold things like:

- notes
- imports
- csv files
- prompt material
- working documents
- user-curated content for agent use
- ad hoc data sources used during evaluation or experimentation

### 8.3 Data Boundary Rule

When deciding whether something belongs in a subsystem-local `Data/` folder or in a
top-level suite folder:

- put it in the subsystem's local `Data/` folder if it is private to that subsystem's
  internal storage engine or domain model
- put it in `datacontrol/` if it is suite operational state
- put it in `datauser/` if it is operator-managed suite content

This keeps private service stores separate from shared operator-facing suite data.

---

## 9. Launch and Navigation Model

### 9.1 Root Launch

The suite root should remain the normal launch point:

```powershell
python .\main.py
```

That root command should launch KoreStack as the default operator workflow.

### 9.2 Selective Launch

The suite should still support selective startup for development and testing:

- launch only docs
- launch only agent + data
- run status-only checks
- run without the KoreStack landing page when needed

### 9.3 KoreStack Landing Page

The KoreStack landing page should become the first navigation surface, not just a process list.

Over time it should provide:

- service health and reachability
- a topology diagram of the active services
- configured IP and port assignments for each service
- direct links into each product area
- suite-level status summary
- key runtime metrics from the active services
- the configured shared data-folder layout from top-level config
- possibly recent activity and operator shortcuts

The KoreStack landing page should not replace subsystem UIs. It should connect them.

---

## 10. Near-Term Consolidation Steps

The next consolidation steps implied by this design are:

1. Create a real `KoreConfig/` directory and define canonical suite config structure.
2. Continue reducing duplication between `KoreAgent/default.json` and the suite-level
  `config/default.json`.
3. Keep suite-owned `datacontrol/` and `datauser/` authoritative at the workspace root and
  avoid reintroducing service-local ownership for shared runtime state.
4. Update shared path utilities and config readers to prefer `config/` and top-level
   suite paths, with launcher-provided overrides during migration.
5. Expand the KoreStack landing page into a richer control and navigation hub.
6. Move each user-facing app toward UIElements shell adoption.
7. Continue extracting service-specific code, such as KoreConversation internals, into
  their own top-level homes where that improves clarity.

---

## 11. Decision Snapshot

The following decisions have been made for the current design direction.

### 11.1 Configuration

- `config/` should start with one canonical `default.json`.
- Machine-local overrides should live in `config/local.json`.
- Shared suite config should be resolved by KoreStack and passed down to services.

### 11.2 KoreConversation

- KoreConversation is treated as an always-on suite service.
- KoreConversation is part of the normal suite startup contract.

### 11.3 UI Consolidation

- UIElements is a shared library layer, not a service.
- The near-term KoreStack landing page should stay a launcher and health dashboard rather than become a
  full portal immediately.

### 11.4 Data Ownership

- `datauser/` is the umbrella folder for user-owned suite content.

### 11.5 Integration Boundaries

- KoreData and KoreDocs remain MCP-first integrations for the agent runtime.
- KoreData child services stay behind KoreDataGateway at suite level.

---

## 12. Remaining Questions

The design is directionally clearer now, but a few concrete questions still need explicit
answers during implementation planning.

1. What exact environment-variable and/or CLI contract should KoreStack use when it
   passes shared config into child services?
2. Do we want `config/default.json` to contain MCP registrations generically from the
   start, anticipating additional MCP providers beyond KoreData and KoreDocs?
3. Which remaining compatibility shims inside `KoreAgent/` can be removed safely?
4. Which current files inside the historical agent-local data snapshot under `KoreAgent/datauser`
   should remain suite-wide, and
   which should be split into more explicit subfolders inside the top-level umbrella?
5. How much suite-level cross-navigation should appear inside each subsystem beyond basic app
   switching?

---

## 13. Summary

The target architecture is a **unified suite with preserved service boundaries**.

The key moves are:

- keep KoreData and KoreDocs independent and MCP-capable
- make UIElements the shared shell across products
- introduce `config/` as the suite-level home for path and network configuration
- promote `datacontrol` and `datauser` to explicit suite-owned root folders
- keep a single top-level launch and navigation story for the operator

That produces a system that is easier to start, easier to navigate, and easier to reason
about, without flattening the system into one oversized application.