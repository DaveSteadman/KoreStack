from __future__ import annotations

import json
from functools import wraps

from indepth_planner_archives import export_plan_archive, list_plan_archives, load_plan_archive
from indepth_planner_store import add_simple_task, clear_plan, create_simple_plan, get_simple_plan
from indepth_planner_store import clear_simple_dynamic, clear_simple_task_data
from indepth_planner_store import list_simple_tasks, mark_simple_task_ran
from indepth_planner_store import reset_simple_task_run
from indepth_planner_store import simple_run_to_completion_context
from indepth_planner_store import set_simple_task_data, update_simple_task


def _json(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def _summary(plan: dict) -> dict:
    tasks = list_simple_tasks()
    return {
        "objective": plan.get("static", {}).get("objective", ""),
        "tasks": [
            {"id": task["id"], "title": task["static"].get("title", ""), "ran": bool(task["dynamic"].get("ran")), "data": task["dynamic"].get("data", {})}
            for task in tasks
        ],
    }


_NO_WORKFLOW_ERROR = "Error: No Workflow exists in this KoreChat. Use workflow_create or workflow_import first."


def _requires_existing_workflow(function):
    """Reject Workflow operations until KoreChat contains a persisted Workflow."""
    @wraps(function)
    def guarded(*args, **kwargs):
        if not get_simple_plan():
            return _NO_WORKFLOW_ERROR
        return function(*args, **kwargs)
    return guarded


def workflow_create(objective: str, initial_tasks: list[object] | None = None) -> str:
    return _json(_summary(create_simple_plan(objective=objective, initial_tasks=initial_tasks)))


@_requires_existing_workflow
def workflow_get() -> str:
    return _json(get_simple_plan())


@_requires_existing_workflow
def workflow_get_summary() -> str:
    return _json(_summary(get_simple_plan()))


@_requires_existing_workflow
def workflow_get_task(task_id: str) -> str:
    for index, task in enumerate(list_simple_tasks(), start=1):
        if str(task["id"]) == str(task_id) or str(task_id).isdigit() and index == int(task_id):
            return _json(task)
    return f"Error: Task '{task_id}' not found."


@_requires_existing_workflow
def workflow_add_task(title: str, description: str = "", instruction: str = "", depends_on: list[str] | None = None) -> str:
    return _json(_summary(add_simple_task(title=title, description=description, instruction=instruction, depends_on=depends_on)))


@_requires_existing_workflow
def workflow_update_task(task_id: str, title: str | None = None, description: str | None = None, instruction: str | None = None, depends_on: list[str] | None = None) -> str:
    return _json(_summary(update_simple_task(task_id=task_id, title=title, description=description, instruction=instruction, depends_on=depends_on)))


@_requires_existing_workflow
def workflow_set_task_data(task_id: str, data: dict) -> str:
    return _json(_summary(set_simple_task_data(task_id=task_id, data=data)))


@_requires_existing_workflow
def workflow_clear_task_data(task_id: str) -> str:
    return _json(_summary(clear_simple_task_data(task_id=task_id)))


@_requires_existing_workflow
def workflow_reset_task_run(task_id: str) -> str:
    return _json(_summary(reset_simple_task_run(task_id=task_id)))


@_requires_existing_workflow
def workflow_clear_dynamic() -> str:
    return _json(_summary(clear_simple_dynamic()))


@_requires_existing_workflow
def workflow_mark_task_ran(task_id: str, note: str = "") -> str:
    return _json(_summary(mark_simple_task_ran(task_id=task_id, note=note)))


@_requires_existing_workflow
def workflow_run_to_completion() -> str:
    return _json(simple_run_to_completion_context())


@_requires_existing_workflow
def workflow_clear() -> str:
    clear_plan()
    return _json({"message": "Workflow cleared."})


@_requires_existing_workflow
def workflow_export(name: str) -> str:
    exported = export_plan_archive(name=name, plan=get_simple_plan())
    return _json({"name": exported["name"], "path": exported["path"]})


def workflow_import(name: str, replace: bool = False) -> str:
    if get_simple_plan() and not replace:
        return "Error: An active Workflow exists; use replace=true to replace it."
    loaded = load_plan_archive(name)
    from indepth_planner_store import save_workflow
    save_workflow(loaded["archive"]["plan"])
    return _json(_summary(get_simple_plan()))


@_requires_existing_workflow
def workflow_list_archives() -> str:
    return _json(list_plan_archives())
