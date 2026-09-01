# Working Data

## Purpose

Store prompt-supporting material outside the active context window. A named Working Data item can
be a single statement, generated text, an object, or a list of structured records. Use it to retain
large tool output across steps and retrieve only the relevant part later.

## Interface

- Module: `KoreAgent/app/system_skills/WorkingData/working_data_skill.py`
- Functions:
  - `working_data_save(name: str, value: str | list[dict] | dict, source_tool: str = "", source_args: dict = None, replace: bool = False)`
  - `working_data_get(name: str, indices: list[int] = None, max_records: int = 0, fields: list[str] = None, offset: int = 0, limit: int = 0, excerpt_chars: int = 1200)`
  - `working_data_list()`
  - `working_data_inspect(name: str)`
  - `working_data_delete(name: str)`
  - `working_data_clear()`
  - `working_data_search(substring: str)`
  - `working_data_peek(name: str, substring: str, context_chars: int = 250)`
  - `working_data_query(name: str, query: str, save_result_name: str = "", instructions: str = "")`
  - `working_data_rename(name: str, new_name: str)`
  - `working_data_rank(name: str, criteria: str, count: int = 5, save_as: str = "", fields: list[str] = None, excerpt_chars: int = 700, offset: int = 0, limit: int = 30)`
  - `working_data_select(name: str, indices: list[int], save_as: str = "")`
  - `working_data_fetch_full_text(name: str, indices: list[int] = None, save_as: str = "")`

## Parameters

- `name` is a short identifier using letters, digits, and underscores.
- `value` accepts a string for one statement or text item, an object for one record, or a list of
  objects for a record collection.
- `working_data_get` returns at most five records by default and excerpts text fields. Pass explicit
  `indices`, `fields`, `limit`, and `excerpt_chars` when a different bounded view is needed.
- `working_data_rank` makes one isolated ranking pass over compact record views and saves the selected
  subset. Use `criteria="score"` when records already carry a relevance score; this is a direct
  deterministic ranking. It ranks at most 30 candidates at once; use `offset` and `limit` to process
  a larger collection in pages before ranking the finalists.
- `working_data_select` saves known record indices as a subset. `working_data_fetch_full_text` retrieves
  full text only for that subset (at most five records).

## Tool selection guidance

Use Working Data whenever useful material must survive more than one tool call without repeatedly
entering the main prompt. An inspect result is a compact preview, not material to copy into Python.
For a report with an exact number of items: inspect; rank or select that exact number of records;
fetch full text for that same subset; then write the final answer directly. Check `working_data_list`
before re-fetching information. Do not use Python for any part of this workflow.
