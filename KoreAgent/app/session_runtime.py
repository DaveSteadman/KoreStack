# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Active session ID context variable and binding helper.
#
# Provides a ContextVar that tracks the current session_id per-thread/async-task so that
# modules deep in the call stack (e.g. scratchpad.py) can identify the active session
# without explicit parameter threading.
#
# Public API:
#   get_active_session_id()    -- returns current session_id (default: "default")
#   set_active_session_id(id)  -- sets it for the current context
#   bind_session(session_id)   -- context manager that sets and restores the session
#
# Related modules:
#   - scratchpad.py      -- reads active session_id for per-session key namespacing
#   - orchestration.py   -- sets session_id at the start of each orchestration run
#   - delegate_runner.py -- uses get_active_session_id for scratchpad isolation
# ====================================================================================================
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


_ACTIVE_SESSION_ID: ContextVar[str] = ContextVar("active_session_id", default="default")


def get_active_session_id() -> str:
    return _ACTIVE_SESSION_ID.get()


def set_active_session_id(session_id: str) -> None:
    cleaned = str(session_id or "").strip()
    _ACTIVE_SESSION_ID.set(cleaned or "default")


@dataclass
class SessionBinding:
    session_id: str


@contextmanager
def bind_session(session_id: str):
    cleaned = str(session_id or "").strip() or "default"
    token = _ACTIVE_SESSION_ID.set(cleaned)
    try:
        yield SessionBinding(session_id=cleaned)
    finally:
        _ACTIVE_SESSION_ID.reset(token)
