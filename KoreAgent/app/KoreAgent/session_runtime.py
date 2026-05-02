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
