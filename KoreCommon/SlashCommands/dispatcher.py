from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Dispatcher helpers for KoreCommon/SlashCommands.
# Provides the focused helpers and module-level behaviour grouped into this file.
# ====================================================================================================

from .context import SlashCommandContext
from .models import SlashCommandDefinition
from .registry import SlashCommandRegistry


def handle_slash_command(
    text: str,
    ctx: SlashCommandContext,
    registry: dict[str, object] | SlashCommandRegistry,
    *,
    unknown_message: str = "Unknown command. Type /help for available commands.",
) -> bool:
    if isinstance(registry, SlashCommandRegistry):
        return registry.dispatch(text, ctx, unknown_message=unknown_message)

    temp = SlashCommandRegistry()
    for name, handler in registry.items():
        temp.register(
            SlashCommandDefinition(
                name        = str(name),
                description = "",
                handler     = handler,
            )
        )
    return temp.dispatch(text, ctx, unknown_message=unknown_message)


def write_help_lines(
    ctx: SlashCommandContext,
    descriptions: dict[str, str] | SlashCommandRegistry,
    *,
    heading: str = "Available slash commands:",
) -> None:
    ctx.output(heading, "info")
    if isinstance(descriptions, SlashCommandRegistry):
        for definition in descriptions.definitions():
            description = definition.usage or definition.description
            ctx.output(f"  {definition.name:<16} {description}", "item")
        return

    for name, description in sorted(descriptions.items()):
        ctx.output(f"  {name:<16} {description}", "item")
