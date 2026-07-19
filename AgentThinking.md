# Agent Thinking Plan

## Objective

Enable KoreAgent to complete increasingly complex, multi-step tasks reliably. The
agent must retain durable working state, choose a constrained set of suitable
tools, execute a controlled workflow, validate its result, and recover from
tool-call mistakes without wandering or silently abandoning work. The LLM
remains responsible for understanding text and decomposing the task; host logic
governs execution after that interpretation has been made.

This plan concerns the existing Scratchpad, Datasets, Delegate, tool selection,
and task-management capabilities. The immediate problem is not a lack of tools.
It is that too much of the workflow is left to an LLM to infer and coordinate.

## Current Assessment

Scratchpad is the strongest primitive. It retains large tool results, supports
context compaction recovery, and is persisted with the KoreChat conversation.
It is still an untyped string store, so the agent must remember what each key
means and when it is safe to reuse it.

Datasets provide better structure, lineage, filtering, paging, and spillover
storage. They are not reliably discovered because their manifests are only
placed in the prompt when the relevant dataset tools are already selected.

Delegate has durable child-task records and controlled result targets. Its
function signature is too demanding for a model to construct reliably as an
unassisted first step, and the controller must still remember to inspect and
collect the child result.

Tool selection limits the active set, which is correct. It needs deterministic
planning before the LLM call, rather than asking the LLM to search a large
catalog and select the correct tools while also solving the task.

## Implementation Status

The first control-plane vertical slice is implemented in KoreAgent. Each normal
prompt now receives a dedicated LLM planning pass before tool execution. The
resulting `TaskPlan` is validated against the capability catalog, persisted in
the conversation scratchpad, included in the working-agent context, and traced
through tool rounds and completion.

Phase-tool enforcement is implemented behind an opt-in configuration flag. It
is not enabled by default until automatic phase transitions are available; a
strict initial-phase guard would otherwise block valid multi-step requests that
need to inspect and then execute within the same run. Existing duplicate-call
and recovery controls remain active while this trace data is collected.

## Ten Actions

### 1. Deterministic Task Planner

Add a dedicated planning LLM pass before tool execution. It receives the normal
user prompt, workspace and task context, and a compact capability catalog. It
returns a structured interpretation: objective, scope, candidate workflow,
required evidence, likely tool groups, risk, and completion contract.

The host validates that plan against available tools, safety policy, active
workspace scope, and existing artifacts. It must not use keyword or regex
routing as the primary interpretation mechanism. Deterministic logic belongs
only in validation, compatibility normalization, and conservative fallback when
the planner is unavailable or explicitly uncertain.

### 2. Workflow Templates

Define reusable workflows for recurring task shapes. Examples include research
to dataset to report, inspect to edit to validate to run, and investigate in
parallel to delegate collection to synthesis. Each template defines allowed
tools, required artifacts, transition rules, and completion checks.

Templates reduce free-form planning without removing useful reasoning. The LLM
chooses details within a phase; the host controls the phase order.

### 3. Host-Generated Delegation Plans

Replace raw multi-field delegation requests with a high-level planning action.
The model provides a child objective, expected result type, and any relevant
artifact references. The host creates the task identifier, constrained child
tool allowlist, result target, timeout, and collection policy.

The controller should receive a concise result manifest automatically when a
child completes. It should not need to remember opaque task identifiers or
manually poll simple synchronous work.

### 4. Execution State Machine

Represent work as explicit phases: clarify, inspect, plan, act, validate,
recover, and complete. Every phase has allowed tools, required evidence, a
maximum number of attempts, and legal transitions. The runner rejects duplicate
exploration and asks for a final action once evidence is sufficient.

This prevents the repeated-read and unlimited-tool-loop failures currently
visible in coding tasks. It also supplies meaningful UI progress rather than
exposing raw model planning JSON.

### 5. Datasets As Planner State

Treat datasets as durable artifacts that are visible to the planner even before
dataset tools are active. The planner should auto-select the minimal dataset
tool bundle when a task mentions existing datasets, structured collections, or
research results that exceed normal prompt size.

The model should see a small manifest containing name, schema, count, source,
lineage, freshness, and intended use. Full records remain tool-accessed.

### 6. Typed Artifact Store

Build a typed layer above scratchpad strings. Artifact kinds should include
evidence, plan, finding, dataset reference, file patch, execution result,
validation result, and delegated result. Each artifact should record producer,
source inputs, freshness, scope, and a stable identifier.

Scratchpad remains the underlying content store where useful, but planning and
delegation operate on artifact references rather than undocumented keys.

### 7. Tool-Call Repair And Retry Policy

Make common tool failures recoverable at the host layer. Normalize known aliases,
validate required arguments before execution, supply schema-specific correction
feedback, reject duplicate calls, and use bounded retries. A repeated read of
the same unchanged source should be denied and followed by a completion prompt.

Every tool should publish a machine-readable result envelope: success state,
artifact IDs produced, retryability, remediation hint, and whether the result
satisfies a workflow evidence requirement.

### 8. Durable Work Items

Persist a work item for substantial tasks. It should store objective,
constraints, selected workflow, phase, plan, artifacts, decisions, validation
criteria, child tasks, and outcome. A chat conversation is evidence history,
not a dependable task record.

Work items make restarts, navigation, long-running delegation, and user review
safe. They also create an audit trail for why an edit or conclusion was made.

### 9. End-To-End Evaluation Suite

Extend testing beyond isolated tool prompts. Add scored scenarios covering
multi-step execution, restarts, dataset lifecycle and lineage, delegated result
collection, duplicate-tool denial, context compaction, coding edits, validation,
and final artifact correctness.

Record structured traces and grade both task success and process quality. A
successful answer with an unvalidated or incorrect file change is a failure.

### 10. Observability And Policy Metrics

Instrument every run with workflow, phase, selected tools, artifacts, retries,
duplicate calls, delegate lifecycle, validation result, and termination reason.
Track selection precision, repair success, phase regressions, artifact reuse,
completion rate, and user-visible failure rate over time.

Use these metrics to decide which workflows and tools to improve. Do not expand
the skill catalog merely because a failure occurred.

## Priority Programme

The first implementation programme is actions 1, 4, 7, and 8. Together they
turn the existing skills into a controlled agent runtime. Actions 2, 3, 5, and
6 then become extensions of that runtime rather than disconnected features.

### A. Task Planner: Action 1

#### Scope

Create a `TaskPlan` with a dedicated planning LLM pass before tool execution. It
is a small, typed record:

```text
objective, task_class, workflow_id, active_scope, allowed_tools,
required_artifacts, validation_requirements, risk_level, completion_contract
```

The planning prompt must cover coding edits, Python execution, workspace
investigation, structured-data work, research, document export, and delegation
candidates without relying on trigger words. If the model is uncertain, it
returns a confidence value and a conservative exploration plan rather than a
fabricated classification.

#### Rules

- Interpret the user's natural-language intent before selecting tools; do not
  infer intent primarily from keywords or regular expressions.
- Select no more than the tools required for the current phase.
- Keep `delegate`, catalog discovery, and tool activation host-managed.
- Activate dataset tools when input/output is a collection or a known dataset is referenced.
- Select execution tools only for an explicit run, test, check, or validation step.
- Record the routing reason in the work item and run trace.

#### Acceptance Criteria

- Every non-trivial run has an LLM-produced, persisted `TaskPlan` before its
  first tool call.
- The active tool list is reproducible from the plan.
- Tests verify semantically varied phrasings for the major task classes and a
  conservative fallback for uncertain plans.
- The UI can display the selected workflow and current phase in one short line.

### B. Execution State Machine: Action 4

#### Phases

```text
clarify -> inspect -> plan -> act -> validate -> complete
                         |          |
                         +-> recover+
```

`clarify` is used only for missing user intent. `inspect` collects bounded
evidence. `plan` records the intended change or analysis method. `act` performs
tool calls or validated edits. `validate` checks acceptance criteria. `recover`
handles a bounded tool or validation failure. `complete` is permitted only when
the completion contract is satisfied.

#### Enforcement

- Each phase declares its allowed tools and evidence requirements.
- Read tools add evidence; they do not reset the phase.
- Identical tool calls against unchanged inputs are denied as duplicates.
- Once evidence requirements are satisfied, the runner requires an action or final result.
- Recovery has a fixed retry budget with specific error feedback.
- Exceeding a budget produces a useful blocked outcome including evidence already collected.

#### Acceptance Criteria

- A coding request cannot loop through repeated reads after the target file is known.
- An edit request records inspected source, patch, syntax/test validation, and outcome.
- Run traces expose phase transitions and termination reason.
- The UI never renders internal tool-plan JSON as an assistant answer.

### C. Tool Repair And Retry: Action 7

#### Tool Contract

Replace unstructured strings with a common result envelope:

```json
{
  "ok": true,
  "kind": "read|artifact|execution|mutation",
  "artifacts": ["artifact-id"],
  "evidence_satisfied": ["target_source"],
  "retryable": false,
  "remediation": ""
}
```

Keep display text for the user, but make orchestration decisions from this
envelope. Existing tools can be adapted incrementally behind a compatibility
adapter.

#### Repair Policy

- Normalize safe, unambiguous aliases such as documented legacy parameter names.
- Reject missing required arguments before dispatch and return the exact schema delta.
- Detect duplicate calls by tool name, canonical arguments, input artifact hash, and phase.
- Retry only retryable operational failures; do not retry semantic failures unchanged.
- Feed the model a short corrective instruction containing the valid argument names and available artifacts.

#### Acceptance Criteria

- Parameter drift is repaired or rejected without consuming an unrestricted tool loop.
- Duplicate reads produce no new execution and no loss of earlier evidence.
- Every failed tool call has a classified failure reason and remediation action.
- Tool-loop limits are fallback protection, not normal task-control logic.

### D. Durable Work Items: Action 8

#### Data Model

Extend the existing work-item concept into a durable task ledger. Store:

```text
id, workspace/conversation scope, objective, constraints, TaskPlan, phase,
artifacts, evidence ledger, decisions, pending actions, validation results,
delegate task IDs, status, outcome, timestamps, and version
```

Persist state atomically after every phase transition and mutating action. Keep
large payloads in datasets or scratchpad/artifact storage; the work item stores
references and summaries only.

#### Lifecycle

The user can start a work item explicitly, or the host creates one for a task
that requires multiple phases, delegation, mutation, or continuation across
turns. On restart, the agent reloads the work item, reconciles artifact hashes,
and resumes from the first unmet acceptance criterion.

#### Acceptance Criteria

- A multi-step task survives process restart without losing objective, plan, or evidence.
- The selected project/workspace scopes every artifact and delegate task.
- A user can inspect the current phase, pending validation, and result without reading chat history.
- Completed work items include an outcome and validation evidence; failed items include a precise blocked reason.

## Delivery Sequence

1. Define common models for `TaskPlan`, `WorkflowPhase`, `ArtifactRef`,
   `ToolResultEnvelope`, and `WorkItem`.
2. Add the dedicated planning LLM pass and persist the initial work item without
   changing existing tool execution behavior.
3. Introduce phase-aware tool selection and trace all transitions in shadow mode.
4. Enable duplicate detection and argument validation for read, execution, and
   file-edit tools.
5. Enforce completion contracts for bounded coding and execution workflows.
6. Migrate dataset and delegation flows onto artifact references and work items.
7. Add end-to-end benchmark scenarios and publish the operational metrics.

## Non-Goals

- Do not add a large catalog of new skills before the runtime can govern the
  existing set.
- Do not expose all tools to every prompt.
- Do not treat conversation transcripts as the authoritative task state.
- Do not use unrestricted retries as a substitute for a workflow contract.
