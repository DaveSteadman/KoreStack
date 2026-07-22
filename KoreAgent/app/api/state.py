import json
import queue
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from scheduler.shared_state import SchedulerSharedState
from agent.orchestration.engine import OrchestratorConfig


_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_config: OrchestratorConfig | None = None
_last_run: dict[str, datetime | None] = {}
_enabled_tasks: list[dict] = []
_scheduler_state: SchedulerSharedState | None = None
_shutdown_event: threading.Event = threading.Event()

_run_event_queues: dict[str, queue.Queue] = {}
_run_queues_lock: threading.Lock = threading.Lock()

_latest_log_path: str | None = None

_pending_switch: dict | None = None
_pending_switch_lock: threading.Lock = threading.Lock()

_startup_state: dict[str, Any] = {
    "service_status": "starting",
    "started_at": datetime.now().isoformat(timespec="seconds"),
    "message": "HTTP server booting",
    "dependencies": {
        "llm": {"status": "pending", "detail": ""},
        "mcp": {"status": "pending", "detail": ""},
        "korechat": {"status": "pending", "detail": ""},
    },
}
_startup_state_lock: threading.Lock = threading.Lock()

_llm_direct_enabled: bool = False


def setup(
    config: OrchestratorConfig,
    enabled_tasks: list[dict],
    last_run: dict[str, datetime | None],
    shutdown_event: threading.Event,
    scheduler_state: SchedulerSharedState | None = None,
) -> None:
    global _config, _enabled_tasks, _last_run, _shutdown_event, _scheduler_state
    _config = config
    _enabled_tasks = enabled_tasks
    _last_run = last_run
    _scheduler_state = scheduler_state
    _shutdown_event = shutdown_event


def get_config() -> OrchestratorConfig | None:
    return _config


def get_scheduler_snapshot() -> tuple[list[dict], dict[str, datetime | None]]:
    if _scheduler_state is not None:
        return _scheduler_state.snapshot()
    return list(_enabled_tasks), dict(_last_run)


def get_shutdown_event() -> threading.Event:
    return _shutdown_event


def set_latest_log_path(path: str | Path | None) -> None:
    global _latest_log_path
    _latest_log_path = str(path) if path else None


def get_latest_log_path() -> str | None:
    return _latest_log_path


def set_startup_state_snapshot(state: dict[str, Any]) -> None:
    global _startup_state
    with _startup_state_lock:
        deps = state.get("dependencies")
        _startup_state = {
            **state,
            "dependencies": dict(deps) if isinstance(deps, dict) else {},
        }


def update_startup_state(
    *,
    service_status: str | None = None,
    message: str | None = None,
    dependencies: dict[str, dict[str, str]] | None = None,
) -> None:
    with _startup_state_lock:
        if service_status is not None:
            _startup_state["service_status"] = service_status
        if message is not None:
            _startup_state["message"] = message
        if dependencies:
            current = _startup_state.setdefault("dependencies", {})
            for name, payload in dependencies.items():
                if not isinstance(payload, dict):
                    continue
                current[name] = {
                    **(current.get(name) or {}),
                    **payload,
                }


def get_startup_state_snapshot() -> dict[str, Any]:
    with _startup_state_lock:
        deps = _startup_state.get("dependencies")
        return {
            **_startup_state,
            "dependencies": dict(deps) if isinstance(deps, dict) else {},
        }


def make_run_event_queue(run_id: str) -> queue.Queue:
    run_q: queue.Queue = queue.Queue(maxsize=2000)
    with _run_queues_lock:
        _run_event_queues[run_id] = run_q
    return run_q


def queue_run_event(run_q: queue.Queue, event: dict | None, priority: bool = False) -> None:
    try:
        run_q.put_nowait(event)
        return
    except queue.Full:
        if not priority:
            return

    while True:
        try:
            dropped = run_q.get_nowait()
            if dropped is None:
                try:
                    run_q.put_nowait(None)
                except queue.Full:
                    pass
                return
        except queue.Empty:
            return

        try:
            run_q.put_nowait(event)
            return
        except queue.Full:
            continue


def validate_session_id(session_id: str) -> None:
    if not _SESSION_ID_RE.match(session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id: use only letters, digits, hyphens and underscores")


def finish_run_event_queue(run_id: str) -> None:
    with _run_queues_lock:
        run_q = _run_event_queues.get(run_id)
    if run_q:
        queue_run_event(run_q, None, priority=True)


def get_run_event_queues() -> dict[str, queue.Queue]:
    return _run_event_queues


def get_run_queues_lock() -> threading.Lock:
    return _run_queues_lock


def get_llm_direct_enabled() -> bool:
    return _llm_direct_enabled


def set_llm_direct_enabled(enabled: bool) -> None:
    global _llm_direct_enabled
    _llm_direct_enabled = bool(enabled)


def pop_pending_switch() -> dict | None:
    global _pending_switch
    with _pending_switch_lock:
        pending = _pending_switch
        _pending_switch = None
    return pending


def set_pending_switch(value: dict) -> None:
    global _pending_switch
    with _pending_switch_lock:
        _pending_switch = dict(value)


def format_sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"
