# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Explicit import/export controls for durable InDepthPlanner archives.
# ====================================================================================================

from __future__ import annotations

from typing import Callable

from indepth_planner_archives import export_plan_archive
from indepth_planner_archives import list_plan_archives
from indepth_planner_archives import load_plan_archive
from indepth_planner_store import get_plan
from indepth_planner_store import save_indepth_planner
from input_layer.slash_command_context import SlashCommandContext


def _plan_usage_lines() -> list[str]:
    return [
        "/plans",
        "  List saved plan archives.",
        "/plan export <name>",
        "  Export the active plan to a named archive.",
        "/plan import <name> [--replace]",
        "  Import a saved plan archive. Name matching supports a unique substring.",
        "/plan inspect <name>",
        "  Show a compact summary for one saved plan archive.",
    ]


def _archive_list_lines() -> list[str]:
    archives = list_plan_archives()
    if not archives:
        return ["No saved plan archives."]
    return [f"{item['name']:<32} {item['modified_at']}" for item in archives]


def _cmd_plans(arg: str, ctx: SlashCommandContext) -> None:
    ctx.output("Saved plan archives:", "info")
    for line in _archive_list_lines():
        ctx.output(f"  {line}", "item")


def _cmd_plan(arg: str, ctx: SlashCommandContext) -> None:
    parts = arg.split()
    action = parts[0].lower() if parts else ""
    name = " ".join(parts[1:]).strip()

    if action == "":
        ctx.output("Plan archive commands:", "info")
        for line in _plan_usage_lines():
            ctx.output(line, "item" if not line.startswith("  ") else "dim")
        return

    if action == "list":
        _cmd_plans(arg="", ctx=ctx)
        return

    if action == "export":
        if not name:
            ctx.output("Usage: /plan export <name>", "dim")
            return
        try:
            exported = export_plan_archive(name=name, plan=get_plan(session_id=ctx.session_id))
        except RuntimeError as exc:
            ctx.output(str(exc), "error")
            return
        ctx.output(f"Plan exported as '{exported['name']}'.", "success")
        return

    if action == "import":
        replace = "--replace" in parts[1:]
        archive_name = " ".join(part for part in parts[1:] if part != "--replace").strip()
        if not archive_name:
            ctx.output("Usage: /plan import <name> [--replace]", "dim")
            return
        current = get_plan(session_id=ctx.session_id)
        if current and not replace:
            ctx.output("An active plan exists. Use /plan import <name> --replace to replace it.", "error")
            return
        try:
            loaded = load_plan_archive(archive_name)
            save_indepth_planner(loaded["archive"]["plan"], session_id=ctx.session_id)
        except RuntimeError as exc:
            ctx.output(str(exc), "error")
            return
        ctx.output(f"Plan archive '{loaded['name']}' imported.", "success")
        return

    if action == "inspect":
        if not name:
            ctx.output("Usage: /plan inspect <name>", "dim")
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

    ctx.output("Usage: /plan [export <name> | import <name> [--replace] | inspect <name>]", "dim")
    ctx.output("Use /plans to list saved plan archives.", "dim")


def register_plan_slash_commands(registry: dict[str, Callable], descriptions: dict[str, str]) -> None:
    registry["/plans"] = _cmd_plans
    registry["/plan"] = _cmd_plan
    descriptions["/plans"] = "List saved durable plan archives"
    descriptions["/plan"] = "[export <name> | import <name> [--replace] | inspect <name>]  Show plan archive subcommands or act on one archive"
