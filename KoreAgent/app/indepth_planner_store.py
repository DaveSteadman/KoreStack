from __future__ import annotations

import json
import re
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sessions.runtime import get_active_session_id
from utils.workspace_utils import load_runtime_config

_PLAN_TRIGGER_RE = re.compile(
    r"\b(workflow|workflows|plan|steps|multi-step|multistep|phase|phases|roadmap|revisit|reopen|iterate|iteration|long term|long-term|run to completion|continue later|worker)\b",
    re.IGNORECASE,
)
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_VALID_PLAN_STATUSES = {"draft", "active", "completed", "blocked", "cancelled"}
_VALID_TASK_STATUSES = {"draft", "active", "completed", "blocked", "failed", "cancelled"}
_DEFAULT_TIMEOUT = 8


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _active_session_id() -> str:
    session_id = str(get_active_session_id() or "").strip()
    if not session_id:
        raise RuntimeError("No active session is bound for InDepthPlanner.")
    return session_id


def _external_id_for_session(session_id: str) -> str:
    return f"webchat_{session_id}"


def _subject_for_session(session_id: str) -> str:
    return f"Webchat {session_id}"


def _get_korechat_base() -> str:
    try:
        cfg = load_runtime_config()
        base = str(cfg.get("korechaturl") or "").strip().rstrip("/")
    except Exception:
        base = ""
    if not base:
        raise RuntimeError("KoreChat is not configured")
    return base


def _http_get(base: str, path: str, timeout: int = _DEFAULT_TIMEOUT) -> dict | list | None:
    req = urllib.request.Request(f"{base}{path}", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 204:
                return None
            raw = response.read().decode("utf-8").strip()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        if exc.code == 204:
            return None
        raise RuntimeError(f"KC HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:120]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"KC unreachable: {exc.reason}") from exc


def _http_post(base: str, path: str, payload: dict[str, Any], timeout: int = _DEFAULT_TIMEOUT) -> dict | None:
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8").strip()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"KC HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:120]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"KC unreachable: {exc.reason}") from exc


def _http_patch(base: str, path: str, payload: dict[str, Any], timeout: int = _DEFAULT_TIMEOUT) -> dict | None:
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8").strip()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"KC HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:120]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"KC unreachable: {exc.reason}") from exc


def _find_conversation_for_session(session_id: str) -> dict | None:
    base = _get_korechat_base()
    external_id = urllib.parse.quote(_external_id_for_session(session_id), safe="")
    try:
        result = _http_get(base, f"/api/conversations/by-external-id/{external_id}")
    except RuntimeError as exc:
        if "KC HTTP 404" in str(exc):
            return None
        raise
    return result if isinstance(result, dict) else None


def ensure_conversation_for_session(session_id: str | None = None) -> dict:
    active_session = session_id or _active_session_id()
    existing = _find_conversation_for_session(active_session)
    if existing is not None:
        return existing

    base = _get_korechat_base()
    created = _http_post(
        base,
        "/api/conversations",
        {
            "channel_type": "webchat",
            "subject": _subject_for_session(active_session),
            "external_id": _external_id_for_session(active_session),
        },
    )
    if not isinstance(created, dict):
        raise RuntimeError("Failed to create KoreChat conversation for InDepthPlanner.")
    return created


def load_workflow(session_id: str | None = None) -> tuple[dict, dict]:
    conversation = ensure_conversation_for_session(session_id)
    payload = conversation.get("workflow")
    if not isinstance(payload, dict):
        payload = conversation.get("indepth_planner")
    return conversation, payload if isinstance(payload, dict) else {}


def save_workflow(payload: dict[str, Any], *, session_id: str | None = None) -> dict:
    conversation = ensure_conversation_for_session(session_id)
    base = _get_korechat_base()
    persisted_payload = _to_persisted_plan(payload) if payload else {}
    result = _http_patch(
        base,
        f"/api/conversations/{conversation['id']}",
        {"workflow": persisted_payload},
    )
    if not isinstance(result, dict):
        raise RuntimeError("Failed to persist Workflow state.")
    if "workflow" not in result:
        raise RuntimeError(
            "KoreChat did not return the workflow field after the update. "
            "The running KoreChat service may not support durable Workflow storage."
        )
    updated_payload = result.get("workflow")
    if not isinstance(updated_payload, dict):
        raise RuntimeError("KoreChat returned an invalid Workflow payload after the update.")
    if updated_payload != persisted_payload:
        raise RuntimeError("KoreChat did not persist the requested Workflow state.")
    return _to_runtime_plan(updated_payload)


def delete_workflow(*, session_id: str | None = None) -> None:
    save_workflow({}, session_id=session_id)


# Compatibility aliases for internal callers during the Workflow migration.
load_indepth_planner   = load_workflow
save_indepth_planner   = save_workflow
delete_indepth_planner = delete_workflow


def _slugify(value: str) -> str:
    lowered = str(value or "").strip().lower()
    lowered = _SLUG_RE.sub("_", lowered).strip("_")
    return lowered or "item"


def _as_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        cleaned = str(item or "").strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _normalise_task_outputs(value: object) -> list[dict[str, Any]]:
    """Keep a durable, bounded output contract on a Workflow task."""
    if not isinstance(value, list):
        return []
    outputs: list[dict[str, Any]] = []
    for item in value[:12]:
        if not isinstance(item, dict):
            continue
        output_type = str(item.get("type") or "file").strip().lower()
        target = str(item.get("target") or item.get("path") or item.get("name") or "").strip()[:240]
        if output_type not in {"file", "dataset", "scratchpad"} or not target:
            continue
        if output_type == "dataset" and Path(target).suffix:
            output_type = "file"
        normalized: dict[str, Any] = {"type": output_type, "target": target}
        if isinstance(item.get("minimum_bytes"), int) and item["minimum_bytes"] >= 0:
            normalized["minimum_bytes"] = item["minimum_bytes"]
        if isinstance(item.get("minimum_items"), int) and item["minimum_items"] >= 0:
            normalized["minimum_items"] = item["minimum_items"]
        outputs.append(normalized)
    return outputs


def _normalise_evidence_requirements(value: object) -> list[dict[str, Any]]:
    """Keep only verifiable evidence requirements in the static task definition."""
    if not isinstance(value, list):
        return []
    requirements: list[dict[str, Any]] = []
    for item in value[:12]:
        if not isinstance(item, dict):
            continue
        requirement_type = str(item.get("type") or "").strip().lower()
        minimum = item.get("minimum")
        if requirement_type not in {"dataset_count", "unique_field_count"} or not isinstance(minimum, int) or minimum < 0:
            continue
        dataset = str(item.get("dataset") or "").strip()
        field = str(item.get("field") or "").strip()
        if not dataset or (requirement_type == "unique_field_count" and not field):
            continue
        normalized = {"type": requirement_type, "dataset": dataset, "minimum": minimum}
        if field:
            normalized["field"] = field
        requirements.append(normalized)
    return requirements


def _as_ref_list(value: object) -> list[object]:
    if not isinstance(value, list):
        return []
    result: list[object] = []
    for item in value:
        if isinstance(item, dict):
            result.append(dict(item))
        else:
            cleaned = str(item or "").strip()
            if cleaned:
                result.append(cleaned)
    return result


def _new_revision_entry(*, revision: int, reason: str, changes: list[object], actor: str = "assistant") -> dict[str, Any]:
    return {
        "revision": revision,
        "at": _utc_now(),
        "actor": actor,
        "reason": str(reason or "update").strip() or "update",
        "changes": list(changes),
    }


def _next_revision(payload: dict) -> int:
    current = payload.get("current") if isinstance(payload.get("current"), dict) else {}
    try:
        return int(current.get("revision") or 0) + 1
    except (TypeError, ValueError):
        return 1


def _copy_plan(payload: dict) -> dict[str, Any]:
    return json.loads(json.dumps(payload or {}))


def _to_persisted_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Store only durable task definitions plus disposable runtime data."""
    if isinstance(payload.get("static"), dict):
        return {
            "static":  _copy_plan(payload["static"]),
            "dynamic": _copy_plan(payload.get("dynamic")) if isinstance(payload.get("dynamic"), dict) else {"tasks": {}},
        }

    current = payload.get("current") if isinstance(payload.get("current"), dict) else {}
    static_tasks: list[dict[str, Any]] = []
    dynamic_tasks: dict[str, dict[str, Any]] = {}
    for index, raw_task in enumerate(current.get("tasks") or [], start=1):
        definition = raw_task.get("definition") if isinstance(raw_task, dict) and isinstance(raw_task.get("definition"), dict) else {}
        execution  = raw_task.get("execution")  if isinstance(raw_task, dict) and isinstance(raw_task.get("execution"),  dict) else {}
        task_id    = str(raw_task.get("task_id") or index) if isinstance(raw_task, dict) else str(index)
        static_tasks.append(
            {
                "id":          task_id,
                "title":       str(definition.get("title") or "Untitled task"),
                "description": str(definition.get("description") or ""),
                "instruction": str(definition.get("task_statement") or definition.get("title") or ""),
                "depends_on":  _as_string_list(definition.get("depends_on")),
                "outputs":     _normalise_task_outputs(definition.get("outputs")),
                "evidence_requirements": _normalise_evidence_requirements(definition.get("evidence_requirements")),
            }
        )
        task_data = {
            key: value
            for key, value in {
                "input_refs":     definition.get("input_refs"),
                "output_refs":    execution.get("output_refs"),
                "result_summary": execution.get("result_summary"),
            }.items()
            if value
        }
        dynamic_tasks[task_id] = {
            "ran":  str(execution.get("status") or "draft") != "draft",
            "data": task_data,
            "outputs": _as_ref_list(execution.get("output_refs")),
            "evidence": {},
        }
    return {
        "static":  {"objective": str(current.get("objective") or ""), "tasks": static_tasks},
        "dynamic": {"tasks": dynamic_tasks},
    }


def _to_runtime_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Provide the legacy runtime view while all public tools are migrated."""
    if not isinstance(payload.get("static"), dict):
        return _copy_plan(payload)
    static  = payload["static"]
    dynamic = payload.get("dynamic") if isinstance(payload.get("dynamic"), dict) else {}
    states  = dynamic.get("tasks") if isinstance(dynamic.get("tasks"), dict) else {}
    tasks = []
    for raw_task in static.get("tasks") or []:
        if not isinstance(raw_task, dict):
            continue
        task_id = str(raw_task.get("id") or "")
        state   = states.get(task_id) if isinstance(states.get(task_id), dict) else {}
        data    = state.get("data") if isinstance(state.get("data"), dict) else {}
        tasks.append(
            {
                "task_id": task_id,
                "definition": {
                    "title":          str(raw_task.get("title") or "Untitled task"),
                    "description":    str(raw_task.get("description") or ""),
                    "task_statement": str(raw_task.get("instruction") or raw_task.get("title") or ""),
                    "depends_on":     _as_string_list(raw_task.get("depends_on")),
                    "outputs":        _normalise_task_outputs(raw_task.get("outputs")),
                    "evidence_requirements": _normalise_evidence_requirements(raw_task.get("evidence_requirements")),
                    "priority":       "normal",
                    "owner":          {"kind": "assistant"},
                    "input_refs":     _as_ref_list(data.get("input_refs")),
                },
                "execution": {
                    "status":         "completed" if state.get("ran") else "draft",
                    "status_history": [],
                    "effort":         {},
                    "output_refs":    _as_ref_list(data.get("output_refs")),
                    "workflow_outputs": _as_ref_list(state.get("outputs")),
                    "workflow_evidence": _copy_plan(state.get("evidence")) if isinstance(state.get("evidence"), dict) else {},
                    "result_summary": str(data.get("result_summary") or ""),
                },
            }
        )
    return {"current": {"objective": str(static.get("objective") or ""), "tasks": tasks}}


def _task_status_history(status: str) -> list[dict[str, Any]]:
    return [{"status": status, "at": _utc_now()}]


def _next_task_id(used_ids: set[str]) -> str:
    candidate = 1
    while str(candidate) in used_ids:
        candidate += 1
    return str(candidate)


def _coerce_plan_task(raw: object, *, used_ids: set[str] | None = None) -> dict[str, Any]:
    used_ids = used_ids if used_ids is not None else set()
    if isinstance(raw, str):
        title = raw.strip() or "Untitled task"
        data: dict[str, Any] = {"title": title}
    elif isinstance(raw, dict):
        data = dict(raw)
    else:
        data = {"title": "Untitled task"}

    # Accept both a flat task supplied by a skill and the nested task shape
    # already persisted in a KoreChat plan.  Traversal methods call this
    # normalizer before writing, so losing nested values here would corrupt a
    # previously valid PlanTask on its next update.
    definition = data.get("definition") if isinstance(data.get("definition"), dict) else {}
    execution  = data.get("execution")  if isinstance(data.get("execution"),  dict) else {}

    title = str(data.get("title") or data.get("name") or definition.get("title") or "Untitled task").strip() or "Untitled task"
    description = str(data.get("description") or definition.get("description") or "").strip()
    task_statement = str(data.get("task_statement") or definition.get("task_statement") or description or title).strip()
    task_id = str(data.get("task_id") or "").strip()
    if not task_id:
        task_id = _next_task_id(used_ids)
    if task_id in used_ids:
        raise RuntimeError(f"PlanTask ID '{task_id}' is already in use.")
    used_ids.add(task_id)

    status = str(data.get("status") or execution.get("status") or "draft").strip().lower()
    if status not in _VALID_TASK_STATUSES:
        status = "draft"

    return {
        "task_id": task_id,
        "definition": {
            "title": title,
            "description": description,
            "task_statement": task_statement,
            "priority": str(data.get("priority") or definition.get("priority") or "normal").strip() or "normal",
            "depends_on": _as_string_list(data.get("depends_on") or definition.get("depends_on")),
            "owner": (
                dict(data.get("owner"))
                if isinstance(data.get("owner"), dict)
                else dict(definition.get("owner"))
                if isinstance(definition.get("owner"), dict)
                else {"kind": "assistant"}
            ),
            "input_refs": _as_ref_list(data.get("input_refs") or definition.get("input_refs")),
        },
        "execution": {
            "status": status,
            "status_history": (
                list(data.get("status_history"))
                if isinstance(data.get("status_history"), list)
                else list(execution.get("status_history"))
                if isinstance(execution.get("status_history"), list)
                else _task_status_history(status)
            ),
            "effort": dict(data.get("effort")) if isinstance(data.get("effort"), dict) else dict(execution.get("effort")) if isinstance(execution.get("effort"), dict) else {
                "attempt_count": 0,
                "worker_runs": 0,
                "dataset_refs": [],
                "scratchpad_refs": [],
            },
            "output_refs": _as_ref_list(data.get("output_refs") or execution.get("output_refs")),
            "result_summary": str(data.get("result_summary") or execution.get("result_summary") or "").strip(),
        },
    }


def _task_list_from_payload(payload: dict) -> list[dict[str, Any]]:
    current = payload.get("current") if isinstance(payload.get("current"), dict) else {}
    tasks = current.get("tasks") if isinstance(current.get("tasks"), list) else []
    result: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for task in tasks:
        result.append(_coerce_plan_task(task, used_ids=used_ids))
    return result


def _find_task(tasks: list[dict[str, Any]], task_id: str) -> dict[str, Any] | None:
    target = str(task_id or "").strip()
    for task in tasks:
        if str(task.get("task_id") or "") == target:
            return task

    # Compatibility for plans created before numeric IDs were introduced: a
    # user can still refer to their fourth listed task simply as "4".
    if target.isdecimal():
        index = int(target) - 1
        if 0 <= index < len(tasks):
            return tasks[index]
    return None


def _eligible_next_task(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    completed = {str(task.get("task_id") or "") for task in tasks if str(task.get("execution", {}).get("status") or "") == "completed"}
    for task in tasks:
        status = str(task.get("execution", {}).get("status") or "")
        depends_on = task.get("definition", {}).get("depends_on") if isinstance(task.get("definition"), dict) else []
        if status != "draft":
            continue
        if all(dep in completed for dep in (depends_on or [])):
            return task
    return None


def build_plan_payload(
    *,
    objective: str,
    acceptance_criteria: list[str] | None = None,
    constraints: list[str] | None = None,
    initial_tasks: list[object] | None = None,
    source: str = "manual",
) -> dict[str, Any]:
    now = _utc_now()
    used_ids: set[str] = set()
    tasks = [_coerce_plan_task(item, used_ids=used_ids) for item in (initial_tasks or [])]
    return {
        "baseline": {
            "objective": str(objective or "").strip(),
            "acceptance_criteria": _as_string_list(acceptance_criteria),
            "initial_tasks": _copy_plan({"tasks": tasks}).get("tasks", []),
        },
        "current": {
            "revision": 1,
            "status": "draft",
            "objective": str(objective or "").strip(),
            "acceptance_criteria": _as_string_list(acceptance_criteria),
            "constraints": _as_string_list(constraints),
            "tasks": tasks,
            "decisions": [],
        },
        "revisions": [
            {
                "revision": 1,
                "at": now,
                "actor": "assistant",
                "reason": f"Plan created ({source}).",
                "changes": ["initial plan created"],
            }
        ],
    }


def summarize_plan(payload: dict) -> dict[str, Any]:
    if not payload:
        return {
            "plan_status": "empty",
            "objective": "",
            "progress": {"completed": 0, "active": 0, "draft": 0, "blocked": 0, "failed": 0, "cancelled": 0, "total": 0},
            "tasks": [],
            "where_we_are": [],
            "next": None,
            "needs_attention": [],
            "recent_changes": [],
        }

    if payload.get("deleted") is True:
        return {
            "plan_status": "deleted",
            "objective": str(payload.get("objective") or ""),
            "progress": {"completed": 0, "active": 0, "draft": 0, "blocked": 0, "failed": 0, "cancelled": 0, "total": 0},
            "tasks": [],
            "where_we_are": [],
            "next": None,
            "needs_attention": [{"kind": "deleted", "reason": str(payload.get("deleted_reason") or "Deleted") }],
            "recent_changes": [f"Plan deleted at {payload.get('deleted_at') or '?'}"],
        }

    current = payload.get("current") if isinstance(payload.get("current"), dict) else {}
    tasks = _task_list_from_payload(payload)
    task_summaries = [
        {
            "display_id": str(index),
            "task_id": str(task.get("task_id") or ""),
            "title": str(task.get("definition", {}).get("title") or "Untitled task"),
            "status": str(task.get("execution", {}).get("status") or "draft"),
        }
        for index, task in enumerate(tasks, start=1)
    ]
    progress = {"completed": 0, "active": 0, "draft": 0, "blocked": 0, "failed": 0, "cancelled": 0, "total": len(tasks)}
    for task in tasks:
        status = str(task.get("execution", {}).get("status") or "draft")
        progress[status] = progress.get(status, 0) + 1

    next_task = _eligible_next_task(tasks)
    blockers = []
    for task in tasks:
        if str(task.get("execution", {}).get("status") or "") == "blocked":
            blockers.append(
                {
                    "task_id": task.get("task_id"),
                    "kind": "blocked",
                    "reason": str(task.get("execution", {}).get("result_summary") or "Blocked"),
                }
            )

    revisions = payload.get("revisions") if isinstance(payload.get("revisions"), list) else []
    recent_changes = []
    for entry in revisions[-3:]:
        if isinstance(entry, dict):
            recent_changes.append(str(entry.get("reason") or "update"))

    active_titles = [
        f"Task {index}: {task.get('definition', {}).get('title') or task.get('task_id') or ''}"
        for index, task in enumerate(tasks, start=1)
        if str(task.get("execution", {}).get("status") or "") == "active"
    ]
    where_we_are = [f"Active: {title}" for title in active_titles[:3]]
    if not where_we_are and progress["completed"] > 0:
        where_we_are.append(f"Completed PlanTasks: {progress['completed']}")

    return {
        "plan_status": str(current.get("status") or "draft"),
        "objective": str(current.get("objective") or payload.get("baseline", {}).get("objective") or ""),
        "progress": progress,
        "tasks": task_summaries,
        "where_we_are": where_we_are,
        "next": (
            {
                "display_id": str(tasks.index(next_task) + 1),
                "task_id": next_task.get("task_id"),
                "title": str(next_task.get("definition", {}).get("title") or ""),
                "status": str(next_task.get("execution", {}).get("status") or "draft"),
                "why_now": "Its dependencies are complete.",
            }
            if next_task is not None else None
        ),
        "needs_attention": blockers,
        "recent_changes": recent_changes,
    }


def create_plan(
    *,
    objective: str,
    acceptance_criteria: list[str] | None = None,
    constraints: list[str] | None = None,
    initial_tasks: list[object] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    payload = build_plan_payload(
        objective=objective,
        acceptance_criteria=acceptance_criteria,
        constraints=constraints,
        initial_tasks=initial_tasks,
        source="skill",
    )
    return save_indepth_planner(payload, session_id=session_id)


def clear_plan(*, session_id: str | None = None) -> None:
    """Remove the active plan from the current KoreChat conversation."""
    delete_indepth_planner(session_id=session_id)


def get_plan(*, session_id: str | None = None) -> dict[str, Any]:
    _conversation, payload = load_indepth_planner(session_id)
    return _to_runtime_plan(payload)


def list_plan_tasks(*, session_id: str | None = None) -> list[dict[str, Any]]:
    return _task_list_from_payload(get_plan(session_id=session_id))


def get_plan_task(*, task_id: str, session_id: str | None = None) -> dict[str, Any] | None:
    return _find_task(list_plan_tasks(session_id=session_id), task_id)


def _save_with_revision(payload: dict[str, Any], *, reason: str, changes: list[object], session_id: str | None = None) -> dict[str, Any]:
    next_revision = _next_revision(payload)
    current = payload.setdefault("current", {})
    current["revision"] = next_revision
    revisions = payload.setdefault("revisions", [])
    if isinstance(revisions, list):
        revisions.append(_new_revision_entry(revision=next_revision, reason=reason, changes=changes))
    return save_indepth_planner(payload, session_id=session_id)


def add_plan_task(
    *,
    title: str,
    task_id: str = "",
    description: str = "",
    task_statement: str = "",
    depends_on: list[str] | None = None,
    priority: str = "normal",
    input_refs: list[object] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    payload = get_plan(session_id=session_id)
    if not payload:
        raise RuntimeError("No active InDepthPlanner plan exists for this conversation.")
    tasks = _task_list_from_payload(payload)
    used_ids = {str(task.get("task_id") or "") for task in tasks}
    new_task = _coerce_plan_task(
        {
            "task_id": task_id,
            "title": title,
            "description": description,
            "task_statement": task_statement,
            "depends_on": depends_on or [],
            "priority": priority,
            "input_refs": input_refs or [],
        },
        used_ids=used_ids,
    )
    payload["current"]["tasks"] = tasks + [new_task]
    if str(payload["current"].get("status") or "draft") == "draft":
        payload["current"]["status"] = "active"
    return _save_with_revision(payload, reason=f"Added PlanTask '{title}'.", changes=[{"add_task": new_task.get("task_id")}], session_id=session_id)


def update_plan_task(
    *,
    task_id: str,
    title: str | None = None,
    description: str | None = None,
    task_statement: str | None = None,
    priority: str | None = None,
    depends_on: list[str] | None = None,
    input_refs: list[object] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    payload = get_plan(session_id=session_id)
    tasks = _task_list_from_payload(payload)
    task = _find_task(tasks, task_id)
    if task is None:
        raise RuntimeError(f"PlanTask '{task_id}' not found.")
    definition = task["definition"]
    if title is not None:
        definition["title"] = str(title).strip() or definition["title"]
    if description is not None:
        definition["description"] = str(description).strip()
    if task_statement is not None:
        definition["task_statement"] = str(task_statement).strip() or definition["task_statement"]
    if priority is not None:
        definition["priority"] = str(priority).strip() or definition.get("priority") or "normal"
    if depends_on is not None:
        definition["depends_on"] = _as_string_list(depends_on)
    if input_refs is not None:
        definition["input_refs"] = _as_ref_list(input_refs)
    payload["current"]["tasks"] = tasks
    return _save_with_revision(payload, reason=f"Updated PlanTask '{task_id}'.", changes=[{"update_task": task_id}], session_id=session_id)


def set_plan_task_status(*, task_id: str, status: str, reason: str = "", session_id: str | None = None) -> dict[str, Any]:
    normalized = str(status or "").strip().lower()
    if normalized not in _VALID_TASK_STATUSES:
        raise RuntimeError(f"Invalid PlanTask status '{status}'.")
    payload = get_plan(session_id=session_id)
    tasks = _task_list_from_payload(payload)
    task = _find_task(tasks, task_id)
    if task is None:
        raise RuntimeError(f"PlanTask '{task_id}' not found.")
    task["execution"]["status"] = normalized
    history = task["execution"].setdefault("status_history", [])
    if isinstance(history, list):
        history.append({"status": normalized, "at": _utc_now()})
    if reason:
        task["execution"]["result_summary"] = str(reason).strip()
    payload["current"]["tasks"] = tasks
    return _save_with_revision(payload, reason=f"PlanTask '{task_id}' status -> {normalized}.", changes=[{"set_status": task_id, "status": normalized}], session_id=session_id)


def complete_plan_task(
    *,
    task_id: str,
    result_summary: str,
    output_refs: list[object] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Record a completed PlanTask with its completion evidence in one revision."""
    payload = get_plan(session_id=session_id)
    tasks = _task_list_from_payload(payload)
    task = _find_task(tasks, task_id)
    if task is None:
        raise RuntimeError(f"PlanTask '{task_id}' not found.")

    execution = task["execution"]
    execution["status"] = "completed"
    history = execution.setdefault("status_history", [])
    if isinstance(history, list):
        history.append({"status": "completed", "at": _utc_now()})
    execution["result_summary"] = str(result_summary or "").strip()

    refs = execution.setdefault("output_refs", [])
    if isinstance(refs, list):
        refs.extend(_as_ref_list(output_refs))

    payload["current"]["tasks"] = tasks
    return _save_with_revision(
        payload,
        reason  = f"PlanTask '{task_id}' completed.",
        changes = [{"complete_task": task_id, "output_ref_count": len(_as_ref_list(output_refs))}],
        session_id = session_id,
    )


def attach_plan_reference(*, task_id: str, reference: object, summary: str = "", target: str = "output", session_id: str | None = None) -> dict[str, Any]:
    payload = get_plan(session_id=session_id)
    tasks = _task_list_from_payload(payload)
    task = _find_task(tasks, task_id)
    if task is None:
        raise RuntimeError(f"PlanTask '{task_id}' not found.")
    if target == "input":
        refs = task["definition"].setdefault("input_refs", [])
    else:
        refs = task["execution"].setdefault("output_refs", [])
    if isinstance(reference, dict):
        refs.append(dict(reference))
    else:
        refs.append(str(reference))
    if summary:
        task["execution"]["result_summary"] = str(summary).strip()
    payload["current"]["tasks"] = tasks
    return _save_with_revision(payload, reason=f"Attached {target} reference to PlanTask '{task_id}'.", changes=[{"attach_ref": task_id, "target": target}], session_id=session_id)


def record_plan_decision(*, summary: str, rationale: str = "", affected_task_ids: list[str] | None = None, session_id: str | None = None) -> dict[str, Any]:
    payload = get_plan(session_id=session_id)
    current = payload.get("current") if isinstance(payload.get("current"), dict) else {}
    decisions = current.setdefault("decisions", [])
    decisions.append(
        {
            "summary": str(summary).strip(),
            "rationale": str(rationale).strip(),
            "affected_task_ids": _as_string_list(affected_task_ids),
            "at": _utc_now(),
        }
    )
    return _save_with_revision(payload, reason=f"Recorded decision: {summary}", changes=[{"decision": str(summary).strip()}], session_id=session_id)


def complete_plan(*, summary: str = "", session_id: str | None = None) -> dict[str, Any]:
    payload = get_plan(session_id=session_id)
    payload.setdefault("current", {})["status"] = "completed"
    if summary:
        payload["current"]["completion_summary"] = str(summary).strip()
    return _save_with_revision(payload, reason="Plan completed.", changes=["complete"], session_id=session_id)


def cancel_plan(*, reason: str, session_id: str | None = None) -> dict[str, Any]:
    payload = get_plan(session_id=session_id)
    payload.setdefault("current", {})["status"] = "cancelled"
    payload["current"]["cancel_reason"] = str(reason).strip()
    return _save_with_revision(payload, reason=f"Plan cancelled: {reason}", changes=["cancel"], session_id=session_id)


def reopen_plan(*, reason: str, proposed_changes: list[object] | None = None, session_id: str | None = None) -> dict[str, Any]:
    payload = get_plan(session_id=session_id)
    payload.setdefault("current", {})["status"] = "active"
    return _save_with_revision(payload, reason=f"Plan reopened: {reason}", changes=list(proposed_changes or ["reopen"]), session_id=session_id)


def get_blockers(*, session_id: str | None = None) -> list[dict[str, Any]]:
    summary = summarize_plan(get_plan(session_id=session_id))
    needs_attention = summary.get("needs_attention")
    return needs_attention if isinstance(needs_attention, list) else []


def get_next_task(*, session_id: str | None = None) -> dict[str, Any] | None:
    return _eligible_next_task(_task_list_from_payload(get_plan(session_id=session_id)))


def activate_task(*, task_id: str, reason: str = "", session_id: str | None = None) -> dict[str, Any]:
    return set_plan_task_status(task_id=task_id, status="active", reason=reason, session_id=session_id)


def do_next(*, session_id: str | None = None) -> dict[str, Any]:
    next_task = get_next_task(session_id=session_id)
    if next_task is None:
        raise RuntimeError("No eligible draft PlanTask is ready.")
    return activate_task(task_id=str(next_task.get("task_id") or ""), reason="Activated by plan_do_next().", session_id=session_id)


def reassess_plan(*, session_id: str | None = None) -> dict[str, Any]:
    payload = get_plan(session_id=session_id)
    summary = summarize_plan(payload)
    proposal: dict[str, Any] = {
        "summary": summary,
        "proposed_changes": [],
        "reason": "No structural changes proposed.",
    }
    if summary.get("next") is None and summary.get("plan_status") == "active":
        proposal["reason"] = "No eligible next PlanTask is ready. Review blockers or add more work."
    if summary.get("progress", {}).get("failed", 0) > 0:
        proposal["proposed_changes"].append({"action": "review_failed_tasks"})
        proposal["reason"] = "Failed PlanTasks need review before continuing."
    return proposal


# ====================================================================================================
# MARK: SIMPLE STATIC / DYNAMIC PUBLIC MODEL
# ====================================================================================================
def get_simple_plan(*, session_id: str | None = None) -> dict[str, Any]:
    _conversation, payload = load_indepth_planner(session_id)
    return _to_persisted_plan(payload) if payload else {}


def _save_simple_plan(payload: dict[str, Any], *, session_id: str | None = None) -> dict[str, Any]:
    _validate_simple_plan(payload)
    save_indepth_planner(payload, session_id=session_id)
    return payload


def _validate_simple_plan(plan: dict[str, Any]) -> None:
    tasks = plan.get("static", {}).get("tasks", [])
    if not isinstance(tasks, list):
        raise RuntimeError("Plan static.tasks must be a list.")
    ids = [str(task.get("id") or "") for task in tasks if isinstance(task, dict)]
    if len(ids) != len(tasks) or not all(ids) or len(set(ids)) != len(ids):
        raise RuntimeError("Plan task IDs must be present and unique.")
    known = set(ids)
    dependencies = {str(task["id"]): set(_as_string_list(task.get("depends_on"))) for task in tasks}
    if any(not refs <= known for refs in dependencies.values()):
        raise RuntimeError("Every task dependency must refer to a task in the plan.")
    visited: set[str] = set()
    visiting: set[str] = set()
    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise RuntimeError("Plan task dependencies cannot contain a cycle.")
        if task_id not in visited:
            visiting.add(task_id)
            for dependency in dependencies[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)
    for task_id in dependencies:
        visit(task_id)


def create_simple_plan(*, objective: str, initial_tasks: list[object] | None = None, session_id: str | None = None) -> dict[str, Any]:
    used_ids: set[str] = set()
    tasks = []
    for raw_task in initial_tasks or []:
        task = _coerce_plan_task(raw_task, used_ids=used_ids)
        definition = task["definition"]
        tasks.append(
            {
                "id":                    task["task_id"],
                "title":                 definition["title"],
                "description":           definition["description"],
                "instruction":           definition["task_statement"],
                "depends_on":            definition["depends_on"],
                "outputs":               _normalise_task_outputs(raw_task.get("outputs") if isinstance(raw_task, dict) else None),
                "evidence_requirements": _normalise_evidence_requirements(raw_task.get("evidence_requirements") if isinstance(raw_task, dict) else None),
            }
        )
    return _save_simple_plan({"static": {"objective": str(objective or "").strip(), "tasks": tasks}, "dynamic": {"tasks": {}}}, session_id=session_id)


def add_simple_task(*, title: str, description: str = "", instruction: str = "", depends_on: list[str] | None = None, outputs: list[object] | None = None, evidence_requirements: list[object] | None = None, session_id: str | None = None) -> dict[str, Any]:
    plan = get_simple_plan(session_id=session_id)
    tasks = plan.setdefault("static", {}).setdefault("tasks", [])
    task_id = _next_task_id({str(task.get("id")) for task in tasks if isinstance(task, dict)})
    tasks.append(
        {
            "id":                    task_id,
            "title":                 str(title).strip(),
            "description":           str(description).strip(),
            "instruction":           str(instruction or title).strip(),
            "depends_on":            _as_string_list(depends_on),
            "outputs":               _normalise_task_outputs(outputs),
            "evidence_requirements": _normalise_evidence_requirements(evidence_requirements),
        }
    )
    return _save_simple_plan(plan, session_id=session_id)


def list_simple_tasks(*, session_id: str | None = None) -> list[dict[str, Any]]:
    plan = get_simple_plan(session_id=session_id)
    static = plan.get("static", {})
    states = plan.get("dynamic", {}).get("tasks", {})
    return [
        {
            "id": task.get("id"),
            "static": dict(task),
            "dynamic": dict(states.get(str(task.get("id")), {"ran": False, "data": {}, "outputs": [], "evidence": {}})),
        }
        for task in static.get("tasks", [])
        if isinstance(task, dict)
    ]


def _simple_task(plan: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    target = str(task_id).strip()
    labelled = re.match(r"^task\s+(\d+)\b", target, flags=re.IGNORECASE)
    if labelled:
        target = labelled.group(1)
    tasks = plan.get("static", {}).get("tasks", [])
    for index, task in enumerate(tasks, start=1):
        if str(task.get("id")) == target or (target.isdecimal() and index == int(target)):
            return task
    return None


def update_simple_task(*, task_id: str, title: str | None = None, description: str | None = None, instruction: str | None = None, depends_on: list[str] | None = None, outputs: list[object] | None = None, evidence_requirements: list[object] | None = None, session_id: str | None = None) -> dict[str, Any]:
    plan = get_simple_plan(session_id=session_id)
    task = _simple_task(plan, task_id)
    if task is None:
        raise RuntimeError(f"Task '{task_id}' not found.")
    for key, value in (("title", title), ("description", description), ("instruction", instruction)):
        if value is not None:
            task[key] = str(value).strip()
    if depends_on is not None:
        task["depends_on"] = _as_string_list(depends_on)
    if outputs is not None:
        task["outputs"] = _normalise_task_outputs(outputs)
    if evidence_requirements is not None:
        task["evidence_requirements"] = _normalise_evidence_requirements(evidence_requirements)
    return _save_simple_plan(plan, session_id=session_id)


def set_simple_task_data(*, task_id: str, data: dict[str, Any], session_id: str | None = None) -> dict[str, Any]:
    plan = get_simple_plan(session_id=session_id)
    task = _simple_task(plan, task_id)
    if task is None:
        raise RuntimeError(f"Task '{task_id}' not found.")
    states = plan.setdefault("dynamic", {}).setdefault("tasks", {})
    state = states.setdefault(str(task["id"]), {"ran": False, "data": {}, "outputs": [], "evidence": {}})
    state.setdefault("data", {}).update(dict(data or {}))
    return _save_simple_plan(plan, session_id=session_id)


def record_simple_task_result(*, task_id: str, outputs: list[object] | None = None, evidence: dict[str, Any] | None = None, note: str = "", session_id: str | None = None) -> dict[str, Any]:
    """Record run-specific output references and evidence without changing the static task contract."""
    plan = get_simple_plan(session_id=session_id)
    task = _simple_task(plan, task_id)
    if task is None:
        raise RuntimeError(f"Task '{task_id}' not found.")
    states = plan.setdefault("dynamic", {}).setdefault("tasks", {})
    state = states.setdefault(str(task["id"]), {"ran": False, "data": {}, "outputs": [], "evidence": {}})
    if outputs is not None:
        state["outputs"] = _as_ref_list(outputs)
    if evidence is not None:
        state["evidence"] = _copy_plan(evidence) if isinstance(evidence, dict) else {}
    if note:
        state.setdefault("data", {})["note"] = str(note).strip()
    return _save_simple_plan(plan, session_id=session_id)


def evaluate_simple_task_contract(*, task_id: str, session_id: str | None = None) -> list[str]:
    """Verify the static output/evidence contract; `ran` remains an attempt marker, not a quality flag."""
    plan = get_simple_plan(session_id=session_id)
    task = _simple_task(plan, task_id)
    if task is None:
        raise RuntimeError(f"Task '{task_id}' not found.")
    gaps: list[str] = []
    for output in _normalise_task_outputs(task.get("outputs")):
        output_type = output["type"]
        target = output["target"]
        if output_type == "file":
            from KoreCommon.datauser_fs import DataUserPathError, resolve_datauser_path
            try:
                candidate = resolve_datauser_path(target)
            except DataUserPathError:
                gaps.append(f"Output file '{target}' is outside the permitted data directory.")
                continue
            minimum_bytes = int(output.get("minimum_bytes") or 1)
            if not candidate.is_file() or candidate.stat().st_size < minimum_bytes:
                gaps.append(f"Required output file '{target}' is missing or smaller than {minimum_bytes} bytes.")
        elif output_type == "dataset":
            try:
                from datasets_pkg import dataset_inspect
                inspected = json.loads(dataset_inspect(target))
                minimum_items = int(output.get("minimum_items") or 0)
                if not inspected.get("ok") or int(inspected.get("count") or 0) < minimum_items:
                    gaps.append(f"Required dataset '{target}' is missing or has fewer than {minimum_items} items.")
            except Exception:
                gaps.append(f"Required dataset '{target}' could not be verified.")
        elif output_type == "scratchpad":
            from scratchpad import scratchpad_load
            value = scratchpad_load(target)
            if not isinstance(value, str) or value.startswith("Error:") or len(value.encode("utf-8")) < int(output.get("minimum_bytes") or 1):
                gaps.append(f"Required scratchpad output '{target}' is missing or too small.")
    for requirement in _normalise_evidence_requirements(task.get("evidence_requirements")):
        try:
            from datasets_pkg import dataset_get, dataset_inspect
            inspected = json.loads(dataset_inspect(requirement["dataset"]))
            if not inspected.get("ok"):
                gaps.append(f"Evidence dataset '{requirement['dataset']}' is unavailable.")
                continue
            if requirement["type"] == "dataset_count":
                if int(inspected.get("count") or 0) < requirement["minimum"]:
                    gaps.append(f"Evidence dataset '{requirement['dataset']}' has fewer than {requirement['minimum']} items.")
            else:
                records = json.loads(dataset_get(requirement["dataset"], max_records=0)).get("records") or []
                values = {str(record.get(requirement["field"])) for record in records if isinstance(record, dict) and record.get(requirement["field"]) not in (None, "")}
                if len(values) < requirement["minimum"]:
                    gaps.append(f"Evidence dataset '{requirement['dataset']}' has fewer than {requirement['minimum']} unique '{requirement['field']}' values.")
        except Exception:
            gaps.append(f"Evidence requirement for dataset '{requirement['dataset']}' could not be verified.")
    return gaps


def clear_simple_task_data(*, task_id: str, session_id: str | None = None) -> dict[str, Any]:
    """Discard all disposable run-specific state for one task, retaining its ran flag."""
    plan = get_simple_plan(session_id=session_id)
    task = _simple_task(plan, task_id)
    if task is None:
        raise RuntimeError(f"Task '{task_id}' not found.")
    states = plan.setdefault("dynamic", {}).setdefault("tasks", {})
    state = states.setdefault(str(task["id"]), {"ran": False, "data": {}, "outputs": [], "evidence": {}})
    state["data"]     = {}
    state["outputs"]  = []
    state["evidence"] = {}
    return _save_simple_plan(plan, session_id=session_id)


def reset_simple_task_run(*, task_id: str, session_id: str | None = None) -> dict[str, Any]:
    """Make one task eligible to run again, retaining any useful dynamic data."""
    plan = get_simple_plan(session_id=session_id)
    task = _simple_task(plan, task_id)
    if task is None:
        raise RuntimeError(f"Task '{task_id}' not found.")
    states = plan.setdefault("dynamic", {}).setdefault("tasks", {})
    state = states.setdefault(str(task["id"]), {"ran": False, "data": {}, "outputs": [], "evidence": {}})
    state["ran"] = False
    return _save_simple_plan(plan, session_id=session_id)


def clear_simple_dynamic(*, session_id: str | None = None) -> dict[str, Any]:
    """Discard all run-specific state while preserving the static plan."""
    plan = get_simple_plan(session_id=session_id)
    if not plan:
        raise RuntimeError("No active plan exists.")
    plan["dynamic"] = {"tasks": {}}
    return _save_simple_plan(plan, session_id=session_id)


def mark_simple_task_ran(*, task_id: str, note: str = "", session_id: str | None = None) -> dict[str, Any]:
    plan = get_simple_plan(session_id=session_id)
    task = _simple_task(plan, task_id)
    if task is None:
        raise RuntimeError(f"Task '{task_id}' not found.")
    states = plan.setdefault("dynamic", {}).setdefault("tasks", {})
    state = states.setdefault(str(task["id"]), {"ran": False, "data": {}, "outputs": [], "evidence": {}})
    state["ran"] = True
    if note:
        state.setdefault("data", {})["note"] = str(note).strip()
    return _save_simple_plan(plan, session_id=session_id)


def simple_run_to_completion_context(*, session_id: str | None = None) -> dict[str, Any]:
    """Return every remaining task in plan order; execution remains the caller's responsibility."""
    plan = get_simple_plan(session_id=session_id)
    remaining = [task for task in list_simple_tasks(session_id=session_id) if not task.get("dynamic", {}).get("ran")]
    return {
        "objective": plan.get("static", {}).get("objective", ""),
        "remaining_tasks": remaining,
        "instruction": (
            "Run every remaining task in the listed order. For each task, carry out its full static instruction, "
            "record outputs and evidence with workflow_record_task_result, save other useful instance data with workflow_set_task_data, "
            "and call workflow_mark_task_ran before moving to the next task. "
            "Do not mark a task ran without actually attempting it."
        ),
    }


def should_bootstrap_indepth_plan(user_prompt: str, task_plan: dict[str, Any] | None = None) -> bool:
    """Return whether the user explicitly requested the persistent InDepth Planner."""
    prompt = str(user_prompt or "").strip()
    if not prompt:
        return False
    return bool(_PLAN_TRIGGER_RE.search(prompt))


def maybe_seed_indepth_plan_from_task_plan(*, user_prompt: str, task_plan: dict[str, Any]) -> dict[str, Any]:
    payload = get_plan()
    current = payload.get("current") if isinstance(payload.get("current"), dict) else {}
    if current:
        return payload

    objective = str(task_plan.get("objective") or user_prompt).strip() or "Planned work"
    bootstrap_task = {
        "title": str(task_plan.get("objective") or "Initial PlanTask").strip() or "Initial PlanTask",
        "description": str(task_plan.get("rationale") or "Seeded from the lightweight task planner.").strip(),
        "task_statement": str(user_prompt).strip(),
        "priority": "normal",
        "depends_on": [],
        "input_refs": [],
    }
    payload = build_plan_payload(
        objective=objective,
        acceptance_criteria=[str(task_plan.get("completion_contract") or "Complete the requested work.").strip()],
        constraints=[],
        initial_tasks=[bootstrap_task],
        source="orchestrator",
    )
    payload.setdefault("current", {})["status"] = "active"
    payload["current"]["planner_hint"] = {
        "task_class": str(task_plan.get("task_class") or "general"),
        "current_phase": str(task_plan.get("current_phase") or "plan"),
        "workflow": list(task_plan.get("workflow") or []),
        "validation_requirements": list(task_plan.get("validation_requirements") or []),
    }
    return save_indepth_planner(payload)


# Workflow-facing names. Legacy implementation names remain private compatibility paths while
# callers transition to the durable Workflow vocabulary.
get_simple_workflow                 = get_simple_plan
list_workflow_tasks                 = list_simple_tasks
should_bootstrap_workflow           = should_bootstrap_indepth_plan
maybe_seed_workflow_from_task_plan  = maybe_seed_indepth_plan_from_task_plan
