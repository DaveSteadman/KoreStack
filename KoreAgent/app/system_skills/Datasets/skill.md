# Datasets Skill

## Purpose
Store and refine structured record collections across prompts and sessions. Use datasets when a tool
returns a collection of objects such as feed items, search results, references, or any other
record-shaped working set that needs iterative filtering.

## Trigger keyword: datasets

## Interface
- Module: `KoreAgent/app/system_skills/Datasets/datasets_skill.py`
- Functions:
  - `dataset_save(name: str, records: list[dict], source_tool: str = "", source_args: dict = None, replace: bool = False)`
  - `dataset_rename(name: str, new_name: str)`
  - `dataset_list()`
  - `dataset_inspect(name: str)`
  - `dataset_get(name: str, indices: list[int] = None, max_records: int = 0, fields: list[str] = None, offset: int = 0, limit: int = 0)`
  - `dataset_write_koredoc(name: str, folder_path: str, document_name: str = "", fields: list[str] = None, offset: int = 0, limit: int = 0)`
  - `dataset_delete(name: str)`
  - `dataset_drop_where(name: str, predicate: str, save_as: str = "", replace: bool = False)`
  - `dataset_expand_full_text(name: str, save_as: str = "", replace: bool = False, offset: int = 0, limit: int = 0)`
  - `dataset_filter(name: str, prompt: str, save_as: str = "", replace: bool = False, fields: list[str] = None, excerpt_chars: int = 300)`

## Parameters

### `dataset_save(name, records, source_tool = "", source_args = None, replace = False)`
- `name` *(required)* - dataset name. Letters, digits, and underscores only.
- `records` *(required)* - list of structured objects to store.
- `source_tool` *(optional)* - tool name that produced the records.
- `source_args` *(optional)* - original tool arguments as an object.
- `replace` *(optional, default false)* - overwrite an existing dataset of the same name.

### `dataset_rename(name, new_name)`
- `name` *(required)* - current dataset name.
- `new_name` *(required)* - replacement name. Renames the label only; the internal dataset id stays fixed.

### `dataset_list()`
No parameters.

### `dataset_inspect(name)`
- `name` *(required)* - dataset to inspect.

### `dataset_get(name, indices = None, max_records = 0, fields = None, offset = 0, limit = 0)`
- `name` *(required)* - dataset to retrieve from.
- `indices` *(optional)* - specific zero-based record indices to return.
- `max_records` *(optional)* - maximum number of leading records to return when indices are omitted.
- `offset` *(optional)* - zero-based starting offset for paged reads when indices are omitted.
- `limit` *(optional)* - page size for paged reads when indices are omitted.
- `fields` *(optional)* - field projection for each returned record.

### `dataset_write_koredoc(name, folder_path, document_name = "", fields = None, offset = 0, limit = 0)`
- `name` *(required)* - dataset to export.
- `folder_path` *(required)* - folder path relative to the shared KoreDocs/datauser root, such as `feeds2` or `RadarData/exports`. Legacy `KoreDocs/...` prefixes are accepted for compatibility but should not be preferred in new calls.
- `document_name` *(optional)* - `.koredoc` file name; defaults to the dataset name.
- `fields` *(optional)* - field projection for each exported record; defaults to the dataset schema.
- `offset` *(optional)* - zero-based starting offset for paged exports.
- `limit` *(optional)* - maximum number of records to export; omit or set to 0 to export all remaining records.

### `dataset_delete(name)`
- `name` *(required)* - dataset to remove.

### `dataset_drop_where(name, predicate, save_as = "", replace = False)`
- `name` *(required)* - dataset to transform.
- `predicate` *(required)* - deterministic drop rule such as `duplicate by url` or `missing field body`.
- `save_as` *(optional)* - name for a forked dataset. When omitted and `replace` is false, a derived name is created automatically.
- `replace` *(optional, default false)* - mutate the original dataset in place.

### `dataset_expand_full_text(name, save_as = "", replace = False, offset = 0, limit = 0)`
- `name` *(required)* - source dataset whose records already include `artifact_ref` values from `koredata_search(...)`.
- `save_as` *(optional)* - name for the enriched fork. When omitted and `replace` is false, a `_fulltext` dataset name is derived automatically.
- `replace` *(optional, default false)* - mutate the original dataset in place.
- `offset` *(optional)* - zero-based starting offset for partial enrichment of large datasets.
- `limit` *(optional)* - maximum number of records to enrich; omit or set to 0 to enrich all remaining records.

### `dataset_filter(name, prompt, save_as = "", replace = False, fields = None, excerpt_chars = 300)`
- `name` *(required)* - dataset to filter.
- `prompt` *(required)* - keep/drop instruction for the LLM.
- `save_as` *(optional)* - name for a forked dataset. When omitted and `replace` is false, a derived name is created automatically.
- `replace` *(optional, default false)* - mutate the original dataset in place.
- `fields` *(optional)* - projected fields to show the LLM for each record.
- `excerpt_chars` *(optional, default 300)* - maximum characters to include from large text fields when projection needs a snippet.

## Output
- `dataset_save(...)` - confirmation with dataset id, count, and schema.
- `dataset_rename(...)` - confirmation including the unchanged dataset id.
- `dataset_list()` - compact manifest list of all active datasets.
- `dataset_inspect(...)` - JSON manifest with metadata, history tail, and sample records.
- `dataset_get(...)` - JSON object with paging metadata plus the selected records under `records`.
- `dataset_write_koredoc(...)` - deterministic export of real dataset rows to a KoreDocs `.koredoc` document.
- `dataset_delete(...)` - confirmation or not-found message.
- `dataset_drop_where(...)` - confirmation describing the predicate and resulting counts.
- `dataset_expand_full_text(...)` - confirmation describing how many rows were expanded through `artifact_ref` and how many were skipped.
- `dataset_filter(...)` - confirmation describing the filter pass and resulting counts.

## Tool selection guidance

Use datasets for structured collections. Use the existing scratchpad for string content.

Typical workflow:
1. Save or auto-ingest a record collection into a dataset.
2. Inspect it with `dataset_list()` or `dataset_inspect(name)`.
3. Remove obvious junk cheaply with `dataset_drop_where(...)`.
4. Apply judgement with `dataset_filter(...)`, using `fields` so the model sees only the relevant record projection.
5. Fetch the retained records with `dataset_get(...)` when you need them for synthesis.
6. When KoreData search results include `artifact_ref` and the user wants full bodies instead of snippets, call `dataset_expand_full_text(...)` rather than hand-looping per-row fetches.
7. Use `dataset_write_koredoc(...)` when the user wants a faithful KoreDocs export of real rows.

Prefer forking over replacement. Keep earlier stages unless the user explicitly wants to overwrite them.

## Triggers
Invoke this skill when the prompt contains any of these concepts or phrases:
- `dataset`, `working set`, `record set`, `retained set`
- `filter these results`, `keep the best items`, `dedupe these rows`
- `save this list for later`, `work on these records across prompts`
- `inspect the current set`, `show the retained items`

## Examples
- `dataset_save("feed_items_raw", [{"title": "A", "url": "https://a"}], source_tool="koredata_search")`
- `dataset_drop_where("feed_items_raw", "duplicate by url", save_as="feed_items_deduped")`
- `dataset_filter("feed_items_deduped", "Keep only items directly about topic X", save_as="feed_items_relevant", fields=["title", "source", "published_at", "url", "snippet"])`
- `dataset_expand_full_text("feed_items_relevant", save_as="feed_items_fulltext")`
- `dataset_get("feed_items_fulltext", max_records=5, fields=["title", "url", "artifact_ref"])`