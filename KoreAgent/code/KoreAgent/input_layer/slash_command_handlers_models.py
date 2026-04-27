import json
import urllib.request
from typing import Callable

from KoreAgent.llm_client import configure_host
from KoreAgent.llm_client import configure_server
from KoreAgent.llm_client import get_active_backend
from KoreAgent.llm_client import get_active_host
from KoreAgent.llm_client import get_active_num_ctx
from KoreAgent.llm_client import get_ollama_ps_rows
from KoreAgent.llm_client import is_explicit_model_name
from KoreAgent.llm_client import list_ollama_models
from KoreAgent.llm_client import register_session_config
from KoreAgent.llm_client import resolve_model_name
from KoreAgent.llm_client import stop_model
from KoreAgent.input_layer.slash_command_context import SlashCommandContext
from KoreAgent.utils.workspace_utils import get_bootstrap_defaults_file


def _cmd_llmserverconfig(arg: str, ctx: SlashCommandContext) -> None:
    # /llmserverconfig                  -> show current model + ctx + backend
    # /llmserverconfig model list       -> list models available on the active server
    # /llmserverconfig model <name>     -> switch active model; clears history
    # /llmserverconfig ctx <n>          -> set context window size
    if not arg:
        ctx.output(
            f"Model: {ctx.config.resolved_model}  |  ctx: {ctx.config.num_ctx:,}  |  "
            f"backend: {get_active_backend()} @ {get_active_host()}",
            "info",
        )
        ctx.output("Usage: /llmserverconfig model list | model <name> | ctx <n>", "dim")
        return

    parts = arg.strip().split(None, 1)
    first = parts[0].lower()
    rest  = parts[1].strip() if len(parts) > 1 else ""

    if first == "ctx":
        if not rest or not rest.strip().isdigit():
            ctx.output(f"Usage: /llmserverconfig ctx <n>  |  current: {ctx.config.num_ctx:,}", "dim")
            return
        n = int(rest.strip())
        ctx.config.num_ctx = n
        register_session_config(ctx.config.resolved_model, n)
        ctx.output(f"Context window: {n:,} tokens", "success")
        return

    if first == "model":
        if not rest or rest == "list":
            try:
                available = list_ollama_models()
                host      = get_active_host()
                backend   = get_active_backend()
                label     = "model(s) installed on"
                ctx.output(f"{len(available)} {label}: {host}", "info")
                for model_name in available:
                    marker = ">" if model_name == ctx.config.resolved_model else " "
                    ctx.output(f"  {marker} {model_name}", "item")
            except Exception as exc:
                ctx.output(f"Error listing models: {exc}", "error")
            return

        model_arg = rest
        try:
            available = list_ollama_models()
            resolved  = resolve_model_name(model_arg, available) if available else None
            if resolved is None:
                if is_explicit_model_name(model_arg):
                    resolved = model_arg.strip()
                    ctx.output(
                        f"Model '{resolved}' not in listed models; using as explicit override.",
                        "dim",
                    )
                elif get_active_backend() == "lmstudio":
                    # LM Studio model IDs (e.g. openai/gpt-oss-20b) may not contain ':'
                    # but the server routes to the correct model via the name in the payload.
                    resolved = model_arg.strip()
                else:
                    if not available:
                        ctx.output("No models available on the inference server.", "error")
                        return
                    ctx.output(f"Model '{model_arg}' not found. Available: {', '.join(available)}", "error")
                    return
            old = ctx.config.resolved_model
            ctx.config.resolved_model = resolved
            register_session_config(resolved, ctx.config.num_ctx)
            ctx.clear_history()
            ctx.output(f"Model switched: {old} -> {resolved}", "success")
            ctx.output("(conversation history cleared)", "dim")
        except Exception as exc:
            ctx.output(f"Error: {exc}", "error")
        return

    ctx.output(
        f"Unknown subcommand '{first}'. Usage: /llmserverconfig model list | model <name> | ctx <n>",
        "error",
    )


def _cmd_stopmodel(arg: str, ctx: SlashCommandContext) -> None:
    if get_active_backend() == "lmstudio":
        ctx.output("Model unloading is not supported via LM Studio's API.", "dim")
        ctx.output("Use the LM Studio UI to change or unload the served model.", "dim")
        return

    target_name = arg.strip() if arg.strip() else ctx.config.resolved_model
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
        ctx.output(
            f"Model '{target_name}' is not currently loaded.  Running: {', '.join(running_names)}",
            "error",
        )
        return

    try:
        stop_model(resolved)
        ctx.output(f"Model unloaded: {resolved}", "success")
    except Exception as exc:
        ctx.output(f"Error stopping model: {exc}", "error")


def _cmd_llmserver(arg: str, ctx: SlashCommandContext) -> None:
    # /llmserver                      -> show current server
    # /llmserver ollama <host|url>    -> switch to Ollama at the given host/url
    # /llmserver lmstudio <host|url>  -> switch to LM Studio at the given host/url
    if not arg:
        ctx.output(f"Current server: {get_active_host()} ({get_active_backend()})", "info")
        return

    parts = arg.strip().split(None, 1)
    token = parts[0].lower()

    if token not in ("ollama", "lmstudio") or len(parts) < 2:
        ctx.output("Usage: /llmserver <ollama|lmstudio> <host|url>", "error")
        ctx.output(f"Current: {get_active_host()} ({get_active_backend()})", "dim")
        return

    host_arg = parts[1].strip()
    old_host = get_active_host()

    try:
        configure_server(token, host_arg)

        new_host = get_active_host()
        models   = list_ollama_models()
        # Sync the session model to a valid choice on the new server.
        # If the currently configured model isn't in the new server's list, pick the first available.
        current_model = ctx.config.resolved_model
        if models and current_model not in models:
            ctx.config.resolved_model = models[0]
        register_session_config(ctx.config.resolved_model, ctx.config.num_ctx)
        ctx.clear_history()
        ctx.output(f"Server: {old_host} -> {new_host} ({get_active_backend()})", "success")
        if models:
            ctx.output(f"  {len(models)} model(s): {', '.join(models)}", "item")
        ctx.output("(conversation history cleared)", "dim")
    except Exception as exc:
        new_host = get_active_host()
        configure_host(old_host)
        ctx.output(f"Cannot reach '{new_host}': {exc}", "error")
        ctx.output(f"Still using: {old_host} ({get_active_backend()})", "dim")


def register_model_slash_commands(registry: dict[str, Callable], descriptions: dict[str, str]) -> None:
    registry.update(
        {
            "/llmserver":       _cmd_llmserver,
            "/llmserverconfig": _cmd_llmserverconfig,
            "/stopmodel":       _cmd_stopmodel,
        }
    )
    descriptions.update(
        {
            "/llmserver":       "<ollama|lmstudio> [host]  Switch the active model server backend and host",
            "/llmserverconfig": "model list | model <name> | ctx <n>  Configure the active model and context window",
            "/stopmodel":       "[name]  Unload a running model from VRAM (Ollama only, defaults to active model)",
        }
    )

