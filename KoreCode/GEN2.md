# KoreCode Gen2

> Status: Discussion Draft
> Date: 2026-07-17

---

## 1. Purpose

This document starts the discussion for **KoreCode Gen2**.

Gen2 is not a small feature expansion of the current editor. It is a step-change in both:

- capability
- interaction model
- UI density and polish
- system intelligence
- execution safety

The current KoreCode is a local-first browser editor with attached AI assistance. Gen2 should become a **code operating surface** for the whole KoreStack workspace: part editor, part architect console, part execution cockpit, part agent collaboration environment.

---

## 2. Why Gen2 Exists

The current design is intentionally narrow:

- open files
- edit files
- search within files
- ask for targeted AI help

That baseline is useful, but it leaves too much manual glue work between:

- understanding the codebase
- planning changes
- editing multiple files
- reviewing diffs
- running work
- validating outcomes
- iterating safely

Gen2 should close that gap.

The goal is not to imitate a general IDE. The goal is to create the strongest possible **AI-native coding environment for KoreStack-style local work**.

Another forcing function is external: tools such as **LM Studio Bionic** now define a higher minimum bar for local coding agents.

Bionic appears to validate several baseline expectations:

- repository-scoped agentic search
- visible investigate / inspect / edit loop
- inline diff review
- local-first model execution
- escalation to stronger models when needed

That does not remove the need for KoreCode. It does remove the option of staying at the current level of editor-plus-chat utility. Gen2 must be meaningfully stronger where KoreCode can truly differentiate.

---

## 3. Product Thesis

KoreCode Gen2 should feel like:

- a serious engineering tool
- a high-trust workspace for large changes
- an AI collaborator that operates on explicit contracts
- a visually rich but disciplined environment

It should not feel like:

- a chat box bolted onto a text editor
- a generic VS Code clone
- a fragile autonomous agent that silently rewrites files

The central product idea is:

> The human stays in command of intent and acceptance.  
> The system becomes dramatically stronger at context gathering, proposal generation, scoped execution, and verification.

---

## 4. Core Experience Shift

Gen1 is primarily **file-centric**.

Gen2 should be primarily **task-centric**.

That means the primary unit of work is no longer just "open file and type". It becomes:

1. define or select a task
2. gather relevant workspace context
3. inspect proposed plan
4. execute scoped edits
5. review diffs and evidence
6. run validation
7. accept, refine, or revert

The editor remains central, but it becomes one panel inside a broader coding workflow.

---

## 5. Gen2 Capability Pillars

### 5.1 Workspace Intelligence

KoreCode should maintain a live mechanical model of the workspace:

- files
- symbols
- imports
- call relationships where derivable
- tests related to changed code
- recent edits
- execution artifacts
- task history

This should support:

- codebase navigation by concept, not just path
- impact analysis before edits
- better prompt assembly
- better review and validation suggestions

The repository map should become a first-class operating view, not background infrastructure.

Selecting a symbol should immediately expose:

- source
- signature
- callers
- callees
- dependencies
- related tests
- existing work items
- prior agent actions
- attached documentation

### 5.2 Agentic Change Execution

Gen2 should support controlled multi-file operations:

- propose edits across several files
- explain why each file is touched
- preview diffs before apply
- apply in one transaction where practical
- retain a restorable change set

The model should be able to act at several levels:

- inline continuation
- single-range rewrite
- function-level refactor
- cross-file implementation task
- bug hunt and repair pass

Gen2 should make these actions explicit and bounded. One accepted work item should be the principal unit of execution:

1. one diagnosed issue
2. one accepted plan
3. one bounded implementation
4. one targeted validation run
5. one recorded result

### 5.3 First-Class Validation

Editing without verification is not enough.

Gen2 should make validation a first-class object:

- suggested tests
- targeted command execution
- lint and syntax checks
- structured output capture
- pass/fail summaries tied back to the task

Every significant AI-generated change should be able to answer:

- what changed
- why it changed
- what was run
- what passed
- what still looks uncertain

### 5.4 Rich Review UX

Diff review should be a product strength, not an afterthought.

Gen2 should support:

- file diff timeline
- per-hunk accept/reject
- explanation attached to each edit group
- reviewer notes from the agent
- links from findings to exact code

### 5.5 Multi-View Coding

The user should be able to work in several synchronized views:

- repository map
- work item view
- change review view
- agent activity view
- editor / diff canvas
- run / validation console
- architecture map or symbol graph

These should feel like one coherent environment, not separate tools stitched together.

---

## 6. UX Direction

Gen2 should represent a massive uplift in UI and UX quality.

### 6.1 Design Character

The interface should be:

- bold
- technical
- information-dense
- highly legible
- calm under heavy use

It should avoid:

- flat generic SaaS styling
- oversized whitespace that wastes working area
- modal overload
- hidden system state

### 6.2 Main Workspace Model

A likely Gen2 workspace is a four-zone environment:

- left navigation: workspace, symbols, tasks, saved views
- center canvas: editor or diff as the primary focus
- right intelligence rail: plan, context, evidence, chat, findings
- bottom execution rail: terminal, runs, logs, test results, artifacts

This should be configurable per task mode.

#### Conversation Focus

The agent conversation must be able to expand from the intelligence rail into the full centre canvas. This is a **conversation focus** state for planning, diagnosis, and longer exchanges where a narrow side panel would hide the important detail.

In conversation focus:

- the centre canvas shows the full conversation, structured plan, tool evidence, and proposed next actions
- the active editor or diff remains available as a restorable tab or return target
- the right intelligence rail remains available for compact task facts, attached context, and activity state
- the bottom execution rail remains available when a command or validation job is active
- selecting a referenced file, symbol, diff, or validation result can return the user directly to the relevant canvas view

This should be a reversible layout action, not a separate mode or a new conversation. The work item remains the owner of the conversation and its evidence.

Within that shell, four persistent views should define the product:

- **Repository map**: projects, namespaces, classes, functions, references, callers, callees, tests, diagnostics, and recent changes
- **Work item**: problem, evidence, diagnosis, plan, impacted files/functions, implementation, diff, tests, outcome, and residual risk
- **Change review**: grouped diffs, function-level before/after, rationale, warnings for unrelated edits, test impact, accept/reject, and revert at function granularity where possible
- **Agent activity**: current investigation path, symbols opened, tools used, hypotheses, rejected paths, stage, confidence, and context budget

### 6.3 Modes of Work

Gen2 should expose explicit modes so the UI matches user intent:

- **Explore**: inspect codebase, symbols, architecture, references
- **Edit**: focused single-file or multi-file editing
- **Plan**: scope a change before touching files
- **Review**: inspect diffs, findings, and risk
- **Validate**: run commands, tests, and checks

Mode changes should reorganize emphasis, not fully replace the page.

### 6.4 Persistent State Visibility

The user should always be able to see:

- active task
- attached context
- edited files
- dirty state
- pending agent actions
- validation status
- last successful run

The current system hides too much of the operational story behind chat exchanges and implicit state.

The UI must continuously answer:

- what is the agent doing
- why is it doing it
- what evidence does it have
- what does it intend to change
- what happened when it tried

---

## 7. Interaction Model

### 7.1 From Prompting to Contracts

Gen2 should move away from raw conversational prompting as the default.

Instead, major actions should use explicit contracts:

- **Intent**: what the user wants
- **Scope**: which files, symbols, or directories are in play
- **Constraints**: what must not be changed
- **Output shape**: commentary, diff, patch set, test plan, explanation
- **Validation**: what should be run afterward

Free-form chat still matters, but it should feed structured actions.

The agent conversation should be attached to the work item, not treated as the work item itself.

Chat should be available both as a compact intelligence-rail surface and as the wider conversation-focus canvas. The compact view supports active editing; conversation focus supports investigation and planning without sacrificing readability.

### 7.2 Progressive Autonomy

Not every task needs the same level of automation.

Gen2 should support a ladder:

1. suggest only
2. draft edits
3. apply to working draft
4. run validation automatically
5. chain scoped sub-steps with review gates

The user chooses the rung per task.

### 7.3 Evidence Before Trust

Agent confidence should never be implied purely by fluent prose.

Each substantial action should expose:

- source context used
- assumptions made
- files affected
- commands run
- outputs observed
- unresolved uncertainty

This is visible operational state, not hidden reasoning.

### 7.4 Skill Execution Plans

Every non-trivial skill run should create a visible execution graph.

Example:

```text
Investigate API timeout
|-- inspect endpoint
|-- trace service call
|-- inspect HTTP client configuration
|-- inspect retry policy
|-- inspect logs
|-- identify failure mode
|-- create regression test
|-- implement correction
`-- validate
```

Each node should carry:

- status
- evidence
- files inspected
- result
- confidence
- output artifacts

This creates interruption recovery, auditability, and resumable engineering state.

---

## 8. Candidate Gen2 Features

Potential feature areas:

- task cards with lifecycle and status
- workspace-wide semantic search
- symbol graph explorer
- code change plans before patch generation
- multi-file patch staging
- inline risk flags on generated edits
- test recommendation engine
- architecture notes pinned to code regions
- replayable work sessions
- "why was this changed?" provenance trails
- branch or snapshot aware change capsules
- slash-command style structured actions
- saved prompts and repeatable engineering recipes
- model routing between small local, larger local, and remote models
- visible context compaction and retrieval boundaries
- explicit investigation-only actions that forbid edits
- change-scope warnings when edits escape the expected impact set

Not all of these belong in the first Gen2 milestone, but they define the design space.

---

## 9. Skill System

Gen2 should stop treating "coding" as one generic capability.

It should expose a skill library with explicit procedures and output contracts.

### 9.1 Skill Families

The first broad families should be:

- **Code understanding**: trace a request path, explain a subsystem, identify ownership, find configuration paths, map data flow, map dependencies, locate extension points, identify dead or duplicated code
- **Diagnosis**: compile failure analysis, runtime exception tracing, failing test diagnosis, concurrency issues, lifecycle leaks, nullability problems, performance bottlenecks, API contract mismatches, state corruption, error-handling gaps
- **Implementation**: add a feature, alter behaviour, refactor a class, extract an interface, introduce a service, add an endpoint, modify persistence, add serialization, migrate schema, improve logging, add configuration, remove deprecated behaviour
- **Testing**: identify missing coverage, create unit tests, create integration tests, reproduce a bug, add regression tests, generate fixtures, run targeted tests, interpret failures, distinguish flaky from deterministic failures
- **Maintenance**: update dependencies, remove obsolete code, rename across the repository, migrate APIs, improve documentation, normalize patterns, enforce conventions, reduce warnings, inspect security boundaries

### 9.2 Skill Contract Shape

Each skill should define:

- inputs required
- investigation procedure
- evidence expected
- permitted tools
- output artifacts
- validation steps
- failure conditions
- escalation criteria

This is much stronger than a general-purpose system prompt claiming expert programming ability.

### 9.3 Procedure Depth

Depth should come from procedure, not from assuming model brilliance.

For example, `diagnose failing test` should be a defined procedure:

1. read the failing test and exact output
2. identify the tested contract
3. trace the production path
4. identify the first divergence from expected state
5. check recent changes and related tests
6. form competing hypotheses
7. gather evidence for each
8. select the most likely root cause
9. propose the smallest correction
10. re-run targeted tests
11. run adjacent regression tests
12. record confidence and residual risk

A weaker local model can often follow a strong procedure. Without that procedure, even a stronger model will improvise and drift.

### 9.4 Code Graph as an Operational Primitive

The structural code index should not only support browsing. It should drive skill execution.

Before a change, KoreCode should calculate an impact set:

```text
target function
-> direct callers
-> interfaces
-> implementations
-> tests
-> serializers
-> config
-> persistence
-> public API surface
```

That impact set becomes:

- the initial context
- the review boundary
- the expected test scope
- the warning system for unexpected edits

This is one of the clearest paths for KoreCode to exceed generic coding agents.

---

## 10. System Architecture Implications

A real Gen2 uplift requires backend and state-model changes, not only new UI.

Likely additions:

- durable task model
- richer workspace index
- edit batches with provenance
- validation job records
- per-task context assemblies
- event stream for long-running actions
- stronger mapping between editor state and agent state
- skill definitions and execution graphs
- model routing policy
- expected impact-set calculation
- durable evidence and results store

The system should treat a coding task as a durable object, not a transient chat exchange.

---

## 11. Safety Model

More power requires clearer safeguards.

Gen2 should default to:

- explicit scope selection
- visible proposed diffs
- reversible apply operations
- non-silent command execution
- durable logs of agent actions
- strong separation between proposal, apply, and validate
- warnings for unrelated edits outside the approved impact set
- explicit no-edit investigation modes

High-trust does not mean low-friction everywhere. It means the friction appears at the right control points.

---

## 12. Suggested Milestone Shape

The immediate development sequence should favor one polished vertical slice over broad shallow coverage.

### Confirmed Gen2 Baseline

The current preferred decisions are:

- lightweight durable work-item records, initially stored locally
- curated, approved validation commands with captured output
- staged, all-or-nothing multi-file edit application
- Python-first incremental workspace index, with basic search for non-Python files
- deterministic impact-set tiers: required, likely, and possible
- explicit fixed agent workflow states for the first skill implementations
- multi-workspace-aware local persistence, without multi-user collaboration requirements
- work item as the main UI surface, with contextual repository, review, activity, and validation panes
- compact chat plus a full-width conversation-focus canvas

These decisions are intentionally conservative. They establish a reliable platform for the first vertical slice without preventing richer skill graphs, indexing, or suite-wide work items later.

### Initial Implementation Slice

The first implementation slice establishes the durable work-item spine and its primary interaction points:

- local persisted work items with scope, constraints, plan, evidence, outcome, lifecycle state, and linked agent-run IDs
- work-item create, list, read, and update API operations
- active work-item selection and lifecycle control in the editor workspace
- automatic attachment of new chat-agent runs to the selected work item
- compact conversation rail plus a persistent full-width conversation-focus canvas

Validation execution, impact-set calculation, and transactional multi-file apply remain subsequent slices. The existing edit proposal system is reused for now, but it is not yet a complete Gen2 transaction model.

### Milestone A: One Excellent Workflow

Build `diagnose and fix a failing test`.

This single workflow exercises nearly everything that matters:

- navigation
- code search
- tracing
- diagnosis
- planning
- implementation
- diff review
- test execution
- result recording

If this workflow is visibly excellent, the rest of Gen2 has a credible spine.

### Milestone B: Repository Comprehension

Add:

- explain subsystem
- trace execution path
- map dependencies
- find likely change location

This is the point where the navigation UI becomes genuinely useful even when no edits are made.

### Milestone C: Common Implementation Skills

Add:

- bounded bug fix
- add small feature
- refactor function or class
- add endpoint
- add test coverage

### Milestone D: Engineering Depth

Add:

- performance diagnosis
- concurrency diagnosis
- persistence and schema changes
- security review
- multi-project changes

### Milestone E: Gen2 Platform Refinement

Add:

- chained task execution
- user-selectable autonomy levels
- repeatable engineering workflows
- stronger model routing and escalation
- durable work-item history across sessions

---

## 13. Conceptual Structure

A likely Gen2 internal structure is:

```text
KoreCode
|-- Workspace
|   |-- Repository index
|   |-- Symbol graph
|   |-- Search
|   `-- Change history
|-- Skills
|   |-- Understand
|   |-- Diagnose
|   |-- Plan
|   |-- Implement
|   `-- Test
|-- Work
|   |-- Tickets
|   |-- Evidence
|   |-- Plans
|   |-- Changes
|   `-- Results
|-- Agent
|   |-- Model routing
|   |-- Context builder
|   |-- Tool execution
|   `-- State machine
`-- UI
    |-- Repository map
    |-- Work item
    |-- Change review
    `-- Agent activity
```

The correction here is conceptual, not cosmetic:

KoreCode should not be a chat interface with code tools. It should be an engineering workbench in which agents perform structured tasks.

---

## 14. Open Questions

- Should Gen2 remain tightly KoreStack-specific, or broaden toward general local repositories?
- Should chat remain visible at all times, or become a collapsible instruction surface behind structured actions?
- What is the smallest milestone that already feels unmistakably "Gen2"?
- How much terminal and execution power belongs inside KoreCode versus staying in KoreAgent?
- Should task state live entirely in KoreCode, or become a suite-wide object that KoreAgent and KoreChat can inspect too?

---

## 15. Working Conclusion

KoreCode Gen2 should be framed as an **AI-native software delivery workspace**, not merely a better embedded code editor.

The defining upgrade is not just more AI. It is the combination of:

- stronger context awareness
- stronger task structure
- stronger skill procedures
- stronger review controls
- stronger validation loops
- much more intentional UI

If Gen1 proves that KoreCode can edit code locally, Gen2 should prove that KoreCode can help deliver meaningful code changes with speed, clarity, and control.
