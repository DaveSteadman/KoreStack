# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Owns session-scoped text values used by Working Data.  This complements the collections service:
# values hold named text; collections hold structured records.
# ====================================================================================================

from __future__ import annotations

from sessions.runtime import get_active_session_id


_VALUES: dict[str, dict[str, str]] = {}
_PINS:   dict[str, set[str]]       = {}


def normalise_name(name: str) -> str:
    """Return one canonical Working Data item name."""
    return str(name or "").strip().lower()


def resolved_session(session_id: str | None = None) -> str:
    """Return the explicit, active, or default session identifier."""
    return str(session_id or get_active_session_id() or "default").strip() or "default"


def get_values(session_id: str | None = None) -> dict[str, str]:
    """Return the live text-value map for one session."""
    return _VALUES.setdefault(resolved_session(session_id), {})


def clear_values(session_id: str | None = None) -> str:
    """Delete every text value in one session."""
    count = len(get_values(session_id))
    get_values(session_id).clear()
    return f"Cleared {count} Working Data value(s)."


def save_value(name: str, value: str, session_id: str | None = None) -> str:
    """Store one named text value."""
    normalized = normalise_name(name)
    get_values(session_id)[normalized] = str(value)
    return f"Saved Working Data item '{normalized}' ({len(str(value))} chars)."


def get_value(name: str, session_id: str | None = None) -> str:
    """Return one text value or its standard not-found response."""
    normalized = normalise_name(name)
    value = get_values(session_id).get(normalized)
    return value if value is not None else f"Working Data item '{normalized}' not found."


def delete_value(name: str, session_id: str | None = None) -> str:
    """Delete one text value."""
    normalized = normalise_name(name)
    deleted = get_values(session_id).pop(normalized, None) is not None
    return f"Deleted Working Data item '{normalized}'." if deleted else f"Working Data item '{normalized}' not found."


def list_values(session_id: str | None = None) -> str:
    """List text-value names and sizes without returning their contents."""
    values = get_values(session_id)
    if not values:
        return "Working Data values are empty."
    return "Working Data values:\n" + "\n".join(
        f"  {key} ({len(value)} chars)"
        for key, value in sorted(values.items())
    )


def search_values(substring: str, session_id: str | None = None) -> str:
    """Return text-value names whose contents contain the requested phrase."""
    needle = str(substring or "").lower()
    matches = [key for key, value in get_values(session_id).items() if needle in value.lower()]
    return "\n".join(matches) if matches else "No Working Data values matched."


def peek_value(name: str, substring: str, context_chars: int = 250, session_id: str | None = None) -> str:
    """Return a bounded excerpt surrounding a phrase in one text value."""
    value = get_value(name, session_id)
    index = value.lower().find(str(substring or "").lower())
    if index < 0:
        return "Not found in Working Data."
    return value[max(0, index - context_chars):index + len(str(substring)) + context_chars]


def query_value(
    name: str,
    query: str,
    save_result_name: str = "",
    instructions: str = "",
    session_id: str | None = None,
) -> str:
    """Return the matching text excerpt and optionally save it as another value."""
    del instructions
    result = peek_value(name, query, session_id=session_id)
    if save_result_name and not result.startswith("Not found"):
        save_value(save_result_name, result, session_id)
    return result


def build_persisted_values(session_id: str | None = None) -> dict[str, str]:
    """Return persistent values while excluding per-run transient keys."""
    return {
        key: value
        for key, value in get_values(session_id).items()
        if not key.startswith(("_tc_", "_cx_", "_wd_", "research_page_"))
    }


def pin_value(name: str, session_id: str | None = None) -> None:
    """Mark a value as required for the current tool run."""
    _PINS.setdefault(resolved_session(session_id), set()).add(normalise_name(name))


def unpin_all_values(session_id: str | None = None) -> None:
    """Release all per-run value pins for one session."""
    _PINS.pop(resolved_session(session_id), None)
