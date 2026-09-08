# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Implements the model-callable interface for listing and selecting Skills and their tools for the
# active conversation. Selection state is owned by sessions.tool_selection, not this module.
#
# Public API:
#   - skills_list()        -- lists selectable skills.
#   - skills_search()      -- finds selectable Skills relevant to a task.
#   - select_skills()      -- selects complete skills.
#   - tools_catalog_list() -- lists every catalogue tool.
#   - tools_active_add()   -- activates exact tool names for the current session.
#
# Private helpers filter unavailable web skills and merge permanent local system tools.
# ====================================================================================================
"""Model-callable controls for selecting complete Skills into the active tool list."""


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================

import json
from pathlib import Path

from agent.orchestration.engine import _filter_web_skills
from agent.orchestration.engine import get_web_skills_enabled
from skills_catalog_builder import DEFAULT_OUTPUT_FILE, load_skills_payload
from sessions.tool_selection import get_selected_tools, local_tool_names, promote_selected_tools
from skill_manager import skill_manager


# ====================================================================================================
# MARK: CATALOG CONFIGURATION
# ====================================================================================================
SYSTEM_SKILLS_MANIFEST = Path(__file__).resolve().parents[1] / "skill_registration.json"


# ====================================================================================================
# MARK: CATALOG FILTERING (PRIVATE)
# ====================================================================================================
def _available_payload(payload: dict) -> dict:
    return payload if get_web_skills_enabled() else _filter_web_skills(payload)


def _local_skills(payload: dict) -> dict[str, list[str]]:
    skills = {
        str(skill.get("skill_name") or "").strip(): [
            str(signature).split("(", 1)[0].strip()
            for signature in skill.get("functions", [])
            if str(signature).split("(", 1)[0].strip()
        ]
        for skill in payload.get("skills", [])
        if str(skill.get("skill_name") or "").strip() and not skill.get("is_system_skill")
    }
    try:
        system_group = json.loads(SYSTEM_SKILLS_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        system_group = {}
    declared_skills = system_group.get("skills") if isinstance(system_group, dict) else []
    group = declared_skills[0] if isinstance(declared_skills, list) and declared_skills and isinstance(declared_skills[0], dict) else {}
    group_name = str(group.get("name") or "").strip()
    group_tools = group.get("tools") if isinstance(group, dict) else None
    if group_name and isinstance(group_tools, list):
        skills[group_name] = [
            str(tool.get("name") or "").strip()
            for tool in group_tools
            if isinstance(tool, dict) and str(tool.get("name") or "").strip()
        ]
    return skills


def _skill_search_records(payload: dict) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    local_skill_names: set[str] = set()
    for skill in payload.get("skills", []):
        name = str(skill.get("skill_name") or "").strip()
        if not name or skill.get("is_system_skill"):
            continue
        local_skill_names.add(name)
        tools = [
            str(signature).split("(", 1)[0].strip()
            for signature in skill.get("functions", [])
            if str(signature).split("(", 1)[0].strip()
        ]
        records.append(
            {
                "name":        name,
                "origin":      "local",
                "description": str(skill.get("purpose") or "").strip(),
                "tools":       tools,
                "search_text": " ".join(
                    [
                        name,
                        str(skill.get("purpose") or ""),
                        " ".join(str(item or "") for item in skill.get("triggers", [])),
                        " ".join(tools),
                    ]
                ).lower(),
            }
        )
    for skill in skill_manager.list_skills():
        if skill["name"] in local_skill_names or skill["name"] == "system_skills":
            continue
        records.append(
            {
                "name":        skill["name"],
                "origin":      "registered",
                "description": skill["selection_description"],
                "tools":       [tool["name"] for tool in skill["tools"]],
                "search_text": " ".join(
                    [
                        skill["name"],
                        skill["selection_description"],
                        skill.get("purpose", ""),
                        " ".join(tool["name"] for tool in skill["tools"]),
                    ]
                ).lower(),
            }
        )
    return records


def _eviction_notice(evicted_tools: list[str]) -> str:
    if not evicted_tools:
        return ""
    names = ", ".join(f"`{name}`" for name in evicted_tools)
    return (
        f"Tool capacity evicted {names}. Those tools are no longer callable in this conversation; "
        "reactivate them explicitly if they are needed again."
    )


# ====================================================================================================
# MARK: MODEL-CALLABLE SELECTION TOOLS (PUBLIC)
# ====================================================================================================
def skills_list() -> dict:
    """List exact Skill names.  Select a Skill to activate all of its tools."""
    payload = _available_payload(load_skills_payload(DEFAULT_OUTPUT_FILE))
    local = _local_skills(payload)
    registered = [skill for skill in skill_manager.list_skills() if skill["name"] not in local]
    return {
        "instruction": "Choose exact skill names, then call select_skills. Each selected skill adds all of its tools to the active tool list.",
        "skills": sorted(
            [{"name": name, "tool_count": len(tools), "origin": "local", "default_active": name == "system_skills"} for name, tools in local.items()]
            + [{"name": skill["name"], "tool_count": len(skill["tools"]), "origin": "registered", "description": skill["selection_description"]} for skill in registered],
            key=lambda item: item["name"],
        ),
    }


def skills_search(query: str, limit: int = 8) -> dict:
    """Find relevant selectable Skills without returning the entire catalog."""
    payload          = _available_payload(load_skills_payload(DEFAULT_OUTPUT_FILE))
    normalized_query = " ".join(str(query or "").lower().split())
    terms            = [term for term in normalized_query.split() if len(term) > 1]
    try:
        requested_limit = int(limit or 8)
    except (TypeError, ValueError):
        requested_limit = 8
    bounded_limit = max(1, min(requested_limit, 12))
    matches: list[tuple[int, dict[str, object]]] = []
    for record in _skill_search_records(payload):
        name       = str(record["name"]).lower()
        searchable = str(record["search_text"])
        score      = 0
        if normalized_query and normalized_query in name:
            score += 100
        elif normalized_query and normalized_query in searchable:
            score += 40
        score += sum(20 if term in name else 5 for term in terms if term in searchable)
        if score:
            matches.append((score, record))
    selected = sorted(matches, key=lambda item: (-item[0], str(item[1]["name"])))[:bounded_limit]
    return {
        "instruction": "Call select_skills with one or more exact names from these matches.",
        "query": normalized_query,
        "skills": [
            {
                "name":        record["name"],
                "origin":      record["origin"],
                "description": record["description"],
                "tool_count":  len(record["tools"]),
            }
            for _score, record in selected
        ],
    }


def select_skills(skill_names: list[str]) -> dict:
    """Activate every tool belonging to the requested exact Skill names."""
    payload = _available_payload(load_skills_payload(DEFAULT_OUTPUT_FILE))
    local = _local_skills(payload)
    registered = {skill["name"]: skill for skill in skill_manager.list_skills()}
    requested = list(dict.fromkeys(str(name or "").strip() for name in skill_names if str(name or "").strip()))
    matched = [name for name in requested if name in local or name in registered]
    unknown = [name for name in requested if name not in local and name not in registered]
    tools = [tool for name in matched for tool in (local.get(name) or [item["name"] for item in registered[name]["tools"]])]
    system_tools = set(local.get("system_skills") or [])
    activation = promote_selected_tools([tool for tool in tools if tool not in system_tools])
    return {
        "selected_skills":     matched,
        "unknown_skills":      unknown,
        "activated_tools":     tools,
        "already_active_tools": [tool for tool in tools if tool in system_tools],
        **activation,
        "eviction_notice": _eviction_notice(activation["evicted"]),
    }


def tools_catalog_list() -> dict:
    """List exact individual tool names for direct activation."""
    payload = _available_payload(load_skills_payload(DEFAULT_OUTPUT_FILE))
    return {"tools": sorted(local_tool_names(payload) | {tool["name"] for tool in skill_manager.list_tools()})}


def tools_active_add(tool_names: list[str]) -> dict:
    """Add exact individual tools to the active FIFO working set."""
    payload = _available_payload(load_skills_payload(DEFAULT_OUTPUT_FILE))
    known = local_tool_names(payload) | {tool["name"] for tool in skill_manager.list_tools()}
    requested = [str(name or "").strip() for name in tool_names if str(name or "").strip()]
    system_tools = set(_local_skills(payload).get("system_skills") or [])
    valid = [name for name in requested if name in known and name not in system_tools]
    activation = promote_selected_tools(valid)
    return {
        "unknown":        [name for name in requested if name not in known],
        "already_active": [name for name in requested if name in system_tools],
        **activation,
        "active_tools":   get_selected_tools(),
        "eviction_notice": _eviction_notice(activation["evicted"]),
    }


__all__ = ["skills_list", "skills_search", "select_skills", "tools_catalog_list", "tools_active_add"]
