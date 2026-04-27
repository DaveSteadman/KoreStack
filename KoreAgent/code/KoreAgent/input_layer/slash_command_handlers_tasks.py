import json
import re
from typing import Callable

from KoreAgent.run_helpers import run_prompt_batch
from KoreAgent.input_layer.slash_command_context import SlashCommandContext
from KoreAgent.utils.workspace_utils import get_schedules_dir
from KoreAgent.utils.workspace_utils import trunc


def _cmd_tasks(arg: str, ctx: SlashCommandContext) -> None:
    schedules_dir = get_schedules_dir()
    if not schedules_dir.exists():
        ctx.output("Schedules directory not found.", "error")
        return

    json_files = sorted(schedules_dir.glob("*.json"))
    if not json_files:
        ctx.output("No schedule files found.", "dim")
        return

    total = 0
    for json_path in json_files:
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            tasks = data.get("tasks", [])
        except Exception as exc:
            ctx.output(f"  {json_path.name}: read error ({exc})", "error")
            continue
        for task in tasks:
            name = task.get("name", "?")
            enabled = task.get("enabled", True)
            schedule = task.get("schedule", {})
            schedule_type = schedule.get("type", "?")
            if schedule_type == "interval":
                sched_str = f"every {schedule.get('minutes', '?')} min"
            elif schedule_type == "daily":
                sched_str = f"daily @ {schedule.get('time', '?')}"
            else:
                sched_str = schedule_type
            prompts = task.get("prompts", [])
            status = "on " if enabled else "off"
            first_prompt = prompts[0] if prompts else ""
            if isinstance(first_prompt, dict):
                first_prompt = first_prompt.get("prompt", "")
            first_prompt = trunc(str(first_prompt), 60) if first_prompt else "(no prompts)"
            ctx.output(f"  [{status}]  {name:<28}  {sched_str:<18}  {first_prompt}", "item")
            total += 1

    ctx.output(f"{total} task(s) across {len(json_files)} file(s).", "info")


def _task_find(name: str):
    schedules_dir = get_schedules_dir()
    if not schedules_dir.exists():
        return None
    for json_path in sorted(schedules_dir.glob("*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            tasks = data.get("tasks", [])
        except Exception:
            continue
        for index, task in enumerate(tasks):
            if task.get("name", "").lower() == name.lower():
                return (json_path, data, index)
    return None


def _task_find_substr(fragment: str) -> list[tuple]:
    schedules_dir = get_schedules_dir()
    if not schedules_dir.exists():
        return []
    hits = []
    for json_path in sorted(schedules_dir.glob("*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            tasks = data.get("tasks", [])
        except Exception:
            continue
        for index, task in enumerate(tasks):
            if fragment.lower() in task.get("name", "").lower():
                hits.append((json_path, data, index))
    return hits


def _task_save(json_path, data: dict) -> None:
    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _cmd_task(arg: str, ctx: SlashCommandContext) -> None:
    parts = arg.strip().split(None, 1)
    if not parts:
        ctx.output("Usage: /task <enable|disable|add|delete|run> [args]  |  /tasks to list all tasks", "dim")
        return

    sub = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    if sub in ("enable", "disable"):
        if not rest:
            ctx.output(f"Usage: /task {sub} <name>", "dim")
            return
        found = _task_find(rest)
        if found is None:
            ctx.output(f"Task '{rest}' not found.", "error")
            return
        json_path, data, index = found
        data["tasks"][index]["enabled"] = sub == "enable"
        _task_save(json_path, data)
        ctx.output(f"Task '{data['tasks'][index]['name']}' {sub}d.", "success")
        return

    if sub == "delete":
        if not rest:
            ctx.output("Usage: /task delete <name>", "dim")
            return
        found = _task_find(rest)
        if found is None:
            ctx.output(f"Task '{rest}' not found.", "error")
            return
        json_path, data, index = found
        removed_name = data["tasks"][index]["name"]
        data["tasks"].pop(index)
        if data["tasks"]:
            _task_save(json_path, data)
        else:
            json_path.unlink()
        ctx.output(f"Task '{removed_name}' deleted.", "success")
        return

    if sub == "add":
        add_parts = rest.split(None, 2)
        if len(add_parts) < 3:
            ctx.output("Usage: /task add <name> <minutes|HH:MM> <prompt>", "dim")
            ctx.output("  minutes  = interval schedule (e.g. 60)", "dim")
            ctx.output("  HH:MM    = daily schedule at that wall-clock time (e.g. 08:30)", "dim")
            return
        task_name, sched_arg, prompt_text = add_parts[0], add_parts[1], add_parts[2].strip()
        if re.fullmatch(r"\d{1,2}:\d{2}", sched_arg):
            schedule = {"type": "daily", "time": sched_arg}
            sched_str = f"daily @ {sched_arg}"
        else:
            try:
                minutes = int(sched_arg)
            except ValueError:
                ctx.output(f"Invalid schedule '{sched_arg}': use a number of minutes or HH:MM.", "error")
                return
            schedule = {"type": "interval", "minutes": minutes}
            sched_str = f"every {minutes} min"
        if _task_find(task_name) is not None:
            ctx.output(f"A task named '{task_name}' already exists. Delete it first or choose a different name.", "error")
            return
        schedules_dir = get_schedules_dir()
        schedules_dir.mkdir(parents=True, exist_ok=True)
        json_path = schedules_dir / f"task_{task_name}.json"
        new_data = {"tasks": [{"name": task_name, "enabled": True, "schedule": schedule, "prompts": [prompt_text]}]}
        _task_save(json_path, new_data)
        ctx.output(f"Task '{task_name}' created ({sched_str}).", "success")
        ctx.output(f"  Prompt: {trunc(prompt_text, 80)}", "dim")
        return

    if sub == "run":
        if not rest:
            ctx.output("Usage: /task run <name>", "dim")
            return
        found = _task_find(rest)
        if found is None:
            hits = _task_find_substr(rest)
            if not hits:
                ctx.output(f"Task '{rest}' not found.", "error")
                return
            if len(hits) > 1:
                ctx.output(f"'{rest}' matches {len(hits)} tasks - be more specific:", "error")
                for _, data, index in hits:
                    ctx.output(f"  {data['tasks'][index]['name']}", "item")
                return
            _, matched_data, matched_index = hits[0]
            rest = matched_data["tasks"][matched_index]["name"]
            task_dict = matched_data["tasks"][matched_index]
            ctx.output(f"Matched: {rest}", "dim")
        else:
            _, found_data, found_index = found
            task_dict = found_data["tasks"][found_index]
        prompts = task_dict.get("prompts", [])
        if not prompts:
            ctx.output(f"Task '{rest}' has no prompts.", "error")
            return
        ctx.output(f"Running task '{rest}' ...", "info")

        def _run_task(_rest=rest, _prompts=list(prompts), _ctx=ctx) -> None:
            from KoreAgent.utils.runtime_logger import SessionLogger
            from KoreAgent.utils.runtime_logger import create_log_file_path
            from KoreAgent.utils.workspace_utils import get_logs_dir

            run_log_path = create_log_file_path(log_dir=get_logs_dir())
            try:
                with SessionLogger(run_log_path) as run_logger:
                    for prompt_entry in _prompts:
                        current = prompt_entry.get("prompt", "") if isinstance(prompt_entry, dict) else str(prompt_entry)
                        if current:
                            _ctx.output(f"[task] {_rest}: {current[:80]}", "dim")
                    results = run_prompt_batch(
                        _prompts,
                        session_id=f"task_{_rest}",
                        persist_path=None,
                        config=_ctx.config,
                        logger=run_logger,
                        quiet=True,
                        max_turns=10,
                    )
                    for item in results:
                        tps_str = f"{item['tps']:.1f}" if item["tps"] > 0 else "0"
                        _ctx.output(f"[task] {_rest}: done [{item['prompt_tokens']:,} tok, {tps_str} tok/s]", "dim")
                        _ctx.output(item["response"], "info")
                _ctx.output(f"Task '{_rest}' completed.", "success")
            except Exception as exc:
                _ctx.output(f"Error running task: {exc}", "error")

        from KoreAgent.scheduler.scheduler import task_queue

        queue_name = f"task_run_{rest}"
        if task_queue.enqueue(queue_name, "task_run", _run_task):
            ctx.output(f"Task '{rest}' queued.", "dim")
        else:
            ctx.output(f"Task '{rest}' is already queued or in progress.", "error")
        return

    ctx.output(f"Unknown sub-command '{sub}'. Use: enable, disable, add, delete, run", "error")


def register_task_slash_commands(registry: dict[str, Callable], descriptions: dict[str, str]) -> None:
    registry.update({"/tasks": _cmd_tasks, "/task": _cmd_task})
    descriptions.update(
        {
            "/tasks": "List all scheduled tasks with status, schedule, and first prompt",
            "/task": "enable|disable|add|delete|run <name> [schedule] [prompt]  Manage scheduled tasks; /task run <name> executes a task immediately",
        }
    )
