from __future__ import annotations

import json

from indepth_planner_store import activate_task
from indepth_planner_store import add_plan_task
from indepth_planner_store import attach_plan_reference
from indepth_planner_store import cancel_plan
from indepth_planner_store import clear_plan
from indepth_planner_store import complete_plan
from indepth_planner_store import complete_plan_task
from indepth_planner_store import create_plan
from indepth_planner_store import do_next
from indepth_planner_store import get_blockers
from indepth_planner_store import get_next_task
from indepth_planner_store import get_plan
from indepth_planner_store import get_plan_task
from indepth_planner_store import list_plan_tasks
from indepth_planner_store import record_plan_decision
from indepth_planner_store import reopen_plan
from indepth_planner_store import reassess_plan
from indepth_planner_store import set_plan_task_status
from indepth_planner_store import summarize_plan
from indepth_planner_store import update_plan_task


def _json(payload: object) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


def plan_create(
    objective: str,
    acceptance_criteria: list[str] | None = None,
    constraints: list[str] | None = None,
    initial_tasks: list[object] | None = None,
) -> str:
    replaced = bool(get_plan())
    saved = create_plan(
        objective=objective,
        acceptance_criteria=acceptance_criteria,
        constraints=constraints,
        initial_tasks=initial_tasks,
    )
    message = "Existing plan replaced." if replaced else "Plan created."
    return _json({"message": message, "summary": summarize_plan(saved)})


def plan_get(include_history: bool = False) -> str:
    payload = get_plan()
    if include_history:
        return _json(payload)
    payload = dict(payload)
    payload.pop("revisions", None)
    return _json(payload)


def plan_get_summary() -> str:
    return _json(summarize_plan(get_plan()))


def plan_history(limit: int = 20) -> str:
    payload = get_plan()
    revisions = payload.get("revisions") if isinstance(payload.get("revisions"), list) else []
    return _json(revisions[-max(1, int(limit)):])


def plan_reassess() -> str:
    return _json(reassess_plan())


def plan_reexamine() -> str:
    return plan_reassess()


def plan_add_task(
    title: str,
    task_id: str = "",
    description: str = "",
    task_statement: str = "",
    depends_on: list[str] | None = None,
    priority: str = "normal",
    input_refs: list[object] | None = None,
) -> str:
    saved = add_plan_task(
        title=title,
        task_id=task_id,
        description=description,
        task_statement=task_statement,
        depends_on=depends_on,
        priority=priority,
        input_refs=input_refs,
    )
    return _json({"message": "PlanTask added.", "summary": summarize_plan(saved)})


def plan_list_tasks(status: str | None = None, owner_kind: str | None = None, blocked_only: bool = False) -> str:
    tasks = list_plan_tasks()
    filtered = []
    for index, task in enumerate(tasks, start=1):
        execution = task.get("execution") if isinstance(task.get("execution"), dict) else {}
        definition = task.get("definition") if isinstance(task.get("definition"), dict) else {}
        if status and str(execution.get("status") or "") != str(status):
            continue
        if blocked_only and str(execution.get("status") or "") != "blocked":
            continue
        if owner_kind and str(definition.get("owner", {}).get("kind") or "") != str(owner_kind):
            continue
        item = json.loads(json.dumps(task))
        item["display_id"] = str(index)
        filtered.append(item)
    return _json(filtered)


def plan_update_task(
    task_id: str,
    title: str | None = None,
    description: str | None = None,
    task_statement: str | None = None,
    priority: str | None = None,
    depends_on: list[str] | None = None,
    input_refs: list[object] | None = None,
) -> str:
    saved = update_plan_task(
        task_id=task_id,
        title=title,
        description=description,
        task_statement=task_statement,
        priority=priority,
        depends_on=depends_on,
        input_refs=input_refs,
    )
    return _json({"message": f"PlanTask '{task_id}' updated.", "summary": summarize_plan(saved)})


def plan_get_task(task_id: str, include_outputs: bool = True) -> str:
    task = get_plan_task(task_id=task_id)
    if task is None:
        return f"Error: PlanTask '{task_id}' not found."
    if include_outputs:
        return _json(task)
    task = json.loads(json.dumps(task))
    task.get("execution", {}).pop("output_refs", None)
    return _json(task)


def plan_set_task_status(task_id: str, status: str, reason: str = "") -> str:
    saved = set_plan_task_status(task_id=task_id, status=status, reason=reason)
    return _json({"message": f"PlanTask '{task_id}' status updated.", "summary": summarize_plan(saved)})


def plan_complete_task(task_id: str, result_summary: str, output_refs: list[object] | None = None) -> str:
    saved = complete_plan_task(
        task_id        = task_id,
        result_summary = result_summary,
        output_refs    = output_refs,
    )
    return _json({"message": f"PlanTask '{task_id}' completed.", "summary": summarize_plan(saved)})


def plan_get_next() -> str:
    task = get_next_task()
    return _json(task or {})


def plan_activate_task(task_id: str, reason: str = "") -> str:
    saved = activate_task(task_id=task_id, reason=reason)
    return _json({"message": f"PlanTask '{task_id}' activated.", "summary": summarize_plan(saved)})


def plan_do_next() -> str:
    saved = do_next()
    return _json({"message": "Next eligible PlanTask activated.", "summary": summarize_plan(saved)})


def plan_attach_input(task_id: str, reference: object, summary: str = "") -> str:
    saved = attach_plan_reference(task_id=task_id, reference=reference, summary=summary, target="input")
    return _json({"message": f"Input attached to PlanTask '{task_id}'.", "summary": summarize_plan(saved)})


def plan_attach_output(task_id: str, reference: object, summary: str = "") -> str:
    saved = attach_plan_reference(task_id=task_id, reference=reference, summary=summary, target="output")
    return _json({"message": f"Output attached to PlanTask '{task_id}'.", "summary": summarize_plan(saved)})


def plan_get_blockers() -> str:
    return _json(get_blockers())


def plan_record_decision(summary: str, rationale: str = "", affected_task_ids: list[str] | None = None) -> str:
    saved = record_plan_decision(summary=summary, rationale=rationale, affected_task_ids=affected_task_ids)
    return _json({"message": "Decision recorded.", "summary": summarize_plan(saved)})


def plan_complete(summary: str = "") -> str:
    saved = complete_plan(summary=summary)
    return _json({"message": "Plan completed.", "summary": summarize_plan(saved)})


def plan_cancel(reason: str) -> str:
    saved = cancel_plan(reason=reason)
    return _json({"message": "Plan cancelled.", "summary": summarize_plan(saved)})


def plan_reopen(reason: str, proposed_changes: list[object] | None = None) -> str:
    saved = reopen_plan(reason=reason, proposed_changes=proposed_changes)
    return _json({"message": "Plan reopened.", "summary": summarize_plan(saved)})


def plan_clear() -> str:
    clear_plan()
    return _json({"message": "Plan cleared.", "summary": summarize_plan(get_plan())})
