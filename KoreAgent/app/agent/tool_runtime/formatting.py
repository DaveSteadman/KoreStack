# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Pure presentation helpers for tool output and degraded model replies. This module does not invoke
# tools or mutate conversation state.
#
# Public API:
#   - extract_result_fields() -- normalises a search-like result record.
#   - format_tool_outputs()   -- renders completed tool calls for the model.
#   - build_fallback_answer() -- creates a useful response when model generation degrades.
#   - strip_cot_preamble()    -- removes an accidental planning preamble from a reply.
# ====================================================================================================
"""Formatting for tool results and degraded model replies."""


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================

from pathlib import Path
import re

from tool_result import ToolCallResult
from utils.workspace_utils import trunc

# ====================================================================================================
# MARK: FORMATTING PATTERNS
# ====================================================================================================
_COT_PLANNING_RE = re.compile(
    r"\b(?:we should|we can|we need|we will|we could|we\'ll|we\'re|we must|"
    r"let me|let\'s|let us|thus we|so we|now we|next we|i need|i should|i will|i\'ll|"
    r"provide an?\b|provide the\b|need to |should |we want|we are going|"
    r"maybe |perhaps )",
    re.IGNORECASE,
)
_CONTENT_MARKER_RE = re.compile(r"(?:^|\n)(\*\*|#{1,3} |\| |\d+\. |- )")


# ====================================================================================================
# MARK: TOOL OUTPUT FORMATTING (PUBLIC)
# ====================================================================================================
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


# ====================================================================================================
# MARK: DEGRADED RESPONSE FORMATTING (PUBLIC)
# ====================================================================================================
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


__all__ = ["build_fallback_answer", "extract_result_fields", "format_tool_outputs", "strip_cot_preamble"]
