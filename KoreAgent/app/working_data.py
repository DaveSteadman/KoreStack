# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Provides the unified, session-scoped Working Data boundary for text values and record collections
# held outside the active model context.
#
# Public API:
#   - Persistence and prompt helpers: coerce_persisted_working_data_payload(), hydrate_working_data(),
#     build_persisted_working_data_payload(), get_working_data_values(), and
#     get_prompt_working_data_collections().
#   - Tool-loop support: auto_route_working_data_result(), working_data_pin(), and
#     working_data_unpin_all().
#   - LLM tools: the working_data_* functions that save, retrieve, transform, and export items.
#
# Value and collection storage are delegated to their own WorkingData services; this module owns
# only the public unified boundary and routing policy.
# ====================================================================================================
"""Session-scoped Working Data for material held outside the active prompt context.

This is the public boundary for prompt-supporting data. A named item is either a
text value or a structured collection; callers do not need to choose a storage
subsystem. Existing conversations are migrated to the unified payload.
"""

# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
from __future__ import annotations

import json

from system_skills.WorkingData.collections.service import auto_route_tool_result as _auto_route_tool_result
from system_skills.WorkingData.collections.service import coerce_persisted_collections_payload
from system_skills.WorkingData.collections.service import dataset_clear as _clear_collections
from system_skills.WorkingData.collections.service import dataset_delete as _delete_collection
from system_skills.WorkingData.collections.service import dataset_drop_where as _drop_collection_records
from system_skills.WorkingData.collections.service import dataset_expand_full_text as _expand_collection_full_text
from system_skills.WorkingData.collections.service import dataset_fetch_full_text as _fetch_collection_full_text
from system_skills.WorkingData.collections.service import dataset_filter as _filter_collection
from system_skills.WorkingData.collections.service import dataset_get as _get_collection
from system_skills.WorkingData.collections.service import dataset_inspect as _inspect_collection
from system_skills.WorkingData.collections.service import dataset_rank as _rank_collection
from system_skills.WorkingData.collections.service import dataset_select as _select_collection
from system_skills.WorkingData.collections.service import dataset_list as _list_collections
from system_skills.WorkingData.collections.service import dataset_rename as _rename_collection
from system_skills.WorkingData.collections.service import dataset_save as _save_collection
from system_skills.WorkingData.collections.service import dataset_write_koredoc as _write_collection_koredoc
from system_skills.WorkingData.collections.service import get_persisted_collections_payload
from system_skills.WorkingData.collections.service import get_prompt_collection_manifests
from system_skills.WorkingData.collections.service import hydrate_working_data_state
from system_skills.WorkingData.values.service import build_persisted_values as _build_persisted_values
from system_skills.WorkingData.values.service import clear_values as _clear_values
from system_skills.WorkingData.values.service import delete_value as _delete_value
from system_skills.WorkingData.values.service import get_value as _get_value
from system_skills.WorkingData.values.service import get_values as _get_values
from system_skills.WorkingData.values.service import list_values as _list_values
from system_skills.WorkingData.values.service import normalise_name as _normalise_name
from system_skills.WorkingData.values.service import peek_value as _peek_value
from system_skills.WorkingData.values.service import pin_value as _pin_value
from system_skills.WorkingData.values.service import query_value as _query_value
from system_skills.WorkingData.values.service import save_value as _save_value
from system_skills.WorkingData.values.service import search_values as _search_values
from system_skills.WorkingData.values.service import unpin_all_values as _unpin_all_values


def _collection_names(session_id: str | None = None) -> set[str]:
    return {str(item.get("name") or "").lower() for item in get_prompt_collection_manifests(session_id)}


def _name_is_taken(name: str, session_id: str | None = None) -> bool:
    normalized = _normalise_name(name)
    return normalized in _get_values(session_id) or normalized in _collection_names(session_id)


# ====================================================================================================
# MARK: SESSION PERSISTENCE AND PROMPT STATE (PUBLIC)
# ====================================================================================================
def coerce_persisted_working_data_payload(
    payload: object,
) -> dict[str, dict]:
    """Return the canonical Working Data envelope."""
    candidate = payload if isinstance(payload, dict) else {}
    values      = candidate.get("values")
    collections = candidate.get("collections")
    canonical_values = {
        _normalise_name(str(key)): value
        for key, value in (values or {}).items()
        if _normalise_name(str(key))
    } if isinstance(values, dict) else {}
    canonical_collections = coerce_persisted_collections_payload(collections)
    canonical_collections = {
        name: collection
        for name, collection in canonical_collections.items()
        if _normalise_name(name) not in canonical_values
    }
    return {
        "values":      canonical_values,
        "collections": canonical_collections,
    }


def hydrate_working_data(
    payload: object,
    session_id: str | None = None,
    *,
    warning_logger=None,
) -> dict[str, dict]:
    """Restore a session's unified Working Data payload into its runtime stores."""
    state = coerce_persisted_working_data_payload(payload)
    hydrate_working_data_state(
        state["values"],
        session_id,
        collections_payload = state["collections"],
        values_clearer      = _clear_values,
        values_restorer     = _save_value,
        warning_logger      = warning_logger,
    )
    return state


def build_persisted_working_data_payload(session_id: str | None = None) -> dict[str, dict]:
    """Build the canonical persistable envelope, excluding transient tool-loop values."""
    return {
        "values":      _build_persisted_values(session_id),
        "collections": get_persisted_collections_payload(session_id),
    }


def get_working_data_values(session_id: str | None = None) -> dict[str, str]:
    """Return active text items for prompt construction only."""
    return _get_values(session_id)


def get_working_data_value(name: str, session_id: str | None = None) -> str | None:
    """Return one live Working Data value for token substitution."""
    return _get_values(session_id).get(_normalise_name(name))


def get_prompt_working_data_collections(session_id: str | None = None) -> list[dict]:
    """Return compact collection manifests for prompt construction only."""
    return get_prompt_collection_manifests(session_id)


# ====================================================================================================
# MARK: TOOL-LOOP INTEGRATION (PUBLIC)
# ====================================================================================================
def auto_route_working_data_result(func_name: str, arguments: dict, result: object) -> str | None:
    """Store record-shaped tool results as Working Data collections when appropriate."""
    return _auto_route_tool_result(func_name, arguments, result)


def working_data_pin(name: str, session_id: str | None = None) -> None:
    """Keep a transient Working Data item available until the current run ends."""
    _pin_value(name, session_id=session_id)


def working_data_unpin_all(session_id: str | None = None) -> None:
    """Release transient Working Data item pins after a run."""
    _unpin_all_values(session_id=session_id)


# ====================================================================================================
# MARK: LLM-CALLABLE WORKING DATA TOOLS (PUBLIC)
# ====================================================================================================
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
        return _save_collection(normalized, records, source_tool, source_args, replace, session_id)
    if normalized in _collection_names(session_id):
        _delete_collection(normalized, session_id=session_id)
    return _save_value(normalized, str(value), session_id=session_id)


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
    return _get_collection(normalized, indices, max_records, fields, offset, limit, excerpt_chars, session_id)


def working_data_list(session_id: str | None = None) -> str:
    """List all stored statements and record collections with compact size manifests."""
    values = _list_values(session_id=session_id)
    collections = _list_collections(session_id=session_id).replace("Datasets", "Working-data collections").replace("datasets", "collections")
    return f"Working Data:\n{values}\n{collections}"


def working_data_inspect(name: str, session_id: str | None = None) -> str:
    """Inspect a named item without loading an entire record collection into the prompt."""
    normalized = _normalise_name(name)
    if normalized in _get_values(session_id):
        value = _get_value(normalized, session_id=session_id)
        return json.dumps({"ok": True, "name": normalized, "kind": "value", "chars": len(value), "preview": value[:500]}, ensure_ascii=False)
    return _inspect_collection(normalized, session_id=session_id)


def working_data_delete(name: str, session_id: str | None = None) -> str:
    """Delete one named statement or record collection from Working Data."""
    normalized = _normalise_name(name)
    if normalized in _get_values(session_id):
        return _delete_value(normalized, session_id=session_id)
    return _delete_collection(normalized, session_id=session_id)


def working_data_clear(session_id: str | None = None) -> str:
    """Clear every statement and collection stored for the current session."""
    value_count = len(_get_values(session_id))
    collection_count = len(get_prompt_collection_manifests(session_id))
    _clear_values(session_id)
    _clear_collections(session_id)
    return f"Cleared Working Data ({value_count} value(s), {collection_count} collection(s) removed)."


def working_data_search(substring: str, session_id: str | None = None) -> str:
    """Find text-bearing Working Data values containing a phrase without loading them all."""
    return _search_values(substring, session_id=session_id)


def working_data_peek(name: str, substring: str, context_chars: int = 250, session_id: str | None = None) -> str:
    """Show a small excerpt around matching text in one stored statement."""
    return _peek_value(name, substring, context_chars, session_id=session_id)


def working_data_query(name: str, query: str, save_result_name: str = "", instructions: str = "", session_id: str | None = None) -> str:
    """Ask an isolated LLM to extract a compact answer from one large stored statement."""
    return _query_value(name, query, save_result_name, instructions, session_id=session_id)


def working_data_rename(name: str, new_name: str, session_id: str | None = None) -> str:
    """Rename a Working Data statement or collection without loading its full content."""
    normalized = _normalise_name(name)
    target     = _normalise_name(new_name)
    if normalized != target and _name_is_taken(target, session_id):
        return f"Error: Working Data item '{target}' already exists."
    if normalized in _get_values(session_id):
        value = _get_value(normalized, session_id=session_id)
        _save_value(target, value, session_id=session_id)
        _delete_value(normalized, session_id=session_id)
        return f"Renamed Working-data item '{normalized}' -> '{target}'."
    return _rename_collection(normalized, target, session_id=session_id)


def working_data_filter(name: str, prompt: str, save_as: str = "", replace: bool = False, fields: list[str] = None, excerpt_chars: int = 300, session_id: str | None = None) -> str:
    """Use an isolated LLM pass to retain relevant records from a Working Data collection."""
    return _filter_collection(name, prompt, save_as, replace, fields, excerpt_chars, session_id)


def working_data_rank(name: str, criteria: str, count: int = 5, save_as: str = "", fields: list[str] = None, excerpt_chars: int = 700, offset: int = 0, limit: int = 30, session_id: str | None = None) -> str:
    """Rank records in one isolated pass and save the top subset for a report or synthesis."""
    return _rank_collection(name, criteria, count, save_as, fields, excerpt_chars, offset, limit, session_id)


def working_data_select(name: str, indices: list[int], save_as: str = "", session_id: str | None = None) -> str:
    """Save explicitly selected source records as a smaller Working Data collection."""
    return _select_collection(name, indices, save_as, session_id)


def working_data_fetch_full_text(name: str, indices: list[int] = None, save_as: str = "", session_id: str | None = None) -> str:
    """Fetch full text for no more than five selected records into a new collection."""
    return _fetch_collection_full_text(name, indices, save_as, session_id)


def working_data_drop_where(name: str, predicate: str, save_as: str = "", replace: bool = False, session_id: str | None = None) -> str:
    """Apply a deterministic cleanup rule to a Working Data record collection."""
    return _drop_collection_records(name, predicate, save_as, replace, session_id)


def working_data_expand_full_text(name: str, save_as: str = "", replace: bool = False, offset: int = 0, limit: int = 0, session_id: str | None = None) -> str:
    """Expand artifact references in a Working Data collection into full text records."""
    return _expand_collection_full_text(name, save_as, replace, offset, limit, session_id)


def working_data_export(name: str, folder_path: str, document_name: str = "", fields: list[str] = None, offset: int = 0, limit: int = 0, session_id: str | None = None) -> str:
    """Export a Working Data record collection to a KoreDocs document."""
    return _write_collection_koredoc(name, folder_path, document_name, fields, offset, limit, session_id)
