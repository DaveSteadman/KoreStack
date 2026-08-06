# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Explicit import/export controls for durable Workflow archives.
# ====================================================================================================

from __future__ import annotations

from typing import Callable

from workflow_archives import export_plan_archive
from workflow_archives import list_plan_archives
from workflow_archives import load_plan_archive
from workflow_store import get_simple_plan
from workflow_store import save_workflow
from input_layer.slash_command_context import SlashCommandContext


def _workflow_usage_lines() -> list[str]:
    return [
        "/workflows",
        "  List saved Workflow archives.",
        "/workflow export <name>",
        "  Export the active Workflow to a named archive.",
        "/workflow import <name> [--replace]",
        "  Import a saved Workflow archive. Name matching supports a unique substring.",
        "/workflow inspect <name>",
        "  Show a compact summary for one saved Workflow archive.",
    ]


def _archive_list_lines() -> list[str]:
    archives = list_plan_archives()
    if not archives:
        return ["No saved plan archives."]
    return [f"{item['name']:<32} {item['modified_at']}" for item in archives]


def _cmd_workflows(arg: str, ctx: SlashCommandContext) -> None:
    ctx.output("Saved Workflow archives:", "info")
    for line in _archive_list_lines():
        ctx.output(f"  {line}", "item")


def _cmd_workflow(arg: str, ctx: SlashCommandContext) -> None:
    parts = arg.split()
    action = parts[0].lower() if parts else ""
    name = " ".join(parts[1:]).strip()

    if action == "":
        ctx.output("Workflow archive commands:", "info")
        for line in _workflow_usage_lines():
            ctx.output(line, "item" if not line.startswith("  ") else "dim")
        return

    if action == "list":
        _cmd_workflows(arg="", ctx=ctx)
        return

    if action == "export":
        if not name:
            ctx.output("Usage: /workflow export <name>", "dim")
            return
        try:
            exported = export_plan_archive(name=name, plan=get_simple_plan(session_id=ctx.session_id))
        except RuntimeError as exc:
            ctx.output(str(exc), "error")
            return
        ctx.output(f"Workflow exported as '{exported['name']}'.", "success")
        return

    if action == "import":
        replace = "--replace" in parts[1:]
        archive_name = " ".join(part for part in parts[1:] if part != "--replace").strip()
        if not archive_name:
            ctx.output("Usage: /workflow import <name> [--replace]", "dim")
            return
        current = get_simple_plan(session_id=ctx.session_id)
        if current and not replace:
            ctx.output("An active Workflow exists. Use /workflow import <name> --replace to replace it.", "error")
            return
        try:
            loaded = load_plan_archive(archive_name)
            save_workflow(loaded["archive"]["plan"], session_id=ctx.session_id)
        except RuntimeError as exc:
            ctx.output(str(exc), "error")
            return
        ctx.output(f"Workflow archive '{loaded['name']}' imported.", "success")
        return

    if action == "inspect":
        if not name:
            ctx.output("Usage: /workflow inspect <name>", "dim")
            return
        try:
            loaded = load_plan_archive(name)
        except RuntimeError as exc:
            ctx.output(str(exc), "error")
            return
        static = loaded["archive"]["plan"].get("static", {})
        tasks = static.get("tasks") if isinstance(static.get("tasks"), list) else []
        ctx.output(f"{loaded['name']}: {static.get('objective') or '(no objective)'} ({len(tasks)} task(s))", "item")
        return

    ctx.output("Usage: /workflow [export <name> | import <name> [--replace] | inspect <name>]", "dim")
    ctx.output("Use /workflows to list saved Workflow archives.", "dim")


def register_workflow_slash_commands(registry: dict[str, Callable], descriptions: dict[str, str]) -> None:
    registry["/workflows"] = _cmd_workflows
    registry["/workflow"] = _cmd_workflow
    descriptions["/workflows"] = "List saved durable Workflow archives"
    descriptions["/workflow"] = "[export <name> | import <name> [--replace] | inspect <name>]  Show Workflow archive subcommands or act on one archive"
