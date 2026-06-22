from __future__ import annotations

from typing import Callable

from .slash_command_context import KoreCodeSlashCommandContext
from .workspace_menu import build_workspace_menu


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
        result = build_workspace_menu(_workspace_root())
        ctx.output("Workspace context enabled.", "success")
        ctx.output(f"Workspace menu refreshed: {result['menu_file_name']} ({result['file_count']} files indexed).", "info")
        return
    if sub == "off":
        ctx.add_action("set_workspace_context", enabled=False)
        ctx.output("Workspace context disabled.", "success")
        return
    if sub == "regen":
        result = build_workspace_menu(_workspace_root())
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


def register_workspace_slash_commands(
    registry: dict[str, Callable],
    descriptions: dict[str, str],
    *,
    workspace_root_getter: Callable[[], object],
) -> None:
    global _workspace_root
    _workspace_root = workspace_root_getter

    registry["/clear"]     = _cmd_clear
    registry["/retry"]     = _cmd_retry
    registry["/workspace"] = _cmd_workspace
    registry["/mode"]      = _cmd_mode
    registry["/chat"]      = lambda arg, ctx: _cmd_mode_alias(arg, ctx, "chat")
    registry["/continue"]  = lambda arg, ctx: _cmd_mode_alias(arg, ctx, "continue")
    registry["/explain"]   = lambda arg, ctx: _cmd_mode_alias(arg, ctx, "explain")
    registry["/bughunt"]   = lambda arg, ctx: _cmd_mode_alias(arg, ctx, "bughunt")
    registry["/refactor"]  = lambda arg, ctx: _cmd_mode_alias(arg, ctx, "refactor")
    registry["/tests"]     = lambda arg, ctx: _cmd_mode_alias(arg, ctx, "tests")

    descriptions["/clear"]     = "Clear the current conversation"
    descriptions["/retry"]     = "Retry the last user prompt in the current conversation"
    descriptions["/workspace"] = "<on|off|regen>  Enable, disable, or rebuild workspace context"
    descriptions["/mode"]      = "<chat|continue|explain|bughunt|refactor|tests>  Change chat mode"
    descriptions["/chat"]      = "Switch to chat mode"
    descriptions["/continue"]  = "Switch to continue mode and run continuation"
    descriptions["/explain"]   = "Switch to explain mode"
    descriptions["/bughunt"]   = "Switch to bughunt mode"
    descriptions["/refactor"]  = "Switch to refactor mode"
    descriptions["/tests"]     = "Switch to tests mode"


_workspace_root: Callable[[], object]
