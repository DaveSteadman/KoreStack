# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Normalises malformed model tool requests and produces recovery guidance that preserves the exact
# tool-name contract. This module classifies recovery events; the execution loop applies them.
#
# Public API:
#   - tool_call_fingerprint()       -- gives equivalent provider calls a stable identity.
#   - normalize_tool_request()      -- unwraps recognised model call envelopes.
#   - classify_tool_recovery()      -- converts a failed request into a structured recovery event.
#   - build_tool_recovery_message() -- formats the immediate model-facing correction.
#   - build_tool_recovery_reminder() -- formats the repeated-failure reminder.
# ====================================================================================================
"""Exact-name tool recovery shared by the execution loop."""


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================

import json


# ====================================================================================================
# MARK: REQUEST NORMALISATION (PUBLIC)
# ====================================================================================================
def tool_call_fingerprint(tool_call: dict) -> tuple[str, str]:
    """Compare argument values rather than provider-specific JSON formatting."""
    function  = tool_call.get("function", {})
    arguments = function.get("arguments", {})
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            pass
    return function.get("name", ""), json.dumps(arguments, sort_keys=True, ensure_ascii=False)


def normalize_tool_request(func_name: str, arguments: dict | None) -> tuple[str, dict, str | None]:
    normalized_args = dict(arguments or {})
    normalized_name = func_name
    note_parts: list[str] = []
    if normalized_name == "assistant":
        nested_name = str(normalized_args.get("name") or "").strip()
        nested_args = normalized_args.get("arguments")
        if nested_name and isinstance(nested_args, dict):
            normalized_name = nested_name
            normalized_args = dict(nested_args)
            note_parts.append(f"assistant(...) -> {nested_name}(...)")
    # Handle model wrapping a tool call in its own function-call envelope:
    # e.g. get_page_links(id='functions.get_page_links', arguments={...})
    nested_args = normalized_args.get("arguments")
    if isinstance(nested_args, dict) and "id" in normalized_args and len(normalized_args) == 2:
        normalized_args = dict(nested_args)
        note_parts.append(f"{normalized_name}(id=..., arguments={{...}}) -> {normalized_name}(...)")
    return normalized_name, normalized_args, "; ".join(note_parts) if note_parts else None


# ====================================================================================================
# MARK: RECOVERY CLASSIFICATION
# ====================================================================================================
def _compact_tool_name_list(tool_names: set[str] | list[str] | tuple[str, ...] | None, *, limit: int = 10) -> str:
    names = sorted({str(name or "").strip() for name in (tool_names or []) if str(name or "").strip()})
    if not names:
        return "(none)"
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f", ... (+{len(names) - limit} more)"


def classify_tool_recovery(
    requested_tool_name: str,
    *,
    active_tool_names: set[str] | None = None,
    all_known_tool_names: set[str] | None,
) -> dict[str, object]:
    requested = str(requested_tool_name or "").strip()
    active_names = set(active_tool_names or set())
    known_names = set(all_known_tool_names or set())
    if not requested:
        return {"classification": "unknown_name", "requested_tool": requested, "active_tool_names": sorted(active_names)}

    if requested in known_names:
        return {
            "classification": "active_known" if requested in active_names else "inactive_known",
            "requested_tool": requested,
            "active_tool_names": sorted(active_names),
        }

    return {
        "classification": "unknown_name",
        "requested_tool": requested,
        "active_tool_names": sorted(active_names),
    }


# ====================================================================================================
# MARK: RECOVERY MESSAGE FORMATTING (PUBLIC)
# ====================================================================================================
def build_tool_recovery_message(event: dict[str, object]) -> str:
    classification = str(event.get("classification") or "unknown_name")
    requested = str(event.get("requested_tool") or "").strip()
    active_names = event.get("active_tool_names")
    active_summary = _compact_tool_name_list(active_names if isinstance(active_names, list) else [])

    if classification == "inactive_known":
        return (
            f"Recovery required: tool `{requested}` exists in the runtime catalog but is not active for this conversation.\n"
            "Do not answer the user yet.\n"
            "Use ToolSelection now.\n"
            f"Call `tools_active_add([\"{requested}\"])`, then continue the task.\n"
            f"Currently active tools: {active_summary}"
        )

    return (
        f"Recovery required: requested tool `{requested}` is not a valid tool name in this runtime.\n"
        "Do not answer the user yet.\n"
        "Use ToolSelection now.\n"
        f"Call `skills_search(query={requested!r})` and select the correct Skill, or activate the exact tool, then continue the task.\n"
        f"Currently active tools: {active_summary}"
    )


def build_tool_recovery_reminder(event: dict[str, object]) -> str:
    requested = str(event.get("requested_tool") or "").strip()
    return f"Recovery still required: do not answer yet. Inspect the full tool catalog or Skill list and choose the exact capability needed for `{requested}`."
