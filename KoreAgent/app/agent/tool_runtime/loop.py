# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Tool-calling loop: drives the multi-turn LLM <-> skill conversation for one orchestration round.
#
# The main entry point is run_tool_loop(), called by orchestration.py.  Each iteration:
#   1. Builds the user message from conversation history and scratchpad context
#   2. Calls the LLM (call_llm_chat) with available tool definitions
#   3. Parses any tool_calls from the response
#   4. Validates and executes each call via skill_executor.execute_tool_call()
#   5. Injects tool results back into the message thread
#   6. Loops until the model returns a plain text response (no tool calls)
#
# Large tool results are auto-saved to scratchpad and truncated in the thread.
# Compaction is assessed after each round and triggered when context fill is high.
#
# Related modules:
#   - skill_executor.py   -- execute_tool_call dispatches to the correct skill function
#   - context_manager.py  -- assess_compact, compact_context, store_last_run_state
#   - orchestration.py    -- calls run_tool_loop() for each conversation turn
#   - scratchpad.py       -- auto-saves large tool results
# MARK: FUNCTIONS
# Function inventory:
# - _build_data_envelope: Implements the  build data envelope operation for this module.
# - _safe_scratch_component: Implements the  safe scratch component operation for this module.
# - _derive_auto_scratchpad_key: Implements the  derive auto scratchpad key operation for this module.
# - normalize_tool_request: Normalizes tool request for this module.
# - extract_result_fields: Extracts result fields for this module.
# - format_tool_outputs: Formats tool outputs for this module.
# - build_fallback_answer: Builds fallback answer for this module.
# - _is_textual_tool_call_attempt: Detects textual tool call attempts for this module.
# - _compact_tool_name_list: Implements the  compact tool name list operation for this module.
# - _classify_tool_recovery: Implements the  classify tool recovery operation for this module.
# - _build_tool_recovery_message: Implements the  build tool recovery message operation for this module.
# - _build_tool_recovery_reminder: Implements the  build tool recovery reminder operation for this module.
# - strip_cot_preamble: Implements the strip cot preamble operation for this module.
# - run_tool_loop: Runs tool loop for this module.
# - _log: Implements the  log operation for this module.
# - _log_section: Implements the  log section operation for this module.
# - _log_file_only: Implements the  log file only operation for this module.
# ====================================================================================================
import json
import re

from agent.orchestration.context_window import choose_context_window
from context_manager import COMPACT_THRESHOLD
from context_manager import assess_compact
from working_data import auto_route_working_data_result
from working_data import working_data_save as working_data_auto_save
from working_data import working_data_pin
from working_data import working_data_unpin_all
from skill_executor import execute_tool_call
from tool_result import ToolCallResult
from utils.workspace_utils import trunc


# Cap for tool result content in messages; longer content is auto-saved to scratchpad and truncated in the message with a reference note
TOOL_MSG_MAX_CHARS: int = 4096

# Tool results at or above this length are auto-saved to scratchpad before being injected
# into the thread.  Keeping this low means more results are available for later retrieval
# even after their thread message is compacted.
TOOL_MSG_AUTO_WORKING_DATA_MIN: int = 200

_DATA_TOOL_SOURCE: dict[str, str] = {
    "koredata_get_reference_article": "KoreReference",
    "koredata_get_feed_entry": "KoreFeed",
    "koredata_get_library_book": "KoreLibrary",
    "koredata_get_rag_chunk": "KoreRAG",
    "lookup_wikipedia": "Wikipedia",
    "fetch_page_text": "WebFetch",
    "fetch_page_text_text": "WebFetch",
    "search_web": "WebSearch",
    "search_web_text": "WebSearch",
}

def _build_data_envelope(func_name: str, arguments: dict, result_content: str) -> str:
    """Prepend a compact structured header to results from known data-sourcing tools.

    The header gives the LLM clear provenance (source service, query, result count)
    without relying on it parsing the raw payload to infer context.
    Results from unknown/non-data tools are returned unchanged.
    """
    fn = func_name.lower()
    if fn.startswith("koredata_search"):
        source = "KoreData"
    else:
        source = _DATA_TOOL_SOURCE.get(fn)
    if source is None:
        return result_content

    query = (
        arguments.get("query")
        or arguments.get("topic")
        or arguments.get("title")
        or arguments.get("url")
        or ""
    )
    query_part = f' | query: "{str(query)[:60]}"' if query else ""

    # Try to extract a result count from JSON payload.
    result_count_part = ""
    try:
        parsed = json.loads(result_content)
        if isinstance(parsed, list):
            result_count_part = f" | results: {len(parsed)}"
        elif isinstance(parsed, dict):
            results = parsed.get("results")
            if isinstance(results, list):
                result_count_part = f" | results: {len(results)}"
            elif "title" in parsed and "body" in parsed:
                word_count = parsed.get("word_count") or len((parsed.get("body") or "").split())
                result_count_part = f" | article: \"{str(parsed['title'])[:50]}\" | ~{word_count:,} words"
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    header = f"[SOURCE: {source}{query_part}{result_count_part}]\n"
    return header + result_content


_COT_PLANNING_RE = re.compile(
    r"\b(?:we should|we can|we need|we will|we could|we\'ll|we\'re|we must|"
    r"let me|let\'s|let us|thus we|so we|now we|next we|i need|i should|i will|i\'ll|"
    r"provide an?\b|provide the\b|need to |should |we want|we are going|"
    r"maybe |perhaps )",
    re.IGNORECASE,
)
_CONTENT_MARKER_RE = re.compile(r"(?:^|\n)(\*\*|#{1,3} |\| |\d+\. |- )")
_WORKING_DATA_KEY_SAFE_RE = re.compile(r"[^a-z0-9_]+")

def _safe_working_data_component(value: object, fallback: str = "x") -> str:
    cleaned = _WORKING_DATA_KEY_SAFE_RE.sub("_", str(value or "").strip().lower()).strip("_")
    return cleaned[:40] or fallback


def _derive_auto_working_data_key(func_name: str, arguments: dict, round_num: int, tool_ordinal: int) -> str:
    normalized_name = str(func_name or "").strip().lower()
    if normalized_name == "working_data_get":
        item_name = _safe_working_data_component(arguments.get("name"), "item")
        indices = arguments.get("indices")
        selector = "page"
        if isinstance(indices, list) and indices:
            int_indices = [index for index in indices if isinstance(index, int)]
            if int_indices:
                if len(int_indices) == 1:
                    selector = f"i{int_indices[0]}"
                else:
                    selector = f"i{int_indices[0]}_{int_indices[-1]}_{len(int_indices)}"
            else:
                selector = "indices"
        else:
            offset = max(0, int(arguments.get("offset") or 0)) if str(arguments.get("offset") or "").strip() else 0
            limit = arguments.get("limit") or arguments.get("max_records") or 20
            try:
                limit = max(0, int(limit)) or 20
            except (TypeError, ValueError):
                limit = 20
            selector = f"o{offset}_l{limit}"
        fields = arguments.get("fields")
        if isinstance(fields, list) and fields:
            field_fragment = "_".join(_safe_working_data_component(field) for field in fields[:3])
            if field_fragment:
                selector += f"_f{field_fragment}"
        return f"_working_data_get_{item_name}_{selector}"

    safe_name = normalized_name[:24]
    return f"_wd_r{round_num}_{tool_ordinal}_{safe_name}"


def normalize_tool_request(func_name: str, arguments: dict | None) -> tuple[str, dict, str | None]:
    normalized_args = dict(arguments or {})
    normalized_name = func_name
    note_parts: list[str] = []
    if normalized_name == "assistant":
        nested_name = str(normalized_args.get("name") or "").strip()
        nested_args = normalized_args.get("arguments")
        if nested_name and isinstance(nested_args, dict):
            normalized_name = nested_name
            normalized_args = dict(nested_args)
            note_parts.append(f"assistant(...) -> {nested_name}(...)")
    # Handle model wrapping a tool call in its own function-call envelope:
    # e.g. get_page_links(id='functions.get_page_links', arguments={...})
    nested_args = normalized_args.get("arguments")
    if isinstance(nested_args, dict) and "id" in normalized_args and len(normalized_args) == 2:
        normalized_args = dict(nested_args)
        note_parts.append(f"{normalized_name}(id=..., arguments={{...}}) -> {normalized_name}(...)")
    return normalized_name, normalized_args, "; ".join(note_parts) if note_parts else None


def extract_result_fields(item: dict) -> tuple[str, str, str]:
    return item.get("title", ""), item.get("url", ""), item.get("snippet") or item.get("body", "")


def format_tool_outputs(tool_outputs: list[ToolCallResult]) -> str:
    if not tool_outputs:
        return "(no tool calls executed)"
    lines: list[str] = []
    for output in tool_outputs:
        tool_name = output.get("tool", "")
        module = Path(output.get("module", "")).stem
        function = output.get("function", "?")
        args = output.get("arguments", {}) or {}
        result = output.get("result")
        heading = f"{tool_name} -> {module}.{function}()" if tool_name else f"{module}.{function}()"
        lines.append(heading)
        for key, value in args.items():
            lines.append(f"  {key} = {trunc(repr(value), 120)}")
        if result is None:
            lines.append("  -> None")
        elif isinstance(result, str):
            stripped = result.strip()
            preview_lines = stripped.splitlines()[:50]
            total_lines = stripped.count("\n") + 1
            lines.append(f"  -> str  {len(result)} chars / {total_lines} lines")
            for line in preview_lines:
                lines.append(f"  {trunc(line, 110)}")
            if total_lines > 50:
                lines.append(f"  ... ({total_lines - 50} more lines)")
        elif isinstance(result, dict):
            lines.append(f"  -> dict  [{', '.join(str(key) for key in result.keys())}]")
        elif isinstance(result, list):
            lines.append(f"  -> list  len={len(result)}")
            for item in result:
                if isinstance(item, dict):
                    title, url, snippet = extract_result_fields(item)
                    if title:
                        lines.append(f"  {trunc(title, 80)}")
                    if url:
                        lines.append(f"    {url}")
                    if snippet:
                        lines.append(f"    {trunc(snippet, 110)}")
        else:
            lines.append(f"  -> {type(result).__name__}: {trunc(str(result), 110)}")
        lines.append("")
    return "\n".join(lines)


def build_fallback_answer(user_prompt: str, tool_outputs: list[ToolCallResult]) -> str:
    lines = [
        f"(Note: the model did not produce a synthesized answer for: \"{trunc(user_prompt, 80)}\")",
        "Raw tool results follow:",
        "",
    ]
    for output in tool_outputs:
        tool_name = output.get("tool", "") or output.get("function", "unknown")
        args = output.get("arguments", {}) or {}
        result = output.get("result")
        lines.append(f"[{tool_name}({', '.join(f'{k}={v!r}' for k, v in args.items())})]")
        if isinstance(result, list):
            for item in result:
                if isinstance(item, dict):
                    title, url, snippet = extract_result_fields(item)
                    if title:
                        lines.append(f"  - {title}")
                    if url:
                        lines.append(f"    {url}")
                    if snippet:
                        lines.append(f"    {trunc(str(snippet), 200)}")
                else:
                    lines.append(f"  {trunc(str(item), 200)}")
        elif isinstance(result, dict):
            for key, value in result.items():
                lines.append(f"  {key}: {trunc(str(value), 200)}")
        elif isinstance(result, str):
            for line in result.splitlines()[:20]:
                lines.append(f"  {line}")
            if result.count("\n") >= 20:
                lines.append("  ...")
        elif result is not None:
            lines.append(f"  {trunc(str(result), 400)}")
        lines.append("")
    return "\n".join(lines).strip()


def _is_textual_tool_call_attempt(text: str, active_tool_names: set[str]) -> bool:
    """Return True for a whole-message attempt to express an active tool call as text.

    This deliberately identifies the bad protocol without interpreting it.  Tool calls
    must arrive in the provider's native structured ``tool_calls`` field; free-form
    model output is never converted into an executable request.
    """
    stripped = (text or "").strip()
    if not stripped:
        return False

    function_match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*\([\s\S]*\)", stripped)
    if function_match and function_match.group(1) in active_tool_names:
        return True

    # Do not parse or trust the JSON.  This is only a narrow protocol-violation
    # detector for the usual {"tool": "name", ...} / {"name": "name", ...} form.
    json_match = re.fullmatch(
        r'\{\s*"(?:tool|name|function)"\s*:\s*"([A-Za-z_][A-Za-z0-9_]*)"[\s\S]*\}',
        stripped,
    )
    return bool(json_match and json_match.group(1) in active_tool_names)


def _compact_tool_name_list(tool_names: set[str] | list[str] | tuple[str, ...] | None, *, limit: int = 10) -> str:
    names = sorted({str(name or "").strip() for name in (tool_names or []) if str(name or "").strip()})
    if not names:
        return "(none)"
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f", ... (+{len(names) - limit} more)"


def _classify_tool_recovery(
    requested_tool_name: str,
    *,
    active_tool_names: set[str] | None = None,
    all_known_tool_names: set[str] | None,
) -> dict[str, object]:
    requested = str(requested_tool_name or "").strip()
    active_names = set(active_tool_names or set())
    known_names = set(all_known_tool_names or set())
    if not requested:
        return {"classification": "unknown_name", "requested_tool": requested, "active_tool_names": sorted(active_names)}

    if requested in known_names:
        return {
            "classification": "active_known" if requested in active_names else "inactive_known",
            "requested_tool": requested,
            "active_tool_names": sorted(active_names),
        }

    return {
        "classification": "unknown_name",
        "requested_tool": requested,
        "active_tool_names": sorted(active_names),
    }


def _build_tool_recovery_message(event: dict[str, object]) -> str:
    classification = str(event.get("classification") or "unknown_name")
    requested = str(event.get("requested_tool") or "").strip()
    active_names = event.get("active_tool_names")
    active_summary = _compact_tool_name_list(active_names if isinstance(active_names, list) else [])

    if classification == "inactive_known":
        if event.get("auto_activated"):
            return (
                f"Recovery required: tool `{requested}` exists in the runtime catalog but was not active for this conversation.\n"
                f"It has been added to the active tool set for this conversation.\n"
                "Do not answer the user yet.\n"
                f"Retry the same tool name now: `{requested}`.\n"
                f"Currently active tools: {active_summary}"
            )
        return (
            f"Recovery required: tool `{requested}` exists in the runtime catalog but is not active for this conversation.\n"
            "Do not answer the user yet.\n"
            "Use ToolSelection now.\n"
            f"Call `tools_active_add([\"{requested}\"])`, then continue the task.\n"
            f"Currently active tools: {active_summary}"
        )

    return (
        f"Recovery required: requested tool `{requested}` is not a valid tool name in this runtime.\n"
        "Do not answer the user yet.\n"
        "Use ToolSelection now.\n"
        "Call `skills_list()` and select the correct Skill, or activate the exact tool, then continue the task.\n"
        f"Currently active tools: {active_summary}"
    )


def _build_tool_recovery_reminder(event: dict[str, object]) -> str:
    classification = str(event.get("classification") or "unknown_name")
    requested = str(event.get("requested_tool") or "").strip()
    if classification == "inactive_known" and event.get("auto_activated"):
        return f"Recovery still required: do not answer yet. Retry `{requested}` now; it is already active for this conversation."
    return f"Recovery still required: do not answer yet. Inspect the full tool catalog or Skill list and choose the exact capability needed for `{requested}`."


# ----------------------------------------------------------------------------------------------------
def strip_cot_preamble(text: str) -> str:
    if not text:
        return text
    stripped_start = text.lstrip("\n")
    if stripped_start[:2] in ("**", "# ", "##", "| ") or (stripped_start and stripped_start[0] in "#|"):
        return text
    marker = _CONTENT_MARKER_RE.search(text)
    if not marker:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text.strip()) if part.strip()]
        if len(paragraphs) >= 2:
            last_para = paragraphs[-1]
            prior_text = "\n\n".join(paragraphs[:-1])
            if _COT_PLANNING_RE.search(prior_text) and not _COT_PLANNING_RE.search(last_para):
                return last_para
        return text
    split_pos = marker.start()
    if text[split_pos] == "\n":
        split_pos += 1
    preamble = text[:split_pos]
    if preamble.strip() and _COT_PLANNING_RE.search(preamble):
        return text[split_pos:].lstrip("\n")
    return text


def run_tool_loop(
    *,
    config,
    messages: list[dict],
    tool_defs: list[dict],
    catalog_gates: dict,
    active_tool_names: set[str] | None = None,
    context_map: list[dict],
    user_prompt: str,
    logger,
    quiet: bool,
    call_llm_chat,
    stop_requested,
    clear_stop,
    tool_runtime_provider: object | None = None,
    on_tool_round_complete: object | None = None,
    on_token: object | None = None,
) -> tuple[str, int, int, bool, float, list[ToolCallResult]]:
    def _log(message: str = "") -> None:
        logger.log_file_only(message) if quiet else logger.log(message)

    def _log_section(title: str) -> None:
        logger.log_section_file_only(title) if quiet else logger.log_section(title)

    def _log_file_only(message: str = "") -> None:
        logger.log_file_only(message)

    tool_outputs: list[ToolCallResult] = []
    prompt_tokens = 0
    completion_tokens = 0
    final_tps = 0.0
    run_success = False
    final_response = ""
    prev_round_tc_fingerprints: frozenset = frozenset()
    recovery_pending: dict[str, object] | None = None
    textual_tool_call_corrections = 0
    clear_stop()
    try:
        for round_num in range(1, config.max_iterations + 1):
            current_tool_defs = tool_defs
            current_catalog_gates = catalog_gates
            current_active_tool_names = set(active_tool_names or set()) if active_tool_names is not None else None
            current_all_known_tool_names = set(current_active_tool_names or set())
            if tool_runtime_provider is not None:
                runtime = tool_runtime_provider() or {}
                current_tool_defs = runtime.get("tool_defs", current_tool_defs)
                current_catalog_gates = runtime.get("catalog_gates", current_catalog_gates)
                current_active_tool_names = set(runtime.get("active_tool_names", current_active_tool_names) or set())
                current_all_known_tool_names = set(runtime.get("all_known_tool_names", current_all_known_tool_names) or set())
                missing_selected = list(runtime.get("missing_selected", []) or [])
                if missing_selected:
                    missing_names = ", ".join(missing_selected)
                    correction = (
                        f"Previously selected tool(s) are no longer present in the current runtime inventory: {missing_names}. "
                        "They were removed from the active set. Inspect the tool catalog and choose another tool if you still need that capability."
                    )
                    messages.append({"role": "user", "content": correction})
                    context_map.append({"round": round_num, "role": "user", "label": "[missing tool correction]", "chars": len(correction), "auto_key": None, "msg_idx": len(messages) - 1})
            if stop_requested():
                clear_stop()
                _log(f"[/stoprun] Stop requested - halting before round {round_num}.")
                final_response = "[Run stopped by /stoprun. The previous response may be incomplete.]"
                break

            _log_section(f"TOOL ROUND {round_num}")
            _log_file_only(f"[progress] Round {round_num}: calling model...")
            thread_chars, compact_count = assess_compact(context_map, messages, round_num, config.num_ctx, save_fn=working_data_auto_save)
            if compact_count:
                _log_file_only(f"[context] compacted {compact_count} message(s) (threshold {COMPACT_THRESHOLD:.0%} exceeded)")
            _log_file_only(f"[context] thread: {thread_chars:,} chars (~{thread_chars // 4:,} tok est.) | window: {config.num_ctx:,} | remaining est.: ~{config.num_ctx - thread_chars // 4:,}")

            try:
                request_num_ctx = choose_context_window(
                    config.num_ctx,
                    messages,
                    current_tool_defs if current_tool_defs else None,
                )
                _log_file_only(f"[context] request window: {request_num_ctx:,} / {config.num_ctx:,} tokens")
                result = call_llm_chat(
                    model_name = config.resolved_model,
                    messages   = messages,
                    tools      = current_tool_defs if current_tool_defs else None,
                    num_ctx    = request_num_ctx,
                    on_token  = on_token,
                )
            except Exception as error:
                error_str = str(error)
                if "error parsing tool call" in error_str:
                    correction = (
                        "Your previous tool call could not be executed because the argument JSON was truncated or malformed. "
                        "Do not embed large multi-line strings directly in a tool call argument. Instead: (1) build the content using "
                        "code_execute and print() it, (2) save the output with working_data_save, then (3) pass the Working Data reference to the destination tool."
                    )
                    _log(f"[error] Tool call JSON parse error in round {round_num} - injecting correction message.")
                    messages.append({"role": "user", "content": correction})
                    context_map.append({"round": round_num, "role": "user", "label": "[tool-call correction injected]", "chars": len(correction), "auto_key": None, "msg_idx": len(messages) - 1})
                    continue
                _log(f"[error] LLM call failed in round {round_num}: {error}")
                final_response = f"(LLM call failed: {error})"
                break

            prompt_tokens += result.prompt_tokens
            completion_tokens += result.completion_tokens
            final_tps = result.tokens_per_second
            _log(f"Round {round_num} TPS: {final_tps:.1f} tok/s  ({result.completion_tokens} completion | {result.prompt_tokens:,} prompt tokens)")
            _log_file_only(f"[context] actual prompt tokens used: {result.prompt_tokens:,} | remaining: ~{config.num_ctx - result.prompt_tokens:,}")
            thinking = (result.message.get("thinking") or result.message.get("reasoning") or "").strip()
            if thinking:
                _log_file_only(f"[thinking]\n{thinking}\n[/thinking]")

            tool_calls = list(result.tool_calls or [])

            if not tool_calls:
                candidate = strip_cot_preamble(result.response)
                active_names = {
                    str(tool_def.get("function", {}).get("name") or "").strip()
                    for tool_def in current_tool_defs
                }
                active_names.discard("")
                if _is_textual_tool_call_attempt(candidate, active_names):
                    if textual_tool_call_corrections < 1:
                        correction = (
                            "You attempted to express a tool call as ordinary text. That did not execute anything. "
                            "Do not print JSON or function-call syntax. Use the native structured tool-call interface for the required active tool now."
                        )
                        textual_tool_call_corrections += 1
                        _log_file_only(f"[warn] Round {round_num}: rejected textual tool-call attempt; requesting a native structured call.")
                        messages.append({"role": "user", "content": correction})
                        context_map.append({"round": round_num, "role": "user", "label": "[native tool-call correction]", "chars": len(correction), "auto_key": None, "msg_idx": len(messages) - 1})
                        continue
                    final_response = "I could not complete that action because the model did not issue a valid native tool call."
                    run_success = False
                    _log(final_response)
                    _log_file_only(f"[progress] Round {round_num}: stopped after repeated textual tool-call attempt.")
                    messages.append({"role": "assistant", "content": final_response})
                    context_map.append({"round": round_num, "role": "asst", "label": "native tool-call protocol failure", "chars": len(final_response), "auto_key": None, "msg_idx": len(messages) - 1})
                    break
                elif recovery_pending is not None:
                    reminders_sent = int(recovery_pending.get("reminders_sent") or 0)
                    if reminders_sent < 1:
                        reminder = _build_tool_recovery_reminder(recovery_pending)
                        recovery_pending["reminders_sent"] = reminders_sent + 1
                        prev_round_tc_fingerprints = frozenset()
                        _log_file_only(f"[warn] Round {round_num}: model attempted to finish while tool recovery was still pending - injecting reminder.")
                        messages.append({"role": "user", "content": reminder})
                        context_map.append({"round": round_num, "role": "user", "label": "[tool recovery reminder]", "chars": len(reminder), "auto_key": None, "msg_idx": len(messages) - 1})
                        continue
                    recovery_pending = None
                else:
                    final_response = candidate
                    run_success = bool(final_response)
                    _log(final_response)
                    _log_file_only(f"[progress] Round {round_num}: model gave final answer.")
                    messages.append({"role": "assistant", "content": final_response})
                    context_map.append({"round": round_num, "role": "asst", "label": "final answer", "chars": len(final_response), "auto_key": None, "msg_idx": len(messages) - 1})
                    break

            _log(f"Round {round_num}: model requested {len(tool_calls)} tool call(s).")
            _log_file_only("[progress] Executing tool calls...")
            if recovery_pending is not None:
                recovery_pending = None
            current_tc_fingerprints = frozenset((tc.get("function", {}).get("name", ""), tc.get("function", {}).get("arguments", "{}")) for tc in tool_calls)
            if current_tc_fingerprints and current_tc_fingerprints == prev_round_tc_fingerprints:
                correction = (
                    "You have requested the exact same tool call(s) as the previous round. "
                    "The results will not change. Please use the information you already have "
                    "to answer the question, or try a different approach (different query, different tool, or synthesize an answer from existing results)."
                )
                _log(f"[warn] Round {round_num}: identical tool calls repeated from previous round - injecting correction.")
                messages.append({"role": "user", "content": correction})
                context_map.append({"round": round_num, "role": "user", "label": "[duplicate tool-call correction]", "chars": len(correction), "auto_key": None, "msg_idx": len(messages) - 1})
                prev_round_tc_fingerprints = frozenset()
                continue
            prev_round_tc_fingerprints = current_tc_fingerprints

            # Strip planning text when tool calls are present - the spec allows empty content
            # alongside tool_calls, and the planning prose adds tokens to every subsequent round
            # without providing information the model needs.
            assistant_content = "" if tool_calls else (result.response or "")
            messages.append({"role": "assistant", "content": assistant_content, "tool_calls": tool_calls})
            context_map.append({"round": round_num, "role": "asst", "label": f"(tool calls x{len(tool_calls)})", "chars": len(assistant_content), "auto_key": None, "msg_idx": len(messages) - 1})

            round_outputs: list[ToolCallResult] = []
            round_recovery_events: list[dict[str, object]] = []
            for tc_idx, tool_call in enumerate(tool_calls):
                tc_id = tool_call.get("id", "")
                tc_func = tool_call.get("function", {})
                func_name = tc_func.get("name", "")
                raw_args = tc_func.get("arguments", "{}")
                try:
                    arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except json.JSONDecodeError as exc:
                    _log(f"  [warn] Could not parse arguments for {func_name}: {exc} - raw: {raw_args!r}")
                    error_content = f"[SKILL_ERROR] Malformed tool call - could not parse JSON arguments for {func_name}: {exc}"
                    error_output = ToolCallResult(tool=func_name, function=func_name, module="", arguments={}, result=error_content, status="error", error=str(exc))
                    round_outputs.append(error_output)
                    tool_outputs.append(error_output)
                    messages.append({"role": "tool", "tool_call_id": tc_id, "name": func_name, "content": error_content})
                    context_map.append({"round": round_num, "role": "tool", "label": func_name, "chars": len(error_content), "auto_key": None, "msg_idx": len(messages) - 1})
                    continue
                func_name, arguments, normalization_note = normalize_tool_request(func_name, arguments)
                _log(f"  -> {func_name}({', '.join(f'{k}={v!r}' for k, v in arguments.items())})")
                if normalization_note:
                    _log_file_only(f"[tool-normalize] {normalization_note}")
                output                = None
                recovery_event        = None
                auto_dataset_manifest = None
                try:
                    output = execute_tool_call(func_name, arguments, config.skills_payload, user_prompt, current_catalog_gates, current_active_tool_names)
                except Exception as exc:
                    recovery_event = _classify_tool_recovery(
                        func_name,
                        active_tool_names=current_active_tool_names,
                        all_known_tool_names=current_all_known_tool_names,
                    )
                    if recovery_event is not None:
                        recovery_event["active_tool_names"] = sorted(current_active_tool_names or set())
                        if recovery_event.get("classification") != "active_known":
                            round_recovery_events.append(recovery_event)
                        result_content = f"[SKILL_ERROR] Error executing {func_name}: {exc}"
                        output = ToolCallResult(tool=func_name, function=func_name, module="", arguments=arguments, result=result_content, status="error", error=str(exc))

                raw_result_content = output["result"]
                if not output.get("is_error"):
                    try:
                        auto_dataset_manifest = auto_route_working_data_result(func_name, arguments, raw_result_content)
                    except Exception as exc:
                        _log_file_only(f"[dataset-auto-route] skipped for {func_name}: {exc}")
                result_content = auto_dataset_manifest or raw_result_content
                if not isinstance(result_content, str):
                    result_content = json.dumps(result_content, default=str)
                if output.get("is_error"):
                    result_content = f"[SKILL_ERROR] {result_content}"

                is_working_data_reader = func_name.lower().startswith("working_data_")
                auto_working_data_key = None
                preserve_exact_python_output = func_name.lower() == "python_execute"
                if not output.get("is_error") and not is_working_data_reader and isinstance(result_content, str) and (len(result_content) >= TOOL_MSG_AUTO_WORKING_DATA_MIN or preserve_exact_python_output) and not auto_dataset_manifest:
                    auto_working_data_key = _derive_auto_working_data_key(func_name, arguments, round_num, tc_idx + 1)
                    working_data_auto_save(auto_working_data_key, result_content)
                    working_data_pin(auto_working_data_key)

                # Build thread content: add provenance envelope for data tools, then truncate.
                # The Working Data copy keeps the raw content available outside the prompt.
                thread_content = _build_data_envelope(func_name, arguments, result_content) if not output.get("is_error") else result_content
                if output.get("is_error"):
                    lowered_error = result_content.lower()
                    if "path escapes data directory" in lowered_error or "not allowed" in lowered_error:
                        thread_content += "\n[TERMINAL_POLICY_DENIAL] Do not retry this path or search for a workaround. Explain the boundary and offer an allowed path only if useful."
                    elif "blocked in the sandbox" in lowered_error or "open() is blocked" in lowered_error:
                        thread_content += "\n[TERMINAL_SANDBOX_DENIAL] Do not retry the blocked import or file operation. Use a dedicated tool or a safe computation-only alternative."
                if auto_working_data_key:
                    thread_content += (
                        f"\n[exact tool-result Working Data key: {auto_working_data_key}; "
                        f"use {{working_data:{auto_working_data_key}}} or working_data_get to preserve this exact value]"
                    )
                if auto_working_data_key and len(thread_content) > TOOL_MSG_MAX_CHARS:
                    thread_content = thread_content[:TOOL_MSG_MAX_CHARS] + f"\n... [truncated - full content auto-saved to Working Data key: {auto_working_data_key}]"

                _log(f"     {trunc(str(result_content), 120)}")
                round_outputs.append(output)
                tool_outputs.append(output)
                if not output.get("is_error"):
                    try:
                        from sessions.tool_selection import note_tool_used
                        note_tool_used(func_name)
                    except Exception as exc:
                        _log_file_only(f"[tool-selection] could not promote MRU tool '{func_name}': {exc}")
                messages.append({"role": "tool", "tool_call_id": tc_id, "name": func_name, "content": thread_content})
                context_map.append({"round": round_num, "role": "tool", "label": func_name, "chars": len(thread_content), "auto_key": auto_working_data_key, "msg_idx": len(messages) - 1})

            if round_recovery_events:
                recovery_pending = dict(round_recovery_events[0])
                recovery_pending["reminders_sent"] = 0
                correction = _build_tool_recovery_message(recovery_pending)
                _log_file_only(
                    f"[tool-recovery] {recovery_pending.get('classification')}: requested={recovery_pending.get('requested_tool')} corrected={recovery_pending.get('corrected_tool', '')}"
                )
                messages.append({"role": "user", "content": correction})
                context_map.append({"round": round_num, "role": "user", "label": "[tool recovery correction]", "chars": len(correction), "auto_key": None, "msg_idx": len(messages) - 1})

            if on_tool_round_complete is not None:
                try:
                    on_tool_round_complete(round_outputs)
                except TypeError:
                    on_tool_round_complete()
                except Exception as exc:
                    _log_file_only(f"[error] on_tool_round_complete callback failed: {exc}")

            _log_file_only(f"TOOL ROUND {round_num} - EXECUTION FLOW")
            _log_file_only(format_tool_outputs(round_outputs))
        else:
            _log("[warn] Max tool rounds exhausted - requesting final synthesis.")
            try:
                synthesis_messages = messages + [{"role": "user", "content": "Based on the tool results above, please answer my original question now."}]
                result = call_llm_chat(
                    model_name = config.resolved_model,
                    messages   = synthesis_messages,
                    tools      = None,
                    num_ctx    = choose_context_window(config.num_ctx, synthesis_messages),
                )
                final_response = strip_cot_preamble(result.response)
                prompt_tokens += result.prompt_tokens
                completion_tokens += result.completion_tokens
                final_tps = result.tokens_per_second
                _log_section("FINAL RESPONSE")
                thinking = (result.message.get("thinking") or result.message.get("reasoning") or "").strip()
                if thinking:
                    _log_file_only(f"[thinking]\n{thinking}\n[/thinking]")
                _log(final_response)
                if not final_response and tool_outputs:
                    _log_file_only("[warn] Synthesis returned empty - falling back to tool-output summary.")
                    final_response = build_fallback_answer(user_prompt, tool_outputs)
                    _log(final_response)
                run_success = bool(final_response)
            except Exception as error:
                final_response = f"(synthesis failed: {error})"
    finally:
        working_data_unpin_all()
    return final_response, prompt_tokens, completion_tokens, run_success, final_tps, tool_outputs
