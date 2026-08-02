# Plan Foundation for KoreChat

## Purpose

KoreChat needs a durable, controllable planning foundation before it attempts
deep planning activities or complex worker-chat arrangements. A plan is a
first-class, revisioned object owned by a conversation. It is not a prompt
convention, an opaque scratchpad value, or a one-shot autonomous run.

The plan makes the conversation's intent, current state, dependencies,
delegated work, evidence, and changes inspectable and controllable across a
number of prompts.

## Scope and ownership

The planning model separates orchestration data from working data:

| Concern | Owner / storage |
| --- | --- |
| Objective, tasks, dependencies, status, acceptance criteria | Conversation plan |
| Large text and exact tool outputs | Scratchpad / artefacts |
| Structured record collections | Datasets |
| Worker inputs and results | Referenced artefacts or datasets, attached to a PlanTask |
| User and assistant discussion | Conversation history |

Scratchpad remains an ephemeral transport and cache for text values and exact
tool-result hand-offs. Datasets remain structured, queryable working sets.
Neither becomes the system of record for PlanTask orchestration.

## Conversation model

KoreChat owns a durable plan element for a conversation. It includes an
immutable baseline, one authoritative current plan, and a concise revision
history:

```json
{
  "baseline": {
    "objective": "Original agreed goal",
    "acceptance_criteria": [],
    "initial_tasks": []
  },
  "current": {
    "revision": 6,
    "status": "active",
    "objective": "Current agreed goal",
    "acceptance_criteria": [],
    "constraints": [],
    "tasks": [],
    "decisions": []
  },
  "revisions": [
    {
      "revision": 2,
      "at": "2026-08-02T12:00:00Z",
      "actor": "assistant",
      "reason": "Split research into two tasks.",
      "changes": []
    }
  ]
}
```

The baseline records what was originally agreed and is never mutated. The
`current` object is the live, authoritative plan. Each revision records who
made a change, when, why, and what changed.

For an initial implementation, this can live in dedicated KoreChat plan JSON
and plan-revision fields or tables associated with a conversation. It should
not be placed in the existing scratchpad JSON column.

## PlanTask model

Each PlanTask has a stable generated identifier and should separate its static
definition from its dynamic execution state. The static half describes what the
task is meant to do. The dynamic half records what has happened while working
it, including status progression, effort spent, and references to working
artifacts such as datasets and scratchpads.

```json
{
  "task_id": "task_research",
  "definition": {
    "title": "Inspect the existing implementation",
    "description": "Identify the current data flow and its constraints.",
    "task_statement": "Trace the current implementation path, identify the control points, and summarize the constraints that affect the change.",
    "priority": "normal",
    "depends_on": [],
    "owner": {
      "kind": "worker",
      "worker_id": "chat_example"
    },
    "input_refs": [
      {
        "kind": "koredoc",
        "name": "CurrentArchitecture.koredoc",
        "ref": "doc_current_architecture"
      }
    ]
  },
  "execution": {
    "status": "completed",
    "status_history": [
      {
        "status": "draft",
        "at": "2026-08-02T12:00:00Z"
      },
      {
        "status": "active",
        "at": "2026-08-02T12:01:00Z"
      },
      {
        "status": "completed",
        "at": "2026-08-02T12:05:00Z"
      }
    ],
    "effort": {
      "attempt_count": 1,
      "worker_runs": 1,
      "dataset_refs": ["dataset_trace_notes"],
      "scratchpad_refs": ["scratchpad_task_research_v1"]
    },
    "output_refs": [
      {
        "kind": "koredoc",
        "name": "ResearchSummary.koredoc",
        "ref": "doc_research_summary"
      }
    ],
    "result_summary": "Located the current data flow."
  }
}
```

The `definition` half should change only when the plan is intentionally
revised. The `execution` half changes as work progresses. This keeps the plan's
statement of intent separate from the operational record of how much work was
spent, what temporary working sets were used, and what results were produced.
When an input or output reference points to a KoreDoc document, the reference
should include the KoreDoc filename as well as a stable reference identifier so
the plan can show the exact document involved without relying on scratchpad-only
context.

Tasks use a small, enforced state machine:

The plan and its PlanTasks do not represent the same level of control. A plan is
the durable orchestration object for the whole multi-step objective across
prompts. PlanTasks are the individual work units inside that plan, and each one
progresses independently. Plan state answers whether the overall body of work
is still open and being managed. PlanTask state answers whether a specific unit of
work has not started, is active, is blocked, has failed, was cancelled, or is
complete.

```text
Plan: draft -> active -> completed
                |  |
                v  v
          blocked  cancelled

PlanTask: draft -> active -> completed
                |   |
                v   +-> failed
             blocked +-> cancelled
```

A PlanTask becomes `active` only after its dependencies have completed. Terminal
states require an explanatory reason or result reference. Dependency changes
must reject missing references and cycles.

## Progression across prompts

Plans persist for the lifetime of their KoreChat conversation. A prompt is an
interaction with the plan, not the plan's lifetime:

```text
Prompt 1: Plan the work   -> create a draft plan
Prompt 2: Start research  -> activate a bounded PlanTask or worker
Prompt 3: Change priority -> create a revision
Prompt 4: Show progress   -> read the plan summary
Prompt 5: Finish the work -> complete or close the plan
```

Normal chat remains conversational. A plan should be created for multi-step
or delegated work, either because the user asks for one or because the agent
proposes one and the user accepts it. It should not be created or automatically
executed for every prompt.

The normal agent context receives only a compact plan summary: the objective,
plan status, draft tasks that are now eligible, active tasks, blockers, and
recent decisions. The complete plan and history remain persisted and are
accessed through planning commands when needed. This keeps long-lived chats
from filling their context with old plan history.

## Worker-chat boundary

A worker operates on a PlanTask rather than directly on the parent plan:

```text
Parent creates PlanTask
      |
      v
PlanTask becomes active
      |
      v
Parent starts worker with PlanTask ID, immutable input references,
and an explicit capability policy
      |
      v
Worker produces result artefacts or datasets
      |
      v
Worker reports a terminal PlanTask result
      |
      v
Parent reviews or accepts the result, updates the plan,
and may advance dependent tasks
```

Workers must have an immutable input snapshot, an enforced tools policy, a
durable status/result envelope, and clearly owned cancellation and timeout
handling. Workers can update their assigned PlanTask's execution result, but do not
silently rewrite the parent plan, its dependencies, or its priorities. The
parent conversation owns replanning, cancellation, reassignment, and result
acceptance.

## Plan invariants

- The conversation has one authoritative current plan.
- PlanTask identifiers are stable and service-generated.
- Each PlanTask separates its definition from its execution record.
- Every mutation validates dependencies, state transitions, and references.
- Each mutation increments a plan revision and records an actor, timestamp,
  and concise reason.
- Only draft tasks whose dependencies are complete can be started.
- KoreDoc input and output references include both a stable reference ID and
  the document filename.
- A worker result is attached to a PlanTask as an output reference and summary.
- A plan can complete only when acceptance criteria are satisfied or explicitly
  waived.

## Initial planning skill surface

The initial planning capability should be one focused Planning system skill.
Its commands all work on the same persisted plan document; callers never edit
or resubmit the entire JSON structure.

That one skill should cover five capability groups:

- Plan lifecycle management: create, inspect, revise, complete, cancel, and delete a plan.
- PlanTask management: add, inspect, update, remove, reprioritize, and attach references or results.
- Execution orchestration: identify the next eligible PlanTask, activate one PlanTask, run the next step as a bounded convenience action, or run a bounded reassess-and-continue loop to completion.
- Status and reporting: return compact summaries, full task detail, history, blockers, and recent changes.
- Cleanup and retention: delete cancelled or draft-only plans, delete removable PlanTasks, and preserve revision evidence when destructive actions are allowed.

The concrete command surface can stay under the `plan_` prefix:

```text
# Plan lifecycle
plan_create(
    objective,
    acceptance_criteria = [],
    constraints         = [],
    initial_tasks       = []
)

plan_get(
    include_history = false
)

plan_get_summary()
    # Primary status view: where we are, what is next, and what needs attention.

plan_history(
    limit = 20
)

plan_reassess()
    # Preferred reassessment entry point. Evaluates progress, blockers, outputs,
    # and dependencies; returns a proposal only.

plan_reexamine()
    # Alias for plan_reassess().

plan_apply_revision(
    proposed_changes,
    reason
)

plan_complete(
    summary = ""
)

plan_cancel(
    reason
)

plan_clear()
    # Explicitly removes the active plan from the conversation.

plan_reopen(
    reason,
    proposed_changes = []
)
    # Reopens a completed or cancelled plan by creating a new revision and
    # returning the plan to active status.

plan_delete(
    reason,
    mode = "soft"
)
    # Destructive operation. Hard delete is allowed only by explicit policy.

# PlanTask management
plan_add_task(
    title,
    description = "",
    task_statement = "",
    depends_on  = [],
    priority    = "normal",
    input_refs  = []
)

plan_list_tasks(
    status       = null,
    owner_kind   = null,
    blocked_only = false
)

plan_update_task(
    task_id,
    title       = null,
    description = null,
    task_statement = null,
    priority    = null,
    depends_on  = null,
    input_refs  = null
)

plan_get_task(
    task_id,
    include_outputs = true
)

plan_set_task_status(
    task_id,
    status,
    reason = ""
)

plan_delete_task(
    task_id,
    reason
)
    # Allowed only when dependency and evidence rules permit removal.

plan_attach_input(
    task_id,
    reference,
    summary = ""
)

plan_attach_output(
    task_id,
    reference,
    summary = ""
)

# Execution orchestration
plan_get_next()
    # Returns the highest-priority draft PlanTask whose dependencies are complete.

plan_activate_task(
    task_id,
    reason = ""
)
    # Starts one specific eligible draft PlanTask.

plan_do_next()
    # Starts one eligible draft PlanTask; it does not silently execute the whole plan.

plan_run_to_completion(
    max_steps = null,
    reassess_between_steps = true,
    stop_on_blocked = true,
    stop_on_failed = true
)
    # Bounded orchestration loop. Advances one eligible PlanTask at a time,
    # reassesses after each step when enabled, and stops on blockers, failures,
    # user-input requirements, or completion.

# Status and reporting
plan_get_blockers()
    # Returns blocked PlanTasks and the reasons they cannot advance.

plan_record_decision(
    summary,
    rationale = "",
    affected_task_ids = []
)
```

`plan_reassess()` must return a proposed revision, not immediately mutate the
plan. `plan_reexamine()` can remain as a compatibility alias, but `plan_reassess()`
is the clearer name. `plan_apply_revision()` makes the chosen changes durable.
`plan_do_next()` is a convenience orchestration command built on the core
PlanTask and worker operations, and advances only one eligible draft PlanTask
at a time. `plan_run_to_completion()` is the bounded multi-step form: it loops
through one eligible PlanTask at a time, reassesses between steps, and stops
when the plan completes or when intervention is required.

`plan_create(...)` is the explicit replacement operation. It replaces any
existing plan stored on the conversation with the supplied plan and starts the
new revision sequence at 1. `plan_clear()` is the only operation that returns
the conversation to the no-plan state.

This supports the user flow of "run to completion" followed by "do it again
with more of XYZ". After completion, the normal follow-up is `plan_reopen()`
plus `plan_apply_revision()` with the new emphasis, constraints, or additional
PlanTasks. Prior outputs can be retained as inputs to the reopened plan so the
next pass extends previous work instead of starting from nothing.

There is no generic `plan_delete()` command. Use `plan_create(...)` to replace
the active plan and `plan_clear()` only when the conversation should have no
plan at all. Task removal, if introduced later, must be an explicit revision
that preserves dependency validation.

## `plan_get_summary()` contract

`plan_get_summary()` is the primary read command and the compact representation
that should enter normal agent context. It answers: where are we, what can
happen next, and what needs intervention?

```json
{
  "plan_status": "active",
  "objective": "Prepare the implementation proposal",
  "progress": {
    "completed": 2,
    "active": 1,
    "draft": 1,
    "blocked": 1,
    "total": 5
  },
  "where_we_are": [
    "Existing implementation has been inspected.",
    "The research worker is validating the migration path."
  ],
  "next": {
    "task_id": "task_design",
    "title": "Draft the implementation design",
    "status": "draft",
    "why_now": "Its only dependency is complete."
  },
  "needs_attention": [
    {
      "task_id": "task_security_review",
      "kind": "blocked",
      "reason": "Awaiting credentials from the user."
    }
  ],
  "recent_changes": [
    "Research PlanTask completed and evidence attached."
  ]
}
```

## Deliberate initial limits

Start with one active plan per conversation and a PlanTask list that can express a
dependency graph. Do not initially add nested plans, multiple concurrent plans,
unbounded automatic whole-plan execution, worker-controlled replanning, or broad generic
data-store abstractions. The first user interface can be modest: objective,
PlanTask list and statuses, expandable evidence/results, and a revision timeline.

These limits make the planning foundation visible, predictable, and stable
enough to support more advanced planning later.

## Example prompts that should create and progress a plan

The following user prompts should trigger plan creation, activation of one or
more PlanTasks, reassessment, or a bounded run-to-completion loop.

1. "Break this implementation request into a plan and show me the tasks before you start."
  Expected behavior: Create a draft plan with draft PlanTasks and return the initial summary without starting work.

2. "Set up a plan for migrating the planner data out of conversation JSON and into real tables."
  Expected behavior: Create a draft or active plan with PlanTasks for schema design, migration, API changes, UI impact, and validation.

3. "Start the first task in that plan and tell me what you are doing."
  Expected behavior: Activate the next eligible draft PlanTask and record execution progress for that task only.

4. "Do the next planned step."
  Expected behavior: Run `plan_do_next()` and advance exactly one eligible PlanTask.

5. "Keep going until the plan is blocked, complete, or you need input from me."
  Expected behavior: Run a bounded `plan_run_to_completion()` loop and stop on blocker, failure, completion, or required user intervention.

6. "Reassess the plan now that the schema is drafted and tell me what should change."
  Expected behavior: Run `plan_reassess()` and return proposed revisions without mutating the plan until accepted.

7. "Apply that revision and reprioritize API work ahead of UI work."
  Expected behavior: Create a new plan revision, update task ordering or priority, and keep execution history intact.

8. "Mark the migration design task complete and attach the design doc as output."
  Expected behavior: Complete the named PlanTask, attach the KoreDoc reference, and update the plan summary.

9. "What is blocked right now, and what can run next?"
  Expected behavior: Return `plan_get_summary()` or blocker detail with the next eligible PlanTask and any intervention points.

10. "Run this again with more emphasis on audit history and rollback safety."
   Expected behavior: Reopen or revise the completed plan, retain prior outputs as inputs, and add or adjust PlanTasks for the new emphasis.

11. "Cancel the current execution path and switch the plan to a lighter MVP."
   Expected behavior: Cancel active work where needed, create a revision with reduced scope, and resume from the new plan state.

12. "Delete that mistaken draft plan; we are not doing this work."
   Expected behavior: Soft-delete or remove the draft plan only when dependency and evidence rules allow it.
