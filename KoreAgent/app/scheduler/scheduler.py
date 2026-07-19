# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Lightweight schedule management utilities for KoreAgent.
#
# Provides:
#   task_queue   -- module-level TaskQueue that serialises all LLM calls sequentially.
#                  Enqueue a callable with task_queue.enqueue(name, kind, fn); the worker
#                  thread executes items one at a time, preventing concurrent LLM calls.
#                  Tasks with a name already queued or active are silently deduplicated.
#   llm_lock     -- the raw threading.Lock exposed by task_queue; held for the duration of
#                  each task.  Back-compat alias for task_queue.run_lock.
#   load_schedules_dir -- scans a directory for *.json schedule files and merges all tasks lists.
#   is_task_due        -- pure function; tests whether a task should fire given current time.
#
# Schedule directory layout:
#   datacontrol/schedules/*.json   each file must have a top-level "tasks" list.
#   Files are loaded in sorted filename order; tasks from all files are merged into one flat list.
#
# Schedule types:
#   interval   fires every N minutes  {"type": "interval", "minutes": N}
#   daily      fires once per day at a fixed wall-clock time  {"type": "daily", "time": "HH:MM"}
#
# Queue state is persisted to datacontrol/koreagent/task_queue.json on every enqueue/dequeue so the
# web UI and other tooling can observe pending and active tasks.
#
# The scheduler loop lives in input_layer/server_startup.py.
#
# Related modules:
#   - input_layer/server_startup.py -- uses task_queue, load_schedules_dir, is_task_due
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import json
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path


_DEFAULT_TIMEOUT_BY_KIND: dict[str, int] = {
    "api_chat": 15 * 60,
    "koreconv": 15 * 60,
    "scheduled": 30 * 60,
    "task_run": 30 * 60,
}
_TIMEOUT_POLL_SECS = 0.5
_STALL_GRACE_SECS = 30


# ====================================================================================================
# MARK: TASK QUEUE
# ====================================================================================================
# task_queue is the module-level singleton that serialises all LLM calls.
#
# Enqueue a task with task_queue.enqueue(name, kind, fn) -- returns False if a task with the
# same name is already queued or running (deduplication).  The queue worker executes items
# sequentially on its own daemon thread, holding run_lock for the duration of each item.
#
# llm_lock exposes the raw threading.Lock so code that needs to hold the serialisation
# token outside the queue (e.g. /test and /task run commands) can do so directly.
class TaskQueue:
    """Sequential LLM-call queue with deduplication and state-file visibility.

    One item executes at a time.  Enqueueing a name that is already queued or active
    is a no-op (returns False) to prevent backlog from repeated schedule triggers.
    """

    def __init__(self) -> None:
        self._run_lock   = threading.Lock()    # held while a task executes
        self._state_lock = threading.Lock()    # protects _deque / _queued_names / _active
        self._has_work   = threading.Event()
        self._shutdown   = threading.Event()
        self._deque:         deque      = deque()
        self._queued_names:  set[str]   = set()
        self._active:        dict | None = None
        self._worker         = threading.Thread(
            target = self._worker_loop,
            daemon = True,
            name   = "task-queue-worker",
        )
        self._worker.start()
        self._write_state()

    # ----------------------------------------------------------------------------------------------------
    @property
    def run_lock(self) -> threading.Lock:
        """The raw serialisation lock.  Acquire this to pause the queue (e.g. /test commands)."""
        return self._run_lock

    # ----------------------------------------------------------------------------------------------------
    def enqueue(
        self,
        name: str,
        kind: str,
        fn,
        label: str = "",
        timeout_seconds: int | None = None,
        cancel_fn=None,
    ) -> bool:
        """Append *fn* to the execution queue.

        Returns False without enqueueing if *name* already appears in the queue or is
        currently active (deduplication guard for repeated schedule triggers).
        """
        with self._state_lock:
            if name in self._queued_names or (self._active and self._active["name"] == name):
                return False
            self._deque.append({
                "name":      name,
                "kind":      kind,
                "label":     label,
                "queued_at": datetime.now().isoformat(timespec="seconds"),
                "queued_ts": time.time(),
                "fn":        fn,
                "timeout_seconds": timeout_seconds,
                "cancel_fn": cancel_fn,
            })
            self._queued_names.add(name)
        self._has_work.set()
        self._write_state()
        return True

    # ----------------------------------------------------------------------------------------------------
    def get_state(self, pending_limit: int | None = None) -> dict:
        """Return a JSON-serialisable snapshot of current queue state."""
        now_ts = time.time()
        with self._state_lock:
            active        = dict(self._active) if self._active else None
            pending_count = len(self._deque)   # authoritative count before any preview truncation
            queue_count   = pending_count + (1 if active else 0)
            pending_items = list(self._deque)
            if pending_limit is not None:
                visible_pending_limit = max(0, pending_limit - (1 if active else 0))
                pending_items = pending_items[:visible_pending_limit]
            pending = [
                {
                    "name": item["name"],
                    "kind": item["kind"],
                    "label": item.get("label", ""),
                    "queued_at": item["queued_at"],
                    "timeout_seconds": item.get("timeout_seconds"),
                }
                for item in pending_items
            ]
            next_prompts: list[dict] = []
            if active:
                next_prompts.append({
                    "name":       active["name"],
                    "kind":       active["kind"],
                    "label":      active.get("label", ""),
                    "started_at": active.get("started_at"),
                    "timeout_seconds": active.get("timeout_seconds"),
                    "cancel_requested": bool(active.get("cancel_requested", False)),
                    "cancel_reason": active.get("cancel_reason"),
                    "timed_out": bool(active.get("timed_out", False)),
                    "state":      "active",
                })
            next_prompts.extend([
                {
                    "name":      item["name"],
                    "kind":      item["kind"],
                    "label":     item.get("label", ""),
                    "queued_at": item["queued_at"],
                    "timeout_seconds": item.get("timeout_seconds"),
                    "state":     "pending",
                }
                for item in pending_items
            ])

            active_age_s = None
            active_timeout_s = None
            active_timeout_exceeded = False
            active_cancel_requested = False
            if active:
                started_ts = active.get("started_ts")
                timeout_s = active.get("timeout_seconds")
                if isinstance(started_ts, (int, float)):
                    active_age_s = max(0.0, now_ts - float(started_ts))
                if isinstance(timeout_s, (int, float)) and timeout_s > 0:
                    active_timeout_s = int(timeout_s)
                    if active_age_s is not None and active_age_s >= float(timeout_s):
                        active_timeout_exceeded = True
                active_cancel_requested = bool(active.get("cancel_requested", False))

            pending_ages = [
                max(0.0, now_ts - float(item.get("queued_ts")))
                for item in self._deque
                if isinstance(item.get("queued_ts"), (int, float))
            ]
            oldest_pending_age_s = max(pending_ages) if pending_ages else None
            queue_lag_s = active_age_s if active_age_s is not None else oldest_pending_age_s
            stalled = bool(
                active
                and (
                    (active_timeout_exceeded and (active_age_s or 0.0) >= (float(active_timeout_s or 0) + _STALL_GRACE_SECS))
                    or (active_cancel_requested and (active_age_s or 0.0) >= _STALL_GRACE_SECS)
                )
            )

        return {
            "active":                active,
            "pending":               pending,
            "pending_count":         pending_count,
            "queue_count":           queue_count,
            "queued_prompt_count":   queue_count,
            "next_prompts":          next_prompts,
            "next_prompts_limit":    pending_limit,
            "pending_preview_limit": pending_limit,
            "active_age_s":          int(active_age_s) if active_age_s is not None else None,
            "active_timeout_s":      active_timeout_s,
            "active_timeout_exceeded": active_timeout_exceeded,
            "active_cancel_requested": active_cancel_requested,
            "oldest_pending_age_s":  int(oldest_pending_age_s) if oldest_pending_age_s is not None else None,
            "queue_lag_s":           int(queue_lag_s) if queue_lag_s is not None else None,
            "stalled":               stalled,
            "updated_at":            datetime.now().isoformat(timespec="seconds"),
        }

    # ----------------------------------------------------------------------------------------------------
    def clear_pending(self) -> list[str]:
        """Remove all not-yet-started items from the queue.

        Returns the names of every cancelled item so callers can close their associated
        event queues.  The currently active item is not affected.
        """
        with self._state_lock:
            cancelled = [item["name"] for item in self._deque]
            self._deque.clear()
            self._queued_names.clear()
        self._write_state()
        return cancelled

    # ----------------------------------------------------------------------------------------------------
  
    def stop(self) -> None:
        """Request worker shutdown.  The in-flight task runs to completion."""
        self._shutdown.set()
        self._has_work.set()
        self._delete_state()

    # ----------------------------------------------------------------------------------------------------
  
    def _delete_state(self) -> None:
        try:
            from utils.workspace_utils import get_controldata_dir
            path = get_controldata_dir() / "koreagent" / "task_queue.json"
            path.unlink(missing_ok=True)
        except Exception as exc:
            try:
                from llm_client import log_to_session
                log_to_session(f"[scheduler] Could not delete task queue state file: {exc}")
            except Exception:
                # State-file cleanup is observability only.  Never let a logging
                # failure turn queue shutdown into a secondary exception path.
                pass

    # ----------------------------------------------------------------------------------------------------
  
    def _write_state(self) -> None:
        try:
            from utils.workspace_utils import get_controldata_dir
            path = get_controldata_dir() / "koreagent" / "task_queue.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self.get_state(), indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            try:
                from llm_client import log_to_session
                log_to_session(f"[scheduler] Could not write task queue state file: {exc}")
            except Exception:
                # Queue execution must continue even if the status mirror cannot be
                # written or logged; the in-memory queue is authoritative.
                pass

    # ----------------------------------------------------------------------------------------------------
  
    def _worker_loop(self) -> None:
        while not self._shutdown.is_set():
            self._has_work.wait(timeout=1.0)
            self._has_work.clear()

            while not self._shutdown.is_set():
                # Bail if nothing to do.
                with self._state_lock:
                    if not self._deque:
                        break

                # Block here while /test or /task run holds the lock; then re-check for shutdown.
                with self._run_lock:
                    if self._shutdown.is_set():
                        return

                    # Dequeue under state lock; re-check in case the queue was cleared while waiting.
                    with self._state_lock:
                        if not self._deque:
                            break
                        item             = self._deque.popleft()
                        self._queued_names.discard(item["name"])
                        timeout_s = item.get("timeout_seconds")
                        if timeout_s is None:
                            timeout_s = _DEFAULT_TIMEOUT_BY_KIND.get(str(item.get("kind", "")))
                        now_ts = time.time()
                        self._active     = {
                            "name":       item["name"],
                            "kind":       item["kind"],
                            "label":      item.get("label", ""),
                            "started_at": datetime.now().isoformat(timespec="seconds"),
                            "started_ts": now_ts,
                            "timeout_seconds": timeout_s,
                            "cancel_requested": False,
                            "cancel_reason": None,
                            "cancel_requested_at": None,
                            "timed_out": False,
                        }

                    self._write_state()
                    try:
                        # Reset per-task search session state so that DDG success flags from a
                        # previous task do not carry over and trigger spurious retry delays.
                        try:
                            from KoreLiveWeb.app.web_search import reset_search_session
                            reset_search_session()
                        except Exception:
                            # Search-session reset is a hygiene step between tasks, not
                            # part of task correctness, so failures stay non-fatal.
                            pass

                        cancel_requested = False
                        task_done = threading.Event()
                        task_error: dict[str, Exception] = {}

                        def _run_item() -> None:
                            try:
                                item["fn"]()
                            except Exception as exc:
                                task_error["exc"] = exc
                            finally:
                                task_done.set()

                        runner = threading.Thread(target=_run_item, daemon=True, name=f"task-runner-{item['name']}")
                        runner.start()

                        while not task_done.wait(_TIMEOUT_POLL_SECS):
                            active = self._active or {}
                            timeout_s = active.get("timeout_seconds")
                            started_ts = active.get("started_ts")
                            elapsed_s = (time.time() - float(started_ts)) if isinstance(started_ts, (int, float)) else None

                            if self._shutdown.is_set() and not cancel_requested:
                                self._request_cancel(item, reason="shutdown")
                                cancel_requested = True

                            if (
                                not cancel_requested
                                and isinstance(timeout_s, (int, float))
                                and timeout_s > 0
                                and elapsed_s is not None
                                and elapsed_s >= float(timeout_s)
                            ):
                                self._request_cancel(item, reason="timeout")
                                cancel_requested = True

                        if "exc" in task_error:
                            raise task_error["exc"]
                    except Exception as exc:
                        # Log the failure so it is visible in the log file and
                        # the /logs/stream SSE endpoint.
                        try:
                            import traceback
                            from llm_client import log_to_session
                            log_to_session(
                                f"[scheduler] Task '{item['name']}' raised an exception:\n"
                                + traceback.format_exc()
                            )
                        except Exception:
                            # Avoid recursive failure if session logging itself is broken.
                            pass
                    with self._state_lock:
                        self._active = None
                    self._write_state()

    def _request_cancel(self, item: dict, reason: str) -> None:
        with self._state_lock:
            if self._active is not None:
                self._active["cancel_requested"] = True
                self._active["cancel_reason"] = reason
                self._active["cancel_requested_at"] = datetime.now().isoformat(timespec="seconds")
                if reason == "timeout":
                    self._active["timed_out"] = True
        self._write_state()

        cancel_fn = item.get("cancel_fn")
        if callable(cancel_fn):
            try:
                cancel_fn(reason)
                return
            except Exception:
                # Fall back to the generic orchestration stop path if the task-
                # specific cancellation hook is absent or fails.
                pass

        # Default cancellation path for orchestration-backed work.
        try:
            from orchestration import request_stop
            request_stop(reason)
        except Exception:
            # Cancellation is best-effort here; callers observe timeout/shutdown state
            # via _active even if the downstream stop signal cannot be delivered.
            pass


# ====================================================================================================
# MARK: MODULE INSTANCES
# ====================================================================================================
# task_queue is the shared singleton used by all scheduled-task and server code.
# llm_lock is a back-compat alias for task_queue.run_lock.
task_queue: TaskQueue    = TaskQueue()
llm_lock:   threading.Lock = task_queue.run_lock


# ====================================================================================================
# MARK: SCHEDULE LOADING
# ====================================================================================================
def load_schedules_dir(schedules_dir: Path) -> list[dict]:
    """Scan schedules_dir for *.json files and return a merged flat list of all task dicts.

    Files are processed in sorted filename order.  Each file must contain a top-level
    'tasks' list.  Files with invalid JSON or missing the key are skipped with a warning
    printed to stderr so one bad file does not prevent the others from loading.
    """
    if not schedules_dir.exists():
        raise FileNotFoundError(f"Schedules directory not found: {schedules_dir}")

    tasks: list[dict] = []
    json_files = sorted(schedules_dir.glob("*.json"))

    if not json_files:
        print(f"[scheduler] Warning: no *.json files found in {schedules_dir}", file=sys.stderr)
        return tasks

    for json_path in json_files:
        raw = json_path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"[scheduler] Skipping {json_path.name}: invalid JSON ({exc})", file=sys.stderr)
            continue

        file_tasks = data.get("tasks")
        if not isinstance(file_tasks, list):
            print(f"[scheduler] Skipping {json_path.name}: missing top-level 'tasks' list.", file=sys.stderr)
            continue

        tasks.extend(file_tasks)

    return tasks


# ====================================================================================================
# MARK: SCHEDULE EVALUATION
# ====================================================================================================
def initial_last_run(task: dict, reference: datetime) -> "datetime | None":
    """Return the value to store in last_run when a task is first registered (startup or hot-add).

    interval  -- return reference so the first fire occurs after a full interval, not immediately.
    daily     -- return reference if the scheduled wall-clock time has already passed today
                 (preventing an immediate spurious fire on startup); return None if it hasn't
                 been reached yet so the task still fires at its proper time later today.
    """
    schedule = task.get("schedule", {})
    stype    = schedule.get("type", "")

    if stype == "interval":
        return reference

    if stype == "daily":
        target_str = schedule.get("time", "00:00")
        try:
            target_time = datetime.strptime(target_str, "%H:%M").time()
        except ValueError:
            return reference  # malformed - treat as already fired today
        if reference.time() >= target_time:
            return reference  # time has passed today - defer to tomorrow
        return None  # time not yet reached - will fire naturally later today

    return None


def is_task_due(task: dict, last_run: datetime | None, now: datetime) -> bool:
    """Return True if the task should fire given the current time and its last-run timestamp.

    interval  -- fires immediately on first invocation (last_run is None), then every N minutes.
    daily     -- fires once per calendar day at the configured wall-clock time.
    """
    schedule      = task.get("schedule", {})
    schedule_type = schedule.get("type", "")

    if schedule_type == "interval":
        if last_run is None:
            return True
        elapsed_minutes = (now - last_run).total_seconds() / 60.0
        return elapsed_minutes >= schedule.get("minutes", 60)

    if schedule_type == "daily":
        target_str = schedule.get("time", "00:00")
        try:
            target_time = datetime.strptime(target_str, "%H:%M").time()
        except ValueError:
            return False  # malformed time string - never fire
        if now.time() < target_time:
            return False
        if last_run is None:
            return True  # time reached and never run - fire now
        return last_run.date() < now.date()

    return False
