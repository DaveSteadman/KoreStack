from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Prompt builder helpers for KoreCode/app.
# Provides the focused helpers and module-level behaviour grouped into this file.
# ====================================================================================================

import json
from pathlib import Path
from typing import Any

from .tool_api import tool_guide_payload


DEFAULT_MAX_MENTION_FILE_CHARS = 7000
DEFAULT_MAX_MENTION_COUNT      = 4

AGENT_OUTPUT_SCHEMA = {
    "kind":         "analysis|plan|tool_requests|edits|final",
    "summary":      "short summary string",
    "findings":     [],
    "tool_requests": [
        {
            "tool":   "read_file|read_context|list_tree|search_in_file|get_python_function|replace_python_function|insert_python_function",
            "args":   {},
            "reason": "why this tool is needed",
        },
    ],
    "edits": [
        {
            "file":        "path/to/file",
            "from":        1,
            "to":          1,
            "replacement": "new text",
            "explanation": "why this change is needed",
        },
    ],
    "next": "continue|done",
}

AGENT_TOOL_GUIDE = tool_guide_payload()


def extract_file_mentions(text: str, max_mention_count: int = DEFAULT_MAX_MENTION_COUNT) -> list[str]:
    tokens = str(text or "").replace("\n", " ").replace("\t", " ").split(" ")
    out: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if not token or not token.startswith("@") or len(token) < 3:
            continue
        raw = token[1:].replace("\\", "/").strip()
        while len(raw) > 1 and raw[-1] in "),.;:'\"}]>":
            raw = raw[:-1]
        if "." not in raw or raw.endswith("."):
            continue
        if not raw or raw.startswith("/") or ".." in raw or raw in seen:
            continue
        seen.add(raw)
        out.append(raw)
        if len(out) >= max_mention_count:
            break
    return out


def _read_mention_context(
    *,
    workspace_root: Path,
    paths: list[str],
    max_mention_file_chars: int,
    resolve_relative_path,
    is_probably_text,
    read_text,
) -> list[dict[str, Any]]:
    if not paths:
        return []

    blocks: list[dict[str, Any]] = []
    for rel_path in paths:
        try:
            candidate = resolve_relative_path(rel_path)
        except Exception:
            continue
        if not candidate.exists() or not candidate.is_file() or not is_probably_text(candidate):
            continue
        try:
            content, _encoding = read_text(candidate)
        except Exception:
            continue
        excerpt   = str(content or "")[:max_mention_file_chars]
        lines     = excerpt.split("\n")
        head      = "\n".join(lines[:160])
        truncated = len(str(content or "")) > len(excerpt) or len(lines) > 160
        blocks.append(
            {
                "path":      rel_path,
                "content":   head,
                "truncated": truncated,
            }
        )
    return blocks


def _mention_context_block(mention_blocks: list[dict[str, Any]]) -> str:
    if not mention_blocks:
        return ""
    blocks = "\n\n".join(
        f"FILE: {item['path']}{' (truncated)' if item.get('truncated') else ''}\n```\n{item['content']}\n```"
        for item in mention_blocks
    )
    return f"\n\n[MENTIONED_FILES]\n{blocks}\n[/MENTIONED_FILES]"


def instruction_by_mode(mode: str) -> str:
    contracts = {
        "chat":     "Solve the user request directly. Use tool_requests if you need extra codebase evidence before proposing edits.",
        "explain":  "Explain behavior, edge cases, and side effects. Use tool_requests first if context is insufficient.",
        "bughunt":  "Find bugs and risks with concise severity and evidence. Request tools first when uncertain.",
        "refactor": "Refactor without changing behavior. Prefer minimal edits and preserve style.",
        "tests":    "Create focused tests for behavior and edge cases. Keep edits scoped to test files unless requested.",
    }
    return contracts.get(str(mode or "").strip(), contracts["chat"])


def build_agent_contract(mode: str) -> str:
    lines = [
        "You are KoreCode Agent, a coding agent that can request tools before proposing changes.",
        instruction_by_mode(mode),
        "Always output EXACTLY one valid JSON object and nothing else.",
        "Do not wrap output in markdown fences.",
        'If additional information is required, set kind="tool_requests" and next="continue".',
        'When finished, set next="done" and use kind="analysis", "edits", or "final" as appropriate.',
        "When emitting edits, include line-based ranges with from/to inclusive.",
        "If creating a new file, use from=1 and to=1 and put full file content in replacement.",
        "For Python files, prefer get_python_function before editing an existing function or method.",
        "Before replace_python_function or insert_python_function, first obtain the current file content_hash.",
        "Use replace_python_function when you can safely replace one whole Python function or method; it returns an edit proposal, not an applied write.",
        "Use insert_python_function when adding a new Python function or class method; it returns an edit proposal, not an applied write.",
    ]
    return " ".join(lines)


def build_tool_followup_prompt(
    *,
    mode: str,
    path: str,
    user_text: str,
    previous_response: str,
    tool_results: list[Any],
) -> str:
    return "\n".join(
        [
            build_agent_contract(mode),
            "",
            "[ACTIVE_FILE]",
            str(path or "."),
            "[/ACTIVE_FILE]",
            "",
            "[ORIGINAL_USER_REQUEST]",
            str(user_text or ""),
            "[/ORIGINAL_USER_REQUEST]",
            "",
            "[PREVIOUS_AGENT_RESPONSE_JSON]",
            str(previous_response or ""),
            "[/PREVIOUS_AGENT_RESPONSE_JSON]",
            "",
            "[TOOL_RESULTS]",
            json.dumps(tool_results, indent=2),
            "[/TOOL_RESULTS]",
            "",
            "[OUTPUT_SCHEMA]",
            json.dumps(AGENT_OUTPUT_SCHEMA, indent=2),
            "[/OUTPUT_SCHEMA]",
            "",
            "Return one JSON object now, based on tool results.",
        ]
    )


def build_prompt_by_mode(
    *,
    mode: str,
    user_text: str,
    path: str,
    selection: str | None,
    cursor: dict[str, Any] | None,
    workspace_context_enabled: bool,
    workspace_root: Path,
    resolve_relative_path,
    is_probably_text,
    read_text,
    build_context_pack,
    max_mention_count: int = DEFAULT_MAX_MENTION_COUNT,
    max_mention_file_chars: int = DEFAULT_MAX_MENTION_FILE_CHARS,
) -> str:
    active_path     = str(path or ".")
    has_active_file = bool(active_path.strip() and active_path != ".")
    base            = (
        f"The following code is selected in the editor:\n```\n{selection}\n```\n\n{user_text}"
        if selection
        else str(user_text or "")
    )

    mention_paths         = extract_file_mentions(user_text, max_mention_count=max_mention_count)
    mention_blocks        = _read_mention_context(
        workspace_root          = workspace_root,
        paths                   = mention_paths,
        max_mention_file_chars  = max_mention_file_chars,
        resolve_relative_path   = resolve_relative_path,
        is_probably_text        = is_probably_text,
        read_text               = read_text,
    )
    mention_context_block = _mention_context_block(mention_blocks)

    context_pack = None
    if has_active_file:
        try:
            candidate  = resolve_relative_path(active_path)
            query_seed = "\n".join(item for item in [str(user_text or ""), str(selection or "")] if item).strip()[:1200]
            start_line = None
            end_line   = None
            if isinstance(cursor, dict):
                raw_line = cursor.get("line")
                if isinstance(raw_line, int) and raw_line > 0:
                    start_line = raw_line
                    end_line   = raw_line
            context_pack = build_context_pack(
                candidate,
                start_line,
                end_line,
                query=query_seed if workspace_context_enabled and query_seed else None,
                include_workspace=workspace_context_enabled,
            )
        except Exception:
            context_pack = None

    context_block  = f"\n\n[CONTEXT_PACK]\n{json.dumps(context_pack, indent=2)}\n[/CONTEXT_PACK]" if context_pack else ""
    agent_contract = build_agent_contract(mode)
    return "\n".join(
        [
            agent_contract,
            "",
            "[ACTIVE_FILE]",
            active_path,
            "[/ACTIVE_FILE]",
            "",
            "[AVAILABLE_TOOLS]",
            json.dumps(AGENT_TOOL_GUIDE, indent=2),
            "[/AVAILABLE_TOOLS]",
            "",
            "[OUTPUT_SCHEMA]",
            json.dumps(AGENT_OUTPUT_SCHEMA, indent=2),
            "[/OUTPUT_SCHEMA]",
            "",
            "[USER_TASK]",
            base,
            "[/USER_TASK]",
            mention_context_block,
            context_block,
        ]
    )
