# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# tool catalog module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Function inventory:
# - clear_runtime_caches: Clears runtime caches for this module.
# - _first_sentence: Implements the  first sentence operation for this module.
# - local_tool_names: Implements the local tool names operation for this module.
# - all_known_tool_names: Implements the all known tool names operation for this module.
# - filter_local_payload: Filters local payload for this module.
# - build_all_tool_catalog: Builds all tool catalog for this module.
# - derive_active_tool_runtime: Implements the derive active tool runtime operation for this module.
# ====================================================================================================

import copy

from skill_manager import skill_manager
from web_tools_state import filter_tool_names
from sessions.tool_state import ALWAYS_ON_TOOL_NAMES
from sessions.tool_state import MAX_ACTIVE_TOOLS
from sessions.tool_state import get_selected_tools
from sessions.tool_state import set_selected_tools
from sessions.tool_state import _resolve_session_id


_ACTIVE_RUNTIME_CACHE: dict[tuple, dict[str, object]] = {}
_CATALOG_CACHE: dict[tuple, list[dict]] = {}
MAX_EXPOSED_TOOL_DEFINITIONS = 64
MIN_SELECTED_TOOL_SLOTS = 16
_LEGACY_WORKING_DATA_PREFIXES = ("dataset_", "scratchpad_")


def _is_legacy_working_data_tool(name: str) -> bool:
    return str(name or "").strip().lower().startswith(_LEGACY_WORKING_DATA_PREFIXES)


def clear_runtime_caches() -> None:
    _ACTIVE_RUNTIME_CACHE.clear()
    _CATALOG_CACHE.clear()


def _first_sentence(text: str) -> str:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return ""
    for separator in (". ", "! ", "? "):
        if separator in cleaned:
            return cleaned.split(separator, 1)[0].strip() + separator[0]
    return cleaned[:200]


def _registered_tool_description(tool: dict) -> str:
    """Keep an active service-tool schema self-describing without copying the catalogue."""
    purpose = str(tool.get("purpose") or "").strip()
    service = str(tool.get("service") or "").strip()
    returns = str(tool.get("returns") or "").strip()
    detail = "; ".join(
        part for part in (
            f"service={service}" if service else "",
            f"returns={returns}" if returns else "",
        ) if part
    )
    summary = purpose
    return f"{summary}\n\n[{detail}]" if detail else summary


def local_tool_names(skills_payload: dict) -> set[str]:
    names: set[str] = set()
    for skill in skills_payload.get("skills", []):
        for function_sig in skill.get("functions", []):
            name = str(function_sig).split("(", 1)[0].strip()
            if name and not _is_legacy_working_data_tool(name):
                names.add(name)
    return names


def _system_tool_names(skills_payload: dict) -> set[str]:
    names: set[str] = set()
    for skill in skills_payload.get("skills", []):
        if not skill.get("is_system_skill"):
            continue
        for function_sig in skill.get("functions", []):
            name = str(function_sig).split("(", 1)[0].strip()
            if name and not _is_legacy_working_data_tool(name):
                names.add(name)
    return names


def all_known_tool_names(
    full_local_payload: dict,
    *,
    available_local_payload: dict | None = None,
) -> set[str]:
    from agent.orchestration.engine import get_web_skills_enabled

    source_payload = available_local_payload if available_local_payload is not None else full_local_payload
    web_enabled = get_web_skills_enabled()
    local_names = local_tool_names(source_payload)
    registered_names = {str(tool.get("name") or "").strip() for tool in skill_manager.list_tools() if tool.get("transport") == "http"}
    return filter_tool_names(local_names | registered_names, enabled=web_enabled)


def filter_local_payload(skills_payload: dict, allowed_names: set[str]) -> dict:
    filtered_skills: list[dict] = []
    for skill in skills_payload.get("skills", []):
        kept_functions: list[str] = []
        kept_param_descriptions: dict[str, dict[str, str]] = {}
        param_descriptions = skill.get("param_descriptions", {}) if isinstance(skill.get("param_descriptions"), dict) else {}
        for function_sig in skill.get("functions", []):
            name = str(function_sig).split("(", 1)[0].strip()
            if name in allowed_names:
                kept_functions.append(function_sig)
                if name in param_descriptions:
                    kept_param_descriptions[name] = param_descriptions[name]
        if not kept_functions:
            continue
        copied = copy.deepcopy(skill)
        copied["functions"] = kept_functions
        copied["param_descriptions"] = kept_param_descriptions
        filtered_skills.append(copied)
    return {**skills_payload, "skills": filtered_skills}


def build_all_tool_catalog(
    skills_payload: dict,
    *,
    session_id: str | None = None,
    conversation_entry: dict | None = None,
) -> list[dict]:
    from agent.orchestration.engine import get_web_skills_enabled

    web_enabled = get_web_skills_enabled()
    selected = get_selected_tools(session_id=session_id, conversation_entry=conversation_entry)
    active_names = set(selected) | set(ALWAYS_ON_TOOL_NAMES) | _system_tool_names(skills_payload)
    registered_names = tuple(sorted(str(tool.get("name") or "") for tool in skill_manager.list_tools() if tool.get("transport") == "http"))
    cache_key = (
        id(skills_payload),
        tuple(selected),
        registered_names,
    )
    cached = _CATALOG_CACHE.get(cache_key)
    if cached is not None:
        return cached

    entries: list[dict] = []
    for skill in skills_payload.get("skills", []):
        description = _first_sentence(skill.get("purpose", ""))
        triggers = [str(item or "").strip() for item in (skill.get("triggers") or []) if str(item or "").strip()]
        trigger_keyword = str(skill.get("trigger_keyword") or "").strip()
        if trigger_keyword and trigger_keyword not in triggers:
            triggers.append(trigger_keyword)
        param_descriptions = skill.get("param_descriptions", {}) if isinstance(skill.get("param_descriptions"), dict) else {}
        meta = {
            "origin": skill.get("origin", "local"),
            "availability": skill.get("availability", "configured"),
            "role": skill.get("role", "optional"),
            "trust_boundary": skill.get("trust_boundary", "internal"),
            "skill_name": skill.get("skill_name", ""),
            "triggers": triggers,
        }
        for function_sig in skill.get("functions", []):
            name = str(function_sig).split("(", 1)[0].strip()
            if not name or _is_legacy_working_data_tool(name):
                continue
            func_param_descs = param_descriptions.get(name, {}) if isinstance(param_descriptions.get(name), dict) else {}
            entries.append(
                {
                    "name": name,
                    "description": description,
                    "active": name in active_names,
                    "param_names": sorted(str(param_name) for param_name in func_param_descs.keys()),
                    **meta,
                }
            )
    local_names = {str(entry.get("name") or "") for entry in entries}
    for tool in (tool for tool in skill_manager.list_tools() if tool.get("transport") == "http"):
        name = str(tool.get("name") or "").strip()
        if not name or name in local_names:
            continue
        parameters = tool.get("parameters") if isinstance(tool.get("parameters"), dict) else {}
        properties = parameters.get("properties") if isinstance(parameters.get("properties"), dict) else {}
        entries.append(
            {
                "name": name,
                "description": _first_sentence(tool.get("purpose", "")),
                "selection_description": str(tool.get("purpose") or ""),
                "origin": "registered",
                "availability": "registered",
                "role": "service",
                "trust_boundary": "internal",
                "active": name in active_names,
                "skill_name": str(tool.get("skill_name") or ""),
                "triggers": [],
                "param_names": sorted(str(param_name) for param_name in properties.keys()),
            }
        )
    result = sorted(entries, key=lambda item: (item.get("origin", ""), item.get("name", "")))
    _CATALOG_CACHE.clear()
    _CATALOG_CACHE[cache_key] = result
    return result


def derive_active_tool_runtime(
    full_local_payload: dict,
    *,
    available_local_payload: dict | None = None,
    session_id: str | None = None,
    conversation_entry: dict | None = None,
) -> dict[str, object]:
    from agent.orchestration.engine import get_web_skills_enabled

    web_enabled = get_web_skills_enabled()
    resolved_session_id = _resolve_session_id(session_id)
    selected = get_selected_tools(session_id=resolved_session_id, conversation_entry=conversation_entry)
    registered_tools = [tool for tool in skill_manager.list_tools() if tool.get("transport") == "http"]
    source_payload = available_local_payload if available_local_payload is not None else full_local_payload
    all_known_names = filter_tool_names(
        local_tool_names(source_payload) | {str(tool.get("name") or "") for tool in registered_tools},
        enabled=web_enabled,
    )

    missing_selected = [name for name in selected if name not in all_known_names]
    if missing_selected:
        selected = [name for name in selected if name in all_known_names]
        set_selected_tools(selected, session_id=resolved_session_id, conversation_entry=conversation_entry)

    system_names = _system_tool_names(source_payload)
    # System skills are permanently exposed.  Other explicitly always-on controls
    # (currently the date/time function) reserve schema space as well.
    reserved_names = system_names | set(ALWAYS_ON_TOOL_NAMES)
    selectable_slots = MAX_EXPOSED_TOOL_DEFINITIONS - len(reserved_names)
    if selectable_slots < MIN_SELECTED_TOOL_SLOTS:
        raise RuntimeError(
            f"{len(system_names)} system tools and {len(reserved_names - system_names)} other always-on tools "
            f"leave only {selectable_slots} selectable slots; "
            f"the minimum is {MIN_SELECTED_TOOL_SLOTS} within a {MAX_EXPOSED_TOOL_DEFINITIONS}-tool schema budget"
        )
    selectable_slots = min(MAX_ACTIVE_TOOLS, selectable_slots)
    if len(selected) > selectable_slots:
        selected = selected[-selectable_slots:]
        set_selected_tools(selected, session_id=resolved_session_id, conversation_entry=conversation_entry)

    cache_key = (
        id(source_payload),
        tuple(selected),
        tuple(sorted(tool["name"] for tool in registered_tools)),
    )
    cached = _ACTIVE_RUNTIME_CACHE.get(cache_key)
    if cached is not None:
        if missing_selected:
            cached_result = dict(cached)
            cached_result["missing_selected"] = list(missing_selected)
            cached_result["selected_tools"] = list(selected)
            cached_result["all_known_tool_names"] = set(all_known_names)
            return cached_result
        return cached

    allowed_names = set(selected) | set(ALWAYS_ON_TOOL_NAMES) | system_names
    active_local_payload = filter_local_payload(source_payload, allowed_names)
    active_tool_names = local_tool_names(active_local_payload)
    active_registered_defs = [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": _registered_tool_description(tool),
                "parameters": tool["parameters"],
            },
        }
        for tool in registered_tools
        if tool.get("name") in allowed_names
    ]
    active_tool_names |= {tool_def["function"]["name"] for tool_def in active_registered_defs}

    result = {
        "selected_tools": list(selected),
        "active_tool_names": set(active_tool_names),
        "active_local_payload": active_local_payload,
        "active_registered_defs": active_registered_defs,
        "missing_selected": list(missing_selected),
        "all_known_tool_names": set(all_known_names),
        "system_tool_names": sorted(system_names),
        "system_tool_count": len(system_names),
        "always_on_tool_count": len(reserved_names - system_names),
        "reserved_tool_count": len(reserved_names),
        "selectable_tool_slots": selectable_slots,
    }
    _ACTIVE_RUNTIME_CACHE.clear()
    _ACTIVE_RUNTIME_CACHE[cache_key] = result
    return result
