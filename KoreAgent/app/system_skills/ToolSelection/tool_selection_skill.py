# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# ToolSelection skill for KoreAgent.
#
# Exposes the always-on control-plane functions that let the model inspect the larger tool catalog
# and pull a small subset into the active working set for the current conversation.
# ====================================================================================================

from skills_catalog_builder import DEFAULT_OUTPUT_FILE
from skills_catalog_builder import load_skills_payload
from tool_selection_state import build_all_tool_catalog
from tool_selection_state import get_selected_tools
from tool_selection_state import promote_selected_tools


def tools_catalog_list(filter_text: str = "", max_items: int = 100, include_mcp: bool = True) -> list[dict]:
    """List available tools from the full catalog so the model can activate more when needed."""
    payload = load_skills_payload(DEFAULT_OUTPUT_FILE)
    entries = build_all_tool_catalog(payload, include_mcp=include_mcp)
    needle = str(filter_text or "").strip().lower()
    if needle:
        entries = [
            entry
            for entry in entries
            if needle in str(entry.get("name", "")).lower() or needle in str(entry.get("description", "")).lower()
        ]
    limited = max(1, min(int(max_items), 200))
    return entries[:limited]


def tools_active_add(tool_names: list[str]) -> dict:
    """Add tool names to the active MRU working set for the current conversation."""
    payload = load_skills_payload(DEFAULT_OUTPUT_FILE)
    known_names = {entry.get("name", "") for entry in build_all_tool_catalog(payload, include_mcp=True)}
    requested = [str(name or "").strip() for name in tool_names if str(name or "").strip()]
    current = set(get_selected_tools())
    valid: list[str] = []
    unknown: list[str] = []
    for name in requested:
        if name in known_names:
            valid.append(name)
        else:
            unknown.append(name)
    result = promote_selected_tools(valid)
    return {
        "added": result["added"],
        "promoted": result["promoted"],
        "unknown": unknown,
        "evicted": result["evicted"],
        "active_tools": result["active_tools"],
        "already_active_before_call": sorted(name for name in valid if name in current),
    }


__all__ = ["tools_catalog_list", "tools_active_add"]