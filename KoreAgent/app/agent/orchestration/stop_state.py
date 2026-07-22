"""Shared stop-state management for orchestration runs."""

import threading

_stop_event: threading.Event = threading.Event()
_stop_reason: str = ""
_stop_reason_lock: threading.Lock = threading.Lock()
_active_stop_events: dict[str, threading.Event] = {}
_active_stop_lock: threading.Lock = threading.Lock()


def request_stop(reason: str = "external") -> None:
    global _stop_reason
    with _stop_reason_lock:
        _stop_reason = str(reason or "external").strip() or "external"
    _stop_event.set()
    with _active_stop_lock:
        for event in _active_stop_events.values():
            event.set()


def is_stop_requested() -> bool:
    return _stop_event.is_set()


def get_stop_reason() -> str:
    with _stop_reason_lock:
        return _stop_reason


def clear_stop() -> None:
    global _stop_reason
    _stop_event.clear()
    with _stop_reason_lock:
        _stop_reason = ""


def register_run_stop_event(run_id: str, event: threading.Event) -> None:
    with _active_stop_lock:
        _active_stop_events[run_id] = event


def unregister_run_stop_event(run_id: str) -> None:
    with _active_stop_lock:
        _active_stop_events.pop(run_id, None)


__all__ = [
    "clear_stop",
    "get_stop_reason",
    "is_stop_requested",
    "register_run_stop_event",
    "request_stop",
    "unregister_run_stop_event",
]
