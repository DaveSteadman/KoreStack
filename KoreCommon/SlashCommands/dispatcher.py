from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Dispatcher helpers for KoreCommon/SlashCommands.
# Provides the focused helpers and module-level behaviour grouped into this file.
# ====================================================================================================

from typing import Callable

from .context import SlashCommandContext


def handle_slash_command(
    text: str,
    ctx: SlashCommandContext,
    registry: dict[str, Callable[[str, SlashCommandContext], None]],
    *,
    unknown_message: str = "Unknown command. Type /help for available commands.",
) -> bool:
    stripped = str(text or "").strip()
    if not stripped.startswith("/"):
        return False

    parts   = stripped.split(None, 1)
    command = parts[0].lower()
    arg     = parts[1].strip() if len(parts) > 1 else ""
    handler = registry.get(command)
    if handler is None:
        ctx.output(unknown_message.replace("{command}", command), "dim")
        return True

    handler(arg, ctx)
    return True


def write_help_lines(
    ctx: SlashCommandContext,
    descriptions: dict[str, str],
    *,
    heading: str = "Available slash commands:",
) -> None:
    ctx.output(heading, "info")
    for name, description in sorted(descriptions.items()):
        ctx.output(f"  {name:<16} {description}", "item")
