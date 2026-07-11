# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Scratchpad skill module for KoreAgent.
#
# Thin wrapper that re-exports the four LLM-callable functions from the shared scratchpad
# module (code/scratchpad.py).  Keeping the implementation in scratchpad.py allows
# prompt_tokens.py and orchestration.py to import the same module-level state without
# creating a circular dependency through the skill loader.
#
# Functions exposed to the tool-calling pipeline:
#   scratchpad_save(key, value)           -- store a named string value
#   scratchpad_load(key)                  -- retrieve a stored value
#   scratchpad_list()                     -- list active keys and sizes
#   scratchpad_delete(key)                -- remove one key
#   scratchpad_query(key, query, ...)     -- run an isolated LLM call on stored content, returns compact result
#
# Related modules:
#   - code/scratchpad.py                -- owns the module-level _STORE dict and all logic
#   - code/prompt_tokens.py             -- resolves {scratchpad:key} tokens using get_store()
#   - code/orchestration.py             -- injects active key names into the system prompt
#   - code/system_skills/Scratchpad/skill.md   -- LLM-facing documentation and examples
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
from scratchpad import scratchpad_delete
from scratchpad import scratchpad_dump
from scratchpad import scratchpad_list
from scratchpad import scratchpad_load
from scratchpad import scratchpad_peek
from scratchpad import scratchpad_query
from scratchpad import scratchpad_save
from scratchpad import scratchpad_search


# ====================================================================================================
# MARK: PUBLIC API
# ====================================================================================================
# All four functions are imported directly from code/scratchpad.py and re-exported here so that
# skill_executor._load_callable_from_module_path can resolve them via getattr() on this module.
#
# No additional logic lives here - see code/scratchpad.py for implementation details.
__all__ = [
    "scratchpad_delete",
    "scratchpad_dump",
    "scratchpad_list",
    "scratchpad_load",
    "scratchpad_peek",
    "scratchpad_query",
    "scratchpad_save",
    "scratchpad_search",
]
