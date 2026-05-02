# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# TaskManagement skill for the MiniAgentFramework.
#
# Provides create/read/update/delete operations on scheduled task JSON files stored in
# datacontrol/schedules/.  Each task lives in its own file named task_<name>.json so the
# dashboard scheduler hot-reloads changes within its next poll cycle without a restart.
#
# All public functions return plain-text status strings so the model can present them
# verbatim to the user or chain them into subsequent tool calls.
#
# Related modules:
#   - workspace_utils.py          -- get_schedules_dir()
#   - code/scheduler.py           -- consumes the JSON files produced here
#   - code/slash_commands.py      -- /task and /tasks commands share the same file convention
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import json
import re
from pathlib import Path

from KoreAgent.utils.workspace_utils import get_schedules_dir
from KoreAgent.utils.workspace_utils import trunc


# ====================================================================================================
# MARK: INTERNAL HELPERS
# ====================================================================================================
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_name(name: str) -> str:
    """Strip whitespace and verify the name is safe for use as a filename component."""
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Task name cannot be empty.")
    if not _NAME_RE.match(cleaned):
        raise ValueError(
            f"Task name '{cleaned}' contains invalid characters. "
            "Use only letters, digits, hyphens, and underscores."
        )
    return cleaned


def _parse_schedule(schedule: str) -> dict:
    """Parse a schedule string into a schedule dict.

    Accepts:
      - plain integer string  →  interval every N minutes  (e.g. "60")
      - "HH:MM"               →  daily at that wall-clock time  (e.g. "08:30")
    """
    s = schedule.strip()
    if re.fullmatch(r"\d{1,2}:\d{2}", s):
        h, m = int(s.split(":")[0]), int(s.split(":")[1])
        if h > 23 or m > 59:
            raise ValueError(
                f"Invalid time '{s}' - hour must be 0-23 and minute must be 0-59."
            )
        return {"type": "daily", "time": s}
    try:
        minutes = int(s)
    except ValueError:
        raise ValueError(
            f"Invalid schedule '{s}'. Use a number of minutes (e.g. '60') "
            "or a daily time in HH:MM format (e.g. '08:30')."
        )
    if minutes < 1:
        raise ValueError("Interval must be at least 1 minute.")
    return {"type": "interval", "minutes": minutes}


def _schedule_str(schedule: dict) -> str:
    stype = schedule.get("type", "?")
    if stype == "interval":
        return f"every {schedule.get('minutes', '?')} min"
    if stype == "daily":
        return f"daily @ {schedule.get('time', '?')}"
    return stype


def _find_task(name: str) -> "tuple[Path, dict, int] | None":
    """Locate a task by name (case-insensitive).

    Returns (json_path, full_data_dict, task_index_in_list) or None.
    """
    schedules_dir = get_schedules_dir()
    if not schedules_dir.exists():
        return None
    for json_path in sorted(schedules_dir.glob("*.json")):
        try:
            data  = json.loads(json_path.read_text(encoding="utf-8"))
            tasks = data.get("tasks", [])
        except Exception:
            continue
        for idx, task in enumerate(tasks):
            if task.get("name", "").lower() == name.lower():
                return (json_path, data, idx)
    return None


def _save(json_path: Path, data: dict) -> None:
    json_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ====================================================================================================
# MARK: PUBLIC SKILL FUNCTIONS
# ====================================================================================================

def task_list() -> str:
    """Return a formatted summary of all scheduled tasks."""
    schedules_dir = get_schedules_dir()
    if not schedules_dir.exists():
        return "No schedules directory found."

    json_files = sorted(schedules_dir.glob("*.json"))
    if not json_files:
        return "No scheduled tasks found."

    lines: list[str] = []
    total = 0
    for json_path in json_files:
        try:
            data  = json.loads(json_path.read_text(encoding="utf-8"))
            tasks = data.get("tasks", [])
        except Exception as exc:
            lines.append(f"  (error reading {json_path.name}: {exc})")
            continue
        for task in tasks:
            name     = task.get("name", "?")
            enabled  = task.get("enabled", True)
            schedule = task.get("schedule", {})
            prompts  = task.get("prompts", [])
            status   = "on " if enabled else "off"
            sched    = _schedule_str(schedule)
            first_prompt = prompts[0] if prompts else ""
            if isinstance(first_prompt, dict):
                first_prompt = first_prompt.get("prompt", "")
            preview  = trunc(str(first_prompt), 70) if first_prompt else "(no prompts)"
            lines.append(f"  [{status}]  {name:<28}  {sched:<18}  {preview}")
            total += 1

    lines.append(f"\n{total} task(s) total.")
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------------

def task_get(name: str) -> str:
    """Return full details of a named task."""
    try:
        name = _validate_name(name)
    except ValueError as exc:
        return f"Error: {exc}"

    found = _find_task(name)
    if found is None:
        return f"Task '{name}' not found."

    _, data, idx = found
    task = data["tasks"][idx]

    lines: list[str] = [f"Task: {task.get('name')}"]
    lines.append(f"  enabled:  {task.get('enabled', True)}")
    schedule = task.get("schedule", {})
    lines.append(f"  schedule: {_schedule_str(schedule)}  ({json.dumps(schedule)})")
    prompts = task.get("prompts", [])
    for i, p in enumerate(prompts, 1):
        lines.append(f"  prompt[{i}]: {p}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------------

def task_create(name: str, schedule: str, prompt: str) -> str:
    """Create a new scheduled task.

    name     -- unique task identifier (letters, digits, hyphens, underscores)
    schedule -- interval in minutes (e.g. '60') or daily HH:MM (e.g. '08:30')
    prompt   -- the instruction the scheduler will run on each firing
    """
    try:
        name = _validate_name(name)
    except ValueError as exc:
        return f"Error: {exc}"

    if _find_task(name) is not None:
        return f"Error: A task named '{name}' already exists. Delete it first or choose a different name."

    try:
        schedule_dict = _parse_schedule(schedule)
    except ValueError as exc:
        return f"Error: {exc}"

    prompt = prompt.strip()
    if not prompt:
        return "Error: prompt cannot be empty."

    schedules_dir = get_schedules_dir()
    schedules_dir.mkdir(parents=True, exist_ok=True)
    json_path = schedules_dir / f"task_{name}.json"

    _save(json_path, {
        "tasks": [{
            "name":     name,
            "enabled":  True,
            "schedule": schedule_dict,
            "prompts":  [prompt],
        }]
    })
    return f"Task '{name}' created ({_schedule_str(schedule_dict)})."


# ----------------------------------------------------------------------------------------------------

def task_set_enabled(name: str, enabled: bool) -> str:
    """Enable or disable a task without changing its schedule or prompt."""
    # Coerce string values that some LLMs send instead of JSON booleans
    # (e.g. "false" instead of false).  bool("false") == True, so we must
    # handle this explicitly before using the value.
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in ("false", "0", "no", "off", "")
    try:
        name = _validate_name(name)
    except ValueError as exc:
        return f"Error: {exc}"

    found = _find_task(name)
    if found is None:
        return f"Task '{name}' not found."

    json_path, data, idx = found
    data["tasks"][idx]["enabled"] = bool(enabled)
    _save(json_path, data)
    state = "enabled" if enabled else "disabled"
    return f"Task '{name}' {state}."


# ----------------------------------------------------------------------------------------------------

def task_set_schedule(name: str, schedule: str) -> str:
    """Update the schedule of an existing task.

    schedule -- interval in minutes (e.g. '60') or daily HH:MM (e.g. '08:30')
    """
    try:
        name = _validate_name(name)
    except ValueError as exc:
        return f"Error: {exc}"

    found = _find_task(name)
    if found is None:
        return f"Task '{name}' not found."

    try:
        schedule_dict = _parse_schedule(schedule)
    except ValueError as exc:
        return f"Error: {exc}"

    json_path, data, idx = found
    data["tasks"][idx]["schedule"] = schedule_dict
    _save(json_path, data)
    return f"Task '{name}' schedule updated to {_schedule_str(schedule_dict)}."


# ----------------------------------------------------------------------------------------------------

def task_set_prompt(name: str, prompt: str) -> str:
    """Replace the prompt of an existing task."""
    try:
        name = _validate_name(name)
    except ValueError as exc:
        return f"Error: {exc}"

    found = _find_task(name)
    if found is None:
        return f"Task '{name}' not found."

    prompt = prompt.strip()
    if not prompt:
        return "Error: prompt cannot be empty."

    json_path, data, idx = found
    data["tasks"][idx]["prompts"] = [prompt]
    _save(json_path, data)
    return f"Task '{name}' prompt updated."


# ----------------------------------------------------------------------------------------------------

def task_delete(name: str) -> str:
    """Permanently delete a task. Removes the JSON file if it becomes empty."""
    try:
        name = _validate_name(name)
    except ValueError as exc:
        return f"Error: {exc}"

    found = _find_task(name)
    if found is None:
        return f"Task '{name}' not found."

    json_path, data, idx = found
    removed_name = data["tasks"][idx]["name"]
    data["tasks"].pop(idx)

    if data["tasks"]:
        _save(json_path, data)
    else:
        json_path.unlink()

    return f"Task '{removed_name}' deleted."
