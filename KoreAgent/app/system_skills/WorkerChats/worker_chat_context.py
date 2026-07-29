"""Per-orchestration context made available to worker-chat tools."""
from __future__ import annotations

import threading


_worker_chat_tls: threading.local = threading.local()


def get_worker_chat_runtime_tls() -> threading.local:
    return _worker_chat_tls


def push_worker_chat_runtime(*, logger, config, conversation_entry=None) -> tuple[object, object, object]:
    previous = (
        getattr(_worker_chat_tls, "logger", None),
        getattr(_worker_chat_tls, "config", None),
        getattr(_worker_chat_tls, "conversation_entry", None),
    )
    _worker_chat_tls.logger             = logger
    _worker_chat_tls.config             = config
    _worker_chat_tls.conversation_entry = conversation_entry
    return previous


def pop_worker_chat_runtime(previous: tuple[object, object, object]) -> None:
    _worker_chat_tls.logger, _worker_chat_tls.config, _worker_chat_tls.conversation_entry = previous
