# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# web tools state module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Function inventory:
# - is_web_tool_name: Checks whether web tool name is true.
# - filter_tool_names: Filters tool names for this module.
# ====================================================================================================

from __future__ import annotations

WEB_TOOL_NAMES = frozenset(
    {
        "search_web",
        "search_web_text",
        "fetch_page_text",
        "get_page_links",
        "get_page_links_text",
        "lookup_wikipedia",
    }
)


def is_web_tool_name(tool_name: str) -> bool:
    return str(tool_name or "").strip() in WEB_TOOL_NAMES


def filter_tool_names(tool_names: set[str] | list[str] | tuple[str, ...], *, enabled: bool) -> set[str]:
    normalized = {str(name or "").strip() for name in tool_names if str(name or "").strip()}
    if enabled:
        return normalized
    return {name for name in normalized if name not in WEB_TOOL_NAMES}
