# ====================================================================================================
# Worker-chat tool surface.
# ====================================================================================================
"""Spawn isolated configured chats and read their explicit durable result."""

import importlib
from pathlib import Path

from system_skills.WorkerChats import worker_chat_runtime as _runtime_module


_RUNTIME_PATH          = Path(_runtime_module.__file__ or "")
_RUNTIME_MTIME_NS: int = -1


def _runtime():
    """Return the current worker runtime, reloading it after a source edit."""
    global _RUNTIME_MTIME_NS
    current_mtime_ns = _RUNTIME_PATH.stat().st_mtime_ns
    if current_mtime_ns != _RUNTIME_MTIME_NS:
        importlib.reload(_runtime_module)
        _RUNTIME_MTIME_NS = current_mtime_ns
    return _runtime_module


def chat_spawn(*, prompt: str, result_target: str = "", result_format: str = "", max_iterations: int = 3, inputs: dict | None = None) -> dict:
    """Run an isolated worker prompt and return its durable result to the caller."""
    return _runtime().chat_spawn(
        prompt        = prompt,
        result_target = result_target,
        result_format = result_format,
        max_iterations= max_iterations,
        inputs        = inputs,
    )


def chat_status(chat_id: str) -> dict:
    """Return the durable status of a worker chat."""
    return _runtime().chat_status(chat_id)


def chat_result(chat_id: str) -> dict:
    """Return the durable result of a worker chat."""
    return _runtime().chat_result(chat_id)


def chat_cancel(chat_id: str) -> dict:
    """Cancel a queued or running worker chat."""
    return _runtime().chat_cancel(chat_id)


__all__ = ["chat_spawn", "chat_status", "chat_result", "chat_cancel"]
