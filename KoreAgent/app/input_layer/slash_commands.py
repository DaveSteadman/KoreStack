# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Slash-command processor shared across all input modes.
#
# The command registry lives here, but domain handlers are split into clearly named modules:
#   - slash_command_handlers_models.py
#   - slash_command_handlers_tasks.py
#   - slash_command_handlers_sessions.py
# MARK: FUNCTIONS
# Function inventory:
# - handle: Handles this module's primary operation.
# - _cmd_help: Implements the  cmd help operation for this module.
# - _cmd_rounds: Implements the  cmd rounds operation for this module.
# - _cmd_timeout: Implements the  cmd timeout operation for this module.
# - _cmd_reskills: Implements the  cmd reskills operation for this module.
# - _cmd_stoprun: Implements the  cmd stoprun operation for this module.
# - _cmd_version: Implements the  cmd version operation for this module.
# - _cmd_sandbox: Implements the  cmd sandbox operation for this module.
# - _cmd_deletelogs: Implements the  cmd deletelogs operation for this module.
# - _cmd_tools: Implements the  cmd tools operation for this module.
# - _cmd_defaults: Implements the  cmd defaults operation for this module.
# - _load: Implements the  load operation for this module.
# - _cmd_comms: Implements the  cmd comms operation for this module.
# - _cmd_workspace: Implements the  cmd workspace operation for this module.
# ====================================================================================================

import json
import re
import shlex
import urllib.error
import urllib.request
from datetime import date
from datetime import timedelta
from pathlib import Path
from typing import Callable

from llm_client import get_active_host
from llm_client import get_llm_timeout
from llm_client import get_ollama_sampling_config
from llm_client import set_llm_timeout
from agent.orchestration.engine import _filter_web_skills
from agent.orchestration.engine import get_skill_guidance_enabled
from agent.orchestration.engine import get_web_skills_enabled
from agent.orchestration.engine import request_stop
from agent.orchestration.engine import get_sandbox_enabled
from agent.orchestration.engine import set_sandbox_enabled
from agent.orchestration.engine import set_skill_guidance_enabled
from input_layer.slash_command_context import SlashCommandContext
from input_layer.slash_command_handlers_models import register_model_slash_commands
from input_layer.slash_command_handlers_sessions import register_session_slash_commands
from sessions.tool_selection import ALWAYS_ON_TOOL_NAMES
from sessions.tool_selection import build_all_tool_catalog
from sessions.tool_selection import derive_active_tool_runtime
from utils.workspace_utils import get_agent_config_file
from utils.workspace_utils import get_controldata_dir
from utils.workspace_utils import get_logs_dir
from utils.workspace_utils import get_suite_root
from utils.suite_version import SUITE_VERSION
from working_data import working_data_clear
from sessions.tool_selection import clear_session_tools_active


def handle(text: str, ctx: SlashCommandContext) -> bool:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return False

    parts = stripped.split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    handler = _REGISTRY.get(cmd)
    if handler is None:
        ctx.output(f"Unknown command '{cmd}'.  Type /help for available commands.", "dim")
        return True

    handler(arg, ctx)
    return True


def _cmd_help(arg: str, ctx: SlashCommandContext) -> None:
    ctx.output("Available slash commands:", "info")
    for name, description in sorted(_DESCRIPTIONS.items()):
        ctx.output(f"  {name:<16} {description}", "item")


def _cmd_rounds(arg: str, ctx: SlashCommandContext) -> None:
    if not arg:
        ctx.output(f"Usage: /rounds <n>  |  current: {ctx.config.max_iterations}", "dim")
        return
    try:
        value = int(arg.strip())
    except ValueError:
        ctx.output(f"Invalid value '{arg}' - must be a positive integer (e.g. /rounds 6).", "error")
        return
    if value < 1:
        ctx.output("Rounds must be at least 1.", "error")
        return
    old = ctx.config.max_iterations
    ctx.config.max_iterations = value
    ctx.output(f"Max tool rounds changed: {old} -> {value}", "success")


def _cmd_timeout(arg: str, ctx: SlashCommandContext) -> None:
    if not arg:
        ctx.output(f"Usage: /timeout <seconds>  |  current: {get_llm_timeout()}s", "dim")
        return
    try:
        value = int(arg.strip().replace(",", "").replace("_", ""))
    except ValueError:
        ctx.output(f"Invalid value '{arg}' - must be an integer number of seconds (e.g. /timeout 1800).", "error")
        return
    if value < 10:
        ctx.output("Timeout must be at least 10 seconds.", "error")
        return
    old = get_llm_timeout()
    set_llm_timeout(value)
    ctx.output(f"LLM timeout changed: {old}s -> {value}s", "success")


def _cmd_reskills(arg: str, ctx: SlashCommandContext) -> None:
    sub = arg.strip().lower()
    if sub == "max":
        set_skill_guidance_enabled(True)
        ctx.output("Skill guidance mode: max (tool selection block included in system prompt).", "success")
        ctx.output("  ~925 extra tokens per call.  Good for comparison testing.", "dim")
        return
    if sub == "min":
        set_skill_guidance_enabled(False)
        ctx.output("Skill guidance mode: min (tool selection block omitted from system prompt).", "success")
        ctx.output("  Relies on JSON Schema tool descriptions only.", "dim")
        return
    if sub and sub not in ("min", "max", ""):
        current = "max" if get_skill_guidance_enabled() else "min"
        ctx.output(f"Usage: /reskill [min|max]  |  current mode: {current}", "dim")
        ctx.output("  min  - lean system prompt; no tool selection guidance block (default)", "dim")
        ctx.output("  max  - full guidance block injected for comparison testing", "dim")
        return

    if not sub:
        set_skill_guidance_enabled(False)

    current_mode = "max" if get_skill_guidance_enabled() else "min"
    ctx.output(f"Rebuilding skills catalog (local extraction, no LLM) - mode: {current_mode}...", "dim")
    try:
        from skills_catalog_builder import DEFAULT_OUTPUT_FILE
        from skills_catalog_builder import DEFAULT_SKILLS_ROOT
        from skills_catalog_builder import build_skills_payload
        from skills_catalog_builder import find_skill_files
        from skills_catalog_builder import load_skills_payload
        from skills_catalog_builder import write_skills_catalog

        skill_files = find_skill_files(skills_root=DEFAULT_SKILLS_ROOT)
        if not skill_files:
            ctx.output("No skill.md files found - catalog unchanged.", "error")
            return

        payload = build_skills_payload(DEFAULT_SKILLS_ROOT, use_llm=False, model_name="", num_ctx=0)
        write_skills_catalog(payload, DEFAULT_OUTPUT_FILE)
        ctx.config.skills_payload = load_skills_payload(DEFAULT_OUTPUT_FILE)
        ctx.output(f"Skills catalog rebuilt: {len(payload['skills'])} skill(s) registered.  Mode: {current_mode}.", "success")
    except Exception as exc:
        ctx.output(f"Error rebuilding skills catalog: {exc}", "error")


def _cmd_stoprun(arg: str, ctx: SlashCommandContext) -> None:
    request_stop("stoprun")
    ctx.output("Stop requested. Active run will halt after its current LLM round.", "info")


def _cmd_version(arg: str, ctx: SlashCommandContext) -> None:
    ctx.output(f"KoreAgent {SUITE_VERSION}", "info")


def _cmd_sandbox(arg: str, ctx: SlashCommandContext) -> None:
    sub = arg.strip().lower()
    if sub == "on":
        set_sandbox_enabled(True)
        ctx.output("Python sandbox enabled - imports restricted to the safe whitelist.", "success")
    elif sub == "off":
        set_sandbox_enabled(False)
        ctx.output("Python sandbox disabled - code snippets run with full Python access.", "success")
        ctx.output("Warning: /sandbox off allows unrestricted code execution. Re-enable with /sandbox on.", "dim")
    else:
        state = "on" if get_sandbox_enabled() else "off"
        ctx.output(f"Usage: /sandbox <on|off>  |  current: {state}", "dim")


def _cmd_deletelogs(arg: str, ctx: SlashCommandContext) -> None:
    import re
    import shutil

    if not arg.strip():
        ctx.output("Usage: /deletelogs <days>  |  delete log date-folders older than N days", "dim")
        return
    try:
        days = int(arg.strip())
    except ValueError:
        ctx.output(f"Invalid value '{arg}' - must be an integer number of days.", "error")
        return
    if days < 1:
        ctx.output("Days must be at least 1.", "error")
        return

    cutoff = date.today() - timedelta(days=days)
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    deleted = []
    errors = []
    for base_dir in (get_logs_dir(),):
        if not base_dir.exists():
            continue
        for folder in sorted(base_dir.iterdir()):
            if not folder.is_dir() or not date_re.match(folder.name):
                continue
            try:
                folder_date = date.fromisoformat(folder.name)
            except ValueError:
                continue
            if folder_date <= cutoff:
                try:
                    shutil.rmtree(folder)
                    deleted.append(f"{base_dir.name}/{folder.name}")
                except Exception as exc:
                    errors.append(f"{base_dir.name}/{folder.name}: {exc}")

    stray_deleted = []
    stray_errors = []
    for base_dir in (get_logs_dir(),):
        if not base_dir.exists():
            continue
        for item in sorted(base_dir.iterdir()):
            if item.is_file():
                try:
                    item.unlink()
                    stray_deleted.append(f"{base_dir.name}/{item.name}")
                except Exception as exc:
                    stray_errors.append(f"{base_dir.name}/{item.name}: {exc}")

    if deleted:
        ctx.output(f"Deleted {len(deleted)} date-folder(s):", "success")
        for entry in deleted:
            ctx.output(f"  {entry}", "item")
    else:
        ctx.output(f"No date-folders older than {days} day(s) found.", "dim")
    if stray_deleted:
        ctx.output(f"Deleted {len(stray_deleted)} stray file(s):", "success")
        for entry in stray_deleted:
            ctx.output(f"  {entry}", "item")
    for err in errors + stray_errors:
        ctx.output(f"Error deleting {err}", "error")


def _cmd_tools(arg: str, ctx: SlashCommandContext) -> None:
    sub = str(arg or "").strip().lower()
    if not sub:
        ctx.output("Usage: /tools all  |  /tools active", "dim")
        return
    if sub == "all":
        entries = build_all_tool_catalog(ctx.config.skills_payload, session_id=ctx.session_id)
        if not entries:
            ctx.output("No tools available.", "dim")
            return
        ctx.output(f"{len(entries)} local tool(s) available in the full catalog:", "info")
        for entry in entries:
            active_marker = "active" if entry.get("active") else "idle"
            label = f"{entry.get('origin', 'local')}/{entry.get('role', 'tool')}/{entry.get('availability', 'unknown')}/{active_marker}"
            ctx.output(f"  [{label}] {entry.get('name', '')}", "item")
            desc = str(entry.get("description", "")).strip()
            if desc:
                ctx.output(f"    {desc[:120]}", "dim")
        return
    if sub == "active":
        available_payload = ctx.config.skills_payload if get_web_skills_enabled() else _filter_web_skills(ctx.config.skills_payload)
        runtime = derive_active_tool_runtime(
            ctx.config.skills_payload,
            available_local_payload=available_payload,
            session_id=ctx.session_id,
            conversation_entry=None,
        )
        active_names = set(runtime["active_tool_names"])
        system_names = set(runtime.get("system_tool_names", []) or [])
        selected_names = [
            name for name in runtime["selected_tools"]
            if name in active_names and name not in system_names and name not in ALWAYS_ON_TOOL_NAMES
        ]
        selected_slots = int(runtime.get("selectable_tool_slots", len(selected_names)))
        system_count = int(runtime.get("system_tool_count", 0))
        always_on_count = int(runtime.get("always_on_tool_count", 0))
        always_on_names = sorted(name for name in active_names if name in ALWAYS_ON_TOOL_NAMES)
        if not active_names:
            ctx.output("No active tools. Only the tool-selection control plane is available.", "dim")
            return
        ctx.output(
            f"{len(active_names)} active tool(s) exposed to the model "
            f"(64-schema budget: {system_count} system + {always_on_count} other always-on; "
            f"{selected_slots} selectable slots):",
            "info",
        )
        for name in always_on_names:
            ctx.output(f"  [always-on] {name}", "item")
        for name in sorted(system_names):
            if name not in ALWAYS_ON_TOOL_NAMES:
                ctx.output(f"  [system] {name}", "item")
        for position, name in enumerate(selected_names, start=1):
            ctx.output(f"  [selected {position:>2}/{selected_slots}] {name}", "item")
        missing = runtime.get("missing_selected", []) or []
        if missing:
            ctx.output(f"Pruned missing tools: {', '.join(missing)}", "dim")
        return
    ctx.output("Usage: /tools all  |  /tools active", "dim")


def _cmd_defaults(arg: str, ctx: SlashCommandContext) -> None:
    defaults_path = get_agent_config_file()

    def _load() -> dict:
        try:
            return json.loads(defaults_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    sub = arg.strip().lower()
    if sub == "set":
        existing = _load()
        new_cfg = {
            "model":       ctx.config.resolved_model,
            "ctx":         ctx.config.num_ctx,
            "max_predict": getattr(ctx.config, "max_predict", 1024),
            "llmhost":     get_active_host(),
            **get_ollama_sampling_config(),
        }
        for key in ("agentport", "DataRootFolder", "ControlDataFolder", "UserDataFolder"):
            if key in existing:
                new_cfg[key] = existing[key]
        try:
            defaults_path.parent.mkdir(parents=True, exist_ok=True)
            defaults_path.write_text(json.dumps(new_cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except Exception as exc:
            ctx.output(f"Error saving defaults file: {exc}", "error")
            return
        ctx.output(f"Defaults saved to: {defaults_path}", "success")
        for key, value in new_cfg.items():
            ctx.output(f"  {key:<14} {value}", "item")
        return

    if not sub:
        ctx.output(f"Defaults file: {defaults_path}", "info")
        cfg = _load()
        if cfg:
            for key, value in cfg.items():
                ctx.output(f"  {key:<14} {value}", "item")
        else:
            ctx.output("  (file not found or empty)", "dim")
        return

    ctx.output("Usage: /defaults | /defaults set", "dim")


def _cmd_comms(arg: str, ctx: SlashCommandContext) -> None:
    try:
        parts = shlex.split(arg)
    except ValueError as exc:
        ctx.output(f"Invalid command syntax: {exc}", "error")
        return
    if len(parts) >= 2 and parts[0].lower() == "connection":
        action = parts[1].lower()
        if action not in {"pause", "resume", "publishprevious"}:
            ctx.output("Usage: /comms connection <pause|resume|publishprevious> [--chat <name>]", "dim")
            return
        values = {}
        index = 2
        while index < len(parts):
            if parts[index] != "--chat" or index + 1 >= len(parts):
                ctx.output("Usage: /comms connection <pause|resume|publishprevious> [--chat <name>]", "error")
                return
            values["chat"] = parts[index + 1]
            index += 2
        chat_name = values.get("chat", "").strip() or str(ctx.chat_name or "").strip()
        if not chat_name:
            if not ctx.session_id:
                ctx.output("No active chat. Use --chat <name>.", "error")
                return
            chat_name = f"webchat_{ctx.session_id}"
        try:
            suite_config = json.loads((get_suite_root() / "config" / "korestack_config.json").read_text(encoding="utf-8"))
            port         = int(suite_config["services"]["korecomms"]["port"])
            encoded_name = urllib.parse.quote(chat_name, safe="")
            request      = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/delivery-bindings/{encoded_name}/{action}",
                data    = b"{}",
                method  = "POST",
                headers = {"Content-Type": "application/json", "Accept": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                result = json.loads(response.read())
        except (OSError, ValueError, KeyError, urllib.error.HTTPError) as exc:
            detail = exc.read().decode(errors="replace") if isinstance(exc, urllib.error.HTTPError) else str(exc)
            ctx.output(f"KoreComms connection {action} failed: {detail}", "error")
            return
        if action == "publishprevious":
            ctx.output(f"Published previous output: chat={result['chat_name']} via {result['connection']}", "success")
        else:
            state = "resumed" if result["active"] else "paused"
            ctx.output(f"Connection delivery {state}: chat={result['chat_name']} via {result['connection']}", "success")
        return

    if len(parts) < 2 or parts[0].lower() != "delivery" or parts[1].lower() != "bind":
        ctx.output(
            "Usage: /comms delivery bind [--chat <name>] --connection <name> [--to <email> | --to-list <name>] --subject <text> [--startpaused]. "
            "An SFTP file connection needs no --to or --to-list; it writes to its configured file.",
            "dim",
        )
        return
    values = {}
    index = 2
    while index < len(parts):
        key = parts[index]
        if key == "--startpaused":
            values["startpaused"] = "true"
            index += 1
            continue
        if not key.startswith("--") or index + 1 >= len(parts):
            ctx.output("Use --chat, --connection, --to, --to-list, and --subject options.", "error")
            return
        values[key[2:]] = parts[index + 1]
        index += 2
    if "subject" not in values or (values.get("to") and values.get("to-list")):
        ctx.output("Required: --subject. Use at most one of --to or --to-list; omit both for an SFTP file connection. --chat defaults to the active chat.", "error")
        return
    chat_name = values.get("chat", "").strip()
    if not chat_name:
        chat_name = str(ctx.chat_name or "").strip()
    if not chat_name:
        if not ctx.session_id:
            ctx.output("No active chat. Use --chat <name> to bind a delivery target explicitly.", "error")
            return
        chat_name = f"webchat_{ctx.session_id}"
    try:
        suite_config = json.loads((get_suite_root() / "config" / "korestack_config.json").read_text(encoding="utf-8"))
        port = int(suite_config["services"]["korecomms"]["port"])
        payload = json.dumps({
            "chat_name": chat_name,
            "connection": values.get("connection", ""),
            "recipient": values.get("to", ""),
            "distribution_list": values.get("to-list", ""),
            "subject": values["subject"],
            "enabled": "startpaused" not in values,
        }).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/delivery-bindings",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read())
    except (OSError, ValueError, KeyError, urllib.error.HTTPError) as exc:
        detail = exc.read().decode(errors="replace") if isinstance(exc, urllib.error.HTTPError) else str(exc)
        ctx.output(f"KoreComms delivery binding failed: {detail}", "error")
        return
    ctx.output(
        f"Delivery bound: chat={result['chat_name']} via {result['connection']} to {result['recipient'] or result['distribution_list']}",
        "success",
    )


def _cmd_workspace(arg: str, ctx: SlashCommandContext) -> None:
    if arg.strip().lower() != "clear":
        ctx.output("Usage: /workspace clear", "dim")
        return
    if not ctx.session_id:
        ctx.output("No active chat workspace is available to clear.", "error")
        return
    working_data_result = working_data_clear(ctx.session_id)
    clear_session_tools_active(ctx.session_id)
    ctx.clear_history()
    ctx.output(f"Workspace and conversation history cleared: {working_data_result} Active tool selection reset.", "success")


_REGISTRY: dict[str, Callable] = {
    "/help": _cmd_help,
    "/rounds": _cmd_rounds,
    "/timeout": _cmd_timeout,
    "/stoprun": _cmd_stoprun,
    "/reskill": _cmd_reskills,
    "/version": _cmd_version,
    "/sandbox": _cmd_sandbox,
    "/tools": _cmd_tools,
    "/deletelogs": _cmd_deletelogs,
    "/defaults": _cmd_defaults,
    "/comms":    _cmd_comms,
    "/workspace": _cmd_workspace,
}

_DESCRIPTIONS: dict[str, str] = {
    "/help": "List available slash commands",
    "/rounds": "<n>  Set max tool-call rounds per prompt (e.g. /rounds 6)",
    "/timeout": "<seconds>  Set LLM generation timeout (e.g. /timeout 1800 for heavy analysis)",
    "/stoprun": "Cancel the active LLM run (after its current round) and clear all pending queued prompts",
    "/reskill": "[min|max]  Rebuild skills catalog and set system prompt guidance mode (default: min)",
    "/version": "Show framework version, active model, and context size",
    "/sandbox": "<on|off>  Enable/disable Python code execution sandbox (import whitelist + blocked builtins)",
    "/tools": "[all | active]  Inspect the full local catalog or active tool set",
    "/deletelogs": "<days>  Delete log date-folders older than N days (e.g. /deletelogs 10)",
    "/defaults": "Show current Agent configuration and file path; /defaults set saves current model/ctx/host to the file",
    "/comms":    "delivery bind [--chat <name>] --connection <name> [--to <email>|--to-list <name>] --subject <text> [--startpaused]; connection pause|resume|publishprevious [--chat <name>]",
    "/workspace": "clear  Reset this chat's history, Working Data, and active tool selection",
}

register_model_slash_commands(_REGISTRY, _DESCRIPTIONS)
register_session_slash_commands(_REGISTRY, _DESCRIPTIONS)
