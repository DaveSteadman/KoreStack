# InDepthPlanner Skill

Create and maintain a durable ordered plan in the active KoreChat.

## Model

The plan has two layers:

- `static`: the exportable objective and enduring task instructions.
- `dynamic`: disposable data from this chat instance, including whether a task ran.

## Functions

- `plan_create(objective, initial_tasks=None)`
- `plan_get()` / `plan_get_summary()` / `plan_get_task(task_id)`
- `plan_add_task(title, description="", instruction="", depends_on=None)`
- `plan_update_task(task_id, title=None, description=None, instruction=None, depends_on=None)`
- `plan_set_task_data(task_id, data)`
- `plan_clear_task_data(task_id)`
- `plan_reset_task_run(task_id)`
- `plan_clear_dynamic()`
- `plan_mark_task_ran(task_id, note="")`
- `plan_run_to_completion()`
- `plan_clear()`
- `plan_export(name)` / `plan_import(name, replace=False)` / `plan_list_archives()`

Use `Task 1`, `Task 2`, and so on in user-facing replies. `ran` only means the task was attempted in this chat; it is not a quality judgement or a claim that its output is final.

Dynamic data is disposable: clear a task's `data` without changing its `ran` flag with `plan_clear_task_data`; make a task available to run again without discarding its data with `plan_reset_task_run`; or remove every task's dynamic state with `plan_clear_dynamic`.

`plan_run_to_completion()` returns the remaining ordered work and is an execution directive: carry out every listed task in the same run, save each task's dynamic data, and mark it ran before moving to the next task.
