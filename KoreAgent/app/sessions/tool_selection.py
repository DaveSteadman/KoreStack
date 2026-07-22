from sessions.tool_catalog import all_known_tool_names
from sessions.tool_catalog import build_all_tool_catalog
from sessions.tool_catalog import clear_runtime_caches as _clear_runtime_caches
from sessions.tool_catalog import derive_active_tool_runtime
from sessions.tool_catalog import filter_local_payload
from sessions.tool_catalog import local_tool_names
from sessions.tool_catalog import rank_tool_catalog_entries
from sessions.tool_catalog import suggest_tool_name
from sessions.tool_state import ALWAYS_ON_TOOL_NAMES
from sessions.tool_state import MAX_ACTIVE_TOOLS
from sessions.tool_state import _normalize_tool_names
from sessions.tool_state import _resolve_session_id
from sessions.tool_state import _session_external_id
from sessions.tool_state import clear_session_tools_active as _clear_session_tools_active
from sessions.tool_state import ensure_conversation_for_session as _ensure_conversation_for_session
from sessions.tool_state import fetch_conversation_for_session as _fetch_conversation_for_session
from sessions.tool_state import get_selected_tools
from sessions.tool_state import kc_request_json as _kc_request_json
from sessions.tool_state import note_tool_used as _note_tool_used
from sessions.tool_state import promote_selected_tools as _promote_selected_tools
from sessions.tool_state import set_selected_tools as _set_selected_tools
from sessions.tool_state import update_cache as _update_cache


def clear_session_tools_active(session_id: str) -> None:
    _clear_session_tools_active(session_id)
    _clear_runtime_caches()


def set_selected_tools(
    tool_names: list[str],
    session_id: str | None = None,
    conversation_entry: dict | None = None,
    *,
    persist: bool = True,
) -> list[str]:
    result = _set_selected_tools(
        tool_names,
        session_id=session_id,
        conversation_entry=conversation_entry,
        persist=persist,
    )
    _clear_runtime_caches()
    return result


def promote_selected_tools(
    tool_names: list[str],
    session_id: str | None = None,
    conversation_entry: dict | None = None,
    *,
    persist: bool = True,
) -> dict[str, list[str]]:
    result = _promote_selected_tools(
        tool_names,
        session_id=session_id,
        conversation_entry=conversation_entry,
        persist=persist,
    )
    _clear_runtime_caches()
    return result


def note_tool_used(tool_name: str, session_id: str | None = None, conversation_entry: dict | None = None) -> None:
    before = get_selected_tools(session_id=session_id, conversation_entry=conversation_entry)
    _note_tool_used(tool_name, session_id=session_id, conversation_entry=conversation_entry)
    after = get_selected_tools(session_id=session_id, conversation_entry=conversation_entry)
    if after != before:
        _clear_runtime_caches()


__all__ = [
    "ALWAYS_ON_TOOL_NAMES",
    "MAX_ACTIVE_TOOLS",
    "_ensure_conversation_for_session",
    "_fetch_conversation_for_session",
    "_kc_request_json",
    "_normalize_tool_names",
    "_resolve_session_id",
    "_session_external_id",
    "_update_cache",
    "all_known_tool_names",
    "build_all_tool_catalog",
    "clear_session_tools_active",
    "derive_active_tool_runtime",
    "filter_local_payload",
    "get_selected_tools",
    "local_tool_names",
    "note_tool_used",
    "promote_selected_tools",
    "rank_tool_catalog_entries",
    "set_selected_tools",
    "suggest_tool_name",
]
