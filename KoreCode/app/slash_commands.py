from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Slash commands helpers for KoreCode/app.
# Provides the focused helpers and module-level behaviour grouped into this file.
# ====================================================================================================

from KoreCommon.SlashCommands import handle_slash_command
from KoreCommon.SlashCommands import write_help_lines

from .slash_command_context import KoreCodeSlashCommandContext
from .slash_command_handlers_workspace import register_workspace_slash_commands


def handle(text: str, ctx: KoreCodeSlashCommandContext) -> bool:
    return handle_slash_command(
        text,
        ctx,
        _REGISTRY,
        unknown_message="Unknown command '{command}'. Use /help.",
    )


def _cmd_help(arg: str, ctx: KoreCodeSlashCommandContext) -> None:
    write_help_lines(ctx, _DESCRIPTIONS)


def initialize(*, workspace_root_getter) -> None:
    register_workspace_slash_commands(
        _REGISTRY,
        _DESCRIPTIONS,
        workspace_root_getter = workspace_root_getter,
    )


_REGISTRY: dict[str, object] = {
    "/help": _cmd_help,
}

_DESCRIPTIONS: dict[str, str] = {
    "/help": "List available slash commands",
}
