# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Scratchpad skill module for the MiniAgentFramework.
#
# Thin wrapper that re-exports the four LLM-callable functions from the shared scratchpad
# module (code/scratchpad.py).  Keeping the implementation in scratchpad.py allows
# prompt_tokens.py and orchestration.py to import the same module-level state without
# creating a circular dependency through the skill loader.
#
# Functions exposed to the tool-calling pipeline:
#   scratch_save(key, value)           -- store a named string value
#   scratch_load(key)                  -- retrieve a stored value
#   scratch_list()                     -- list active keys and sizes
#   scratch_delete(key)                -- remove one key
#   scratch_query(key, query, ...)     -- run an isolated LLM call on stored content, returns compact result
#
# Related modules:
#   - code/scratchpad.py                -- owns the module-level _STORE dict and all logic
#   - code/prompt_tokens.py             -- resolves {scratch:key} tokens using get_store()
#   - code/orchestration.py             -- injects active key names into the system prompt
#   - code/system_skills/Scratchpad/skill.md   -- LLM-facing documentation and examples
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
from KoreAgent.scratchpad import scratch_delete
from KoreAgent.scratchpad import scratch_dump
from KoreAgent.scratchpad import scratch_list
from KoreAgent.scratchpad import scratch_load
from KoreAgent.scratchpad import scratch_peek
from KoreAgent.scratchpad import scratch_query
from KoreAgent.scratchpad import scratch_save
from KoreAgent.scratchpad import scratch_search


# ====================================================================================================
# MARK: PUBLIC API
# ====================================================================================================
# All four functions are imported directly from code/scratchpad.py and re-exported here so that
# skill_executor._load_callable_from_module_path can resolve them via getattr() on this module.
#
# No additional logic lives here - see code/scratchpad.py for implementation details.
__all__ = [
    "scratch_delete",
    "scratch_dump",
    "scratch_list",
    "scratch_load",
    "scratch_peek",
    "scratch_query",
    "scratch_save",
    "scratch_search",
]
