"""Session-scoped Working Data for material held outside the active prompt context.

This is the public boundary for prompt-supporting data.  A named item is either a
text value or a structured collection; callers do not need to choose a separate
scratchpad or dataset subsystem.  The legacy stores remain private implementation
details while existing conversations are migrated to the unified payload.
"""

from __future__ import annotations

import json

from system_skills.WorkingData.collections.service import auto_route_tool_result as _auto_route_tool_result
from system_skills.WorkingData.collections.service import coerce_persisted_datasets_payload
from system_skills.WorkingData.collections.service import dataset_clear
from system_skills.WorkingData.collections.service import dataset_delete
from system_skills.WorkingData.collections.service import dataset_drop_where
from system_skills.WorkingData.collections.service import dataset_expand_full_text
from system_skills.WorkingData.collections.service import dataset_fetch_full_text
from system_skills.WorkingData.collections.service import dataset_filter
from system_skills.WorkingData.collections.service import dataset_get
from system_skills.WorkingData.collections.service import dataset_inspect
from system_skills.WorkingData.collections.service import dataset_rank
from system_skills.WorkingData.collections.service import dataset_select
from system_skills.WorkingData.collections.service import dataset_list
from system_skills.WorkingData.collections.service import dataset_rename
from system_skills.WorkingData.collections.service import dataset_save
from system_skills.WorkingData.collections.service import dataset_write_koredoc
from system_skills.WorkingData.collections.service import get_persisted_datasets_payload
from system_skills.WorkingData.collections.service import get_prompt_dataset_manifests
from system_skills.WorkingData.collections.service import hydrate_session_state
from sessions.runtime import get_active_session_id


_VALUES: dict[str, dict[str, str]] = {}
_PINS: dict[str, set[str]] = {}


def _resolved_session(session_id: str | None = None) -> str:
    return str(session_id or get_active_session_id() or "default").strip() or "default"


def _get_values(session_id: str | None = None) -> dict[str, str]:
    return _VALUES.setdefault(_resolved_session(session_id), {})


def _clear_values(session_id: str | None = None) -> str:
    count = len(_get_values(session_id))
    _get_values(session_id).clear()
    return f"Cleared {count} Working Data value(s)."


def _save_value(name: str, value: str, session_id: str | None = None) -> str:
    _get_values(session_id)[_normalise_name(name)] = str(value)
    return f"Saved Working Data item '{_normalise_name(name)}' ({len(str(value))} chars)."


def _get_value(name: str, session_id: str | None = None) -> str:
    value = _get_values(session_id).get(_normalise_name(name))
    return value if value is not None else f"Working Data item '{_normalise_name(name)}' not found."


def _delete_value(name: str, session_id: str | None = None) -> str:
    key = _normalise_name(name)
    return f"Deleted Working Data item '{key}'." if _get_values(session_id).pop(key, None) is not None else f"Working Data item '{key}' not found."


def _list_values(session_id: str | None = None) -> str:
    values = _get_values(session_id)
    return "Working Data values are empty." if not values else "Working Data values:\n" + "\n".join(f"  {key} ({len(value)} chars)" for key, value in sorted(values.items()))


def _search_values(substring: str, session_id: str | None = None) -> str:
    needle = str(substring or "").lower()
    matches = [key for key, value in _get_values(session_id).items() if needle in value.lower()]
    return "\n".join(matches) if matches else "No Working Data values matched."


def _peek_value(name: str, substring: str, context_chars: int = 250, session_id: str | None = None) -> str:
    value = _get_value(name, session_id)
    index = value.lower().find(str(substring or "").lower())
    return "Not found in Working Data." if index < 0 else value[max(0, index - context_chars):index + len(str(substring)) + context_chars]


def _query_value(name: str, query: str, save_result_name: str = "", instructions: str = "", session_id: str | None = None) -> str:
    result = _peek_value(name, query, session_id=session_id)
    if save_result_name and not result.startswith("Not found"):
        _save_value(save_result_name, result, session_id)
    return result


def _normalise_name(name: str) -> str:
    return str(name or "").strip().lower()


def _collection_names(session_id: str | None = None) -> set[str]:
    return {str(item.get("name") or "").lower() for item in get_prompt_dataset_manifests(session_id)}


def coerce_persisted_working_data_payload(
    payload: object,
    *,
    legacy_values: object = None,
    legacy_collections: object = None,
) -> dict[str, dict]:
    """Return the canonical Working Data envelope, accepting legacy conversation state."""
    candidate = payload if isinstance(payload, dict) else {}
    values = candidate.get("values") if isinstance(candidate.get("values"), dict) else legacy_values
    collections = candidate.get("collections") if isinstance(candidate.get("collections"), dict) else legacy_collections
    return {
        "values": {str(key): value for key, value in (values or {}).items()} if isinstance(values, dict) else {},
        "collections": coerce_persisted_datasets_payload(collections),
    }


def hydrate_working_data(
    payload: object,
    session_id: str | None = None,
    *,
    legacy_values: object = None,
    legacy_collections: object = None,
    warning_logger=None,
) -> dict[str, dict]:
    """Restore a session's unified Working Data payload into its runtime stores."""
    state = coerce_persisted_working_data_payload(
        payload,
        legacy_values=legacy_values,
        legacy_collections=legacy_collections,
    )
    hydrate_session_state(
        state["values"],
        session_id,
        datasets_payload=state["collections"],
        scratchpad_clearer=_clear_values,
        scratchpad_restorer=_save_value,
        warning_logger=warning_logger,
    )
    return state


def build_persisted_working_data_payload(session_id: str | None = None) -> dict[str, dict]:
    """Build the canonical persistable envelope, excluding transient tool-loop values."""
    values = {
        key: value
        for key, value in _get_values(session_id).items()
        if not key.startswith(("_tc_", "_cx_", "_wd_", "research_page_"))
    }
    return {"values": values, "collections": get_persisted_datasets_payload(session_id)}


def get_working_data_values(session_id: str | None = None) -> dict[str, str]:
    """Return active text items for prompt construction only."""
    return _get_values(session_id)


def get_working_data_value(name: str, session_id: str | None = None) -> str | None:
    """Return one live Working Data value for token substitution."""
    return _get_values(session_id).get(_normalise_name(name))


def get_prompt_working_data_collections(session_id: str | None = None) -> list[dict]:
    """Return compact collection manifests for prompt construction only."""
    return get_prompt_dataset_manifests(session_id)


def auto_route_working_data_result(func_name: str, arguments: dict, result: object) -> str | None:
    """Store record-shaped tool results as Working Data collections when appropriate."""
    return _auto_route_tool_result(func_name, arguments, result)


def working_data_pin(name: str, session_id: str | None = None) -> None:
    """Keep a transient Working Data item available until the current run ends."""
    _PINS.setdefault(_resolved_session(session_id), set()).add(_normalise_name(name))


def working_data_unpin_all(session_id: str | None = None) -> None:
    """Release transient Working Data item pins after a run."""
    _PINS.pop(_resolved_session(session_id), None)


def working_data_save(
    name: str,
    value: str | list[dict] | dict,
    source_tool: str = "",
    source_args: dict = None,
    replace: bool = False,
    session_id: str | None = None,
) -> str:
    """Save a statement, object, or record list outside active prompt context under one name."""
    normalized = _normalise_name(name)
    if isinstance(value, (list, dict)):
        records = value if isinstance(value, list) else [value]
        _delete_value(normalized, session_id=session_id)
        return dataset_save(normalized, records, source_tool, source_args, replace, session_id)
    if normalized in _collection_names(session_id):
        dataset_delete(normalized, session_id=session_id)
    return _save_value(normalized, str(value), session_id=session_id).replace("scratchpad key", "working-data item")


def working_data_get(
    name: str,
    indices: list[int] = None,
    max_records: int = 0,
    fields: list[str] = None,
    offset: int = 0,
    limit: int = 0,
    excerpt_chars: int = 1200,
    session_id: str | None = None,
) -> str:
    """Retrieve a named statement or bounded, excerpted records from Working Data."""
    normalized = _normalise_name(name)
    if normalized in _get_values(session_id):
        return _get_value(normalized, session_id=session_id)
    return dataset_get(normalized, indices, max_records, fields, offset, limit, excerpt_chars, session_id)


def working_data_list(session_id: str | None = None) -> str:
    """List all stored statements and record collections with compact size manifests."""
    values = _list_values(session_id=session_id).replace("Scratchpad", "Working-data values").replace("scratchpad", "working data")
    collections = dataset_list(session_id=session_id).replace("Datasets", "Working-data collections").replace("datasets", "collections")
    return f"Working Data:\n{values}\n{collections}"


def working_data_inspect(name: str, session_id: str | None = None) -> str:
    """Inspect a named item without loading an entire record collection into the prompt."""
    normalized = _normalise_name(name)
    if normalized in _get_values(session_id):
        value = _get_value(normalized, session_id=session_id)
        return json.dumps({"ok": True, "name": normalized, "kind": "value", "chars": len(value), "preview": value[:500]}, ensure_ascii=False)
    return dataset_inspect(normalized, session_id=session_id)


def working_data_delete(name: str, session_id: str | None = None) -> str:
    """Delete one named statement or record collection from Working Data."""
    normalized = _normalise_name(name)
    if normalized in _get_values(session_id):
        return _delete_value(normalized, session_id=session_id).replace("Scratchpad key", "Working-data item").replace("scratchpad key", "working-data item")
    return dataset_delete(normalized, session_id=session_id)


def working_data_clear(session_id: str | None = None) -> str:
    """Clear every statement and collection stored for the current session."""
    value_count = len(_get_values(session_id))
    collection_count = len(get_prompt_dataset_manifests(session_id))
    _clear_values(session_id)
    dataset_clear(session_id)
    return f"Cleared Working Data ({value_count} value(s), {collection_count} collection(s) removed)."


def working_data_search(substring: str, session_id: str | None = None) -> str:
    """Find text-bearing Working Data values containing a phrase without loading them all."""
    return _search_values(substring, session_id=session_id).replace("Scratchpad", "Working Data").replace("scratchpad", "working data")


def working_data_peek(name: str, substring: str, context_chars: int = 250, session_id: str | None = None) -> str:
    """Show a small excerpt around matching text in one stored statement."""
    return _peek_value(name, substring, context_chars, session_id=session_id).replace("scratchpad key", "working-data item")


def working_data_query(name: str, query: str, save_result_name: str = "", instructions: str = "", session_id: str | None = None) -> str:
    """Ask an isolated LLM to extract a compact answer from one large stored statement."""
    return _query_value(name, query, save_result_name, instructions, session_id=session_id).replace("scratchpad", "working data")


def working_data_rename(name: str, new_name: str, session_id: str | None = None) -> str:
    """Rename a Working Data statement or collection without loading its full content."""
    normalized = _normalise_name(name)
    if normalized in _get_values(session_id):
        value = _get_value(normalized, session_id=session_id)
        _save_value(new_name, value, session_id=session_id)
        _delete_value(normalized, session_id=session_id)
        return f"Renamed Working-data item '{normalized}' -> '{_normalise_name(new_name)}'."
    return dataset_rename(normalized, new_name, session_id=session_id)


def working_data_filter(name: str, prompt: str, save_as: str = "", replace: bool = False, fields: list[str] = None, excerpt_chars: int = 300, session_id: str | None = None) -> str:
    """Use an isolated LLM pass to retain relevant records from a Working Data collection."""
    return dataset_filter(name, prompt, save_as, replace, fields, excerpt_chars, session_id)


def working_data_rank(name: str, criteria: str, count: int = 5, save_as: str = "", fields: list[str] = None, excerpt_chars: int = 700, offset: int = 0, limit: int = 30, session_id: str | None = None) -> str:
    """Rank records in one isolated pass and save the top subset for a report or synthesis."""
    return dataset_rank(name, criteria, count, save_as, fields, excerpt_chars, offset, limit, session_id)


def working_data_select(name: str, indices: list[int], save_as: str = "", session_id: str | None = None) -> str:
    """Save explicitly selected source records as a smaller Working Data collection."""
    return dataset_select(name, indices, save_as, session_id)


def working_data_fetch_full_text(name: str, indices: list[int] = None, save_as: str = "", session_id: str | None = None) -> str:
    """Fetch full text for no more than five selected records into a new collection."""
    return dataset_fetch_full_text(name, indices, save_as, session_id)


def working_data_drop_where(name: str, predicate: str, save_as: str = "", replace: bool = False, session_id: str | None = None) -> str:
    """Apply a deterministic cleanup rule to a Working Data record collection."""
    return dataset_drop_where(name, predicate, save_as, replace, session_id)


def working_data_expand_full_text(name: str, save_as: str = "", replace: bool = False, offset: int = 0, limit: int = 0, session_id: str | None = None) -> str:
    """Expand artifact references in a Working Data collection into full text records."""
    return dataset_expand_full_text(name, save_as, replace, offset, limit, session_id)


def working_data_export(name: str, folder_path: str, document_name: str = "", fields: list[str] = None, offset: int = 0, limit: int = 0, session_id: str | None = None) -> str:
    """Export a Working Data record collection to a KoreDocs document."""
    return dataset_write_koredoc(name, folder_path, document_name, fields, offset, limit, session_id)
