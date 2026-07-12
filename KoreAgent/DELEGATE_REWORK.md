# Delegate Rework

## Purpose

Define delegation that is actually worth using in a local KoreAgent deployment:
- one LLM
- one serial queue
- one controller conversation that may need lower-level task help

This note is intentionally narrow: what must be achieved, what should be avoided, and the minimum implementation likely to work.

---

## Problem

Current `delegate(...)` is a nested synchronous subrun.

Useful parts:
- fresh child prompt thread
- child tool chatter stays out of the parent thread
- child tool set can be restricted

Why it still misses:
1. It is inline and blocking.
2. It is not a real queue-level child job.
3. It is not isolated enough at session-state level.
4. It returns too little structure for larger work.
5. The model often will not bother using it if direct tools are simpler.

That last point matters most. If delegation feels awkward or expensive, the LLM will avoid it.

---

## Goal

We want the agent to act as a controller of smaller task instances.

Example:
- controller task: produce a report
- child task: search KoreData for source material
- child task: draft semi-finished article sections
- controller task: review, revise, and produce the final report

Core model:
- controller owns the user objective
- child tasks own narrow remits
- child tasks write results into agreed targets
- controller later resumes and continues from those results

Delegated child work should be treated like a function call, not a persona:
- task in
- data in
- process constraints
- data out

---

## Required Runtime Shape

When this is working "in anger", the runtime should feel like this:
1. User asks for substantial work.
2. Controller conversation plans the breakdown.
3. Controller spawns one or more child tasks.
4. Controller conversation is parked.
5. Child conversations take turns through the same queue.
6. Each child writes outputs into agreed storage targets.
7. Controller conversation resumes later.
8. Controller reviews child outputs and finishes the user-facing result.

This is still serial execution. That is fine. The value is not parallel compute. The value is:
- cleaner context
- better structure
- durable intermediate work
- more credible divide-and-conquer behavior

---

## What We Should Not Build

Avoid:
- true parallel workers
- permanent worker personas
- fixed worker job titles
- deep agent hierarchies
- heavy orchestration graphs
- distributed execution

That is likely over-complex nonsense for a single-LLM local system.

The child should not be "a researcher" or "a drafter". It should just be a task instance with a remit and a contract.

---

## Recommended Model

Use two primitives, not one.

### 1. Keep `delegate(...)` as a lightweight inline tool
Use it for:
- focused synchronous sub-investigations
- cases where the parent wants the answer now
- cases where a fresh prompt thread helps but durable queue lifecycle is unnecessary

### 2. Add a durable child-task primitive
This should handle real controller/worker behavior. Suggested shape:
- `subtask_spawn(...)`
- `subtask_status(...)`
- `subtask_collect(...)`

This should support:
- parked controller
- queued child conversations
- later controller resume

Why split it:
- inline delegate is synchronous
- durable child task is queued
- inline delegate returns directly
- durable child task writes results for later collection

If these are merged into one vague feature, the LLM will not use it predictably and the implementation will become muddy.

---

## What Must Be True For The LLM To Really Use It

### 1. Child work must be a clear function-like contract

The controller must define:
- `task_in` - what is being asked
- `data_in` - the keys, datasets, files, or refs provided
- `process` - allowed tools, limits, and scope
- `data_out` - what must be produced and where it must be written

If the controller cannot define those four things, it probably should not spawn the child.

### 2. Outputs must be structured

Do not rely on long freeform prose. At minimum, a child result should expose:
- `status`
- `summary`
- `evidence`
- `artifacts`
- `saved_keys`
- `datasets`
- `error`

### 3. The controller must not need to scrape logs

The child must write into explicit result targets:
- scratchpad key
- dataset
- file
- koredoc
- structured task result record

### 4. Queue semantics must be obvious

The controller must not remain in a blocked live orchestration loop while children run:
- controller parks
- children run
- controller resumes later

### 5. Isolation must be real

Child tasks should not silently share parent mutable session state:
- separate child session id
- explicit scratchpad visibility
- no child mutation of parent tool-selection state

---

## Minimal Viable Durable Design

### Child task record
Persist:
- `task_id`
- `parent_conversation_id`
- `child_conversation_id`
- `status`
- `task_in`
- `data_in`
- `process`
- `data_out`
- `tools_allowlist`
- `result_target`
- `created_at`
- `started_at`
- `finished_at`
- `error`

### Controller wait record
Persist:
- `controller_conversation_id`
- `state`
- `waiting_for_task_ids`
- `resume_prompt`
- `result_sources`
- `created_at`
- `resumed_at`

### Required operations
- spawn child task
- inspect child status
- collect child result
- park controller
- resume controller

That is enough for a real workflow.

---

## Result Handoff

Child tasks should not mainly hand back results by returning inline prose to the parent.
Preferred handoff:
1. write result to explicit target
2. record target in child task result
3. controller resumes and reads target

Good initial targets:
- dataset for tabular collections
- scratchpad key for compact text outputs
- file or koredoc for drafted content

This matters because it makes the workflow inspectable and durable.

---

## Queue Behavior

The single queue is not a limitation here. It is the model:
- controller job runs first
- controller spawns child jobs
- controller parks and exits the live run
- child jobs move through the queue one by one
- final controller-resume job is queued
- controller resumes from stored child outputs

That is the behavior we should optimize for.
---

## What Still Needs Design

Before implementation, these details still need to be nailed down:
- where child-task records live
- how controller resume is triggered
- how failures and retries work
- what the first supported result-target types are
- how much current inline `delegate(...)` should be improved first

Likely first supported targets: scratchpad key, dataset, file.
---

## Suggested Phases

### Phase 1
Improve existing inline `delegate(...)`:
- true child session isolation
- enforced scratchpad visibility
- richer return payload

### Phase 2
Add durable child-task records and queue support:
- `subtask_spawn`
- `subtask_status`
- `subtask_collect`

### Phase 3
Add controller parking and resume:
- parked controller record
- controller resume job
- result-source driven continuation

### Phase 4
Add UI visibility:
- parked controllers
- child task lineage
- child results
- pending controller resume jobs

---

## Recommendation

This rework is worth doing only if it produces a feature the LLM will actually choose. That means:
- do not over-complicate it
- do not invent permanent worker identities
- do not chase fake parallelism
- do build durable child task records
- do let the controller park and resume later
- do require explicit result targets

The likely winning design is:
- small synchronous `delegate(...)` for immediate subruns
- separate durable child-task primitive for real queued divide-and-conquer work

That is the narrowest version likely to achieve the real goal.
