# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# ToolSelection skill for KoreAgent.
#
# Exposes the always-on control-plane functions that let the model inspect the larger tool catalog
# and pull a small subset into the active working set for the current conversation.
# MARK: FUNCTIONS
# Function inventory:
# - _available_payload: Implements the  available payload operation for this module.
# - tools_catalog_list: Implements the tools catalog list operation for this module.
# - tools_active_add: Implements the tools active add operation for this module.
# - toolsets_list: Implements the toolsets list operation for this module.
# - toolsets_activate: Implements the toolsets activate operation for this module.
# ====================================================================================================

from agent.orchestration.engine import _filter_web_skills
from agent.orchestration.engine import get_web_skills_enabled
from skills_catalog_builder import DEFAULT_OUTPUT_FILE
from skills_catalog_builder import load_skills_payload
from sessions.tool_selection import all_known_tool_names
from sessions.tool_selection import build_all_tool_catalog
from sessions.tool_selection import get_selected_tools
from sessions.tool_selection import promote_selected_tools
from sessions.tool_selection import rank_tool_catalog_entries
from sessions.tool_aliases import canonical_tool_name
from sessions.tool_sets import load_tool_sets
from sessions.tool_sets import resolve_tool_sets


def _available_payload(payload: dict) -> dict:
    return payload if get_web_skills_enabled() else _filter_web_skills(payload)


def tools_catalog_list(filter_text: str = "", max_items: int = 100, include_mcp: bool = True) -> list[dict]:
    """List available tools from the full catalog so the model can activate more when needed."""
    payload = _available_payload(load_skills_payload(DEFAULT_OUTPUT_FILE))
    entries = build_all_tool_catalog(payload, include_mcp=include_mcp)
    entries = rank_tool_catalog_entries(entries, filter_text)
    limited = max(1, min(int(max_items), 200))
    return entries[:limited]


def tools_active_add(tool_names: list[str]) -> dict:
    """Add tool names to the active FIFO working set for the current conversation."""
    payload = _available_payload(load_skills_payload(DEFAULT_OUTPUT_FILE))
    known_names = all_known_tool_names(payload)
    requested = [str(name or "").strip() for name in tool_names if str(name or "").strip()]
    current = set(get_selected_tools())
    valid: list[str] = []
    unknown: list[str] = []
    aliases: dict[str, str] = {}
    for name in requested:
        canonical = canonical_tool_name(name, payload)
        if canonical in known_names:
            valid.append(canonical)
            if canonical != name:
                aliases[name] = canonical
        else:
            unknown.append(name)
    result = promote_selected_tools(valid)
    return {
        "added": result["added"],
        "promoted": result["promoted"],
        "unknown": unknown,
        "aliases": aliases,
        "evicted": result["evicted"],
        "active_tools": result["active_tools"],
        "already_active_before_call": sorted(name for name in valid if name in current),
    }


def toolsets_list() -> list[dict]:
    """List the current named tool sets available for batch activation."""
    payload = _available_payload(load_skills_payload(DEFAULT_OUTPUT_FILE))
    known_names = all_known_tool_names(payload)
    active_names = set(get_selected_tools())
    return [
        {
            "name":         item["name"],
            "description":  item["description"],
            "tools":        item["tools"],
            "active_tools": [name for name in item["tools"] if name in active_names],
        }
        for item in load_tool_sets(known_tool_names=known_names)
    ]


def toolsets_activate(set_names: list[str]) -> dict:
    """Activate one or more named tool sets in the FIFO working set."""
    payload = _available_payload(load_skills_payload(DEFAULT_OUTPUT_FILE))
    known_names = all_known_tool_names(payload)
    resolved = resolve_tool_sets(set_names, known_tool_names=known_names)
    activation = promote_selected_tools(resolved["tool_names"])
    return {
        **resolved,
        **activation,
    }


__all__ = ["tools_catalog_list", "tools_active_add", "toolsets_list", "toolsets_activate"]
