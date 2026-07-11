# Scratchpad Skill

## Purpose
Store and retrieve named working values within a session so that bulk data returned by other skills
(web pages, file content, computation results) can be parked under a short key and referenced later
without consuming context window space.  Use this skill whenever the plan involves multi-step tool
chains where an intermediate result is needed again in a later step.

## Trigger keyword: scratchpad

## Interface
- Module: `KoreAgent/app/system_skills/Scratchpad/scratchpad_skill.py`
- Functions:
  - `scratchpad_save(key: str, value: str)`
  - `scratchpad_load(key: str)`
  - `scratchpad_list()`
  - `scratchpad_dump()`
  - `scratchpad_delete(key: str)`
  - `scratchpad_search(substring: str)`
  - `scratchpad_peek(key: str, substring: str, context_chars: int = 250)`
  - `scratchpad_query(key: str, query: str, save_result_key: str = "", instructions: str = "")`

## Parameters

### `scratchpad_save(key, value)`
- `key` *(required)* - short alphanumeric identifier for the value, e.g. `"webresult"` or `"step1_output"`. Letters, digits, and underscores only. Stored lowercased.
- `value` *(required)* - the string content to store. Overwrites any previous value at that key.
  **IMPORTANT**: the value must be the return value of a prior skill call (Wikipedia, WebSearch,
  CodeExecute, FileAccess, etc.) - never pass LLM-generated text inline in this argument.
  Inline text containing double-quote characters or apostrophes will produce a JSON parsing
  error in the tool call and the save will fail entirely.

### `scratchpad_load(key)`
- `key` *(required)* - the key to retrieve. Returns an error message when the key does not exist.

### `scratchpad_list()`
No parameters.

### `scratchpad_dump()`
No parameters.  Returns the full content of every key - use this to inspect stored values during debugging.

### `scratchpad_delete(key)`
- `key` *(required)* - the key to remove from the scratchpad.

### `scratchpad_search(substring)`
- `substring` *(required)* - case-insensitive text to search for within stored values. Returns all keys whose value contains the substring.

### `scratchpad_peek(key, substring, context_chars = 250)`
- `key` *(required)* - the scratchpad key to inspect.
- `substring` *(required)* - case-insensitive text to locate within the stored value.
- `context_chars` *(optional, default 250)* - characters to include before and after the match.

### `scratchpad_query(key, query, save_result_key = "", instructions = "")`
- `key` *(required)* - the scratchpad key whose full content will be used as input.
- `query` *(required)* - natural-language question or instruction to apply to the stored content.
- `save_result_key` *(optional)* - if provided, the extracted answer is also saved to this scratchpad key.
- `instructions` *(optional)* - if provided, replaces the default "precise extractor" system prompt entirely.
  Use this to change the isolated LLM's persona for synthesis, transformation, or generation tasks
  rather than extraction. When omitted the default extractor behaviour applies.

  Runs the query against the stored content in a **clean, isolated LLM context** - the raw content
  never enters the caller's context window.  Use this instead of `scratchpad_load` when the stored
  value is large and you only need a compact extracted answer.

## Output
- `scratchpad_save(...)` - returns `"Saved to scratchpad key '<key>' (N chars)"` on success, or `"Error: ..."`.
- `scratchpad_load(...)` - returns the stored string value, or an error message if the key is not found.
- `scratchpad_list()` - returns a formatted list of active keys and their sizes, or `"Scratchpad is empty."`.
- `scratchpad_dump()` - returns every key followed by its full stored value. Use to inspect scratchpad contents for debugging.
- `scratchpad_delete(...)` - returns confirmation or `"Scratchpad key '<key>' not found - nothing deleted."`.
- `scratchpad_search(...)` - returns a formatted list of matching key names and sizes, or `"No scratchpad keys contain the substring '<text>'."` when no match is found.
- `scratchpad_peek(...)` - returns `[Match in 'key' at char N / M total]` followed by the surrounding text with `>>>match<<<` highlighting, or an error string when the key or substring is not found.
- `scratchpad_query(...)` - returns the compact extracted answer from the isolated LLM call, or `"Not found in content."` when the query cannot be answered from the stored value.  When `save_result_key` is provided, prepends `[Result saved to '<key>']` to the output.

## Tool selection guidance

**Check the scratchpad before making any web or file request.**

If the system prompt lists active scratchpad keys, always consider whether the data needed to
answer the current question might already be stored there from an earlier step in this session.
Re-fetching data that is already in the scratchpad wastes an entire LLM call and a network round-trip.

Decision tree when data may already be stored:
1. Call `scratchpad_list()` if the keys are not already visible.
2. If a relevant key exists and you need a specific answer from it - use `scratchpad_query(key, question)`. The query runs in an isolated context; the raw content never enters the main window.
3. If a relevant key exists and you need the full content (e.g. to write it to a file) - use `scratchpad_load(key)` or `{scratchpad:key}` token substitution.
4. If a relevant key exists and you need to locate a specific passage - use `scratchpad_peek(key, substring)`.
5. Only proceed to a web or file skill if the data is confirmed to not be in the scratchpad.

Tool selection hierarchy (prefer earlier options when they can provide the answer):
- `scratchpad_query` / `scratchpad_load` - data already in session, zero network cost
- `lookup_wikipedia` - stable factual reference, single fast call
- `fetch_page_text(query=...)` - known URL, isolated extraction
- `search_web_text` + `fetch_page_text(query=...)` - URL unknown, single page answer
- `research_traverse` - multi-source investigation, most expensive; use only when simpler tools cannot settle the question

## Token substitution
Any skill argument containing `{scratchpad:key}` is automatically resolved to the stored value
before the skill function is called.  This lets you write:
  `file_write("exports/result.txt", "{scratchpad:webresult}")`
without an explicit `scratchpad_load` step.

## Triggers
Invoke this skill when the prompt contains any of these concepts or phrases:
- `save to scratchpad`, `store in scratchpad`, `park this result`
- `load from scratchpad`, `retrieve from scratchpad`, `get scratchpad value`
- `list scratchpad`, `what is in the scratchpad`
- `dump scratchpad`, `show scratchpad contents`, `inspect scratchpad`, `debug scratchpad`
- `delete from scratchpad`, `clear scratchpad key`
- `search scratchpad`, `find scratchpad keys containing`, `which scratchpad keys have`
- `peek at scratchpad`, `show context around`, `find text in scratchpad key`
- `query scratchpad`, `ask scratchpad`, `extract from scratchpad`, `filter scratchpad`, `run query on scratchpad key`

## Scratchpad integration
This is the scratchpad skill itself.  All other skills reference this one for their
scratchpad integration patterns.  No self-referential use needed.

## Examples
- `scratchpad_save("webresult", "page content here...")` - parks a fetched page for later use
  - Returns: `"Saved to scratchpad key 'webresult' (21 chars)"`
- `scratchpad_load("webresult")` - retrieves the previously parked page
  - Returns: `"page content here..."`
- `scratchpad_list()` - shows key names and sizes only; use `scratchpad_dump()` to see the actual values
  - Returns: `"Scratchpad keys:\n  webresult  (21 chars)"`
- `scratchpad_peek("webresult", "content", 100)` - show 100 chars around first occurrence of "content" in key `webresult`
  - Returns: `"[Match in 'webresult' at char 5 / 21 total]\nepage>>>content<<<here"`
- `scratchpad_query("racedata", "Which drivers won at Monaco?")` - extract Monaco winners from a large stored result in an isolated context
  - Returns: the compact LLM-extracted answer, never the full raw value
- `scratchpad_query("racedata", "List only Ferrari wins", "ferrari_wins")` - same but also saves result to key `ferrari_wins`
  - Returns: `"[Result saved to 'ferrari_wins']\n<extracted text>"`
- `scratchpad_dump()` - shows every key and its full content
  - Returns: `"Scratchpad dump:\n\n[webresult]\npage content here..."`
- `scratchpad_delete("webresult")` - removes the key
  - Returns: `"Deleted scratchpad key 'webresult'."`
- `file_write("exports/out.txt", "{scratchpad:webresult}")` - FileAccess write using token substitution, no extra scratchpad_load needed
- `scratchpad_search("error")` - find all keys containing "error" in their value
  - Returns: `"Keys matching 'error':\n  logdata  (312 chars)"`
