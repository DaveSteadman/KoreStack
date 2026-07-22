"""Tool-call recovery helpers."""


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
    if normalized_name == "delegate" and "task" in normalized_args and "task_in" not in normalized_args:
        normalized_args["task_in"] = normalized_args.pop("task")
        note_parts.append("delegate(task=...) -> delegate(task_in=...)")
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

    try:
        from sessions.tool_selection import suggest_tool_name

        suggestion = suggest_tool_name(requested, known_names)
    except Exception:
        suggestion = {"status": "none", "requested_name": requested, "candidates": []}

    status = str(suggestion.get("status") or "none")
    corrected = str(suggestion.get("best_match") or "").strip()
    candidates = suggestion.get("candidates") if isinstance(suggestion.get("candidates"), list) else []

    if status == "corrected" and corrected:
        return {
            "classification": "corrected_active" if corrected in active_names else "corrected_inactive",
            "requested_tool": requested,
            "corrected_tool": corrected,
            "candidates": candidates,
            "active_tool_names": sorted(active_names),
        }
    if status == "ambiguous":
        return {
            "classification": "ambiguous_name",
            "requested_tool": requested,
            "corrected_tool": corrected,
            "candidates": candidates,
            "active_tool_names": sorted(active_names),
        }
    return {
        "classification": "unknown_name",
        "requested_tool": requested,
        "candidates": candidates,
        "active_tool_names": sorted(active_names),
    }


def build_tool_recovery_message(event: dict[str, object]) -> str:
    classification = str(event.get("classification") or "unknown_name")
    requested = str(event.get("requested_tool") or "").strip()
    corrected = str(event.get("corrected_tool") or "").strip()
    candidates = event.get("candidates") if isinstance(event.get("candidates"), list) else []
    active_names = event.get("active_tool_names") if isinstance(event.get("active_tool_names"), list) else []

    if classification == "inactive_known":
        return (
            f"The tool `{requested}` exists but is not currently active. "
            f"Activate it with `tools_active_add([\"{requested}\"])` and then continue."
        )
    if classification == "corrected_active":
        return (
            f"The requested tool name `{requested}` was not exact. "
            f"Use the active tool `{corrected}` instead."
        )
    if classification == "corrected_inactive":
        return (
            f"The requested tool name `{requested}` was not exact. "
            f"The closest known tool is `{corrected}`, but it is not active. "
            f"Activate it with `tools_active_add([\"{corrected}\"])` and continue."
        )
    if classification == "ambiguous_name":
        candidate_text = ", ".join(f"`{name}`" for name in candidates[:8]) or "(no suggestions)"
        return (
            f"The requested tool name `{requested}` is ambiguous or not exact. "
            f"Inspect the tool catalog with `tools_catalog_list(filter_text=\"{requested}\")` "
            f"and choose one of: {candidate_text}."
        )
    active_summary = _compact_tool_name_list(active_names)
    return (
        f"The requested tool `{requested}` is not available. "
        f"Inspect the active catalog with `tools_catalog_list()` and select the exact tool name needed. "
        f"Currently active tools: {active_summary}."
    )


def build_tool_recovery_reminder(event: dict[str, object]) -> str:
    classification = str(event.get("classification") or "unknown_name")
    requested = str(event.get("requested_tool") or "").strip()
    corrected = str(event.get("corrected_tool") or "").strip()
    if classification == "inactive_known" and event.get("auto_activated"):
        return f"Recovery still required: do not answer yet. Retry `{requested}` now; it is already active for this conversation."
    if classification == "corrected_active":
        return f"Recovery still required: do not answer yet. Retry with the corrected active tool name `{corrected}` only."
    if classification == "corrected_inactive":
        return f"Recovery still required: do not answer yet. Activate `{corrected}` with `tools_active_add([\"{corrected}\"])`, then continue the task."
    if classification == "ambiguous_name":
        return f"Recovery still required: do not answer yet. Inspect the catalog with `tools_catalog_list(filter_text=\"{requested}\")` and choose an exact tool name."
    return f"Recovery still required: do not answer yet. Inspect the tool catalog and choose the exact tool needed for `{requested}`."


__all__ = [
    "build_tool_recovery_message",
    "build_tool_recovery_reminder",
    "classify_tool_recovery",
    "normalize_tool_request",
]
