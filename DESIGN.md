# KoreStack Suite Design

## 1. System Definition

KoreStack is the local control plane for the Kore suite.

The suite is a set of cooperating local services presented as one operator-facing system:

- one root workspace
- one suite launcher
- one shared shell language
- one set of shared operator data roots
- one control-plane landing page
- clear service boundaries where they remain useful

The suite is deliberately not a monolith. KoreData, KoreDocs, KoreComms, KoreConversation, and KoreAgent remain separate runtimes because they solve different problems and evolve at different rates. KoreStack makes them feel coherent to the operator.

---

## 2. Top-Level Components

### KoreStack

KoreStack is the entrypoint and control surface.

It is responsible for:

- launching selected services from the workspace root
- applying suite-level environment settings to child processes
- showing live health and reachability on the landing page
- exposing suite paths and operator controls in one place

### KoreAgent

KoreAgent is the orchestration and agent runtime.

It is responsible for:

- the main agent web UI
- prompt execution and run streaming
- slash commands and operator interactions
- scheduling, orchestration, and MCP consumption
- exposing the suite version when version information is explicitly requested

KoreAgent is the only subsystem that still carries an explicit product version identity. Other services expose health and role, not independent product-version surfaces.

### KoreConversation

KoreConversation is the canonical shared conversation-state service.

It owns:

- conversation metadata
- message history
- event coordination between agent and comms flows
- the browser debug UI for conversation inspection

KoreConversation lives in its own top-level folder. The legacy KoreAgent-side path is only a compatibility shim for older launch references.

### KoreComms

KoreComms is the communications hub.

It is responsible for:

- channel and interface configuration
- inbound polling and outbound delivery
- queue management for communication work
- the operator UI for conversations, composition, activity, and state management

KoreComms depends on KoreConversation for shared thread state rather than owning separate conversation records.

### KoreData

KoreData is the knowledge domain of the suite.

It is presented through KoreDataGateway and includes:

- KoreFeed
- KoreLibrary
- KoreReference
- KoreRAG

KoreData is both a user-facing application and an integration boundary for agent access.

### KoreDocs

KoreDocs is the document and file workspace.

It provides:

- KoreFile file management
- KoreDoc editing
- KoreSheet editing
- KoreDiag editing
- related document APIs and MCP-facing behavior where needed

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

- `config/default.json`
- `config/local.json` when present

It then passes the resolved suite paths and shared environment settings into the child runtimes.

### Service Relationships

The primary runtime relationships are:

- KoreAgent consumes KoreData capabilities
- KoreAgent interacts with KoreDocs capabilities
- KoreAgent uses KoreConversation for shared conversation state
- KoreComms uses KoreConversation for thread state and event coordination
- KoreStack monitors all user-facing services and presents the shared landing page

### Boundary Rules

The suite preserves these boundaries intentionally:

- KoreData remains a domain service, not a library inside KoreAgent
- KoreDocs remains a domain service, not a widget set inside KoreAgent
- KoreConversation remains a standalone suite service
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
  KoreConversation/
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

- KoreConversation is top-level and standalone
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

All mutable runtime and operator-managed data belongs under the suite root. Historical
service-local `Data/`, `data/`, `datacontrol/`, and `datauser/` folders are retired.

- put it in `datacontrol/` if it is suite operational state, runtime persistence, queues,
  logs, schedules, service databases, or other system-owned files
- put it in `datauser/` if it is operator-managed content, working files, imported source
  material, KoreDocs documents, or user-curated knowledge
- do not create new mutable subsystem-local data roots under service folders

This keeps one authoritative operational root and one authoritative user-data root for
the whole suite.

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

Landing page behavior and navigation rules are defined in [DESIGN_UI.md](DESIGN_UI.md).

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
5. Continue the landing-page and shell work defined in [DESIGN_UI.md](DESIGN_UI.md).
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
- Detailed UI consolidation rules live in [DESIGN_UI.md](DESIGN_UI.md).

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
5. The remaining suite-level navigation question is tracked in [DESIGN_UI.md](DESIGN_UI.md).

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