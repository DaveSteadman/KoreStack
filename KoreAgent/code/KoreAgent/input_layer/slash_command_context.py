from dataclasses import dataclass
from typing import Callable


@dataclass
class SlashCommandContext:
    """All mutable state and I/O wiring needed by slash command handlers."""

    config: object
    output: Callable[[str, str], None]
    clear_history: Callable[[], None]
    session_context: object | None = None
    session_id: str | None = None
    switch_session: Callable[[str, str], None] | None = None
    rename_session: Callable[[str, str], None] | None = None
    delete_session_state: Callable[[str], None] | None = None
    # Optional in-memory compression fallback used when KoreConversation is unavailable.
    # Called with no arguments; returns a human-readable result string containing "compress".
    compress_history: Callable[[], str] | None = None
