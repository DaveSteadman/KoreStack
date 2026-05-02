import json
import re
from pathlib import Path

from KoreAgent.context_manager import COMPACT_THRESHOLD
from KoreAgent.context_manager import assess_compact
from KoreAgent.scratchpad import scratch_save as scratch_auto_save
from KoreAgent.scratchpad import scratch_pin
from KoreAgent.scratchpad import scratch_unpin_all
from KoreAgent.skill_executor import execute_tool_call
from KoreAgent.tool_result import ToolCallResult
from KoreAgent.utils.workspace_utils import get_workspace_root
from KoreAgent.utils.workspace_utils import trunc


# Cap for tool result content in messages; longer content is auto-saved to scratchpad and truncated in the message with a reference note
TOOL_MSG_MAX_CHARS: int = 4096 

TOOL_MSG_AUTO_SCRATCH_MIN: int = 600

_COT_PLANNING_RE = re.compile(
    r"\b(?:we should|we can|we need|we will|we could|we\'ll|we\'re|we must|"
    r"let me|let\'s|let us|thus we|so we|now we|next we|i need|i should|i will|i\'ll|"
    r"provide an?\b|provide the\b|need to |should |we want|we are going|"
    r"maybe |perhaps )",
    re.IGNORECASE,
)
_CONTENT_MARKER_RE = re.compile(r"(?:^|\n)(\*\*|#{1,3} |\| |\d+\. |- )")
_WRITE_FILE_BLOCK_RE = re.compile(r"WRITE_FILE:\s*([^\n]+)\n---FILE_START---[ \t]*\n(.*?)\n?---FILE_END---", re.DOTALL)


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
    if normalized_name == "delegate" and "task" in normalized_args and "prompt" not in normalized_args:
        normalized_args["prompt"] = normalized_args.pop("task")
        note_parts.append("delegate(task=...) -> delegate(prompt=...)")
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


def _extract_raw_json_tool_call(text: str) -> dict | None:
    # Returns a synthetic tool_calls entry if the text is a bare JSON tool-call object,
    # i.e. {"tool": "<name>", "arguments": {...}} or {"name": "<name>", "arguments": {...}}.
    # Returns None for all other text (normal final answers).
    stripped = (text or "").strip()
    if not stripped.startswith("{"):
        return None
    try:
        obj = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    tool_name = obj.get("tool") or obj.get("name") or obj.get("function")
    arguments  = obj.get("arguments") or obj.get("parameters") or obj.get("args") or {}
    if not tool_name or not isinstance(tool_name, str):
        return None
    if not isinstance(arguments, dict):
        return None
    # Must look like an intentional tool invocation: name must be non-trivial and alphanumeric.
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", tool_name):
        return None
    return {
        "id":       f"raw_json_{tool_name}",
        "type":     "function",
        "function": {
            "name":      tool_name,
            "arguments": json.dumps(arguments),
        },
    }


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


def write_file_blocks(response: str, *, log_to_session) -> list[str]:
    workspace_root = get_workspace_root()
    data_dir       = (workspace_root / "data").resolve()
    written: list[str] = []
    for match in _WRITE_FILE_BLOCK_RE.finditer(response):
        raw_path = match.group(1).strip()
        content = match.group(2)
        normalized = raw_path.replace("\\", "/")
        if normalized.startswith("data/"):
            normalized = normalized[5:]
        candidate = Path(normalized)
        target = (data_dir / normalized).resolve() if not candidate.is_absolute() else candidate.resolve()
        try:
            target.relative_to(data_dir)
        except ValueError:
            log_to_session(f"[file-blocks] Skipping unsafe path: {raw_path!r}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(target.relative_to(workspace_root).as_posix())
    return written


def run_tool_loop(
    *,
    config,
    messages: list[dict],
    tool_defs: list[dict],
    catalog_gates: dict,
    context_map: list[dict],
    user_prompt: str,
    logger,
    quiet: bool,
    call_llm_chat,
    stop_requested,
    clear_stop,
    on_tool_round_complete: object | None = None,
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

    clear_stop()
    try:
        for round_num in range(1, config.max_iterations + 1):
            if stop_requested():
                clear_stop()
                _log(f"[/stoprun] Stop requested - halting before round {round_num}.")
                final_response = "[Run stopped by /stoprun. The previous response may be incomplete.]"
                break

            _log_section(f"TOOL ROUND {round_num}")
            _log_file_only(f"[progress] Round {round_num}: calling model...")
            thread_chars, compact_count = assess_compact(context_map, messages, round_num, config.num_ctx)
            if compact_count:
                _log_file_only(f"[context] compacted {compact_count} message(s) (threshold {COMPACT_THRESHOLD:.0%} exceeded)")
            _log_file_only(f"[context] thread: {thread_chars:,} chars (~{thread_chars // 4:,} tok est.) | window: {config.num_ctx:,} | remaining est.: ~{config.num_ctx - thread_chars // 4:,}")

            try:
                result = call_llm_chat(model_name=config.resolved_model, messages=messages, tools=tool_defs if tool_defs else None, num_ctx=config.num_ctx)
            except Exception as error:
                error_str = str(error)
                if "error parsing tool call" in error_str:
                    correction = (
                        "Your previous tool call could not be executed because the argument JSON was truncated or malformed. "
                        "Do not embed large multi-line strings directly in a tool call argument. Instead: (1) build the content using "
                        "code_execute and print() it, (2) save the output to the scratchpad with scratch_save, then (3) pass the scratchpad reference to write_file."
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

            if not result.tool_calls:
                candidate = strip_cot_preamble(result.response)
                # Guard: detect the "describing a tool call instead of invoking it" hallucination.
                # Some models emit the raw JSON object {"tool": "...", "arguments": {...}} as text
                # when they should have used the native tool-call mechanism.  Detect this pattern
                # and synthesize a synthetic tool call so the round proceeds normally.
                _synthetic_tc = _extract_raw_json_tool_call(candidate)
                if _synthetic_tc is not None:
                    _log_file_only(f"[warn] Round {round_num}: model emitted raw JSON tool call instead of invoking - forcing re-invocation.")
                    result.tool_calls = [_synthetic_tc]
                else:
                    final_response = candidate
                    run_success = bool(final_response)
                    _log(final_response)
                    _log_file_only(f"[progress] Round {round_num}: model gave final answer.")
                    messages.append({"role": "assistant", "content": final_response})
                    context_map.append({"round": round_num, "role": "asst", "label": "final answer", "chars": len(final_response), "auto_key": None, "msg_idx": len(messages) - 1})
                    break

            _log(f"Round {round_num}: model requested {len(result.tool_calls)} tool call(s).")
            _log_file_only("[progress] Executing tool calls...")
            current_tc_fingerprints = frozenset((tc.get("function", {}).get("name", ""), tc.get("function", {}).get("arguments", "{}")) for tc in result.tool_calls)
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
            assistant_content = "" if result.tool_calls else (result.response or "")
            messages.append({"role": "assistant", "content": assistant_content, "tool_calls": result.tool_calls})
            context_map.append({"round": round_num, "role": "asst", "label": f"(tool calls x{len(result.tool_calls)})", "chars": len(assistant_content), "auto_key": None, "msg_idx": len(messages) - 1})

            round_outputs: list[ToolCallResult] = []
            for tool_call in result.tool_calls:
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
                try:
                    output = execute_tool_call(func_name, arguments, config.skills_payload, user_prompt, catalog_gates)
                    result_content = output["result"]
                    if not isinstance(result_content, str):
                        result_content = json.dumps(result_content, default=str)
                    if output.get("is_error"):
                        result_content = f"[SKILL_ERROR] {result_content}"
                except Exception as exc:
                    result_content = f"[SKILL_ERROR] Error executing {func_name}: {exc}"
                    output = ToolCallResult(tool=func_name, function=func_name, module="", arguments=arguments, result=result_content, status="error", error=str(exc))

                is_scratch_reader = func_name.lower().startswith("scratch_")
                auto_scratch_key = None
                if not output.get("is_error") and not is_scratch_reader and isinstance(result_content, str) and len(result_content) >= TOOL_MSG_AUTO_SCRATCH_MIN:
                    safe_name = func_name.lower()[:24]
                    auto_scratch_key = f"_tc_r{round_num}_{safe_name}"
                    scratch_auto_save(auto_scratch_key, result_content)
                    scratch_pin(auto_scratch_key)
                    if len(result_content) > TOOL_MSG_MAX_CHARS:
                        result_content = result_content[:TOOL_MSG_MAX_CHARS] + f"\n... [truncated - full content auto-saved to scratchpad key: {auto_scratch_key}]"

                _log(f"     {trunc(str(result_content), 120)}")
                round_outputs.append(output)
                tool_outputs.append(output)
                messages.append({"role": "tool", "tool_call_id": tc_id, "name": func_name, "content": result_content})
                context_map.append({"round": round_num, "role": "tool", "label": func_name, "chars": len(result_content), "auto_key": auto_scratch_key, "msg_idx": len(messages) - 1})

            if on_tool_round_complete is not None:
                try:
                    on_tool_round_complete()
                except Exception as exc:
                    _log_file_only(f"[error] on_tool_round_complete callback failed: {exc}")

            _log_file_only(f"TOOL ROUND {round_num} - EXECUTION FLOW")
            _log_file_only(format_tool_outputs(round_outputs))
        else:
            _log("[warn] Max tool rounds exhausted - requesting final synthesis.")
            try:
                synthesis_messages = messages + [{"role": "user", "content": "Based on the tool results above, please answer my original question now."}]
                result = call_llm_chat(model_name=config.resolved_model, messages=synthesis_messages, tools=None, num_ctx=config.num_ctx)
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
        scratch_unpin_all()
    return final_response, prompt_tokens, completion_tokens, run_success, final_tps, tool_outputs
