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
# - tools_keywords_list: Lists the manually curated tool keyword map.
# - select_tools_by_keyword: Activates local tools mapped to declared keywords.
# - tools_catalog_list: Returns the complete local tool list.
# - tools_active_add: Activates explicitly named local tools.
# ====================================================================================================

import json
from pathlib import Path

from agent.orchestration.engine import _filter_web_skills
from agent.orchestration.engine import get_web_skills_enabled
from skills_catalog_builder import DEFAULT_OUTPUT_FILE
from skills_catalog_builder import load_skills_payload
from sessions.tool_selection import build_all_tool_catalog
from sessions.tool_selection import get_selected_tools
from sessions.tool_selection import local_tool_names
from sessions.tool_selection import promote_selected_tools
from skill_manager import skill_manager


TOOL_KEYWORDS_FILE = Path(__file__).with_name("tool_keywords.json")


def _normalise_keyword(value: object) -> str:
    return "_".join(str(value or "").strip().lower().replace("-", "_").split())


def _load_tool_keywords(known_tool_names: set[str]) -> dict[str, list[str]]:
    """Load reviewed local-tool keywords, discarding malformed or obsolete entries."""
    try:
        raw = json.loads(TOOL_KEYWORDS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    declared = raw.get("tools", {}) if isinstance(raw, dict) else {}
    if not isinstance(declared, dict):
        return {}

    keywords_by_tool: dict[str, list[str]] = {}
    for tool_name, values in declared.items():
        name = str(tool_name or "").strip()
        if name not in known_tool_names or not isinstance(values, list):
            continue
        keywords = list(dict.fromkeys(
            keyword
            for value in values
            if (keyword := _normalise_keyword(value))
        ))
        if keywords:
            keywords_by_tool[name] = keywords
    return keywords_by_tool


def _available_payload(payload: dict) -> dict:
    return payload if get_web_skills_enabled() else _filter_web_skills(payload)


def _system_tool_names(payload: dict) -> set[str]:
    return {
        str(function_sig).split("(", 1)[0].strip()
        for skill in payload.get("skills", [])
        if skill.get("is_system_skill")
        for function_sig in skill.get("functions", [])
        if str(function_sig).split("(", 1)[0].strip()
    }


def _catalog_by_name(payload: dict) -> dict[str, dict]:
    """Index compact reviewed tool records by their exact names."""
    return {
        str(record.get("name") or ""): record
        for record in build_all_tool_catalog(payload)
        if str(record.get("name") or "")
    }


def tools_keywords_list() -> dict:
    """List reviewed capability tags with compact descriptions of what each tag unlocks."""
    payload = _available_payload(load_skills_payload(DEFAULT_OUTPUT_FILE))
    catalog = _catalog_by_name(payload)
    keywords_by_tool = _load_tool_keywords(local_tool_names(payload))
    for skill in skill_manager.list_skills():
        keywords_by_tool[str(skill["name"])] = list(skill["keywords"])
    tools_by_keyword: dict[str, list[str]] = {}
    for tool_name, keywords in keywords_by_tool.items():
        for keyword in keywords:
            tools_by_keyword.setdefault(keyword, []).append(tool_name)
    keyword_rows: list[dict] = []
    for keyword, tool_names in sorted(tools_by_keyword.items()):
        names = sorted(tool_names)
        examples = []
        for name in names[:3]:
            record = catalog.get(name, {})
            service = str(record.get("skill_name") or "KoreAgent")
            purpose = str(record.get("description") or "").strip()
            examples.append(f"{service}/{name}: {purpose}".rstrip(":"))
        keyword_rows.append({
            "keyword": keyword,
            "tool_count": len(names),
            "tools": names,
            "summary": " | ".join(examples),
        })
    return {
        "instruction": (
            "Choose an exact keyword for the requested capability, then call "
            "select_tools_by_keyword with that keyword. The newly active tool schemas "
            "contain the complete parameter definitions."
        ),
        "keywords": keyword_rows,
    }


def select_tools_by_keyword(keywords: list[str]) -> dict:
    """Activate tools whose manually assigned keyword tags match the supplied keywords exactly."""
    payload = _available_payload(load_skills_payload(DEFAULT_OUTPUT_FILE))
    catalog = _catalog_by_name(payload)
    known_names = local_tool_names(payload) | {str(skill["name"]) for skill in skill_manager.list_skills()}
    keywords_by_tool = _load_tool_keywords(known_names)
    for skill in skill_manager.list_skills():
        keywords_by_tool[str(skill["name"])] = list(skill["keywords"])
    supplied_keywords = keywords if isinstance(keywords, list) else []
    requested = list(dict.fromkeys(
        keyword
        for value in supplied_keywords
        if (keyword := _normalise_keyword(value))
    ))
    available_keywords = {keyword for values in keywords_by_tool.values() for keyword in values}
    matched_tools = sorted(
        tool_name
        for tool_name, tool_keywords in keywords_by_tool.items()
        if set(requested).intersection(tool_keywords)
    )
    system_tools = _system_tool_names(payload)
    activation = promote_selected_tools([name for name in matched_tools if name not in system_tools])
    return {
        "requested_keywords": requested,
        "matched_keywords": [keyword for keyword in requested if keyword in available_keywords],
        "unknown_keywords": [keyword for keyword in requested if keyword not in available_keywords],
        "matched_tools": matched_tools,
        "matched_tool_details": [
            {
                "name": name,
                "service": str(catalog.get(name, {}).get("skill_name") or "KoreAgent"),
                "purpose": str(catalog.get(name, {}).get("description") or ""),
                "parameters": list(catalog.get(name, {}).get("param_names") or []),
            }
            for name in matched_tools
        ],
        "already_active_system_tools": [name for name in matched_tools if name in system_tools],
        **activation,
    }


def tools_catalog_list() -> list[dict]:
    """List every local tool available for explicit activation in this runtime."""
    payload = _available_payload(load_skills_payload(DEFAULT_OUTPUT_FILE))
    return build_all_tool_catalog(payload)


def tools_active_add(tool_names: list[str]) -> dict:
    """Add tool names to the active FIFO working set for the current conversation."""
    payload = _available_payload(load_skills_payload(DEFAULT_OUTPUT_FILE))
    known_names = local_tool_names(payload) | {str(skill["name"]) for skill in skill_manager.list_skills()}
    system_tools = _system_tool_names(payload)
    requested = [str(name or "").strip() for name in tool_names if str(name or "").strip()]
    current = set(get_selected_tools())
    valid: list[str] = []
    unknown: list[str] = []
    for name in requested:
        if name in known_names and name not in system_tools:
            valid.append(name)
        elif name in system_tools:
            continue
        else:
            unknown.append(name)
    result = promote_selected_tools(valid)
    return {
        "added": result["added"],
        "promoted": result["promoted"],
        "unknown": unknown,
        "already_active_system_tools": [name for name in requested if name in system_tools],
        "evicted": result["evicted"],
        "active_tools": result["active_tools"],
        "already_active_before_call": sorted(name for name in valid if name in current),
    }

__all__ = [
    "tools_keywords_list",
    "select_tools_by_keyword",
    "tools_catalog_list",
    "tools_active_add",
]
