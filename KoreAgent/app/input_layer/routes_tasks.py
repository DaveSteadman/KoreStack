# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FastAPI route group for task schedule and queue endpoints.
#
# Registered into the FastAPI app by register_task_routes(), called from server.py.
#
# Endpoints:
#   GET /tasks     -- enabled scheduled tasks with last-run and computed next-fire times
#   GET /queue     -- current task queue state (pending count and preview)
#   GET /timeline  -- 60-slot minute-resolution timeline centred on now
#
# Helper functions:
#   _next_fire(task, last, now)                   -- ISO next-fire for interval/daily tasks
#   _task_at_slot(slot, now, enabled_tasks, last) -- which task fires at a given slot
#
# Related modules:
#   - input_layer/server.py  -- registers this route group; provides _enabled_tasks / _last_run
#   - scheduler.py           -- task_queue, is_task_due
# ====================================================================================================
from datetime import datetime
from datetime import timedelta


def _next_fire(task: dict, last: datetime | None, now: datetime) -> str | None:
    schedule = task.get("schedule", {})
    kind = schedule.get("type", "")
    if kind == "interval":
        minutes = int(schedule.get("minutes", 60))
        next_fire = (last if last else now).replace(second=0, microsecond=0)
        while next_fire <= now:
            next_fire = next_fire + timedelta(minutes=minutes)
        return next_fire.isoformat(timespec="seconds")
    if kind == "daily":
        hour, minute = (int(part) for part in schedule.get("time", "00:00").split(":"))
        next_fire = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_fire <= now:
            next_fire = next_fire + timedelta(days=1)
        return next_fire.isoformat(timespec="seconds")
    return None


def _task_at_slot(slot_dt: datetime, now_min: datetime, enabled_tasks: list[dict], last_run: dict[str, datetime | None]) -> str | None:
    for task in enabled_tasks:
        name = task.get("name", "")
        schedule = task.get("schedule", {})
        schedule_type = schedule.get("type", "")
        if schedule_type == "daily":
            try:
                hour, minute = map(int, schedule.get("time", "00:00").split(":"))
            except ValueError:
                continue
            if slot_dt.replace(hour=hour, minute=minute, second=0, microsecond=0) == slot_dt:
                return name
        elif schedule_type == "interval":
            interval_minutes = int(schedule.get("minutes", 60))
            last = last_run.get(name)
            if last is None:
                if slot_dt == now_min:
                    return name
            else:
                next_fire = last.replace(second=0, microsecond=0) + timedelta(minutes=interval_minutes)
                if next_fire == slot_dt:
                    return name
    return None


def register_task_routes(
    app,
    *,
    get_enabled_tasks,
    get_last_run,
    is_task_due,
    task_queue,
    queue_preview_limit: int,
    get_pending_switch=None,
    get_scheduler_snapshot=None,
) -> None:

    def _snapshot() -> tuple[list[dict], dict[str, datetime | None]]:
        if get_scheduler_snapshot is not None:
            return get_scheduler_snapshot()
        return get_enabled_tasks(), get_last_run()

    @app.get("/tasks")
    def get_tasks():
        now = datetime.now()
        result = []
        enabled_tasks, last_run = _snapshot()
        for task in enabled_tasks:
            name = task.get("name", "")
            last = last_run.get(name)
            result.append(
                {
                    "name": name,
                    "description": task.get("description", ""),
                    "schedule": task.get("schedule", {}),
                    "last_run": last.isoformat(timespec="seconds") if last else None,
                    "next_fire": _next_fire(task, last, now),
                    "due_now": is_task_due(task, last, now),
                }
            )
        return {"tasks": result, "ts": now.isoformat(timespec="seconds")}

    @app.get("/queue")
    def get_queue():
        queue_state = task_queue.get_state(pending_limit=queue_preview_limit)
        pending_switch = get_pending_switch() if get_pending_switch else None
        return {
            "queued_prompt_count": queue_state.get("queued_prompt_count", 0),
            "pending_count": queue_state.get("pending_count", 0),
            "queue_count": queue_state.get("queue_count", 0),
            "active": queue_state.get("active"),
            "next_prompts": queue_state.get("next_prompts", []),
            "next_prompts_limit": queue_state.get("next_prompts_limit", queue_preview_limit),
            "active_age_s": queue_state.get("active_age_s"),
            "active_timeout_s": queue_state.get("active_timeout_s"),
            "active_timeout_exceeded": queue_state.get("active_timeout_exceeded", False),
            "active_cancel_requested": queue_state.get("active_cancel_requested", False),
            "oldest_pending_age_s": queue_state.get("oldest_pending_age_s"),
            "queue_lag_s": queue_state.get("queue_lag_s"),
            "stalled": queue_state.get("stalled", False),
            "updated_at": queue_state.get("updated_at"),
            "pending_switch": pending_switch,
        }

    @app.get("/timeline")
    def get_timeline(minutes_before: int = 40, minutes_after: int = 40):
        now = datetime.now()
        now_min = now.replace(second=0, microsecond=0)
        enabled_tasks, last_run = _snapshot()
        slots = []
        for offset in range(-minutes_before, minutes_after + 1):
            slot_dt = now_min + timedelta(minutes=offset)
            task_name = _task_at_slot(slot_dt, now_min, enabled_tasks, last_run)
            last = None
            if task_name:
                lr = last_run.get(task_name)
                last = lr.isoformat(timespec="seconds") if lr else None
            slots.append({"offset": offset, "hhmm": slot_dt.strftime("%H:%M"), "is_now": offset == 0, "task_name": task_name, "last_run": last})

        q_state = task_queue.get_state()
        active_now = q_state.get("active", {}) or {}
        return {
            "slots": slots,
            "active_task": active_now.get("name"),
            "queue_lag_s": q_state.get("queue_lag_s"),
            "active_age_s": q_state.get("active_age_s"),
            "stalled": q_state.get("stalled", False),
            "ts": now.isoformat(timespec="seconds"),
        }
