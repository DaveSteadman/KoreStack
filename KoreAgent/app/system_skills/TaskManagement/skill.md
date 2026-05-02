# TaskManagement Skill

## Purpose
Create, query, update, enable, disable, and delete scheduled tasks stored as JSON files in `controldata/schedules/`. Each task defines a schedule and a prompt string that the scheduler runs automatically on each firing.

## Trigger keyword: task

## Interface
- Module: `code/KoreAgent/system_skills/TaskManagement/task_management_skill.py`
- Functions:
  - `task_list()`
  - `task_get(name: str)`
  - `task_create(name: str, schedule: str, prompt: str)`
  - `task_set_enabled(name: str, enabled: bool)`
  - `task_set_schedule(name: str, schedule: str)`
  - `task_set_prompt(name: str, prompt: str)`
  - `task_delete(name: str)`

## Parameters

### `task_list()`
No parameters.

### `task_get(name)`
- `name` *(required)* - exact task name (case-insensitive).

### `task_create(name, schedule, prompt)`
- `name` *(required)* - unique task name; alphanumeric, hyphens, underscores only.
- `schedule` *(required)* - interval as a plain integer string, e.g. `"60"` = every 60 minutes; OR a daily wall-clock time as `"HH:MM"`, e.g. `"08:30"` = every day at 08:30.
- `prompt` *(required)* - the natural-language instruction the scheduler will run on each firing.

### `task_set_enabled(name, enabled)`
- `name` *(required)* - task name.
- `enabled` *(required)* - `true` to enable, `false` to disable.

### `task_set_schedule(name, schedule)`
- `name` *(required)* - task name.
- `schedule` *(required)* - same format as `task_create`: integer minutes or `"HH:MM"`.

### `task_set_prompt(name, prompt)`
- `name` *(required)* - task name.
- `prompt` *(required)* - replacement prompt text.

### `task_delete(name)`
- `name` *(required)* - name of the task to permanently remove.

## Output
All functions return a plain-text status string confirming the operation or describing any error.
- `task_list()` - returns one line per task: `[on/off]  name  schedule  prompt-preview`.
- `task_get(...)` - returns a formatted block with all fields of the named task.
- All other functions return a confirmation or error string.

## Tool Selection Guidance
- For natural-language requests that ask to list, show, review, or summarise scheduled tasks, prefer `task_list()` immediately.
- Treat phrases like `list all my scheduled tasks`, `show my tasks`, `what tasks do I have`, `what scheduled tasks are active`, and `what automation is configured` as direct matches for `task_list()`.
- If the user names a specific task and asks for its details, use `task_get(name)` instead of `task_list()`.
- If the user types the literal slash command `/tasks`, that is handled by the slash-command layer; otherwise, natural-language task-listing requests should use this skill.

## Triggers
Invoke this skill when the prompt contains any of these concepts or phrases:
- `create task`, `add task`, `schedule a task`
- `list tasks`, `show tasks`, `what tasks are scheduled`
- `list all my scheduled tasks`, `show my scheduled tasks`, `show all scheduled tasks`
- `what tasks do I have`, `what scheduled tasks are active`, `what automation is configured`
- `enable task`, `disable task`, `turn on task`, `turn off task`
- `update task`, `change schedule`, `delete task`, `remove task`

## Scratchpad integration
Not applicable for scheduled tasks.  Each task fires in a fresh subprocess where the
scratchpad `_STORE` is always empty - values saved by a prior skill call within the
scheduled prompt will not survive to a subsequent one.  Scratchpad can be used normally
when TaskManagement is invoked as one step within an interactive session plan.

## Examples
- `task_list()` - show all scheduled tasks
- User prompt: `list all my scheduled tasks` -> call `task_list()`
- User prompt: `show my scheduled tasks` -> call `task_list()`
- User prompt: `what tasks do I have configured` -> call `task_list()`
- `task_get("PerformanceHeadroom")` - show full details of the named task
- `task_create("DailyWeather", "08:00", "Check the weather forecast for today.")` - create a daily task
  - Returns: `"Task 'DailyWeather' created."`
- `task_create("HourlyMemCheck", "60", "Check free RAM and log it to data/memlog.csv.")` - create an interval task
- `task_set_enabled("PerformanceHeadroom", False)` - disable the task
  - Returns: `"Task 'PerformanceHeadroom' updated."`
- `task_set_schedule("HourlyMemCheck", "30")` - change to every 30 minutes
- `task_delete("OldTask")` - permanently remove the task
