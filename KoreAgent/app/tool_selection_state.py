import copy
from difflib import SequenceMatcher
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import koreconv_client
import mcp_client
from session_runtime import get_active_session_id


MAX_ACTIVE_TOOLS = 32
ALWAYS_ON_TOOL_NAMES = frozenset({"tools_catalog_list", "tools_active_add"})

_KC_TIMEOUT = 8
_SESSION_TOOLS_ACTIVE: dict[str, list[str]] = {}
_SESSION_LOCK = threading.Lock()


def _normalize_tool_names(tool_names: object) -> list[str]:
    if not isinstance(tool_names, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in tool_names:
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        normalized.append(name)
    return normalized


def _resolve_session_id(session_id: str | None = None) -> str:
    cleaned = str(session_id or "").strip()
    return cleaned or get_active_session_id()


def _session_external_id(session_id: str) -> str:
    return f"webchat_{session_id}"


def _kc_request_json(path: str, *, method: str = "GET", payload: dict | None = None) -> dict | list | None:
    base = koreconv_client.get_base_url()
    if not base:
        return None
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_KC_TIMEOUT) as resp:
            if resp.status == 204:
                return None
            raw = resp.read().decode("utf-8").strip()
            return json.loads(raw) if raw else None
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        return None


def _fetch_conversation_for_session(session_id: str) -> dict | None:
    if not session_id:
        return None
    if session_id.startswith("kc_conv_"):
        raw_id = session_id[len("kc_conv_"):].strip()
        if raw_id.isdigit():
            result = _kc_request_json(f"/conversations/{raw_id}")
            return result if isinstance(result, dict) else None
        return None
    external_id = urllib.parse.quote(_session_external_id(session_id), safe="")
    result = _kc_request_json(f"/conversations/by-external-id/{external_id}")
    return result if isinstance(result, dict) else None


def _ensure_conversation_for_session(session_id: str) -> dict | None:
    existing = _fetch_conversation_for_session(session_id)
    if existing is not None or not session_id or session_id.startswith("kc_conv_"):
        return existing
    created = _kc_request_json(
        "/conversations",
        method="POST",
        payload={
            "channel_type": "webchat",
            "subject": f"Webchat {session_id}",
            "protected": False,
            "external_id": _session_external_id(session_id),
        },
    )
    return created if isinstance(created, dict) else _fetch_conversation_for_session(session_id)


def _update_cache(session_id: str, tools_active: list[str]) -> None:
    if not session_id:
        return
    with _SESSION_LOCK:
        _SESSION_TOOLS_ACTIVE[session_id] = list(tools_active)


def clear_session_tools_active(session_id: str) -> None:
    cleaned = _resolve_session_id(session_id)
    if not cleaned:
        return
    with _SESSION_LOCK:
        _SESSION_TOOLS_ACTIVE.pop(cleaned, None)


def get_selected_tools(session_id: str | None = None, conversation_entry: dict | None = None) -> list[str]:
    resolved_session_id = _resolve_session_id(session_id)
    if resolved_session_id:
        with _SESSION_LOCK:
            cached = _SESSION_TOOLS_ACTIVE.get(resolved_session_id)
        if cached is not None:
            return list(cached)

    tools_active = []
    if isinstance(conversation_entry, dict):
        tools_active = _normalize_tool_names(conversation_entry.get("tools_active") or [])
    if not tools_active and resolved_session_id:
        conv = _fetch_conversation_for_session(resolved_session_id)
        if isinstance(conv, dict):
            tools_active = _normalize_tool_names(conv.get("tools_active") or [])
    tools_active = tools_active[:MAX_ACTIVE_TOOLS]
    _update_cache(resolved_session_id, tools_active)
    if isinstance(conversation_entry, dict):
        conversation_entry["tools_active"] = list(tools_active)
    return list(tools_active)


def set_selected_tools(tool_names: list[str], session_id: str | None = None, conversation_entry: dict | None = None) -> list[str]:
    resolved_session_id = _resolve_session_id(session_id)
    normalized = _normalize_tool_names(tool_names)[:MAX_ACTIVE_TOOLS]
    _update_cache(resolved_session_id, normalized)
    if isinstance(conversation_entry, dict):
        conversation_entry["tools_active"] = list(normalized)

    conv = _ensure_conversation_for_session(resolved_session_id) if resolved_session_id else None
    if isinstance(conv, dict) and conv.get("id") is not None:
        patched = _kc_request_json(
            f"/conversations/{int(conv['id'])}",
            method="PATCH",
            payload={"tools_active": normalized},
        )
        if isinstance(patched, dict) and isinstance(conversation_entry, dict):
            conversation_entry.update(patched)
            conversation_entry["tools_active"] = _normalize_tool_names(patched.get("tools_active") or [])[:MAX_ACTIVE_TOOLS]
    return list(normalized)


def promote_selected_tools(tool_names: list[str], session_id: str | None = None, conversation_entry: dict | None = None) -> dict[str, list[str]]:
    requested = _normalize_tool_names(tool_names)
    current = get_selected_tools(session_id=session_id, conversation_entry=conversation_entry)
    current_set = set(current)
    front: list[str] = []
    added: list[str] = []
    promoted: list[str] = []
    for name in requested:
        if name in current_set:
            promoted.append(name)
        else:
            added.append(name)
        if name not in front:
            front.append(name)
    merged = front + [name for name in current if name not in front]
    evicted = merged[MAX_ACTIVE_TOOLS:]
    merged = merged[:MAX_ACTIVE_TOOLS]
    active_tools = set_selected_tools(merged, session_id=session_id, conversation_entry=conversation_entry)
    return {
        "added": added,
        "promoted": promoted,
        "evicted": evicted,
        "active_tools": active_tools,
    }


def note_tool_used(tool_name: str, session_id: str | None = None, conversation_entry: dict | None = None) -> None:
    name = str(tool_name or "").strip()
    if not name or name in ALWAYS_ON_TOOL_NAMES:
        return
    current = get_selected_tools(session_id=session_id, conversation_entry=conversation_entry)
    if name not in current:
        return
    if current and current[0] == name:
        return
    promote_selected_tools([name], session_id=session_id, conversation_entry=conversation_entry)


def _first_sentence(text: str) -> str:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return ""
    for separator in (". ", "! ", "? "):
        if separator in cleaned:
            return cleaned.split(separator, 1)[0].strip() + separator[0]
    return cleaned[:200]


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
    source_payload = available_local_payload if available_local_payload is not None else full_local_payload
    return local_tool_names(source_payload) | set(mcp_client.get_mcp_tool_index().keys())


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
    selected = get_selected_tools(session_id=session_id, conversation_entry=conversation_entry)
    active_names = set(selected) | set(ALWAYS_ON_TOOL_NAMES)
    entries: list[dict] = []
    for skill in skills_payload.get("skills", []):
        description = _first_sentence(skill.get("purpose", ""))
        meta = {
            "origin": skill.get("origin", "local"),
            "availability": skill.get("availability", "configured"),
            "role": skill.get("role", "optional"),
            "trust_boundary": skill.get("trust_boundary", "internal"),
        }
        for function_sig in skill.get("functions", []):
            name = str(function_sig).split("(", 1)[0].strip()
            if not name:
                continue
            entries.append(
                {
                    "name": name,
                    "description": description,
                    "active": name in active_names,
                    **meta,
                }
            )
    if include_mcp:
        mcp_index = mcp_client.get_mcp_tool_index()
        for tool_def in mcp_client.get_mcp_tool_definitions():
            fn = tool_def.get("function", {})
            name = str(fn.get("name", "")).strip()
            if not name:
                continue
            meta = mcp_index.get(name, {})
            entries.append(
                {
                    "name": name,
                    "description": _first_sentence(fn.get("description", "") or meta.get("purpose", "")),
                    "origin": "mcp",
                    "availability": "connected",
                    "role": meta.get("connection", "remote"),
                    "trust_boundary": "external",
                    "active": name in active_names,
                }
            )
    return sorted(entries, key=lambda item: (item.get("origin", ""), item.get("name", "")))


def derive_active_tool_runtime(
    full_local_payload: dict,
    *,
    available_local_payload: dict | None = None,
    session_id: str | None = None,
    conversation_entry: dict | None = None,
) -> dict[str, object]:
    resolved_session_id = _resolve_session_id(session_id)
    selected = get_selected_tools(session_id=resolved_session_id, conversation_entry=conversation_entry)
    all_mcp_defs = mcp_client.get_mcp_tool_definitions()
    all_mcp_index = mcp_client.get_mcp_tool_index()
    source_payload = available_local_payload if available_local_payload is not None else full_local_payload
    all_known_names = local_tool_names(source_payload) | set(all_mcp_index.keys())

    missing_selected = [name for name in selected if name not in all_known_names]
    if missing_selected:
        selected = [name for name in selected if name in all_known_names]
        set_selected_tools(selected, session_id=resolved_session_id, conversation_entry=conversation_entry)

    allowed_names = set(selected) | set(ALWAYS_ON_TOOL_NAMES)
    active_local_payload = filter_local_payload(source_payload, allowed_names)
    active_mcp_defs = [tool_def for tool_def in all_mcp_defs if tool_def.get("function", {}).get("name") in allowed_names]
    active_mcp_index = {name: info for name, info in all_mcp_index.items() if name in allowed_names}
    active_tool_names = local_tool_names(active_local_payload) | set(active_mcp_index.keys())

    return {
        "selected_tools": selected,
        "active_tool_names": active_tool_names,
        "active_local_payload": active_local_payload,
        "active_mcp_defs": active_mcp_defs,
        "active_mcp_index": active_mcp_index,
        "missing_selected": missing_selected,
        "all_known_tool_names": all_known_names,
    }
