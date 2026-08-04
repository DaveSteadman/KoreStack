# Workflow Skill

Create and maintain a durable ordered plan in the active KoreChat.

## Interface

- Module: `KoreAgent/app/system_skills/Workflow/workflow_skill.py`

### Model

The plan has two layers:

- `static`: the exportable objective and enduring task instructions.
- `dynamic`: disposable data from this chat instance, including whether a task ran.

### Functions

- `workflow_create(objective, initial_tasks=None)`
- `workflow_get()` / `workflow_get_summary()` / `workflow_get_task(task_id)`
- `workflow_add_task(title, description="", instruction="", depends_on=None, outputs=None, evidence_requirements=None)`
- `workflow_update_task(task_id, title=None, description=None, instruction=None, depends_on=None, outputs=None, evidence_requirements=None)`
- `workflow_set_task_data(task_id, data)`
- `workflow_record_task_result(task_id, outputs=None, evidence=None, note="")`
- `workflow_check_task_contract(task_id)`
- `workflow_clear_task_data(task_id)`
- `workflow_reset_task_run(task_id)`
- `workflow_clear_dynamic()`
- `workflow_mark_task_ran(task_id, note="")`
- `workflow_run_to_completion()`
- `workflow_clear()`
- `workflow_export(name)` / `workflow_import(name, replace=False)` / `workflow_list_archives()`

Use `Task 1`, `Task 2`, and so on in user-facing replies. `ran` only means the task was attempted in this chat; it is not a quality judgement or a claim that its output is final.

Dynamic data is disposable: clear a task's recorded data, outputs, and evidence without changing its `ran` flag with `workflow_clear_task_data`; make a task available to run again without discarding its data with `workflow_reset_task_run`; or remove every task's dynamic state with `workflow_clear_dynamic`.

Static tasks may declare multiple required `outputs` and `evidence_requirements`. Outputs use `file`, `dataset`, or `scratchpad` targets. Evidence requirements use `dataset_count` or `unique_field_count` with a dataset, minimum, and (for unique counts) field. Record the observed output references and evidence after a run with `workflow_record_task_result`; use `workflow_check_task_contract` or `workflow_get_summary` to show unmet requirements without changing the meaning of `ran`.

`workflow_run_to_completion()` returns the remaining ordered work and is an execution directive: carry out every listed task in the same run, save each task's dynamic data, and mark it ran before moving to the next task.
