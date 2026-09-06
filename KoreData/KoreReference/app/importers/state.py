# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Thread-safe import state tracker and worker launcher for KoreReference bulk imports.
#
# Maintains a shared state dict (running, done, total, limit, errors, mode, seed)
# protected by threading.Lock so the progress can be polled by the API while a
# dedicated import thread is running.  The admission lock is held for that
# thread's whole lifetime, preventing overlapping crawls.
#
# Related modules:
#   - app/importers/kiwix.py  -- reads/writes import state during crawl
#   - app/server.py           -- exposes import state via GET /api/import/status
# MARK: FUNCTIONS
# Function inventory: this module exposes package or declarative configuration only.
# ====================================================================================================
import threading
from collections.abc import Callable
from typing import Any

import_lock: threading.Lock = threading.Lock()
state_lock: threading.Lock = threading.Lock()
import_stop_event: threading.Event = threading.Event()
import_state: dict = {
    "running": False, "done": 0, "total": 0, "limit": 0, "errors": 0,
    "last_error": None, "mode": None, "seed": None,
    "delay_seconds": 0.0,
    "redirects_stored": 0, "last_redirect": None,
    "worker_thread_name": None,
}


def start_import_worker(
    target: Callable[..., None],
    args: tuple[Any, ...],
    name: str,
    initial_state: dict[str, Any],
) -> bool:
    """Start one independently managed importer and retain its admission lock.

    The lock is deliberately held for the worker's complete lifetime.  It is
    not a database lock: SQLite WAL readers remain free to serve committed data.
    """
    if not import_lock.acquire(blocking=False):
        return False

    import_stop_event.clear()
    with state_lock:
        import_state.update(initial_state)
        import_state["running"]            = True
        import_state["worker_thread_name"] = name

    def _run() -> None:
        try:
            target(*args)
        except Exception as exc:
            with state_lock:
                import_state["errors"]     = int(import_state.get("errors") or 0) + 1
                import_state["last_error"] = f"{type(exc).__name__}: {exc}"
        finally:
            with state_lock:
                import_state["running"]            = False
                import_state["worker_thread_name"] = None
            import_lock.release()

    worker = threading.Thread(target=_run, daemon=True, name=name)
    try:
        worker.start()
    except Exception:
        with state_lock:
            import_state["running"]            = False
            import_state["worker_thread_name"] = None
        import_lock.release()
        raise
    return True
