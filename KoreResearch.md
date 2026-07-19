# KoreResearch
## Objective
`KoreResearch` is a proposed long-running research and evidence-building service for KoreStack.
It exists for work that is too large, too stateful, or too long-running for a normal interactive `KoreAgent` prompt/tool loop.
Typical use cases:
- many rounds of search, fetch, retrieval, filtering, and synthesis
- hundreds of prompt/tool cycles
- work that runs for hours or days
- large datasets and multiple output documents
- tasks that must pause, resume, retry, and stay inspectable

## Core Position
`KoreResearch` is not another ordinary skill inside `KoreAgent`.
It should be:
- a separate service
- a long-running run manager
- a host for per-run pages and operational logs
- a scheduler of bounded execution slices
- a publisher of evidence, documents, and artifacts
It should use `KoreAgent` as an execution engine through dedicated sessions.

## What It Is Not
It should not be:
- a single huge prompt
- a leaf tool
- a hidden background thread in `KoreAgent`
- an unbounded delegate loop
- a reimplementation of ordinary web search

## Why Separate It
The split solves a real pressure problem in `KoreAgent`.
Benefits:
- `KoreAgent` stays focused on interactive execution
- long-running work gets better isolation and observability
- research runs gain first-class state, progress, and checkpointing
- failures and resource spikes are easier to contain
- the UI can be designed around runs, evidence, and outputs rather than chat
The risk is duplication. `KoreResearch` will need planning, task tracking, guardrails, evidence policy, and continuation logic that already exists in or near `KoreAgent`.
That duplication must be designed out.

## Clean Split
The separation should be by responsibility, not by copied code.

### `KoreAgent`
Owns:
- interactive chat/session runtime
- prompt construction for execution turns
- tool loop execution
- tool catalog and activation mechanics
- conversation state
- immediate user-facing responses
`KoreAgent` is the execution engine.

### `KoreResearch`
Owns:
- research run lifecycle
- long-running task scheduling
- checkpoints and resume state
- per-run web pages
- progress logs and operational visibility
- run-level budgets and stop conditions
- artifact publication for the run
`KoreResearch` is the run manager.

### Shared Orchestration Core
Owns:
- task-plan models
- step state machines
- guardrail evaluation
- evidence precedence policy
- retry and continuation rules
- generic budget accounting
- stop / pause / blocked rules
This shared core is where task-governance logic belongs.

## Duplication Dilemma
If `KoreResearch` copies `KoreAgent` logic for planning and guardrails, the two systems will drift.
That creates the worst outcome:
- one fix has to be made twice
- behavior differs by host
- evidence policy becomes inconsistent
- debugging becomes unclear
The rule should be:
- do not duplicate the intelligence rules
- only separate the runtime boundary and service responsibilities
Practical test:
- if it governs how a task is reasoned about, constrained, resumed, or validated, it belongs in shared orchestration code
- if it governs live chat execution and session behavior, it belongs in `KoreAgent`
- if it governs long-running job ownership, run pages, scheduling, and checkpoints, it belongs in `KoreResearch`

## Relationship To KoreAgent
`KoreResearch` should drive work by submitting prompts into dedicated `KoreAgent` sessions over time.
That means:
- `KoreResearch` decides what work should happen next
- `KoreAgent` performs the prompt/tool execution slice
- `KoreResearch` records the outcome, updates state, and decides whether to continue
This keeps `KoreAgent` as the tool-using runtime while allowing `KoreResearch` to own long-horizon orchestration.

## Meaningful Differentiation
The split is only worth keeping if the two layers do genuinely different jobs.
`KoreAgent` should answer: `how do I execute this turn well?`
`KoreResearch` should answer:
- `what is the next bounded task?`
- `what evidence or output must come back?`
- `what counts as success, failure, or insufficient progress?`
- `should the run continue, branch, retry, wait, or stop?`
So `KoreResearch` should not be another free-form agent brain.
It should be a controller that frames bounded tasks for the execution layer and then evaluates the result.

## Execution Brief
The handoff from `KoreResearch` to `KoreAgent` should be explicit.
Rather than sending only a loose natural-language prompt, `KoreResearch` should create an execution brief for the next slice.
That brief can then be rendered into the prompt that `KoreAgent` executes.
Suggested brief fields:
- `step_id`
- `objective`
- `context`
- `constraints`
- `allowed_sources`
- `required_evidence`
- `expected_output`
- `completion_test`
- `failure_test`
- `follow_up_hint`
This keeps the split clean:
- `KoreResearch` defines the task and criteria
- `KoreAgent` interprets that brief and handles the actual prompt/tool execution

## Criteria Over Micro-Orchestration
The main value of `KoreResearch` is not low-level execution control.
Its value is:
- selecting the next task
- narrowing the scope for that task
- asserting what evidence is required
- deciding whether the result was good enough
- deciding what happens next
It should avoid owning:
- detailed tool ordering inside a turn
- prompt repair logic inside a turn
- low-level tool activation behavior
- the mechanics of the session runtime
Those stay in `KoreAgent`.

## Session Ownership
Each research run should have its own dedicated `KoreAgent` session.
Recommended rule:
- one research run
- one dedicated session
This avoids:
- turn collisions with foreground user chat
- context pollution
- mixed logs
- race conditions over tool state or prompt history
The user can inspect the session, but the run owns it while active.

## Service Shape
The likely service shape is:
1. `KoreResearch service`
   Owns research runs, lifecycle state, scheduling, persistence, and UI/API.
2. `KoreAgent bridge`
   Creates sessions, submits prompts, reads outputs, and receives tool/prompt round summaries.
3. `Shared stores`
   Persist run records, checkpoints, logs, datasets, and output artifacts.
4. `Output systems`
   Write `KoreDocs`, summaries, evidence packs, and related files.

## Run Model
A research run is the core object.
Suggested fields:
- `run_id`
- `title`
- `objective`
- `scope`
- `constraints`
- `source_policy`
- `deliverables`
- `success_criteria`
- `status`
- `phase`
- `session_id`
- `plan`
- `work_queue`
- `completed_steps`
- `failed_steps`
- `datasets`
- `artifacts`
- `latest_output`
- `metrics`
- `created_at`
- `updated_at`

## Run States
Suggested states:
- `queued`
- `planning`
- `researching`
- `waiting`
- `blocked`
- `paused`
- `complete`
- `failed`
- `cancelled`

Suggested phases:
1. `intake`
2. `plan`
3. `collect`
4. `refine`
5. `synthesize`
6. `publish`
7. `review`

## Execution Model
`KoreResearch` should run in bounded slices, not as one huge loop.
Each slice should:
1. read current run state
2. choose the next step
3. create the execution brief for that step
4. render or request the next prompt from that brief
5. submit the prompt to the run's `KoreAgent` session
6. wait for completion of that prompt/tool slice
7. parse the result against the brief criteria
8. update plan, datasets, artifacts, and metrics
9. log the outcome
10. schedule the next slice or stop

This gives:
- pause / resume
- retry with backoff
- crash recovery
- checkpointing
- supervision
- time and token budgeting

## Long-Running Responsibility
The service must do more than just "keep going for a long time".
It must:
- preserve intent over many cycles
- detect when evidence is missing
- expand or refine the plan when gaps appear
- avoid repeating the same weak searches
- checkpoint enough state to survive restarts
- publish useful partial outputs without pretending the run is complete
- expose what it is doing in a way a user can audit
That is the core operational responsibility of the service.

## Inputs, Process, Outputs
Each run should make three concerns explicit.
### Inputs
- original request
- normalized objective
- constraints
- source preferences
- output targets
- stop conditions
### Process
- phases
- subquestions
- current plan
- current execution brief
- budgets
- checkpoints
- review rules
### Outputs
- evidence sets
- datasets
- draft findings
- generated `KoreDocs`
- final deliverables

## Evidence Policy
The evidence-precedence rule should carry through from the main guardrails:
- when fresh retrieved evidence exists, it has higher precedence than model memory
- when search only gives discovery snippets, the run should prefer fetch/retrieval before synthesis
- when important gaps remain, the run should prefer more collection over unsupported finalization
This applies across `KoreLiveWeb`, `KoreData`, and any future retrieval source.

## Guardrails
Because runs can grow large, the service needs hard boundaries.
Recommended controls:
- maximum prompt/tool cycles per run
- maximum cycles per phase
- maximum retries per step
- token and wall-clock budgets
- dataset size limits
- artifact count limits
- duplicate-search suppression
- evidence-before-finalization checks
- blocked-state detection
It should be possible for a run to stop cleanly as `blocked` or `waiting` rather than failing noisily or looping forever.

## Scheduling And Recovery
`KoreResearch` should behave like a supervised worker, not like a fragile in-memory script.
It should support:
- queueing
- deferred continuation
- periodic wake-up
- manual resume
- cancellation
- process restart recovery from checkpoint
A slice should be restartable from persisted run state without relying on fragile in-memory context.

## Observability
The service should expose live operational detail.
At minimum:
- one-line log entries with timestamps
- current phase and current step
- current execution brief summary
- prompt submission events
- tool usage summaries
- retries, failures, and backoff
- output publication events
- last progress time
- current owning session id
This is not optional. Long-running work without visibility becomes impossible to trust.

## Run Page
Each run should have its own page.
Suggested layout:
- `Input`
  original request, objective, constraints, source policy, deliverables, stop conditions
- `Process`
  phase, current step, current execution brief, current working prompt, next intended action, hypotheses, retries, blockers
- `Output`
  draft answer, extracted findings, evidence summaries, datasets, produced `KoreDocs`, final outputs
Surrounding controls and indicators:
- run status badge
- progress timeline
- token and tool usage counts
- artifact list
- live log stream
- pause / resume / cancel actions

## API Shape
Likely endpoints:
- `POST /research/runs`
- `GET /research/runs`
- `GET /research/runs/{run_id}`
- `GET /research/runs/{run_id}/events`
- `POST /research/runs/{run_id}/pause`
- `POST /research/runs/{run_id}/resume`
- `POST /research/runs/{run_id}/cancel`
- `GET /research/runs/{run_id}/artifacts`
- `GET /research/runs/{run_id}/session`

## Integrations
`KoreResearch` should integrate with:
- `KoreAgent`
  execution of prompt/tool slices
- `KoreLiveWeb`
  web search/fetch visibility and provider-backed web work
- `KoreData`
  retrieval, datasets, and evidence persistence
- `KoreDocs`
  output documents
- scheduling infrastructure
  queueing, wake-ups, and resumable jobs

## Build Direction
Start with:
- one `KoreResearch` service
- one run model
- one per-run page
- one bridge into dedicated `KoreAgent` sessions
- shared orchestration modules for planning and guardrails
- durable storage for checkpoints and outputs
Do not start with:
- a new pile of ordinary skills in `KoreAgent`
- a second independent planning / guardrail implementation
- one mixed user-and-research session
- a hidden background loop with weak visibility

## Summary
`KoreResearch` should be a separate long-running service that manages research runs as first-class objects.
It should own scheduling, checkpoints, run pages, progress logs, and output publication, while relying on dedicated `KoreAgent` sessions for execution and shared orchestration modules for planning, guardrails, evidence rules, and continuation policy.
