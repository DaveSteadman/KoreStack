# Worker Chats

## Purpose

Create a configured, isolated worker chat without switching away from the current chat. A worker
chat has a durable lifecycle and one explicit result contract. Its history and scratchpad remain
available for inspection, but callers use `chat_result` rather than inheriting worker context.

## Trigger keyword: worker chat

## Interface

- Module: `KoreAgent/app/system_skills/WorkerChats/worker_chat_skill.py`
- Functions:
  - `chat_spawn(prompt: str, tools_allowlist: list[str], result_target: str = "", result_format: str = "", max_iterations: int = 3, inputs: dict | None = None)`
  - `chat_status(chat_id: str)`
  - `chat_result(chat_id: str)`

## Rules

- Give each worker a narrow, independently verifiable prompt and tool allowlist.
- State the expected result format and, for material work, a durable result target.
- Do not assume the parent inherits the worker's hidden reasoning or chat history.
- Use `chat_result(chat_id)` as the boundary between task layers.

## Output

`chat_spawn` returns `chat_id` and the isolated `session_id`. `chat_result` returns the durable
`result` object containing summary, artefact references, saved datasets/keys, token metadata, and
any error.
