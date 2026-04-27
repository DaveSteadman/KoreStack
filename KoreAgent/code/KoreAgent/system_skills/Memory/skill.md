# Memory Skill

## Purpose
Persist and recall durable user-stated facts across sessions - identity, preferences, project context, and environment facts. A newer fact on the same subject supersedes the older one. Do not store questions, commands, or ephemeral data such as current time or system stats. Facts persist in `memory_store.json` with category, timestamps, and access tracking.

## Trigger keyword: memory

## Interface
- Module: `code/KoreAgent/system_skills/Memory/memory_skill.py`
- Functions:
  - `store_prompt_memories(user_prompt: str)`
  - `recall_relevant_memories(user_prompt: str, limit: int = 5, min_score: float = 0.25)`
  - `extract_environment_facts(user_prompt: str)`
  - `get_memory_store_text()`

## Parameters

### `store_prompt_memories(user_prompt)`
- `user_prompt` *(required)* - raw user text to extract facts from and store.

### `recall_relevant_memories(user_prompt, limit = 5, min_score = 0.25)`
- `user_prompt` *(required)* - current prompt used as the relevance query.
- `limit` *(optional, default 5)* - maximum number of memories to return.
- `min_score` *(optional, default 0.2)* - minimum token-overlap relevance threshold; lower values return more results.

### `extract_environment_facts(user_prompt)`
- `user_prompt` *(required)* - raw user text to inspect for environment-specific facts only.

### `get_memory_store_text()`
No parameters.

## Output
- `store_prompt_memories(...)` - returns `"Stored N new memory fact(s)."` or `"Updated N existing memory fact(s)."`.
- `recall_relevant_memories(...)` - returns a formatted ranked list of memories with category and relevance score.
- `extract_environment_facts(...)` - returns a list of candidate environment facts extracted from the prompt.
- `get_memory_store_text()` - returns the full pretty-printed JSON of the memory store.

## Triggers
Invoke this skill when the prompt contains any of these concepts or phrases:
- `remember`, `store this`, `save this fact`, `note that`
- `recall`, `what do you know about`, `do you remember`
- `my name is`, `I prefer`, `our project is`, `the default model is`
- `show memory`, `memory store`, `what have you stored`

## Scratchpad integration
Not applicable.  Memory persists durable facts across sessions in `memory_store.json`.
Scratchpad holds in-session working values in RAM.  They serve different lifetimes and
should not be substituted for each other.  Use Memory for facts that must outlive a session;
use Scratchpad for intermediate results within a single session.

## Examples
- `store_prompt_memories("Our workspace path is c:/Util/GithubRepos/MiniAgentFramework")` - stores a new environment fact
  - Returns: `"Stored 1 new memory fact(s)."`
- `store_prompt_memories("Our workspace path is c:/Util/NewLocation")` - updates the existing fact on the same subject
  - Returns: `"Updated 1 existing memory fact(s)."`
- `recall_relevant_memories("what is our workspace path")` - retrieve relevant stored facts
  - Returns: `"Relevant memories:\n- [environment] (0.60) Our workspace path is c:/Util/NewLocation"`
