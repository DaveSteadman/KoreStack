from __future__ import annotations

WEB_TOOL_NAMES = frozenset(
    {
        "search_web",
        "search_web_text",
        "fetch_page_text",
        "get_page_links",
        "get_page_links_text",
        "research_traverse",
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


def filter_mcp_tool_defs(tool_defs: list[dict], *, enabled: bool) -> list[dict]:
    if enabled:
        return list(tool_defs)
    return [
        tool_def
        for tool_def in tool_defs
        if not is_web_tool_name(tool_def.get("function", {}).get("name", ""))
    ]


def filter_mcp_tool_index(tool_index: dict[str, dict], *, enabled: bool) -> dict[str, dict]:
    if enabled:
        return {name: dict(info) for name, info in tool_index.items()}
    return {
        name: dict(info)
        for name, info in tool_index.items()
        if not is_web_tool_name(name)
    }
