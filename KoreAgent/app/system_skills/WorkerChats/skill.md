# Worker Chats

## Purpose

Run a bounded, isolated worker prompt as an LLM function call. The worker has its own chat,
discovers tools when needed, and returns one durable result to the calling chat.

## Trigger keyword: worker chat

## Interface

- Module: `KoreAgent/app/system_skills/WorkerChats/worker_chat_skill.py`
- Functions:
  - `chat_spawn(prompt: str, result_target: str = "", result_format: str = "", max_iterations: int = 3, inputs: dict | None = None)`
  - `chat_status(chat_id: str)`
  - `chat_result(chat_id: str)`
  - `chat_cancel(chat_id: str)`

## Rules

- Give each worker a narrow, independently verifiable prompt.
- Let the worker discover and activate task capabilities for itself; do not curate an allowlist.
- State the expected result format and, for material work, a durable result target.
- Do not assume the parent inherits the worker's hidden reasoning or chat history.
- The default result target is the parent's named scratchpad entry `prompt_result`.

## Output

`chat_spawn` returns only after its bounded worker run is terminal. It writes the result summary to
the parent scratchpad as `prompt_result` by default and returns the same durable `result` object,
with its `chat_id`, artefact references, saved datasets/keys, token metadata, and any error.
`chat_result` remains available for later inspection. `chat_cancel` stops a queued or running
worker and records a terminal cancellation state.
