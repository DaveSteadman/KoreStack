# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Ephemeral first-pass tool selection.  The selector receives the complete registered SkillManager
# catalogue and returns only exact skill names.  Its detailed catalogue is never added to the main
# conversation messages, so it cannot consume the tool-running context on later rounds.
# ====================================================================================================

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from agent.orchestration.context_window import choose_context_window
from skill_manager import skill_manager
from sessions.tool_selection import promote_selected_tools


MAX_SELECTED_PER_PROMPT = 6
_SELECTOR_OUTPUT_RESERVE = 1_024

_SELECTOR_INSTRUCTIONS = """You are the KoreAgent tool selector. Your sole job is to choose the minimum exact registered tool names needed to help answer the user's newest request. Do not answer the user, explain your choice, call tools, or select a tool merely because one of its words appears in the request. Choose no tools for ordinary conversation or when the catalogue has no suitable tool.

Return exactly one JSON object and nothing else:
{"tool_names":["exact_registered_tool_name"]}

Rules:
- tool_names must contain only names from the supplied catalogue.
- Select at most 6 tools.
- Read each selection_description and parameter list; do not infer identifiers, dates, domains, or other required arguments that the user did not provide.
- Prefer a specific end-user capability over a low-level administrative or inspection capability unless the user asks for administration or inspection."""


def build_selector_catalog(skills: list[dict[str, Any]] | None = None) -> list[dict[str, object]]:
    """Return the complete, compact registered-skill catalogue for the isolated selector call."""
    records: list[dict[str, object]] = []
    for skill in skills if skills is not None else skill_manager.list_skills():
        if not isinstance(skill, dict):
            continue
        name = str(skill.get("name") or "").strip()
        if not name:
            continue
        parameters = skill.get("parameters")
        properties = parameters.get("properties") if isinstance(parameters, dict) else {}
        records.append(
            {
                "name": name,
                "service": str(skill.get("service") or "").strip(),
                "keywords": [str(value) for value in skill.get("keywords") or [] if str(value).strip()],
                "selection_description": str(
                    skill.get("selection_description") or skill.get("purpose") or ""
                ).strip(),
                "parameters": sorted(str(parameter) for parameter in properties) if isinstance(properties, dict) else [],
            }
        )
    return sorted(records, key=lambda record: str(record["name"]))


def _parse_selected_names(response: str, valid_names: set[str]) -> list[str]:
    """Accept only the selector's documented JSON shape and known registered names."""
    candidate = str(response or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", candidate, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return []
    raw_names = payload.get("tool_names") if isinstance(payload, dict) else None
    if not isinstance(raw_names, list):
        return []
    selected: list[str] = []
    for raw_name in raw_names:
        name = str(raw_name or "").strip()
        if name and name in valid_names and name not in selected:
            selected.append(name)
        if len(selected) >= MAX_SELECTED_PER_PROMPT:
            break
    return selected


def select_registered_tools(
    user_prompt: str,
    *,
    model_name: str,
    maximum_context_tokens: int,
    session_id: str,
    conversation_entry: dict | None,
    call_llm: Callable[..., Any],
    skills: list[dict[str, Any]] | None = None,
    promote: Callable[..., dict[str, list[str]]] = promote_selected_tools,
) -> dict[str, object]:
    """Select and activate registered service tools without retaining selector context.

    Failures intentionally leave the current active-tool set unchanged: tool selection must not
    prevent an ordinary chat exchange from running.
    """
    catalog = build_selector_catalog(skills)
    if not user_prompt.strip() or not catalog:
        return {"selected": [], "activated": [], "catalog_size": len(catalog), "status": "skipped"}

    catalog_json = json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
    selector_messages = [
        {"role": "system", "content": _SELECTOR_INSTRUCTIONS},
        {"role": "system", "content": f"Registered tool catalogue:\n{catalog_json}"},
        {"role": "user", "content": user_prompt},
    ]
    try:
        result = call_llm(
            model_name=model_name,
            messages=selector_messages,
            tools=None,
            num_ctx=choose_context_window(
                maximum_context_tokens,
                selector_messages,
                output_reserve=_SELECTOR_OUTPUT_RESERVE,
            ),
        )
    except Exception as exc:
        return {
            "selected": [],
            "activated": [],
            "catalog_size": len(catalog),
            "status": f"failed: {type(exc).__name__}",
        }

    selected = _parse_selected_names(getattr(result, "response", ""), {str(record["name"]) for record in catalog})
    if not selected:
        return {"selected": [], "activated": [], "catalog_size": len(catalog), "status": "no_selection"}
    activation = promote(selected, session_id=session_id, conversation_entry=conversation_entry)
    return {
        "selected": selected,
        "activated": list(activation.get("added") or []) + list(activation.get("promoted") or []),
        "evicted": list(activation.get("evicted") or []),
        "catalog_size": len(catalog),
        "status": "selected",
    }


__all__ = [
    "MAX_SELECTED_PER_PROMPT",
    "build_selector_catalog",
    "select_registered_tools",
]
