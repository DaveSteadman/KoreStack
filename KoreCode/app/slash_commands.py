from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Slash commands helpers for KoreCode/app.
# Provides the focused helpers and module-level behaviour grouped into this file.
# MARK: FUNCTIONS
# Function inventory:
# - handle: Handles this module's primary operation.
# - complete: Implements the complete operation for this module.
# - _cmd_help: Implements the  cmd help operation for this module.
# - initialize: Implements the initialize operation for this module.
# ====================================================================================================

from KoreCommon.SlashCommands import SlashCommandDefinition
from KoreCommon.SlashCommands import SlashCommandRegistry
from KoreCommon.SlashCommands import write_help_lines

from .slash_command_context import KoreCodeSlashCommandContext
from .slash_command_handlers_workspace import register_workspace_slash_commands


def handle(text: str, ctx: KoreCodeSlashCommandContext) -> bool:
    return _REGISTRY.dispatch(
        text,
        ctx,
        unknown_message="Unknown command '{command}'. Use /help.",
    )


def complete(text: str, ctx: KoreCodeSlashCommandContext, *, limit: int = 12) -> list[dict]:
    return _REGISTRY.complete(text, ctx, limit=limit)


def _cmd_help(arg: str, ctx: KoreCodeSlashCommandContext) -> None:
    write_help_lines(ctx, _REGISTRY)


def initialize(*, workspace_root_getter) -> None:
    _REGISTRY.clear()
    _REGISTRY.register(
        SlashCommandDefinition(
            name        = "/help",
            description = "List available slash commands",
            handler     = _cmd_help,
        )
    )
    register_workspace_slash_commands(
        _REGISTRY,
        workspace_root_getter = workspace_root_getter,
    )


_REGISTRY = SlashCommandRegistry()
