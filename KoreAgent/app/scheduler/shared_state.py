from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Shared state helpers for KoreAgent/app/scheduler.
# Provides the focused helpers and module-level behaviour grouped into this file.
# ====================================================================================================

import threading
from datetime import datetime


class SchedulerSharedState:
    """Thread-safe shared state for scheduler task metadata.

    Owns two related structures used by both the scheduler loop and API readers:
    - enabled_tasks: current list of enabled task dicts
    - last_run: map of task name -> last run datetime (or None)
    """

    def __init__(self, enabled_tasks: list[dict] | None = None, last_run: dict[str, datetime | None] | None = None) -> None:
        self._lock = threading.Lock()
        self._enabled_tasks: list[dict] = [dict(t) for t in (enabled_tasks or [])]
        self._last_run: dict[str, datetime | None] = dict(last_run or {})

    def snapshot(self) -> tuple[list[dict], dict[str, datetime | None]]:
        """Return copies suitable for lock-free reads by callers."""
        with self._lock:
            return [dict(t) for t in self._enabled_tasks], dict(self._last_run)

    def get_enabled_tasks(self) -> list[dict]:
        with self._lock:
            return [dict(t) for t in self._enabled_tasks]

    def get_last_run(self) -> dict[str, datetime | None]:
        with self._lock:
            return dict(self._last_run)

    def replace_enabled_tasks(self, enabled_tasks: list[dict]) -> None:
        """Replace enabled task definitions and prune orphaned last-run keys."""
        with self._lock:
            self._enabled_tasks = [dict(t) for t in enabled_tasks]
            names = {str(t.get("name", "")) for t in self._enabled_tasks if t.get("name")}
            self._last_run = {k: v for k, v in self._last_run.items() if k in names}

    def ensure_last_run(self, task_name: str, value: datetime | None) -> None:
        with self._lock:
            if task_name not in self._last_run:
                self._last_run[task_name] = value

    def set_last_run(self, task_name: str, value: datetime | None) -> None:
        with self._lock:
            self._last_run[task_name] = value
