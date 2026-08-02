from __future__ import annotations

from system_skills.TaskManagement.task_management_skill import task_create
from system_skills.TaskManagement.task_management_skill import task_delete
from system_skills.TaskManagement.task_management_skill import task_get
from system_skills.TaskManagement.task_management_skill import task_list
from system_skills.TaskManagement.task_management_skill import task_set_enabled
from system_skills.TaskManagement.task_management_skill import task_set_prompt
from system_skills.TaskManagement.task_management_skill import task_set_schedule


def cronprompt_list() -> str:
    return task_list()


def cronprompt_get(name: str) -> str:
    return task_get(name)


def cronprompt_create(name: str, schedule: str, prompt: str, output_template: str = "") -> str:
    return task_create(name, schedule, prompt, output_template=output_template)


def cronprompt_set_enabled(name: str, enabled: bool) -> str:
    return task_set_enabled(name, enabled)


def cronprompt_set_schedule(name: str, schedule: str) -> str:
    return task_set_schedule(name, schedule)


def cronprompt_set_prompt(name: str, prompt: str, output_template: str = "") -> str:
    return task_set_prompt(name, prompt, output_template=output_template)


def cronprompt_delete(name: str) -> str:
    return task_delete(name)
