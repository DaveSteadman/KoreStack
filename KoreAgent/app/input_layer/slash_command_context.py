# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# SlashCommandContext dataclass: shared mutable state threaded through all slash command handlers.
#
# Bundles config, output callback, history-clear callback, and optional session management
# callables so command handlers don't need direct imports from server.py.  This keeps
# the slash command modules decoupled and independently testable.
#
# Related modules:
#   - input_layer/slash_commands.py                    -- creates and passes context to handlers
#   - input_layer/routes_sessions.py                   -- constructs the context per-prompt
#   - input_layer/slash_command_handlers_models.py     -- reads config, calls output
#   - input_layer/slash_command_handlers_sessions.py   -- calls switch_session, delete_session_state
# ====================================================================================================
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
    # Optional in-memory compression fallback used when KoreChat is unavailable.
    # Called with no arguments; returns a human-readable result string containing "compress".
    compress_history: Callable[[], str] | None = None
