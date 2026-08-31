# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Recovery policy for malformed and inactive tool requests. It normalises known wrapper shapes,
# classifies exact names against active and known tools, and directs the model to the complete
# catalog or reviewed keyword map without fuzzy name matching.
# MARK: FUNCTIONS
# Function inventory:
# - normalize_tool_request: Normalizes tool request for this module.
# - _compact_tool_name_list: Implements the  compact tool name list operation for this module.
# - classify_tool_recovery: Implements the classify tool recovery operation for this module.
# - build_tool_recovery_message: Builds tool recovery message for this module.
# - build_tool_recovery_reminder: Builds tool recovery reminder for this module.
# ====================================================================================================


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
    nested_args = normalized_args.get("arguments")
    if isinstance(nested_args, dict) and "id" in normalized_args and len(normalized_args) == 2:
        normalized_args = dict(nested_args)
        note_parts.append(f"{normalized_name}(id=..., arguments={{...}}) -> {normalized_name}(...)")
    return normalized_name, normalized_args, "; ".join(note_parts) if note_parts else None


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


def build_tool_recovery_message(event: dict[str, object]) -> str:
    classification = str(event.get("classification") or "unknown_name")
    requested = str(event.get("requested_tool") or "").strip()
    active_names = event.get("active_tool_names") if isinstance(event.get("active_tool_names"), list) else []

    if classification == "inactive_known":
        return (
            f"The tool `{requested}` exists but is not currently active. "
            f"Activate it with `tools_active_add([\"{requested}\"])` and then continue."
        )
    active_summary = _compact_tool_name_list(active_names)
    return (
        f"The requested tool `{requested}` is not available. "
        "Inspect the full catalog with `tools_catalog_list()` or the reviewed map with "
        "`skills_list()`, then select the exact Skill needed. "
        f"Currently active tools: {active_summary}."
    )


def build_tool_recovery_reminder(event: dict[str, object]) -> str:
    classification = str(event.get("classification") or "unknown_name")
    requested = str(event.get("requested_tool") or "").strip()
    if classification == "inactive_known" and event.get("auto_activated"):
        return f"Recovery still required: do not answer yet. Retry `{requested}` now; it is already active for this conversation."
    return f"Recovery still required: do not answer yet. Inspect the full tool catalog or Skill list and choose the exact capability needed for `{requested}`."


__all__ = [
    "build_tool_recovery_message",
    "build_tool_recovery_reminder",
    "classify_tool_recovery",
    "normalize_tool_request",
]
