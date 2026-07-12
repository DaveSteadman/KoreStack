# Delegate Skill

## Purpose
Spawn durable child tasks when a problem should be split into a controller step and one or more
isolated worker steps. Each worker gets its own session, tool subset, and result record, then
the parent can inspect or collect the result later.

Use this when the task benefits from divide-and-conquer. Do not use it for trivial one-tool actions.

## Trigger keyword: delegate

## Interface
- Module: `KoreAgent/app/system_skills/Delegate/delegate_skill.py`
- Functions:
  - `delegate(task_in: str, data_in: dict | None = None, process: dict | None = None, data_out: dict | None = None)`
  - `delegate_status(task_id: str)`
  - `delegate_collect(task_id: str)`

## Parameters

### `delegate(task_in, data_in = None, process = None, data_out = None)`
- `task_in` *(required)* - the exact child task to perform. This is the worker remit.
- `data_in` *(optional)* - structured inputs for the worker.
  Supported keys:
  - `scratchpad_keys: list[str]` - parent scratchpad keys to copy into the child session.
  - `datasets: list[str]` - parent datasets to copy into the child session.
  - `files: list[str]` - file references to mention in the child prompt.
  - `refs: list[str]` - arbitrary refs or IDs to mention in the child prompt.
  - `text: str` - inline text guidance for the child prompt.
- `process` *(required in practice)* - execution instructions for the worker.
  Supported keys:
  - `tools_allowlist: list[str]` - exact tool names the worker may use. Must contain at least one tool.
  - `max_iterations: int` - child tool-loop budget. Clamped to `1-12`.
  - `instructions: str` - extra procedural guidance for the worker.
  - `constraints: list[str]` - hard constraints added as bullet points in the child prompt.
  - `host_override: str` - optional LLM host override for the worker run.
- `data_out` *(optional)* - result contract for the worker.
  Supported keys:
  - `result_target: str` - where the final answer should be saved.
    Supported forms:
    - `scratchpad:<key>`
    - `dataset:<name>`
    - `file:<path>`
  - `result_format: str` - expected output form, e.g. `json array of records`, `bullet summary`, `csv text`.

### `delegate_status(task_id)`
- `task_id` *(required)* - delegated task id returned by `delegate(...)`.

### `delegate_collect(task_id)`
- `task_id` *(required)* - delegated task id returned by `delegate(...)`.

## Output
- `delegate(...)` - returns queue metadata including `status`, `task_id`, `child_session_id`, `result_target`, and `tools_allowlist`.
- `delegate_status(...)` - returns task lifecycle state such as `queued`, `running`, `completed`, or `failed`.
- `delegate_collect(...)` - returns the stored task result, including summary text, saved targets, token usage, log path, and any error.

## Delegation pattern
Treat delegation like a function call:
- `task_in` - what the worker must do
- `data_in` - what the worker receives
- `process` - how the worker should operate
- `data_out` - what the worker must return and where it should go

The controller should:
1. Break the problem into genuinely useful child remits.
2. Spawn the child with a narrow tool allowlist.
3. Poll with `delegate_status(...)` when needed.
4. Read the final output with `delegate_collect(...)`.
5. Synthesize the combined result at the parent level.

## Triggers
Invoke this skill when:
- the task naturally separates into controller and worker stages
- a child needs its own tool loop and should not clutter the parent context
- the child result should be written durably to scratchpad, dataset, or file

## Avoid
Do NOT use this skill when:
- one direct tool call will do
- the child task is too vague to define as a clear remit
- the controller does not know what result target it actually wants

## Critical rule
Never describe a delegate call as plain text or JSON in the chat response. If delegation is needed,
emit the tool call directly.
