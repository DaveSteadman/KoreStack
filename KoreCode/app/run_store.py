from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Run store helpers for KoreCode/app.
# Provides the focused helpers and module-level behaviour grouped into this file.
# ====================================================================================================

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from KoreCommon.suite_paths import get_suite_datacontrol_dir


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runs_root() -> Path:
    explicit = str(os.environ.get("KORECODE_RUNS_DIR", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (get_suite_datacontrol_dir() / "korecode" / "runs").resolve()


def _ensure_runs_root() -> Path:
    root = _runs_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _run_path(run_id: str) -> Path:
    return _ensure_runs_root() / f"{run_id}.json"


def _write_run(payload: dict[str, Any]) -> dict[str, Any]:
    run_id = str(payload["run_id"])
    path   = _run_path(run_id)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return payload


def _load_run(run_id: str) -> dict[str, Any]:
    path = _run_path(run_id)
    if not path.exists():
        raise FileNotFoundError(run_id)
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def _append_event(run: dict[str, Any], *, event_type: str, payload: dict[str, Any] | None = None) -> None:
    events = run.setdefault("events", [])
    if not isinstance(events, list):
        events = []
        run["events"] = events
    events.append(
        {
            "event_type": event_type,
            "created_at": _utc_now(),
            "payload":     payload or {},
        }
    )


def create_run(
    *,
    run_kind:                 str,
    mode:                     str,
    input_text:               str,
    visible_text:             str,
    prompt_override:          str,
    path:                     str,
    workspace_root:           Path,
    workspace_context_enabled: bool,
    conversation_external_id: str | None = None,
    parent_run_id:            str | None = None,
    work_item_id:             str | None = None,
    context:                  dict[str, Any] | None = None,
) -> dict[str, Any]:
    now    = _utc_now()
    run_id = uuid.uuid4().hex
    run    = {
        "run_id":                   run_id,
        "run_kind":                 str(run_kind or "").strip() or "chat_send",
        "mode":                     str(mode or "").strip() or "chat",
        "status":                   "created",
        "path":                     str(path or "").strip() or "__workspace__",
        "workspace_root":           str(workspace_root),
        "workspace_context_enabled": bool(workspace_context_enabled),
        "conversation_external_id": str(conversation_external_id or "").strip() or None,
        "conversation_id":          None,
        "parent_run_id":            str(parent_run_id or "").strip() or None,
        "work_item_id":             str(work_item_id or "").strip() or None,
        "input": {
            "text":            str(input_text or ""),
            "visible_text":    str(visible_text or ""),
            "prompt_override": str(prompt_override or ""),
        },
        "context":                   context or {},
        "tool_calls":                [],
        "model_responses":           [],
        "edits":                     [],
        "errors":                    [],
        "created_at":                now,
        "updated_at":                now,
        "completed_at":              None,
        "events":                    [],
    }
    _append_event(run, event_type="run_created", payload={"status": "created"})
    return _write_run(run)


def get_run(run_id: str) -> dict[str, Any]:
    return _load_run(run_id)


def list_runs(*, limit: int = 50) -> list[dict[str, Any]]:
    root = _ensure_runs_root()
    paths = sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    items: list[dict[str, Any]] = []
    for path in paths[: max(1, min(int(limit), 200))]:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            items.append(payload)
    return items


def find_latest_run(
    *,
    conversation_external_id: str | None = None,
    path: str | None = None,
    workspace_root: Path | str | None = None,
    statuses: set[str] | None = None,
) -> dict[str, Any] | None:
    target_external_id = str(conversation_external_id or "").strip() or None
    target_path        = str(path or "").strip() or None
    target_root        = str(workspace_root or "").strip() or None
    wanted_statuses    = {str(item).strip() for item in (statuses or set()) if str(item).strip()}

    for item in list_runs(limit=200):
        if target_external_id is not None and item.get("conversation_external_id") != target_external_id:
            continue
        if target_path is not None and item.get("path") != target_path:
            continue
        if target_root is not None and item.get("workspace_root") != target_root:
            continue
        if wanted_statuses and str(item.get("status") or "").strip() not in wanted_statuses:
            continue
        return item
    return None


def update_run(
    run_id: str,
    *,
    status: str | None = None,
    conversation_external_id: str | None = None,
    conversation_id: int | None = None,
    error: dict[str, Any] | None = None,
    event_type: str | None = None,
    event_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run = _load_run(run_id)

    if status is not None:
        run["status"] = str(status)
    if conversation_external_id is not None:
        run["conversation_external_id"] = str(conversation_external_id or "").strip() or None
    if conversation_id is not None:
        run["conversation_id"] = int(conversation_id)
    if error is not None:
        errors = run.setdefault("errors", [])
        if not isinstance(errors, list):
            errors = []
            run["errors"] = errors
        errors.append(error)
    if event_type:
        _append_event(run, event_type=event_type, payload=event_payload)

    if str(run.get("status") or "").strip() in {"completed", "failed"} and not run.get("completed_at"):
        run["completed_at"] = _utc_now()

    run["updated_at"] = _utc_now()
    return _write_run(run)


def append_tool_call(
    run_id: str,
    *,
    tool_name: str,
    request_index: int,
    ok: bool,
    request_args: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    run        = _load_run(run_id)
    tool_calls = run.setdefault("tool_calls", [])
    if not isinstance(tool_calls, list):
        tool_calls = []
        run["tool_calls"] = tool_calls
    tool_calls.append(
        {
            "tool":          str(tool_name or ""),
            "request_index": int(request_index),
            "ok":            bool(ok),
            "request_args":  request_args or {},
            "result":        result if ok else None,
            "error":         str(error or "") if not ok else None,
            "created_at":    _utc_now(),
        }
    )
    run["updated_at"] = _utc_now()
    return _write_run(run)


def append_edit_proposal(
    run_id: str,
    *,
    proposal_id: str,
    source: str,
    summary: str,
    validation_ok: bool,
    edits: list[dict[str, Any]],
) -> dict[str, Any]:
    run          = _load_run(run_id)
    edit_entries = run.setdefault("edits", [])
    if not isinstance(edit_entries, list):
        edit_entries = []
        run["edits"] = edit_entries
    edit_entries.append(
        {
            "proposal_id":   str(proposal_id or ""),
            "source":        str(source or ""),
            "summary":       str(summary or ""),
            "validation_ok": bool(validation_ok),
            "edits":         edits,
            "created_at":    _utc_now(),
        }
    )
    run["updated_at"] = _utc_now()
    return _write_run(run)


def append_model_response(
    run_id: str,
    *,
    role: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run             = _load_run(run_id)
    model_responses = run.setdefault("model_responses", [])
    if not isinstance(model_responses, list):
        model_responses = []
        run["model_responses"] = model_responses
    model_responses.append(
        {
            "role":       str(role or "assistant"),
            "content":    str(content or ""),
            "metadata":   metadata or {},
            "created_at": _utc_now(),
        }
    )
    run["updated_at"] = _utc_now()
    return _write_run(run)


def set_run_output(
    run_id: str,
    *,
    output_text: str,
    output_kind: str = "assistant_text",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run = _load_run(run_id)
    run["output"] = {
        "kind":       str(output_kind or "assistant_text"),
        "text":       str(output_text or ""),
        "metadata":   metadata or {},
        "created_at": _utc_now(),
    }
    run["updated_at"] = _utc_now()
    return _write_run(run)
