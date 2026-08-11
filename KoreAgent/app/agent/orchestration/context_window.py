"""Adaptive per-request context-window selection."""

from __future__ import annotations

import json
from collections.abc import Iterable


MIN_WORKING_CONTEXT_TOKENS = 8_192
DEFAULT_OUTPUT_RESERVE     = 4_096
_CHARS_PER_TOKEN           = 3.5


def estimate_payload_tokens(
    messages: Iterable[dict],
    tools: Iterable[dict] | None = None,
) -> int:
    """Return a conservative token estimate for messages and tool schemas."""
    message_chars = sum(len(str(message.get("content") or "")) for message in messages)
    tool_chars    = len(json.dumps(list(tools or ()), ensure_ascii=False, separators=(",", ":")))
    return max(1, int((message_chars + tool_chars) / _CHARS_PER_TOKEN))


def choose_context_window(
    maximum_tokens: int,
    messages: Iterable[dict],
    tools: Iterable[dict] | None = None,
    *,
    output_reserve: int = DEFAULT_OUTPUT_RESERVE,
) -> int:
    """Choose the smallest power-of-two context that fits this request.

    The configured maximum is never exceeded. Later turns grow through 8k, 16k,
    32k, and so on, as the assembled request requires more room.
    """
    maximum  = max(1, int(maximum_tokens or 0))
    required = estimate_payload_tokens(messages, tools) + max(1, int(output_reserve))
    selected = min(MIN_WORKING_CONTEXT_TOKENS, maximum)
    while selected < required and selected < maximum:
        selected *= 2
    return min(selected, maximum)
