# InDepthPlanner Skill

## Purpose
Create, inspect, revise, and persist durable multi-step plans for the active KoreChat conversation. This skill owns the persisted InDepthPlanner state rather than storing long-lived orchestration only in scratchpad.

## Trigger keyword: plan

## Interface
- Module: `KoreAgent/app/system_skills/InDepthPlanner/indepth_planner_skill.py`
- Functions:
  - `plan_create(objective: str, acceptance_criteria: list[str] | None = None, constraints: list[str] | None = None, initial_tasks: list[object] | None = None)`
  - `plan_clear()`
  - `plan_get(include_history: bool = False)`
  - `plan_get_summary()`
  - `plan_history(limit: int = 20)`
  - `plan_reassess()`
  - `plan_reexamine()`
  - `plan_add_task(title: str, task_id: str = "", description: str = "", task_statement: str = "", depends_on: list[str] | None = None, priority: str = "normal", input_refs: list[object] | None = None)`
  - `plan_list_tasks(status: str | None = None, owner_kind: str | None = None, blocked_only: bool = False)`
  - `plan_update_task(task_id: str, title: str | None = None, description: str | None = None, task_statement: str | None = None, priority: str | None = None, depends_on: list[str] | None = None, input_refs: list[object] | None = None)`
  - `plan_get_task(task_id: str, include_outputs: bool = True)`
  - `plan_set_task_status(task_id: str, status: str, reason: str = "")`
  - `plan_complete_task(task_id: str, result_summary: str, output_refs: list[object] | None = None)`
  - `plan_get_next()`
  - `plan_activate_task(task_id: str, reason: str = "")`
  - `plan_do_next()`
  - `plan_attach_input(task_id: str, reference: object, summary: str = "")`
  - `plan_attach_output(task_id: str, reference: object, summary: str = "")`
  - `plan_get_blockers()`
  - `plan_record_decision(summary: str, rationale: str = "", affected_task_ids: list[str] | None = None)`
  - `plan_complete(summary: str = "")`
  - `plan_cancel(reason: str)`
  - `plan_reopen(reason: str, proposed_changes: list[object] | None = None)`

## Plan lifecycle

- `plan_create(...)` creates the conversation's active plan. When a plan already exists,
  it explicitly replaces all of that plan's stored data with the new task data.
- The active plan remains stored in the KoreChat conversation through task updates,
  completion, cancellation, and reopening.
- `plan_clear()` is the only command that removes the active plan, returning the
  conversation to the no-plan state.
- PlanTasks receive stable, human-facing IDs (`"1"`, `"2"`, …) in creation order.
  Supply `task_id` when adding a task to choose a different stable ID; duplicate IDs
  are rejected. Use these IDs in follow-up requests, for example: “revise task 4 to
  consider XYZ and rerun it”. Older plans with legacy IDs also accept their displayed
  one-based task number as a compatibility alias.

- In all user-facing plan summaries, task lists, progress reports, and completion
  messages, lead with `Task <display_id>` (for example, `Task 3 — Data Synthesis — active`).
  Use the internal `task_id` only where it is required for a tool call or diagnosis.
- A successful `plan_create(...)` requires KoreChat to return the exact persisted
  plan payload. It reports an error when the running KoreChat service cannot store it.

## Running a PlanTask

When the user asks to run, continue, or rerun a specific PlanTask, treat the entire
PlanTask definition—not a single tool call—as the unit of work:

1. Read the task with `plan_get_task(...)` and activate it with `plan_activate_task(...)`.
2. Complete the task statement, including its required evidence and durable outputs.
3. Finish with `plan_complete_task(...)`, recording a concise result summary and output
   references. Do not claim completion without this call.
4. If the task cannot yet be completed, leave it `active` with a progress summary, or set
   it `blocked` with the precise reason. Do not present a partial sub-step as a completed task.

## Tool Selection Guidance
- Use these functions when the user asks for durable planning, multi-step execution tracking, plan status, plan revision, or run-to-completion style work management.
- Prefer `plan_get_summary()` for a compact current-state view.
- Prefer `plan_get()` or `plan_get_task()` when exact persisted details are needed.
- Use `plan_reassess()` to inspect progress and blockers without mutating the plan.
- Use `plan_add_task()` and `plan_update_task()` to evolve the plan deliberately rather than rewriting the whole structure.
