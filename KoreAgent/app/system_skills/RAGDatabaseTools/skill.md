# RAGDatabaseTools Skill

## Purpose
- Manage live KoreRAG databases under the configured suite data path through a small set of explicit runtime tools.
- Use this skill when the task is to create a live runtime RAG database, inspect it, or update its runtime descriptor.
- Use this skill when the task is to create a live runtime RAG database, inspect it, update its runtime descriptor, or add and retrieve chunks.
- This skill follows config-backed runtime data paths. It does not write repo-side ingestor source files.

## Trigger keyword: rag

## Interface
- Module: `KoreAgent/app/system_skills/RAGDatabaseTools/rag_database_tools_skill.py`
- Functions:
  - `rag_database_list(include_runtime: bool = True)`
  - `rag_database_inspect(db_id: str)`
  - `rag_database_create(db_id: str, display_name: str = "", description: str = "")`
  - `rag_database_update_descriptor(db_id: str, display_name: str = "", description: str = "", source_url: str = "", licence: str = "", schedule: str = "", managed_by: str = "", ingestor: str = "", navigation_type: str = "", navigation_tables: list[str] | None = None, template_names: list[str] | None = None, runtime: bool = True)`
  - `rag_chunk_add(db_id: str, content: str, title: str = "", source: str = "", tags: str = "")`
  - `rag_chunk_get(db_id: str, chunk_id: int, include_content: bool = True)`
  - `rag_chunk_list(db_id: str, limit: int = 100, offset: int = 0)`
  - `rag_chunk_search(db_id: str, query: str, limit: int = 20, source: str = "", tags: str = "")`

## Parameters

### `rag_database_list(include_runtime)`
- `include_runtime` *(optional, default true)* - include the configured runtime databases directory.

### `rag_database_inspect(db_id)`
- `db_id` *(required)* - database identifier using `a-z`, `0-9`, and `_`, starting with a letter.

### `rag_database_create(db_id, display_name, description)`
- `db_id` *(required)* - database identifier using `a-z`, `0-9`, and `_`, starting with a letter.
- `display_name` *(optional)* - human-readable display name written into the runtime descriptor.
- `description` *(optional)* - runtime descriptor summary text.
- This creates a live runtime `.db` and descriptor under the configured suite data folder.

### `rag_database_update_descriptor(db_id, display_name, description, source_url, licence, schedule, managed_by, ingestor, navigation_type, navigation_tables, template_names, runtime)`
- `db_id` *(required)* - database identifier using `a-z`, `0-9`, and `_`, starting with a letter.
- `display_name` *(optional)* - descriptor display name.
- `description` *(optional)* - descriptor description.
- `source_url` *(optional)* - primary upstream source URL.
- `licence` *(optional)* - source/licence statement.
- `schedule` *(optional)* - one of `manual`, `daily`, `weekly`, `monthly`.
- `managed_by` *(optional)* - one of `user`, `ingestor`.
- `ingestor` *(optional)* - ingestor identifier, usually the same as `db_id`.
- `navigation_type` *(optional)* - navigation type string to write into the descriptor.
- `navigation_tables` *(optional)* - list of navigation table names.
- `template_names` *(optional)* - list of template file names used by this database.
- `runtime` *(optional, default true)* - must remain `true`; this tool only writes runtime descriptors in the configured suite data path.

### `rag_chunk_add(db_id, content, title, source, tags)`
- `db_id` *(required)* - runtime database identifier.
- `content` *(required)* - full chunk body text.
- `title` *(optional)* - chunk title.
- `source` *(optional)* - source URL or origin label.
- `tags` *(optional)* - tag string stored with the chunk.

### `rag_chunk_get(db_id, chunk_id, include_content)`
- `db_id` *(required)* - runtime database identifier.
- `chunk_id` *(required)* - numeric chunk identifier.
- `include_content` *(optional, default true)* - when `true`, return the full decompressed chunk content.

### `rag_chunk_list(db_id, limit, offset)`
- `db_id` *(required)* - runtime database identifier.
- `limit` *(optional, default 100)* - number of chunk rows to return, clamped to `1-500`.
- `offset` *(optional, default 0)* - pagination offset.

### `rag_chunk_search(db_id, query, limit, source, tags)`
- `db_id` *(required)* - runtime database identifier.
- `query` *(required)* - full-text search string.
- `limit` *(optional, default 20)* - number of matches to return, clamped to `1-200`.
- `source` *(optional)* - optional source substring filter.
- `tags` *(optional)* - optional tags substring filter.

## Output
- `rag_database_list(...)` - list of databases with source/runtime presence flags and template references.
- `rag_database_inspect(...)` - resolved runtime paths and descriptor payload for one database.
- `rag_database_create(...)` - runtime database paths and creation status.
- `rag_database_update_descriptor(...)` - descriptor path and the full updated descriptor payload.
- `rag_chunk_add(...)` - metadata for the newly added chunk.
- `rag_chunk_get(...)` - one chunk, optionally with full content.
- `rag_chunk_list(...)` - chunk metadata rows from one database.
- `rag_chunk_search(...)` - full-text search results with snippets.

## Triggers
Invoke this skill when the prompt contains any of these concepts or phrases:
- `create a rag database`
- `create a live rag database`
- `edit a rag database`
- `add a rag entry`
- `add a rag chunk`
- `insert into rag`
- `search rag`
- `get rag chunk`
- `descriptor json`

## Tool selection guidance
Use this skill for live runtime RAG databases in the configured suite data path.

Recommended workflow:
1. Call `rag_database_list(...)` or `rag_database_inspect(...)` to confirm the current state.
2. Use `rag_database_create(...)` when the goal is a new live runtime RAG database.
3. Use `rag_database_update_descriptor(...)` for runtime metadata updates.
4. Use `rag_chunk_add(...)` to insert content into a runtime database.
5. Use `rag_chunk_search(...)`, `rag_chunk_list(...)`, and `rag_chunk_get(...)` to inspect stored content.

This skill follows the configured suite data path and does not write repo-side ingestor source files.
