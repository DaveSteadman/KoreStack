from __future__ import annotations

from typing import Callable

from KoreCommon.datauser_script_runner import DataUserScriptError
from KoreCommon.datauser_script_runner import run_datauser_script
from input_layer.slash_command_context import SlashCommandContext


def _cmd_run(arg: str, ctx: SlashCommandContext) -> None:
    if not arg.strip():
        ctx.output("Usage: /run ./scripts/<script>.py [arguments]", "dim")
        return
    try:
        result = run_datauser_script(arg)
    except DataUserScriptError as exc:
        ctx.output(f"/run failed: {exc}", "error")
        return

    ctx.output(f"Ran datauser/scripts/{result.script.name} (exit {result.exit_code}).", "success")
    if result.output:
        ctx.output(result.output, "info")


def register_script_slash_commands(registry: dict[str, Callable], descriptions: dict[str, str]) -> None:
    registry["/run"]     = _cmd_run
    descriptions["/run"] = "./scripts/<script>.py [arguments]  Run a trusted datauser Python script"
