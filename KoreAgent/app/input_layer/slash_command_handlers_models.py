# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Slash command handlers for the Ollama server and model configuration.
# ====================================================================================================
from typing import Callable

from llm_client import configure_host
from llm_client import get_active_host
from llm_client import get_active_system_one_model
from llm_client import get_ollama_ps_rows
from llm_client import format_running_model_report
from llm_client import is_explicit_model_name
from llm_client import list_ollama_models
from llm_client import preload_system_one_model
from llm_client import register_session_config
from llm_client import register_system_one_model
from llm_client import resolve_model_name
from llm_client import set_ollama_offload_mode
from llm_client import stop_model
from input_layer.slash_command_context import SlashCommandContext


def _configure_ollama_offload(mode: str, ctx: SlashCommandContext) -> None:
    if mode not in {"forcecpu", "forcegpu", "autogpu"}:
        ctx.output("Usage: /llmserverconfig cpugpu <forcecpu | forcegpu | autogpu>", "error")
        return

    set_ollama_offload_mode(mode)
    try:
        running_names = [row.get("name", "") for row in get_ollama_ps_rows() if row.get("name")]
        loaded_name   = resolve_model_name(ctx.config.resolved_model, running_names)
        if loaded_name:
            stop_model(loaded_name)
            unload_note = " Active model unloaded; the setting applies on its next load."
        else:
            unload_note = " The setting applies the next time Ollama loads the model."
    except Exception:
        unload_note = " The setting applies the next time Ollama loads the model."

    detail = {
        "forcecpu": "CPU only (num_gpu=0).",
        "forcegpu": "request all model layers on GPU (num_gpu=999).",
        "autogpu":  "allow Ollama to choose CPU/GPU placement.",
    }[mode]
    ctx.output(f"Ollama offload: {mode} — {detail}{unload_note}", "success")


def _cmd_llmserverconfig(arg: str, ctx: SlashCommandContext) -> None:
    """Inspect or update the active Ollama model and request parameters."""
    if not arg:
        ctx.output(
            f"Chat model: {ctx.config.resolved_model}  |  System One: {ctx.config.system_one_model}  |  "
            f"ctx: {ctx.config.num_ctx:,}  |  max_predict: {ctx.config.max_predict:,}  |  Ollama: {get_active_host()}",
            "info",
        )
        ctx.output(
            "Usage: /llmserverconfig model list | model <name> | ctx <n> | "
            "max_predict <n> | cpugpu <forcecpu|forcegpu|autogpu> | "
            "systemone [status|model list|model <name>]",
            "dim",
        )
        return

    parts = arg.strip().split(None, 1)
    first = parts[0].lower()
    rest  = parts[1].strip() if len(parts) > 1 else ""

    if first == "ctx":
        if not rest or not rest.isdigit():
            ctx.output(f"Usage: /llmserverconfig ctx <n>  |  current: {ctx.config.num_ctx:,}", "dim")
            return
        ctx.config.num_ctx = int(rest)
        register_session_config(ctx.config.resolved_model, ctx.config.num_ctx)
        ctx.output(f"Context window: {ctx.config.num_ctx:,} tokens", "success")
        return

    if first == "max_predict":
        if rest and not rest.isdigit():
            ctx.output("Usage: /llmserverconfig max_predict <count>", "dim")
            return
        count = int(rest) if rest else 1024
        if count < 1:
            ctx.output("max_predict must be at least 1.", "error")
            return
        ctx.config.max_predict = count
        register_session_config(ctx.config.resolved_model, ctx.config.num_ctx, count)
        ctx.output(f"Max prediction output: {count:,} tokens", "success")
        return

    if first == "cpugpu":
        _configure_ollama_offload(rest.lower(), ctx)
        return

    if first == "systemone":
        _cmd_systemone(rest, ctx)
        return

    if first == "model":
        try:
            available = list_ollama_models(start_if_needed=False)
        except Exception as exc:
            ctx.output(f"Error listing models: {exc}", "error")
            return

        if not rest or rest == "list":
            ctx.output(f"{len(available)} model(s) installed on: {get_active_host()}", "info")
            for model_name in available:
                marker = ">" if model_name == ctx.config.resolved_model else " "
                ctx.output(f"  {marker} {model_name}", "item")
            return

        resolved = resolve_model_name(rest, available) if available else None
        if resolved is None:
            if is_explicit_model_name(rest):
                resolved = rest.strip()
                ctx.output(f"Model '{resolved}' not listed; using it as an explicit override.", "dim")
            elif not available:
                ctx.output("No models available on the Ollama server.", "error")
                return
            else:
                ctx.output(f"Model '{rest}' not found. Available: {', '.join(available)}", "error")
                return

        old_model                 = ctx.config.resolved_model
        ctx.config.resolved_model = resolved
        register_session_config(resolved, ctx.config.num_ctx)
        ctx.clear_history()
        ctx.output(f"Model switched: {old_model} -> {resolved}", "success")
        ctx.output("(conversation history cleared)", "dim")
        return

    ctx.output(f"Unknown model configuration command: {first}", "error")


def _cmd_systemone(arg: str, ctx: SlashCommandContext) -> None:
    """Inspect or select the independent Ollama System One decision model."""
    parts = arg.strip().split(None, 1) if arg.strip() else []
    first = parts[0].lower() if parts else "status"
    rest  = parts[1].strip() if len(parts) > 1 else ""

    if first == "status":
        configured = get_active_system_one_model()
        ctx.output(f"System One model: {configured}  |  Ollama: {get_active_host()}", "info")
        try:
            ctx.output(format_running_model_report(configured), "item")
        except Exception as exc:
            ctx.output(f"System One runtime status unavailable: {exc}", "dim")
        return

    if first != "model":
        ctx.output("Usage: /systemone [status|model list|model <name>]", "dim")
        return

    try:
        available = list_ollama_models(start_if_needed=False)
    except Exception as exc:
        ctx.output(f"Error listing models: {exc}", "error")
        return

    if not rest or rest.lower() == "list":
        ctx.output(f"{len(available)} model(s) installed on: {get_active_host()}", "info")
        for model_name in available:
            marker = ">" if model_name == ctx.config.system_one_model else " "
            ctx.output(f"  {marker} {model_name}", "item")
        return

    resolved = resolve_model_name(rest, available) if available else None
    if resolved is None:
        if is_explicit_model_name(rest):
            resolved = rest.strip()
            ctx.output(f"Model '{resolved}' not listed; using it as an explicit override.", "dim")
        elif not available:
            ctx.output("No models available on the Ollama server.", "error")
            return
        else:
            ctx.output(f"Model '{rest}' not found. Available: {', '.join(available)}", "error")
            return

    old_model                   = ctx.config.system_one_model
    ctx.config.system_one_model = resolved
    register_system_one_model(resolved)
    try:
        preload_system_one_model(resolved)
    except Exception as exc:
        ctx.config.system_one_model = old_model
        register_system_one_model(old_model)
        ctx.output(f"System One model switched: {old_model} -> {resolved}; warmup failed: {exc}", "error")
        return
    if old_model and old_model != resolved:
        try:
            stop_model(old_model)
        except Exception:
            pass
    ctx.output(f"System One model switched and loaded: {old_model} -> {resolved}", "success")


def _cmd_stopmodel(arg: str, ctx: SlashCommandContext) -> None:
    """Unload one currently running Ollama model."""
    target_name = arg.strip() or ctx.config.resolved_model
    try:
        running_rows = get_ollama_ps_rows()
    except Exception as exc:
        ctx.output(f"Error reading running models: {exc}", "error")
        return

    running_names = [row.get("name", "") for row in running_rows if row.get("name")]
    if not running_names:
        ctx.output("No models are currently loaded.", "dim")
        return

    resolved = resolve_model_name(target_name, running_names)
    if resolved is None:
        ctx.output(f"Model '{target_name}' is not loaded. Running: {', '.join(running_names)}", "error")
        return

    try:
        stop_model(resolved)
        ctx.output(f"Model unloaded: {resolved}", "success")
    except Exception as exc:
        ctx.output(f"Error stopping model: {exc}", "error")


def _cmd_llmserver(arg: str, ctx: SlashCommandContext) -> None:
    """Switch to another Ollama server and reset the conversation."""
    if not arg:
        ctx.output(f"Current Ollama server: {get_active_host()}", "info")
        return

    old_host = get_active_host()
    try:
        configure_host(arg)
        new_host = get_active_host()
        models   = list_ollama_models(start_if_needed=False)
        if models and ctx.config.resolved_model not in models:
            ctx.config.resolved_model = models[0]
        register_session_config(ctx.config.resolved_model, ctx.config.num_ctx)
        ctx.clear_history()
        ctx.output(f"Ollama server: {old_host} -> {new_host}", "success")
        if models:
            ctx.output(f"  {len(models)} model(s): {', '.join(models)}", "item")
        ctx.output("(conversation history cleared)", "dim")
    except Exception as exc:
        new_host = get_active_host()
        configure_host(old_host)
        ctx.output(f"Cannot reach '{new_host}': {exc}", "error")
        ctx.output(f"Still using Ollama at: {old_host}", "dim")


def register_model_slash_commands(registry: dict[str, Callable], descriptions: dict[str, str]) -> None:
    registry.update(
        {
            "/llmserver":       _cmd_llmserver,
            "/llmserverconfig": _cmd_llmserverconfig,
            "/systemone":       _cmd_systemone,
            "/stopmodel":       _cmd_stopmodel,
        }
    )
    descriptions.update(
        {
            "/llmserver":       "<host>  Switch Ollama server",
            "/llmserverconfig": "model list | model <name> | ctx <n> | max_predict <n> | cpugpu <forcecpu|forcegpu|autogpu> | systemone ...",
            "/systemone":       "[status | model list | model <name>]  Configure the System One decision model",
            "/stopmodel":       "[name]  Unload a running Ollama model from VRAM",
        }
    )
