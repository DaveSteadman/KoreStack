import copy
from difflib import SequenceMatcher
import re

import mcp_client
from web_tools_state import filter_mcp_tool_defs
from web_tools_state import filter_mcp_tool_index
from web_tools_state import filter_tool_names
from sessions.tool_state import ALWAYS_ON_TOOL_NAMES
from sessions.tool_state import get_selected_tools
from sessions.tool_state import set_selected_tools
from sessions.tool_state import _resolve_session_id


_ACTIVE_RUNTIME_CACHE: dict[tuple, dict[str, object]] = {}
_CATALOG_CACHE: dict[tuple, list[dict]] = {}


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


def _search_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", str(text or "").lower())


def _entry_search_parts(entry: dict) -> list[str]:
    parts = [
        entry.get("name", ""),
        entry.get("description", ""),
        entry.get("origin", ""),
        entry.get("role", ""),
        entry.get("skill_name", ""),
    ]
    parts.extend(entry.get("triggers", []) or [])
    parts.extend(entry.get("param_names", []) or [])
    return [str(part or "") for part in parts if str(part or "").strip()]


def rank_tool_catalog_entries(entries: list[dict], filter_text: str) -> list[dict]:
    needle = str(filter_text or "").strip().lower()
    if not needle:
        return list(entries)

    needle_tokens = _search_tokens(needle)
    ranked: list[tuple[float, dict]] = []
    for entry in entries:
        name = str(entry.get("name", "")).strip().lower()
        parts = _entry_search_parts(entry)
        haystack = " ".join(parts).lower()
        if needle not in haystack and not any(token in haystack for token in needle_tokens):
            continue

        score = 0.0
        if name == needle:
            score += 500.0
        elif needle in name:
            score += 180.0
        score += _score_tool_name_candidate(needle, name) * 10.0
        if entry.get("active"):
            score += 8.0

        trigger_set = {str(item or "").strip().lower() for item in (entry.get("triggers") or []) if str(item or "").strip()}
        param_set = {str(item or "").strip().lower() for item in (entry.get("param_names") or []) if str(item or "").strip()}
        for token in needle_tokens:
            if token in name.split("_"):
                score += 35.0
            if token in haystack:
                score += 12.0
            if token in trigger_set:
                score += 20.0
            if token in param_set:
                score += 10.0

        ranked.append((score, entry))

    ranked.sort(key=lambda item: (-item[0], item[1].get("origin", ""), item[1].get("name", "")))
    return [entry for _score, entry in ranked]


def local_tool_names(skills_payload: dict) -> set[str]:
    names: set[str] = set()
    for skill in skills_payload.get("skills", []):
        for function_sig in skill.get("functions", []):
            name = str(function_sig).split("(", 1)[0].strip()
            if name:
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
    mcp_names = set(filter_mcp_tool_index(mcp_client.get_mcp_tool_index(), enabled=web_enabled).keys())
    return filter_tool_names(local_names | mcp_names, enabled=web_enabled)


def _tool_name_tokens(name: str) -> list[str]:
    return [token for token in str(name or "").strip().lower().split("_") if token]


def _score_tool_name_token(requested: str, candidate: str) -> float:
    if requested == candidate:
        return 4.0
    if len(requested) >= 4 and len(candidate) >= 4 and (requested.startswith(candidate) or candidate.startswith(requested)):
        return 3.0
    ratio = SequenceMatcher(None, requested, candidate).ratio()
    if ratio >= 0.92:
        return 2.8
    if ratio >= 0.84:
        return 2.2
    if len(requested) >= 4 and len(candidate) >= 4 and (requested in candidate or candidate in requested):
        return 1.6
    return 0.0


def _score_tool_name_candidate(requested_name: str, candidate_name: str) -> float:
    requested_tokens = _tool_name_tokens(requested_name)
    candidate_tokens = _tool_name_tokens(candidate_name)
    if not requested_tokens or not candidate_tokens:
        return 0.0

    score = 0.0
    token_gap = abs(len(requested_tokens) - len(candidate_tokens))
    if token_gap:
        score -= 1.5 * token_gap

    max_len = max(len(requested_tokens), len(candidate_tokens))
    for index in range(max_len):
        requested = requested_tokens[index] if index < len(requested_tokens) else ""
        candidate = candidate_tokens[index] if index < len(candidate_tokens) else ""
        if not requested or not candidate:
            score -= 0.75
            continue
        score += _score_tool_name_token(requested, candidate)

    full_ratio = SequenceMatcher(None, str(requested_name or "").strip().lower(), str(candidate_name or "").strip().lower()).ratio()
    score += full_ratio * 2.5
    return score


def suggest_tool_name(requested_name: str, known_tool_names: set[str], *, max_candidates: int = 3) -> dict[str, object]:
    requested = str(requested_name or "").strip()
    if not requested:
        return {"status": "none", "requested_name": requested, "candidates": []}

    normalized_requested = requested.lower()
    normalized_known = {str(name or "").strip() for name in known_tool_names if str(name or "").strip()}
    if normalized_requested in normalized_known:
        return {
            "status": "exact",
            "requested_name": requested,
            "best_match": requested,
            "candidates": [{"name": requested, "score": 999.0}],
        }

    scored: list[tuple[float, str]] = []
    for candidate in normalized_known:
        score = _score_tool_name_candidate(normalized_requested, candidate)
        if score > 0.0:
            scored.append((score, candidate))
    scored.sort(key=lambda item: (-item[0], item[1]))

    candidates = [{"name": name, "score": round(score, 3)} for score, name in scored[:max_candidates]]
    if not scored:
        return {"status": "none", "requested_name": requested, "candidates": candidates}

    best_score, best_name = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else float("-inf")
    token_count_match = len(_tool_name_tokens(normalized_requested)) == len(_tool_name_tokens(best_name))

    if best_score < 9.5 or not token_count_match:
        return {"status": "none", "requested_name": requested, "candidates": candidates}

    if second_score >= best_score - 1.25:
        return {
            "status": "ambiguous",
            "requested_name": requested,
            "best_match": best_name,
            "candidates": candidates,
        }

    return {
        "status": "corrected",
        "requested_name": requested,
        "best_match": best_name,
        "candidates": candidates,
    }


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
    include_mcp: bool = True,
    session_id: str | None = None,
    conversation_entry: dict | None = None,
) -> list[dict]:
    from agent.orchestration.engine import get_web_skills_enabled

    web_enabled = get_web_skills_enabled()
    selected = get_selected_tools(session_id=session_id, conversation_entry=conversation_entry)
    active_names = set(selected) | set(ALWAYS_ON_TOOL_NAMES)
    mcp_index = filter_mcp_tool_index(mcp_client.get_mcp_tool_index(), enabled=web_enabled) if include_mcp else {}
    mcp_defs = filter_mcp_tool_defs(mcp_client.get_mcp_tool_definitions(), enabled=web_enabled) if include_mcp else []
    cache_key = (
        id(skills_payload),
        include_mcp,
        tuple(selected),
        tuple(sorted(mcp_index.keys())),
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
            if not name:
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
    if include_mcp:
        for tool_def in mcp_defs:
            fn = tool_def.get("function", {})
            name = str(fn.get("name", "")).strip()
            if not name:
                continue
            meta = mcp_index.get(name, {})
            parameters = fn.get("parameters", {}) if isinstance(fn.get("parameters"), dict) else {}
            properties = parameters.get("properties", {}) if isinstance(parameters.get("properties"), dict) else {}
            entries.append(
                {
                    "name": name,
                    "description": _first_sentence(fn.get("description", "") or meta.get("purpose", "")),
                    "origin": "mcp",
                    "availability": "connected",
                    "role": meta.get("connection", "remote"),
                    "trust_boundary": "external",
                    "active": name in active_names,
                    "skill_name": meta.get("connection", ""),
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
    all_mcp_defs = filter_mcp_tool_defs(mcp_client.get_mcp_tool_definitions(), enabled=web_enabled)
    all_mcp_index = filter_mcp_tool_index(mcp_client.get_mcp_tool_index(), enabled=web_enabled)
    source_payload = available_local_payload if available_local_payload is not None else full_local_payload
    all_known_names = filter_tool_names(local_tool_names(source_payload) | set(all_mcp_index.keys()), enabled=web_enabled)

    missing_selected = [name for name in selected if name not in all_known_names]
    if missing_selected:
        selected = [name for name in selected if name in all_known_names]
        set_selected_tools(selected, session_id=resolved_session_id, conversation_entry=conversation_entry)

    cache_key = (
        id(source_payload),
        tuple(selected),
        tuple(sorted(all_mcp_index.keys())),
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

    allowed_names = set(selected) | set(ALWAYS_ON_TOOL_NAMES)
    active_local_payload = filter_local_payload(source_payload, allowed_names)
    active_mcp_defs = [tool_def for tool_def in all_mcp_defs if tool_def.get("function", {}).get("name") in allowed_names]
    active_mcp_index = {name: info for name, info in all_mcp_index.items() if name in allowed_names}
    active_tool_names = local_tool_names(active_local_payload) | set(active_mcp_index.keys())

    result = {
        "selected_tools": list(selected),
        "active_tool_names": set(active_tool_names),
        "active_local_payload": active_local_payload,
        "active_mcp_defs": active_mcp_defs,
        "active_mcp_index": active_mcp_index,
        "missing_selected": list(missing_selected),
        "all_known_tool_names": set(all_known_names),
    }
    _ACTIVE_RUNTIME_CACHE.clear()
    _ACTIVE_RUNTIME_CACHE[cache_key] = result
    return result
