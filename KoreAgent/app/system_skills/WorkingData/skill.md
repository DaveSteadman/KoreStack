# Working Data

## Purpose

Store prompt-supporting material outside the active context window. A named Working Data item can
be a single statement, generated text, an object, or a list of structured records. Use it to retain
large tool output across steps and retrieve only the relevant part later.

## Trigger keyword: working data

## Interface

- Module: `KoreAgent/app/system_skills/WorkingData/working_data_skill.py`
- Functions:
  - `working_data_save(name: str, value: str | list[dict] | dict, source_tool: str = "", source_args: dict = None, replace: bool = False)`
  - `working_data_get(name: str, indices: list[int] = None, max_records: int = 0, fields: list[str] = None, offset: int = 0, limit: int = 0)`
  - `working_data_list()`
  - `working_data_inspect(name: str)`
  - `working_data_delete(name: str)`
  - `working_data_clear()`
  - `working_data_search(substring: str)`
  - `working_data_peek(name: str, substring: str, context_chars: int = 250)`
  - `working_data_query(name: str, query: str, save_result_name: str = "", instructions: str = "")`
  - `working_data_rename(name: str, new_name: str)`
  - `working_data_filter(name: str, prompt: str, save_as: str = "", replace: bool = False, fields: list[str] = None, excerpt_chars: int = 300)`
  - `working_data_drop_where(name: str, predicate: str, save_as: str = "", replace: bool = False)`
  - `working_data_expand_full_text(name: str, save_as: str = "", replace: bool = False, offset: int = 0, limit: int = 0)`
  - `working_data_export(name: str, folder_path: str, document_name: str = "", fields: list[str] = None, offset: int = 0, limit: int = 0)`

## Parameters

- `name` is a short identifier using letters, digits, and underscores.
- `value` accepts a string for one statement or text item, an object for one record, or a list of
  objects for a record collection.
- Use `working_data_get` with `indices`, `fields`, `offset`, or `limit` to retrieve only needed
  records; use `working_data_query` to ask an isolated LLM about a large text item.

## Tool selection guidance

Use Working Data whenever useful material must survive more than one tool call without repeatedly
entering the main prompt. Check `working_data_list` before re-fetching information. Do not use it for
the final answer unless the final answer itself needs to be retained for a later operation.
