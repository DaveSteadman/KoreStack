from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Slash command handlers workspace helpers for KoreCode/app.
# Provides the focused helpers and module-level behaviour grouped into this file.
# ====================================================================================================

from typing import Callable

from KoreCommon.SlashCommands import SlashCommandDefinition
from KoreCommon.SlashCommands import SlashCommandRegistry

from .slash_command_context import KoreCodeSlashCommandContext
from .workspace_artifacts import rebuild_workspace_artifacts


_MODE_NAMES = {"chat", "continue", "explain", "bughunt", "refactor", "tests"}


def _cmd_clear(arg: str, ctx: KoreCodeSlashCommandContext) -> None:
    ctx.add_action("clear_thread")


def _cmd_retry(arg: str, ctx: KoreCodeSlashCommandContext) -> None:
    if not ctx.has_last_user_message:
        ctx.output("No previous user prompt to retry in this conversation.", "error")
        return
    ctx.add_action("retry_last_user_message")


def _cmd_workspace(arg: str, ctx: KoreCodeSlashCommandContext) -> None:
    sub = arg.strip().lower()
    if sub == "on":
        ctx.add_action("set_workspace_context", enabled=True)
        result = rebuild_workspace_artifacts(_workspace_root())
        ctx.output("Workspace context enabled.", "success")
        ctx.output(f"Workspace menu refreshed: {result['menu_file_name']} ({result['file_count']} files indexed).", "info")
        return
    if sub == "off":
        ctx.add_action("set_workspace_context", enabled=False)
        ctx.output("Workspace context disabled.", "success")
        return
    if sub == "regen":
        result = rebuild_workspace_artifacts(_workspace_root())
        ctx.output(f"Workspace menu refreshed: {result['menu_file_name']} ({result['file_count']} files indexed).", "success")
        return
    ctx.output("Use /workspace on, /workspace off, or /workspace regen", "dim")


def _cmd_mode(arg: str, ctx: KoreCodeSlashCommandContext) -> None:
    mode = arg.strip().lower()
    if mode not in _MODE_NAMES:
        ctx.output("Unknown mode. Use /mode chat|continue|explain|bughunt|refactor|tests", "error")
        return
    _set_mode_action(mode, ctx)


def _cmd_mode_alias(arg: str, ctx: KoreCodeSlashCommandContext, mode: str) -> None:
    _set_mode_action(mode, ctx)


def _set_mode_action(mode: str, ctx: KoreCodeSlashCommandContext) -> None:
    ctx.add_action(
        "set_mode",
        mode         = mode,
        run_continue = mode == "continue",
    )


def _complete_workspace(arg: str, ctx: KoreCodeSlashCommandContext) -> list[dict]:
    options = ("on", "off", "regen")
    prefix  = arg.strip().lower()
    return [
        {
            "value":       value,
            "label":       value,
            "description": f"/workspace {value}",
        }
        for value in options
        if not prefix or value.startswith(prefix)
    ]


def _complete_mode(arg: str, ctx: KoreCodeSlashCommandContext) -> list[dict]:
    prefix = arg.strip().lower()
    return [
        {
            "value":       value,
            "label":       value,
            "description": f"Switch to {value} mode",
        }
        for value in sorted(_MODE_NAMES)
        if not prefix or value.startswith(prefix)
    ]


def register_workspace_slash_commands(
    registry: SlashCommandRegistry,
    *,
    workspace_root_getter: Callable[[], object],
) -> None:
    global _workspace_root
    _workspace_root = workspace_root_getter

    registry.register(
        SlashCommandDefinition(
            name        = "/clear",
            description = "Clear the current conversation",
            handler     = _cmd_clear,
        )
    )
    registry.register(
        SlashCommandDefinition(
            name        = "/retry",
            description = "Retry the last user prompt in the current conversation",
            handler     = _cmd_retry,
        )
    )
    registry.register(
        SlashCommandDefinition(
            name        = "/workspace",
            description = "Enable, disable, or rebuild workspace context",
            usage       = "<on|off|regen>  Enable, disable, or rebuild workspace context",
            handler     = _cmd_workspace,
            completer   = _complete_workspace,
        )
    )
    registry.register(
        SlashCommandDefinition(
            name        = "/mode",
            description = "Change chat mode",
            usage       = "<chat|continue|explain|bughunt|refactor|tests>  Change chat mode",
            handler     = _cmd_mode,
            completer   = _complete_mode,
        )
    )

    for mode in sorted(_MODE_NAMES):
        registry.register(
            SlashCommandDefinition(
                name        = f"/{mode}",
                description = f"Switch to {mode} mode" if mode != "continue" else "Switch to continue mode and run continuation",
                handler     = lambda arg, ctx, selected_mode=mode: _cmd_mode_alias(arg, ctx, selected_mode),
            )
        )


_workspace_root: Callable[[], object]
