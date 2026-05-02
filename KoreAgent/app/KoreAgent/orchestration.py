# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Core orchestration layer shared by all execution modes.
#
# Provides:
#   OrchestratorConfig      -- session-level settings bundle (mutable by slash commands)
#   ConversationHistory     -- rolling window of user/assistant turn pairs
#   SessionContext          -- per-session skill-output cache for cross-turn injection
#   resolve_execution_model -- model alias → installed Ollama model name
#   orchestrate_prompt      -- tool-calling pipeline: messages → skills → synthesized response
#
# Related modules:
#   - main.py                    -- creates config, dispatches modes
#   - modes/api_mode.py         -- run_api_mode
#   - skill_executor.py          -- execute_tool_call (executes individual skill calls)
#   - skills_catalog_builder.py  -- build_tool_definitions (generates JSON Schema tool specs)
#   - llm_client.py              -- call_llm_chat (/v1/chat/completions with tools support)
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from KoreAgent.context_manager import format_context_map as _context_manager_format_context_map
from KoreAgent.context_manager import store_last_run_state
from KoreAgent.system_skills.Delegate.delegate_runner import get_delegate_runtime_tls
from KoreAgent.system_skills.Delegate.delegate_runner import pop_delegate_runtime
from KoreAgent.system_skills.Delegate.delegate_runner import push_delegate_runtime
from KoreAgent.system_skills.Delegate.delegate_runner import run_delegate_subrun
from KoreAgent.llm_client import call_llm_chat
from KoreAgent.llm_client import get_active_backend
from KoreAgent.llm_client import is_explicit_model_name
from KoreAgent.llm_client import list_ollama_models
from KoreAgent.llm_client import log_to_session
from KoreAgent.llm_client import resolve_model_name
from KoreAgent.prompt_tokens import resolve_tokens
from KoreAgent.scratchpad import scratch_list as _scratch_list
from KoreAgent.prompt_builder import build_system_message as _prompt_builder_build_system_message
from KoreAgent.session_runtime import bind_session
from KoreAgent.skill_executor import build_catalog_gates
from KoreAgent.system_skills.Memory.memory_skill import get_top_facts
from KoreAgent.system_skills.Memory.memory_skill import recall_relevant_memories
from KoreAgent.system_skills.Memory.memory_skill import store_exchange_memories
from KoreAgent.system_skills.Memory.memory_skill import store_prompt_memories
from KoreAgent.skills.SystemInfo.system_info_skill import get_static_system_info_string
from KoreAgent.skills_catalog_builder import build_tool_definitions
from KoreAgent.tool_loop import extract_result_fields as _tool_loop_extract_result_fields
import KoreAgent.mcp_client as _mcp_client
from KoreAgent.tool_loop import format_tool_outputs as _tool_loop_format_tool_outputs
from KoreAgent.tool_loop import run_tool_loop as _tool_loop_run_tool_loop
from KoreAgent.tool_loop import write_file_blocks as _tool_loop_write_file_blocks
from KoreAgent.utils.runtime_logger import SessionLogger
from KoreAgent.utils.workspace_utils import trunc


# ====================================================================================================
# MARK: SKILL GUIDANCE FLAG
# ====================================================================================================
_SKILL_GUIDANCE_ENABLED: bool = False


def get_skill_guidance_enabled() -> bool:
    return _SKILL_GUIDANCE_ENABLED


def set_skill_guidance_enabled(enabled: bool) -> None:
    global _SKILL_GUIDANCE_ENABLED
    _SKILL_GUIDANCE_ENABLED = enabled


# ====================================================================================================
# MARK: SANDBOX FLAG
# ====================================================================================================
_SANDBOX_ENABLED: bool = True


def get_sandbox_enabled() -> bool:
    return _SANDBOX_ENABLED


def set_sandbox_enabled(enabled: bool) -> None:
    global _SANDBOX_ENABLED
    _SANDBOX_ENABLED = enabled


# ====================================================================================================
# MARK: WEB SKILLS FLAG
# ====================================================================================================
# When False, any skill whose skill_name starts with "Web" is stripped from the active payload
# before tool definitions, the system prompt, and the catalog gate index are built.
# The underlying skill modules remain loaded - only their exposure to the model is suppressed.
_WEB_SKILLS_ENABLED: bool = True


def get_web_skills_enabled() -> bool:
    return _WEB_SKILLS_ENABLED


def set_web_skills_enabled(enabled: bool) -> None:
    global _WEB_SKILLS_ENABLED
    _WEB_SKILLS_ENABLED = enabled


def _filter_web_skills(payload: dict) -> dict:
    filtered = [s for s in payload.get("skills", []) if not s.get("skill_name", "").startswith("Web")]
    return {**payload, "skills": filtered}


# ====================================================================================================
# MARK: RUN STATE
# ====================================================================================================
_delegate_tls = get_delegate_runtime_tls()

# Stop event: set by /stoprun to request early termination of the active run.
_stop_event: threading.Event = threading.Event()

# Per-session stop events registered by each orchestrate_prompt call.
# Allows /stoprun to target only the active session rather than all concurrent runs.
# The global _stop_event is retained for callers that use is_stop_requested() directly.
_active_stop_events: dict[str, threading.Event] = {}
_active_stop_lock:   threading.Lock             = threading.Lock()


def request_stop() -> None:
    _stop_event.set()
    with _active_stop_lock:
        for ev in _active_stop_events.values():
            ev.set()


def is_stop_requested() -> bool:
    return _stop_event.is_set()


def clear_stop() -> None:
    _stop_event.clear()


@dataclass
class OrchestratorConfig:
    resolved_model: str
    num_ctx: int
    max_iterations: int
    skills_payload: dict
    skills_catalog_path: Path | None = None
    catalog_mtime: float = 0.0


# ====================================================================================================
class ConversationHistory:
    """Unbounded or capped store of user / assistant turn pairs.

    max_turns=0 (the default) means unlimited - turns accumulate without eviction.
    Any positive value caps the rolling window to that many complete rounds.
    """

    def __init__(self, max_turns: int = 0):
        self._max_turns = max_turns
        self._turns: list[dict] = []

    # ----------------------------------------------------------------------------------------------------

    def add(self, user: str, assistant: str) -> None:
        if len(self._turns) % 2 != 0:
            raise RuntimeError(
                f"ConversationHistory is misaligned - expected even turn count, "
                f"got {len(self._turns)}. A prior add() call is missing its assistant response."
            )
        self._turns.append({"role": "user",      "content": user})
        self._turns.append({"role": "assistant", "content": assistant})
        if self._max_turns > 0:
            cap = self._max_turns * 2
            if len(self._turns) > cap:
                self._turns = self._turns[-cap:]

    def clear(self) -> None:
        self._turns = []

    def as_list(self) -> list[dict]:
        """Return the history as a list suitable for passing to orchestrate_prompt."""
        return list(self._turns)

    def __len__(self) -> int:
        return len(self._turns) // 2   # number of complete turns

    def __bool__(self) -> bool:
        return bool(self._turns)


# ====================================================================================================
# MARK: SESSION CONTEXT
# ====================================================================================================
def _truncate_words(text: str, max_words: int) -> str:
    """Truncate *text* to at most *max_words* words, appending ' ...' when cut."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + " ..."


class SessionContext:
    """Structured per-session cache of skill outputs for cross-turn context injection.

    After each orchestration turn the raw skill outputs are distilled into a compact,
    token-efficient form and stored here.  On subsequent turns the last N turns' summaries
    are automatically injected into the final synthesis prompt so the LLM can reference
    prior fetched data (web pages, code output, file content) without re-running the skills.

    Optionally persisted to a JSON file (e.g. in progress/) so state survives restarts
    and scheduled tasks can optionally cross-load each other's context.
    """

    MAX_CONTENT_WORDS = 300   # max words stored per web-extract / file-read body
    MAX_INJECT_TURNS  = 2     # how many prior turns to include in each new prompt

    def __init__(self, session_id: str, persist_path: Path | None = None) -> None:
        self._session_id = session_id
        self._path       = persist_path
        self._lock       = threading.Lock()
        self._turns: list[dict] = []
        if persist_path and persist_path.exists():
            try:
                data = json.loads(persist_path.read_text(encoding="utf-8"))
                raw_turns = data.get("turns", [])
                if isinstance(raw_turns, list):
                    # API session history files also use a top-level "turns" key but store
                    # plain conversation pairs without the structured session-context schema.
                    # Ignore those entries here instead of crashing on missing keys like "turn".
                    valid_turns = [
                        turn for turn in raw_turns
                        if isinstance(turn, dict)
                        and "turn" in turn
                        and "user_prompt" in turn
                        and "assistant_response" in turn
                        and "skill_outputs" in turn
                    ]
                    dropped = len(raw_turns) - len(valid_turns)
                    if dropped:
                        log_to_session(f"[session_context] WARNING: {dropped} turn(s) dropped from {persist_path} - missing required keys")
                    self._turns = valid_turns
            except Exception as exc:
                log_to_session(f"[session_context] WARNING: could not load session context from {persist_path}: {exc} - starting with empty context")

    @property
    def session_id(self) -> str:
        return self._session_id

    # --------------------------------------------------------------------------

    def add_turn(
        self,
        user_prompt: str,
        assistant_response: str,
        skill_outputs: list[dict],
    ) -> None:
        """Append a completed turn with its compact skill-output summary."""
        compact = [self._compact_output(o) for o in skill_outputs]
        with self._lock:
            turn_num = len(self._turns) + 1
            self._turns.append({
                "turn":               turn_num,
                "user_prompt":        user_prompt,
                "assistant_response": assistant_response,
                "skill_outputs":      compact,
            })
            self._save()

    # --------------------------------------------------------------------------

    def clear(self) -> None:
        with self._lock:
            self._turns = []
            self._save()

    def turn_count(self) -> int:
        with self._lock:
            return len(self._turns)

    def get_turns(self) -> list[dict]:
        """Return a snapshot of all stored turns, safe for external inspection."""
        with self._lock:
            return list(self._turns)

    # --------------------------------------------------------------------------

    def as_inject_block(self, max_turns: int | None = None) -> str:
        """Return a text block for injection into the synthesis prompt.

        Covers the last *max_turns* turns (default: MAX_INJECT_TURNS).  Returns an
        empty string when there are no prior turns to inject.
        """
        n = max_turns if max_turns is not None else self.MAX_INJECT_TURNS
        with self._lock:
            recent = self._turns[-n:] if n else list(self._turns)
        if not recent:
            return ""

        parts = []
        for t in recent:
            lines = [f"Turn {t['turn']} | user: {trunc(t['user_prompt'], 100)}"]
            for o in t["skill_outputs"]:
                skill   = o.get("skill", "?")
                summary = o.get("summary", "")
                lines.append(f"  [{skill}] {summary}")
                for r in o.get("results", []):
                    snippet = trunc(r.get("snippet", ""), 80)
                    lines.append(f"    · {r.get('url', '')}  \"{r.get('title', '')}\"  {snippet}")
                if "content" in o:
                    lines.append(f"    {trunc(o['content'], 1500)}")
            parts.append("\n".join(lines))

        return "Prior turn skill context (for follow-up reference):\n\n" + "\n\n".join(parts)

    # --------------------------------------------------------------------------

    def _compact_output(self, output: dict) -> dict:
        """Distil a raw skill output dict to a compact, token-efficient summary."""
        tool_name = output.get("tool", "")
        module   = Path(output.get("module", "")).stem
        function = output.get("function", "?")
        args     = output.get("arguments", {}) or {}
        result   = output.get("result")

        entry: dict = {"skill": tool_name or f"{module}.{function}"}
        for key in ("query", "url", "path", "file_path", "domain", "topic"):
            if key in args:
                entry[key] = trunc(str(args[key]), 200)
                break

        if result is None:
            entry["summary"] = "(no result)"
        elif isinstance(result, list):
            items = []
            for item in result:
                if isinstance(item, dict):
                    title, url, snippet = _tool_loop_extract_result_fields(item)
                    items.append({
                        "url":     url,
                        "title":   title,
                        "snippet": _truncate_words(snippet, 50),
                    })
            entry["results"] = items
            entry["summary"] = f"{len(items)} result(s) returned"
        elif isinstance(result, dict):
            url  = result.get("url", "")
            text = result.get("text") or result.get("content") or result.get("result", "")
            if url:
                entry["url"] = url
            entry["content"] = _truncate_words(str(text), self.MAX_CONTENT_WORDS)
            entry["summary"] = f"text extracted ({self.MAX_CONTENT_WORDS} word limit)"
        elif isinstance(result, str):
            entry["content"] = _truncate_words(result, self.MAX_CONTENT_WORDS)
            entry["summary"] = f"text output ({len(result)} chars)"
        else:
            entry["summary"] = trunc(str(result), 200)

        return entry

    # --------------------------------------------------------------------------

    def _save(self) -> None:
        if not self._path:
            return
        tmp_path = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data    = {"session_id": self._session_id, "turns": self._turns}
            payload = json.dumps(data, indent=2, ensure_ascii=False)
            # Write atomically: write to a sibling temp file, then os.replace() so a
            # crash during the write leaves the previous file intact.
            tmp_path.write_text(payload, encoding="utf-8")
            os.replace(tmp_path, self._path)
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            log_to_session(f"[session_context] Warning: failed to persist context to {self._path}: {exc}")


# ====================================================================================================
# MARK: MODEL RESOLUTION
# ====================================================================================================
def resolve_execution_model(requested_model: str) -> str:
    """Resolve a short alias or tag to a fully-qualified model name available on the active server.

    For Ollama: matches against all installed models and prints a warning on fallback.
    For LM Studio: matches against the currently-served model(s). When no alias matches
    (the common case, since LM Studio uses verbose IDs), silently adopts the served model.

    If the requested name is already fully-qualified (contains ':' with no whitespace)
    it is returned as-is without querying the host.
    """
    # Already fully qualified - trust it; no need to query the server.
    if is_explicit_model_name(requested_model):
        return requested_model.strip()

    available_models = list_ollama_models()
    if not available_models:
        raise RuntimeError("No models are available on the inference server. Ensure the server is running and models are loaded.")

    resolved = resolve_model_name(requested_model, available_models)
    if resolved is None:
        fallback = available_models[0]
        if get_active_backend() == "lmstudio":
            # LM Studio serves whatever is loaded in the UI; alias matching is not meaningful.
            # Silently adopt the served model so startup is clean.
            print(f"[model] LM Studio is serving: '{fallback}' - using that.")
        else:
            print(
                f"[model] '{requested_model}' not found - falling back to '{fallback}'.\n"
                f"        Available: {', '.join(available_models)}"
            )
        return fallback

    return resolved


# ====================================================================================================
# MARK: ORCHESTRATION PIPELINE
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def orchestrate_prompt(
    user_prompt: str,
    config: OrchestratorConfig,
    logger: SessionLogger,
    conversation_history: list[dict] | None = None,
    session_context: "SessionContext | None" = None,
    quiet: bool = False,
    delegate_depth: int = 0,
    scratchpad_visible_keys: list[str] | None = None,
    conversation_summary: str | None = None,
    on_tool_round_complete: object | None = None,
    bound_session_id: str | None = None,
) -> tuple[str, int, int, bool, float]:
    """Run the tool-calling pipeline for one prompt.

    Sends the user message to /v1/chat/completions with JSON Schema tool definitions
    derived from the skills catalog. The model selects and calls tools; each result is
    fed back into the message thread until the model produces a plain-text final answer.

    Returns (final_response, prompt_tokens, completion_tokens, run_success, tokens_per_second).
    When quiet=True, verbose stages are written to the log file only.
    """
    def _log(msg: str = "") -> None:
        logger.log_file_only(msg) if quiet else logger.log(msg)

    def _log_section(title: str) -> None:
        logger.log_section_file_only(title) if quiet else logger.log_section(title)

    def _log_file_only(msg: str = "") -> None:
        logger.log_file_only(msg)

    def _log_section_file_only(title: str) -> None:
        logger.log_section_file_only(title)

    from KoreAgent.skills_catalog_builder import load_skills_payload

    user_prompt = resolve_tokens(user_prompt)
    active_session_id = (
        str(bound_session_id).strip()
        if bound_session_id is not None and str(bound_session_id).strip()
        else (session_context.session_id if session_context is not None else "default")
    )

    with bind_session(active_session_id):
        # -- Auto-reload catalog if the runtime JSON catalog has been updated since last load --
        if config.skills_catalog_path and config.skills_catalog_path.exists():
            current_mtime = config.skills_catalog_path.stat().st_mtime
            if current_mtime != config.catalog_mtime:
                config.skills_payload  = load_skills_payload(config.skills_catalog_path)
                config.catalog_mtime   = current_mtime
                logger.log_file_only("[catalog] skills catalog reloaded (file changed on disk)")

        _log_section("ORCHESTRATION RUN")
        _log(f"Model:          {config.resolved_model}")
        _log(f"Context window: {config.num_ctx:,} tokens")
        _log(f"Max rounds:     {config.max_iterations}")
        _log(f"Prompt:         {user_prompt[:300]}{' ...' if len(user_prompt) > 300 else ''}")

        # -- Memory --
        _log_file_only("[progress] Storing prompt memories...")
        # Persist any facts in the prompt before tool calls run, so the turn is captured even on failure.
        # Skip for slash commands and very short prompts - they cannot contain storable facts.
        if user_prompt.startswith("/") or len(user_prompt.split()) < 4:
            memory_store_result = "Memory storage skipped."
        else:
            memory_store_result = store_prompt_memories(user_prompt=user_prompt)
        _log_file_only("[progress] Recalling relevant memories...")
        recalled_memories = recall_relevant_memories(user_prompt=user_prompt, limit=5, min_score=0.2)
        top_facts         = get_top_facts(limit=6)

        _log_section("MEMORY")
        _log(memory_store_result)
        _log(recalled_memories)
        if top_facts:
            _log_file_only(f"[memory] Top facts injected ({top_facts.count(chr(10)) + 1} entries).")

        ambient_system_info = get_static_system_info_string()
        _log_section("AMBIENT SYSTEM INFO")
        _log(ambient_system_info)

        active_payload = config.skills_payload if _WEB_SKILLS_ENABLED else _filter_web_skills(config.skills_payload)

        tool_defs = build_tool_definitions(active_payload)
        mcp_defs  = _mcp_client.get_mcp_tool_definitions()
        if mcp_defs:
            tool_defs = tool_defs + mcp_defs
        _log_file_only(f"[progress] Tool definitions built: {len(tool_defs)} tools available.")

        system_message = _prompt_builder_build_system_message(
            ambient_system_info,
            session_context,
            active_payload,
            skill_guidance_enabled=_SKILL_GUIDANCE_ENABLED,
            sandbox_enabled=_SANDBOX_ENABLED,
            scratchpad_visible_keys=scratchpad_visible_keys,
            conversation_summary=conversation_summary,
            top_facts=top_facts or None,
            recalled_memories=recalled_memories if "No" not in recalled_memories[:3] else None,
        )

        messages: list[dict] = [{"role": "system", "content": system_message}]
        _context_map: list[dict] = [
            {"round": 0, "role": "sys", "label": "system prompt", "chars": len(system_message), "auto_key": None, "msg_idx": 0},
        ]
        if conversation_history:
            _hist_start = len(messages)
            _hist_chars = sum(len(m.get("content") or "") for m in conversation_history)
            _context_map.append({"round": 0, "role": "hist", "label": f"history ({len(conversation_history)} msgs)", "chars": _hist_chars, "auto_key": None, "msg_idx": _hist_start, "msg_idx_end": _hist_start + len(conversation_history) - 1})
            messages.extend(conversation_history)
        messages.append({"role": "user", "content": user_prompt})
        _context_map.append({"round": 0, "role": "user", "label": trunc(user_prompt, 50), "chars": len(user_prompt), "auto_key": None, "msg_idx": len(messages) - 1})

        _prev_delegate_runtime = push_delegate_runtime(logger=logger, delegate_depth=delegate_depth, config=config)
        catalog_gates = build_catalog_gates(active_payload)

        # Register a per-run stop event so that /stoprun only affects this session.
        _run_id         = f"{active_session_id}_{id(messages)}"
        _run_stop_event = threading.Event()
        with _active_stop_lock:
            _active_stop_events[_run_id] = _run_stop_event

        try:
            final_response, prompt_tokens, completion_tokens, run_success, final_tps, tool_outputs = _tool_loop_run_tool_loop(
                config        = config,
                messages      = messages,
                tool_defs     = tool_defs,
                catalog_gates = catalog_gates,
                context_map   = _context_map,
                user_prompt   = user_prompt,
                logger        = logger,
                quiet         = quiet,
                call_llm_chat = call_llm_chat,
                stop_requested = _run_stop_event.is_set,
                clear_stop    = _run_stop_event.clear,
                on_tool_round_complete = on_tool_round_complete,
            )

            _file_blocks_written = _tool_loop_write_file_blocks(final_response, log_to_session=log_to_session) if final_response else []
            if _file_blocks_written:
                _log_file_only(f"[file-blocks] Wrote {len(_file_blocks_written)} file(s): {', '.join(_file_blocks_written)}")

            _log_section_file_only("TOOL CALL SUMMARY")
            _log_file_only(_tool_loop_format_tool_outputs(tool_outputs))
            _log_section_file_only("CONTEXT MAP")
            _log_file_only(_context_manager_format_context_map(_context_map, config.num_ctx))
            _log_section_file_only("SCRATCHPAD STATE")
            _log_file_only(_scratch_list())
            _log(f"Total: {prompt_tokens:,} prompt tokens | {completion_tokens:,} completion tokens")

            # Store facts from the assistant response side of the exchange.
            # This captures agent-confirmed actions and entities stated in the reply.
            if final_response and run_success and not user_prompt.startswith("/"):
                _log_file_only("[progress] Storing exchange memories (response side)...")
                exchange_result = store_exchange_memories(
                    user_prompt        = user_prompt,
                    assistant_response = final_response,
                )
                _log_file_only(f"[memory] {exchange_result}")

            store_last_run_state(_context_map, messages)

            if session_context is not None and run_success and tool_outputs:
                session_context.add_turn(
                    user_prompt=user_prompt,
                    assistant_response=final_response,
                    skill_outputs=tool_outputs,
                )

            return final_response, prompt_tokens, completion_tokens, run_success, final_tps
        finally:
            with _active_stop_lock:
                _active_stop_events.pop(_run_id, None)
            pop_delegate_runtime(_prev_delegate_runtime)


# ====================================================================================================
# MARK: DELEGATE SUBRUN
# ====================================================================================================
# Core implementation of the Delegate orchestration primitive.
#
# Creates an isolated child orchestration context for a focused sub-task, runs the normal
# tool-calling pipeline inside it, and returns a compact result dict to the caller.
#
# Accessed by the Delegate skill wrapper (code/skills/Delegate/delegate_skill.py) and also
# callable directly from orchestration code that needs to spawn a sub-run programmatically.
#
# Guard rails enforced here:
#   - Maximum delegation depth: _MAX_DELEGATE_DEPTH
#   - Child loses access to the Delegate tool by default (allow_recursive_delegate=False)
#   - Child does not inherit parent conversation history or session context
#   - Child iteration budget capped at 8 rounds

# ----------------------------------------------------------------------------------------------------
def delegate_subrun(
    prompt: str,
    instructions: str = "",
    max_iterations: int = 3,
    allow_recursive_delegate: bool = False,
    output_key: str = "",
    scratchpad_visible_keys: list[str] | None = None,
    tools_allowlist: list[str] | None = None,
) -> dict:
    """Run a child orchestration context for one isolated sub-task."""
    return run_delegate_subrun(
        prompt=prompt,
        instructions=instructions,
        max_iterations=max_iterations,
        allow_recursive_delegate=allow_recursive_delegate,
        output_key=output_key,
        scratchpad_visible_keys=scratchpad_visible_keys,
        tools_allowlist=tools_allowlist,
        orchestrate_prompt_fn=orchestrate_prompt,
        config_cls=OrchestratorConfig,
    )

