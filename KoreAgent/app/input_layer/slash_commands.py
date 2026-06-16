# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Slash-command processor shared across all input modes.
#
# The command registry lives here, but domain handlers are split into clearly named modules:
#   - slash_command_handlers_models.py
#   - slash_command_handlers_testing.py
#   - slash_command_handlers_tasks.py
#   - slash_command_handlers_sessions.py
# ====================================================================================================

import json
import time
from datetime import date
from datetime import timedelta
from pathlib import Path
from typing import Callable

from llm_client import get_active_host
from llm_client import get_llm_timeout
from llm_client import register_session_config
from llm_client import set_llm_timeout
from context_manager import get_last_context_map
from context_manager import get_last_messages
from context_manager import compact_context
from context_manager import format_context_map
from orchestration import get_skill_guidance_enabled
from orchestration import get_web_skills_enabled
from orchestration import request_stop
from orchestration import get_sandbox_enabled
from orchestration import set_sandbox_enabled
from orchestration import set_skill_guidance_enabled
from input_layer.slash_command_context import SlashCommandContext
from input_layer.slash_command_handlers_models import register_model_slash_commands
from input_layer.slash_command_handlers_sessions import register_session_slash_commands
from input_layer.slash_command_handlers_tasks import register_task_slash_commands
from input_layer.slash_command_handlers_testing import register_testing_slash_commands
import mcp_client
from utils.workspace_utils import get_bootstrap_defaults_file
from utils.workspace_utils import get_controldata_dir
from utils.workspace_utils import get_logs_dir
from utils.workspace_utils import get_test_results_dir
from utils.suite_version import SUITE_VERSION


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


def _cmd_ctx(arg: str, ctx: SlashCommandContext) -> None:
    def _get_map_and_messages():
        return get_last_context_map(), get_last_messages()

    def _show_map(context_map):
        ctx.output(format_context_map(context_map, ctx.config.num_ctx), "item")

    def _resolve_index(rest: str):
        context_map, messages = _get_map_and_messages()
        if not context_map:
            ctx.output("No run context available - send a prompt first.", "error")
            return None
        try:
            index = int(rest)
        except ValueError:
            ctx.output(f"Invalid index '{rest}' - must be an integer.", "error")
            return None
        if index < 0 or index >= len(context_map):
            ctx.output(f"Index {index} out of range (0 - {len(context_map) - 1}).", "error")
            return None
        return context_map, messages, index

    if not arg:
        context_map, _ = _get_map_and_messages()
        if context_map:
            _show_map(context_map)
            ctx.output("", "dim")
        ctx.output(f"Context window size: {ctx.config.num_ctx:,} tokens", "info")
        return

    parts = arg.split(None, 1)
    sub = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub == "size":
        if not rest:
            ctx.output(f"Context window size: {ctx.config.num_ctx:,} tokens", "info")
            return
        try:
            value = int(rest.replace(",", "").replace("_", ""))
        except ValueError:
            ctx.output(f"Invalid value '{rest}' - must be an integer (e.g. /ctx size 32768).", "error")
            return
        if value < 512:
            ctx.output("Context size must be at least 512 tokens.", "error")
            return
        old = ctx.config.num_ctx
        ctx.config.num_ctx = value
        register_session_config(ctx.config.resolved_model, value)
        ctx.output(f"Context window size changed: {old:,} \u2192 {value:,}", "success")
        return

    if sub == "item":
        if not rest:
            ctx.output("Usage: /ctx item <index>", "dim")
            return
        resolved = _resolve_index(rest)
        if resolved is None:
            return
        context_map, messages, index = resolved
        entry = context_map[index]
        msg_idx = entry.get("msg_idx")
        ctx.output(f"Entry {index}: role={entry.get('role')}  label={entry.get('label')}  chars={entry.get('chars'):,}", "info")
        if msg_idx is None:
            ctx.output("(no associated message - not individually addressable)", "dim")
            return
        content = messages[msg_idx].get("content") or ""
        ctx.output(content if content else "(empty)", "item")
        return

    if sub == "compact":
        if not rest:
            ctx.output("Usage: /ctx compact <index>", "dim")
            return
        resolved = _resolve_index(rest)
        if resolved is None:
            return
        context_map, messages, index = resolved
        entry = context_map[index]
        if entry.get("msg_idx") is None:
            ctx.output(f"Entry {index} ({entry.get('role')} / {entry.get('label')}) has no associated message - cannot compact.", "error")
            return
        ctx.output("Before:", "dim")
        _show_map(context_map)
        changed = compact_context(context_map, messages, index)
        if not changed:
            ctx.output(f"Entry {index} was already compacted.", "dim")
            return
        ctx.output("", "dim")
        ctx.output("After:", "dim")
        _show_map(context_map)
        return

    ctx.output("Unknown sub-command. Usage: /ctx | /ctx size [<n>] | /ctx item <n> | /ctx compact <n>", "error")


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
    ctx.output(f"Max tool rounds changed: {old} \u2192 {value}", "success")


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
    ctx.output(f"LLM timeout changed: {old}s \u2192 {value}s", "success")


def _cmd_newchat(arg: str, ctx: SlashCommandContext) -> None:
    # Retired - use /session new [name] instead.
    ctx.output("Note: /newchat is retired. Use '/session new [name]' instead.", "dim")
    name = arg.strip()
    new_session_id = f"web_{int(time.time() * 1000)}"
    if ctx.switch_session:
        ctx.switch_session(new_session_id, name)
        label = f"'{name}'" if name else "a new chat"
        ctx.output(f"Conversation history cleared - starting {label}.", "success")
        return
    ctx.clear_history()
    ctx.output("Conversation history cleared - starting a new chat.", "success")



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
        from skills_catalog_builder import DEFAULT_SUMMARY_FILE
        from skills_catalog_builder import build_skills_payload
        from skills_catalog_builder import find_skill_files
        from skills_catalog_builder import load_skills_payload
        from skills_catalog_builder import write_skills_catalog
        from skills_catalog_builder import write_skills_summary

        skill_files = find_skill_files(skills_root=DEFAULT_SKILLS_ROOT)
        if not skill_files:
            ctx.output("No skill.md files found - catalog unchanged.", "error")
            return

        payload = build_skills_payload(DEFAULT_SKILLS_ROOT, use_llm=False, model_name="", num_ctx=0)
        write_skills_catalog(payload, DEFAULT_OUTPUT_FILE)
        write_skills_summary(payload, DEFAULT_SUMMARY_FILE)
        ctx.config.skills_payload = load_skills_payload(DEFAULT_OUTPUT_FILE)
        ctx.output(f"Skills catalog rebuilt: {len(payload['skills'])} skill(s) registered.  Mode: {current_mode}.", "success")
    except Exception as exc:
        ctx.output(f"Error rebuilding skills catalog: {exc}", "error")


def _cmd_stoprun(arg: str, ctx: SlashCommandContext) -> None:
    request_stop()
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
        ctx.output("Usage: /deletelogs <days>  |  delete log and test_results date-folders older than N days", "dim")
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
    for base_dir in (get_logs_dir(), get_test_results_dir()):
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
    for base_dir in (get_logs_dir(), get_test_results_dir()):
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
    from orchestration import _filter_web_skills
    from skills_catalog_builder import build_tool_definitions

    payload = ctx.config.skills_payload
    if not get_web_skills_enabled():
        payload = _filter_web_skills(payload)

    skill_tool_defs = build_tool_definitions(payload)
    mcp_tool_defs   = mcp_client.get_mcp_tool_definitions()
    mcp_tool_index  = mcp_client.get_mcp_tool_index()
    tool_defs       = skill_tool_defs + mcp_tool_defs
    if not tool_defs:
        ctx.output("No tools available.", "dim")
        return

    local_tool_meta: dict[str, dict] = {}
    for skill in payload.get("skills", []):
        skill_meta = {
            "origin":         skill.get("origin", "local"),
            "availability":   skill.get("availability", "configured"),
            "role":           skill.get("role", "optional"),
            "trust_boundary": skill.get("trust_boundary", "internal"),
        }
        for func_sig in skill.get("functions", []):
            func_name = str(func_sig).split("(", 1)[0].strip()
            if func_name:
                local_tool_meta.setdefault(func_name, skill_meta)

    web_off = not get_web_skills_enabled()
    mcp_count = len(mcp_tool_defs)
    ctx.output(f"{len(tool_defs)} tool(s) active{' (web skills off)' if web_off else ''}{f', {mcp_count} via MCP' if mcp_count else ''}:", "info")
    for tool in tool_defs:
        fn     = tool["function"]
        name   = fn["name"]
        desc   = fn.get("description", "").split("\n")[0][:80]
        params = list(fn.get("parameters", {}).get("properties", {}).keys())
        sig    = f"{name}({', '.join(params)})"
        meta   = local_tool_meta.get(name) or mcp_tool_index.get(name, {})
        label  = f"{meta.get('role', 'tool')}/{meta.get('availability', 'unknown')}/{meta.get('trust_boundary', 'unknown')}"
        ctx.output(f"  [{label}] {sig}", "item")
        if desc:
            ctx.output(f"    {desc}", "dim")


def _cmd_defaults(arg: str, ctx: SlashCommandContext) -> None:
    defaults_path = get_bootstrap_defaults_file()

    def _load() -> dict:
        try:
            return json.loads(defaults_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    sub = arg.strip().lower()
    if sub == "set":
        existing = _load()
        new_cfg = {"model": ctx.config.resolved_model, "ctx": ctx.config.num_ctx, "llmhost": get_active_host()}
        for key in ("agentport", "ControlDataFolder", "UserDataFolder", "mcp_connections", "mcp_servers"):
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


# ----------------------------------------------------------------------------------------------------
def _cmd_mcp(arg: str, ctx: SlashCommandContext) -> None:
    sub = arg.strip().lower()

    if sub in ("", "status"):
        statuses = mcp_client.get_server_status()
        if not statuses:
            ctx.output("No MCP connections configured.", "dim")
            return
        for srv in statuses:
            ok_str = f"({srv['tool_count']} tool(s))" if srv["ok"] else "(no tools - server may be down)"
            purpose = f" - {srv['purpose']}" if srv.get("purpose") else ""
            ctx.output(f"  {'OK  ' if srv['ok'] else 'FAIL'}  {srv['name']}  {srv['url']}  {ok_str}{purpose}", "item")
        return

    if sub == "reconnect":
        ctx.output("Reconnecting to MCP servers...", "dim")
        count, lines = mcp_client.reconnect()
        for line in lines:
            ctx.output(line, "info" if count > 0 else "error")
        ctx.output(f"{count} MCP tool(s) now registered.", "success" if count > 0 else "error")
        return

    ctx.output("Usage: /mcp [status | reconnect]", "dim")


_REGISTRY: dict[str, Callable] = {
    "/help": _cmd_help,
    "/ctx": _cmd_ctx,
    "/rounds": _cmd_rounds,
    "/timeout": _cmd_timeout,
    "/stoprun": _cmd_stoprun,
    "/newchat": _cmd_newchat,
    "/reskill": _cmd_reskills,
    "/version": _cmd_version,
    "/sandbox": _cmd_sandbox,
    "/tools": _cmd_tools,
    "/deletelogs": _cmd_deletelogs,
    "/defaults": _cmd_defaults,
    "/mcp":      _cmd_mcp,
}

_DESCRIPTIONS: dict[str, str] = {
    "/help": "List available slash commands",
    "/ctx": "Show context map + window size; sub-cmds: size [<n>], item <n>, compact <n>",
    "/rounds": "<n>  Set max tool-call rounds per prompt (e.g. /rounds 6)",
    "/timeout": "<seconds>  Set LLM generation timeout (e.g. /timeout 1800 for heavy analysis)",
    "/stoprun": "Cancel the active LLM run (after its current round) and clear all pending queued prompts",
    "/newchat": "(retired - use /session new [name])",
    "/reskill": "[min|max]  Rebuild skills catalog and set system prompt guidance mode (default: min)",
    "/version": "Show framework version, active model, and context size",
    "/sandbox": "<on|off>  Enable/disable Python code execution sandbox (import whitelist + blocked builtins)",
    "/tools": "List all tools currently exposed to the model (respects web skills toggle)",
    "/deletelogs": "<days>  Delete log, chatsession, and test_results date-folders older than N days (e.g. /deletelogs 10)",
    "/defaults": "Show current bootstrap defaults and file path; /defaults set saves current model/ctx/host to the file",
    "/mcp":      "[status | reconnect]  Show MCP server status or re-enumerate tools from all configured servers",
}

register_model_slash_commands(_REGISTRY, _DESCRIPTIONS)
register_testing_slash_commands(_REGISTRY, _DESCRIPTIONS)
register_task_slash_commands(_REGISTRY, _DESCRIPTIONS)
register_session_slash_commands(_REGISTRY, _DESCRIPTIONS)
