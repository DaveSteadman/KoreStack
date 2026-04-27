# FileAccess Skill

## Purpose
Interface for all file read, write, append, and search operations. All relative paths resolve under `./data/`; a `"./"` prefix anchors a path at the workspace root instead. Paths that escape the workspace root are rejected.

## Trigger keyword: file

## Interface
- Module: `code/KoreAgent/system_skills/FileAccess/file_access_skill.py`
- Functions:
  - `file_write(path: str, content: str)`
  - `file_append(path: str, content: str)`
  - `file_read(path: str, max_chars: int = 8000)`
  - `file_write_from_scratch(scratch_key: str, path: str)`
  - `file_find(keywords: list[str], search_root: str = "")`
  - `folder_find(keywords: list[str], search_root: str = "")`
  - `folder_create(path: str)`
  - `folder_exists(path: str)`

## Parameters

### `file_write_from_scratch(scratch_key, path)`
- `scratch_key` *(required)* - scratchpad key holding the content to write, e.g. `"_tc_r5_fetch_page_text"` (the key shown in a truncation notice). Reads the stored value directly without requiring a separate `scratch_load` call.
- `path` *(required)* - destination path; same resolution rules as `file_write`.

Use this when large content was auto-saved to a scratchpad key (e.g. a web page fetch that was truncated in the tool message). Avoids putting large content into tool call arguments where JSON encoding can fail.

### `folder_create(path)`
- `path` *(required)* - path of the directory to create, resolved under `data/`, e.g. `"webresearch/01-Mine/2026-03-22"`. Creates all missing parent directories. Safe to call if the folder already exists.

### `folder_exists(path)`
- `path` *(required)* - workspace-relative path to check.
- Returns `"yes"` or `"no"` so the model can branch on the result.

### `file_write(path, content)`
- `path` *(required)* - workspace-relative path. A bare name like `"x.txt"` resolves to `data/x.txt`. A path starting with `"./"` resolves from workspace root.
- `content` *(required)* - content to write. Overwrites the file if it exists. Supports `{scratch:key}` token substitution - use `"{scratch:mykey}"` to write scratchpad content directly without calling `scratch_load` first.

### `file_append(path, content)`
- `path` *(required)* - same path rules as `file_write`.
- `content` *(required)* - content to append. A newline is added automatically if missing. Supports `{scratch:key}` token substitution - use `"{scratch:mykey}"` to append scratchpad content directly.

### `file_read(path, max_chars = 8000)`
- `path` *(required)* - same path rules as `file_write`.
- `max_chars` *(optional, default 8000)* - maximum characters to return; content is truncated with `[truncated]` if exceeded.

### `file_find(keywords, search_root = "")`
- `keywords` *(required)* - list of case-insensitive fragments that must ALL appear in the file name, e.g. `["pulse", "2026"]`.
- `search_root` *(optional, default "")* - workspace-relative directory to restrict the search, e.g. `"data"`. Leave empty to search the whole workspace.

### `folder_find(keywords, search_root = "")`
- `keywords` *(required)* - list of case-insensitive fragments that must ALL appear in the folder name.
- `search_root` *(optional, default "")* - workspace-relative directory to restrict the search. Leave empty to search the whole workspace.

## Output
- `file_write(...)` - returns `"Wrote data/filename.txt"` on success, or `"Error: ..."` on failure.
- `file_append(...)` - returns `"Appended data/filename.txt"` on success, or `"Error: ..."` on failure.
- `file_read(...)` - returns the file content as a string, or `"File not found: ..."` if the file does not exist.
- `file_find(...)` - returns a newline-separated list of matching workspace-relative paths, or a `"No files found..."` message.
- `folder_find(...)` - returns a newline-separated list of matching workspace-relative paths, or a `"No folders found..."` message.
- `file_write_from_scratch(...)` - returns `"Wrote data/file.md (12345 chars from scratch key '_tc_r5_fetch_page_text')"` on success, or `"Error: ..."` on failure.
- `folder_create(...)` - returns `"Created folder: path"` or `"Folder already exists: path"`, or `"Error: ..."` on failure.
- `folder_exists(...)` - returns `"yes"` or `"no"`.

## Triggers
Invoke this skill when the prompt contains any of these concepts or phrases:
- `write to file`, `create file`, `save to file`
- `write page to file`, `save fetched content to file`, `write from scratch`, `write scratch to file`
- `append to file`, `add to file`
- `read file`, `show file`, `open file`, `contents of`
- `find file`, `find folder`, `locate file`, `search for file`
- `create folder`, `make folder`, `create directory`, `folder exists`, `does folder exist`

## Scratchpad integration
The `content` argument of `file_write` and `file_append` supports `{scratch:key}` token substitution.
This means you can park a large result (web search, code output, file content) with `scratch_save`,
then write it to disk without a separate `scratch_load` call.

- `file_write("data/result.txt", "{scratch:searchresult}")` - writes the stored value directly
- `file_append("data/log.txt", "{scratch:codeoutput}")` - appends the stored value directly

## Examples
- `file_write("notes/meeting.txt", "Discuss project timeline")` - creates or overwrites the file
  - Returns: `"Wrote data/notes/meeting.txt"`
- `file_append("data/log.txt", "new entry")` - appends a line
  - Returns: `"Appended data/log.txt"`
- `file_read(path="data/log.txt")` - returns full content up to 8000 chars
- `file_find(["pulse"], "data")` - find files with "pulse" in the name under data/
  - Returns: `"data/pulse_log.csv\ndata/sys_pulse.csv"`
- `file_find(["test", "2026"])` - find files whose name contains both fragments
- `folder_find(["2026-03"])` - find folders containing "2026-03" in the name
