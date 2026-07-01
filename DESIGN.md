# KoreStack Suite Design

## 1. System Definition

The mission of the whole KoreStack project is to provide a LocalLLM agentic framework.
- Call a single LLM
- Orchestrate prompt inputs from a chat input, to a chat output
- Consider and expanded set of tool inputs and outputs, including numerous data and doc sources 

KoreStack is also the hub on the system, providing the landing page and central code and config elements.

The suite is a set of cooperating local services presented as one operator-facing system:
- one root workspace
- one suite launcher
- one shared shell language
- one set of shared operator data roots
- one control-plane landing page
- clear service boundaries where they remain useful

The suite is deliberately not a monolith. KoreData, KoreDocs, KoreComms, KoreChat, and KoreAgent remain separate runtimes because they solve different problems and evolve at different rates. 
KoreStack present them as a singular coherent system to the operator.

---

## 2. Top-Level Components

### KoreStack

KoreStack is the entrypoint and control surface.

It is responsible for:
- launching selected services from the workspace root
- applying suite-level environment settings to child processes
- showing live health and reachability on the landing page
- exposing suite paths and operator controls in one place
- configuration of tool port numbers

### KoreAgent

KoreAgent is the orchestration and agent runtime.

It is responsible for:
- the main agent web UI
- prompt execution and run streaming
- slash commands and operator interactions
- scheduling, orchestration, and MCP consumption
- exposing the suite version when version information is explicitly requested

KoreAgent is the only subsystem that still carries an explicit product version identity. Other services expose health and role, not independent product-version surfaces.

### KoreChat

KoreChat is the canonical shared conversation-state service.

It owns:

- conversation metadata
- message history
- event coordination between agent and comms flows
- the browser debug UI for conversation inspection

KoreChat lives in its own top-level folder. The legacy KoreAgent-side path is only a compatibility shim for older launch references.

### KoreComms

KoreComms is the external communications hub.

It is responsible for:

- channel and interface configuration
- inbound polling and outbound delivery
- queue management for communication work
- the operator UI for conversations, composition, activity, and state management

KoreComms depends on KoreChat for shared thread state rather than owning separate conversation records.

### KoreData

KoreData is the general knowledge domain of the suite. 

It is presented through KoreDataGateway and includes:
- KoreFeed
- KoreLibrary
- KoreReference
- KoreRAG

KoreData is both a user-facing application and an integration boundary for agent access.
The data can be considered singular and global, this is not specifically user data.

### KoreDocs

KoreDocs is the document and file workspace. This is specific to a user.

It provides:
- KoreFile file management
- KoreDoc editing
- KoreSheet editing
- KoreDiag editing
- related document APIs and MCP-facing behavior where needed

### KoreCode

This is a code editor, focussed on editing the KoreStack codebase.

It provides:
- Code navigation
- File editing

### UIElements

UIElements is the shared shell asset library for all user-facing applications.

It owns:
- shared design tokens
- top bar and application bar assets
- shared tab and menu behavior
- panel, page, and card layout primitives
- cross-application accent themes
- shared icon and shell JavaScript utilities

UIElements is not a service. It is a suite-level shared asset dependency.

---

## 3. Operator Model

The operator works with Kore as one local suite.

The normal flow is:

1. start from the workspace root
2. launch via KoreStack
3. use the KoreStack landing page to inspect service state
4. move between applications using the shared shell
5. rely on shared suite paths for user and operational data

The operator does not need to treat each application as a separate product with separate visual rules, separate version surfaces, or separate root-level operating conventions.

---

## 4. Runtime Architecture

### Control Plane

KoreStack starts and monitors the selected services as separate child processes.

It loads suite configuration from:

- `config/korestack_config.json`
- `config/llm_config.json`

It then passes the resolved suite paths and shared environment settings into the child runtimes.

### Service Relationships

The primary runtime relationships are:

- KoreAgent consumes KoreData capabilities
- KoreAgent interacts with KoreDocs capabilities
- KoreAgent uses KoreChat for shared conversation state
- KoreComms uses KoreChat for thread state and event coordination
- KoreStack monitors all user-facing services and presents the shared landing page
- KoreAgent reads and write content to KoreDoc documents
- KoreCode uses a KoreChat to schedule prompts in advancing code edit tasks

### Boundary Rules

The suite preserves these boundaries intentionally:

- KoreData remains a domain service, not a library inside KoreAgent
- KoreDocs remains a domain service, not a widget set inside KoreAgent
- KoreChat remains a standalone suite service
- KoreComms owns transport and interface behavior, not canonical conversation history
- UIElements owns shell behavior, not domain-specific application internals

---

## 5. Shared Data and Configuration

### Suite Data Roots

The suite-level data roots are:

- `datacontrol/` for operational state
- `datauser/` for operator-owned content and working files

These are suite assets, not KoreAgent-private folders.

Typical usage includes:

- logs
- task queues
- schedules
- test prompts and results
- conversation storage
- user notes and imported files
- document and file data

### Suite Configuration

The canonical suite configuration lives in `config/`.

The shared configuration model covers:

- suite paths
- host bindings
- service ports
- cross-service URLs
- shared connection points

KoreStack is the source of truth for suite-level configuration injection. Subsystems may still keep local settings for domain-specific behavior, but suite topology belongs at the root.

---

## 6. UI Design Reference

Detailed UI guidance now lives in [DESIGN_UI.md](DESIGN_UI.md).

That document owns:

- shared shell behavior
- application identity and accent rules
- shared layout primitives
- KoreStack landing page design
- operator-facing version surfaces and UI adoption direction

---

## 6A. No-Flicker Rule (Global, Mandatory)

No page in the KoreStack suite may flicker during refresh, polling, filtering, or any incremental update. This is a hard design rule and applies to all services and all UI surfaces.

Required behavior:

- Keep existing layout and content mounted while new data is loading.
- Update only the specific rows, cards, or fields that changed.
- Preserve scroll position, focus, and text selection across updates.
- Avoid rapid hide/show toggles for primary containers.
- Use stable dimensions or placeholders when loading to prevent visual jumps.

Forbidden behavior:

- Clearing a whole panel and repainting it on each refresh tick.
- Replacing full page or full panel DOM trees when only part of the data changed.
- Forcing layout collapse/expand cycles to indicate loading state.

Acceptance standard:

- Under periodic updates, the operator must be able to keep reading or interacting without visible flash, blanking, or layout jitter.

---

## 7. Landing Page Reference

The KoreStack landing page UX and navigation behavior are defined in [DESIGN_UI.md](DESIGN_UI.md).

---

## 8. Version Policy

The suite no longer presents independent product-version chips across the subsystems.

The version policy is:

- KoreAgent retains the suite-visible version identity
- other services expose health, role, and state without separate version surfaces
- documentation should not describe deprecated per-service version banners as active behavior

This keeps the operator-facing system identity singular instead of fragmented.

---

## 9. Repository Structure

The suite is organized around top-level service folders and shared roots:

```text
KoreStack/
  main.py
  DESIGN.md
  DESIGN_UI.md
  README.md
  config/
  datacontrol/
  datauser/
  UIElements/
  KoreStack/
  KoreAgent/
  KoreChat/
  KoreComms/
  KoreData/
  KoreDocs/
  progress/
```

Key interpretation rules:

- top-level `KoreX/` folders are runnable service areas
- `UIElements/` is the shared shell layer
- `config/` is the suite configuration root
- `datacontrol/` and `datauser/` are suite-owned state roots
- `progress/` is disposable working material, not production state

---

## 10. Completed-System View

Kore is now defined as a completed multi-service local suite with a shared control plane, shared UI shell, shared data roots, and explicit service boundaries.

The design is not a migration plan. It is the current operating model:

- KoreChat is top-level and standalone
- KoreStack is the suite launcher and dashboard
- UIElements is the shared shell system
- suite data lives in shared root folders
- suite configuration is rooted at the top level
- only KoreAgent retains explicit version identity

That is the baseline architecture all further work should preserve.
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

The former per-project config has been replaced by
`config/korestack_config.json` as the suite's global config file.

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
- KoreDocs has a folder here for the users file actions
- notes
- imports
- csv files
- prompt material
- working documents
- user-curated content for agent use
- ad hoc data sources used during evaluation or experimentation

### 8.3 Data Boundary Rule

All mutable runtime and operator-managed data belongs under a configurable top-level directory@
- put it in `datacontrol/` if it is suite operational state, runtime persistence, queues,
  logs, schedules, service databases, or other system-owned files
- put it in `datauser/` if it is operator-managed content, working files, imported source
  material, KoreDocs documents, or user-curated knowledge
- do not create new mutable subsystem-local data roots under service folders

This keeps one authoritative operational root and one authoritative user-data root for
the whole suite.

---

## 9. Launch and Navigation Model

### 9.0 Service HTTP Contract

Every user-facing Kore service must expose the following HTTP surface:

| Path | Purpose |
|---|---|
| `GET /` | Redirects to `/ui` |
| `GET /ui` | Primary browser entry point — serves the application shell HTML |
| `GET /api/...` | Agent and function-calling entry point — all data and action routes |
| `GET /status` | Health probe — returns `{"status": "ok", "service": "<slug>"}` |
Rules:

- `/ui` is the canonical URL that KoreStack links to and topbar uses for navigation
- `/api/...` is the canonical entry point for agent queries and MCP-backed function calls
- `/status` must always return HTTP 200 with a JSON body while the service is healthy
- browser entry points (`/ui`) and agent entry points (`/api/`) must be on separate paths so they can be distinguished without content-type inspection.

### 9.1 Root Launch

The suite root should remain the normal launch point:

```powershell
python .\main.py
```

That root command should launch KoreStack as the default operator workflow.

### 9.3 KoreStack Landing Page

Landing page behavior and navigation rules are defined in [DESIGN_UI.md](DESIGN_UI.md).

---

## 11. Decision Snapshot

The following decisions have been made for the current design direction.

### 11.1 Configuration

- Use one canonical `config/korestack_config.json` for shared suite config.
- Keep bootstrap LLM settings in `config/llm_config.json`.
- Shared suite config should be resolved by KoreStack and passed down to services.

### 11.2 KoreChat

- KoreChat is treated as an always-on suite service.
- KoreChat is part of the normal suite startup contract.

### 11.3 UI Consolidation

- UIElements is a shared library layer, not a service.
- Detailed UI consolidation rules live in [DESIGN_UI.md](DESIGN_UI.md).

### 11.5 Integration Boundaries

- KoreData and KoreDocs remain MCP-first integrations for the agent runtime.
- KoreData child services stay behind KoreDataGateway at suite level.
