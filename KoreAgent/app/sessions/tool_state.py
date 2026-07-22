import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import sessions.korechat_client as koreconv_client
from sessions.runtime import get_active_session_id


MAX_ACTIVE_TOOLS = 32
ALWAYS_ON_TOOL_NAMES = frozenset({"delegate", "tools_catalog_list", "tools_active_add"})

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


def kc_request_json(path: str, *, method: str = "GET", payload: dict | None = None) -> dict | list | None:
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


def fetch_conversation_for_session(session_id: str) -> dict | None:
    if not session_id:
        return None
    if session_id.startswith("kc_conv_"):
        raw_id = session_id[len("kc_conv_"):].strip()
        if raw_id.isdigit():
            result = kc_request_json(f"/conversations/{raw_id}")
            return result if isinstance(result, dict) else None
        return None
    external_id = urllib.parse.quote(_session_external_id(session_id), safe="")
    result = kc_request_json(f"/conversations/by-external-id/{external_id}")
    return result if isinstance(result, dict) else None


def ensure_conversation_for_session(session_id: str) -> dict | None:
    existing = fetch_conversation_for_session(session_id)
    if existing is not None or not session_id or session_id.startswith("kc_conv_"):
        return existing
    created = kc_request_json(
        "/conversations",
        method="POST",
        payload={
            "channel_type": "webchat",
            "subject": f"Webchat {session_id}",
            "protected": False,
            "external_id": _session_external_id(session_id),
        },
    )
    return created if isinstance(created, dict) else fetch_conversation_for_session(session_id)


def update_cache(session_id: str, tools_active: list[str]) -> None:
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
        conv = fetch_conversation_for_session(resolved_session_id)
        if isinstance(conv, dict):
            tools_active = _normalize_tool_names(conv.get("tools_active") or [])
    tools_active = tools_active[:MAX_ACTIVE_TOOLS]
    update_cache(resolved_session_id, tools_active)
    if isinstance(conversation_entry, dict):
        conversation_entry["tools_active"] = list(tools_active)
    return list(tools_active)


def set_selected_tools(
    tool_names: list[str],
    session_id: str | None = None,
    conversation_entry: dict | None = None,
    *,
    persist: bool = True,
) -> list[str]:
    resolved_session_id = _resolve_session_id(session_id)
    normalized = _normalize_tool_names(tool_names)[:MAX_ACTIVE_TOOLS]
    update_cache(resolved_session_id, normalized)
    if isinstance(conversation_entry, dict):
        conversation_entry["tools_active"] = list(normalized)

    if persist:
        conv = ensure_conversation_for_session(resolved_session_id) if resolved_session_id else None
        if isinstance(conv, dict) and conv.get("id") is not None:
            patched = kc_request_json(
                f"/conversations/{int(conv['id'])}",
                method="PATCH",
                payload={"tools_active": normalized},
            )
            if isinstance(patched, dict) and isinstance(conversation_entry, dict):
                conversation_entry.update(patched)
                conversation_entry["tools_active"] = _normalize_tool_names(patched.get("tools_active") or [])[:MAX_ACTIVE_TOOLS]
    return list(normalized)


def promote_selected_tools(
    tool_names: list[str],
    session_id: str | None = None,
    conversation_entry: dict | None = None,
    *,
    persist: bool = True,
) -> dict[str, list[str]]:
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
    active_tools = set_selected_tools(merged, session_id=session_id, conversation_entry=conversation_entry, persist=persist)
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
    promote_selected_tools([name], session_id=session_id, conversation_entry=conversation_entry, persist=False)
