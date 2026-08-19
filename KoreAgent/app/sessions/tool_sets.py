# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# tool sets module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Function inventory:
# - get_tool_sets_path: Returns tool sets path for this module.
# - _normalise_tool_names: Implements the  normalise tool names operation for this module.
# - normalise_tool_sets: Implements the normalise tool sets operation for this module.
# - load_tool_sets: Loads tool sets for this module.
# - save_tool_sets: Saves tool sets for this module.
# - resolve_tool_sets: Resolves tool sets for this module.
# - related_tool_set: Implements the related tool set operation for this module.
# - relevant_tool_sets: Implements the relevant tool sets operation for this module.
# ====================================================================================================

"""Persisted, user-maintained groupings over the live tool inventory."""

from __future__ import annotations

import json
import re
from datetime import datetime
from datetime import timezone
from pathlib import Path

from sessions.tool_state import ALWAYS_ON_TOOL_NAMES
from utils.workspace_utils import get_controldata_dir


MAX_TOOLS_PER_SET = 16
_SET_NAME_RE      = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_GENERIC_TERMS    = frozenset({
    "add", "append", "clear", "create", "delete", "edit", "find", "get", "list",
    "make", "read", "remove", "save", "search", "set", "update", "write",
})
_TERM_ALIASES     = {"information": "info", "directories": "directory", "files": "file", "folders": "folder"}


def get_tool_sets_path() -> Path:
    return get_controldata_dir() / "koreagent" / "ToolSets.json"


def _normalise_tool_names(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for item in value:
        name = str(item or "").strip()
        if not name or name in seen or name in ALWAYS_ON_TOOL_NAMES:
            continue
        seen.add(name)
        names.append(name)
    return names


def normalise_tool_sets(value: object, *, known_tool_names: set[str] | None = None) -> list[dict]:
    raw_sets = value.get("sets", []) if isinstance(value, dict) else value
    if not isinstance(raw_sets, list):
        return []

    normalised: list[dict] = []
    seen_names: set[str] = set()
    for item in raw_sets:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip().lower()
        description = " ".join(str(item.get("description") or "").split())[:240]
        tools = _normalise_tool_names(item.get("tools"))
        if known_tool_names is not None:
            tools = [tool for tool in tools if tool in known_tool_names]
        if (
            not _SET_NAME_RE.fullmatch(name)
            or name in seen_names
            or not description
            or not tools
            or len(tools) > MAX_TOOLS_PER_SET
        ):
            continue
        seen_names.add(name)
        normalised.append({
            "name":        name,
            "description": description,
            "tools":       tools,
        })
    return normalised


def load_tool_sets(*, known_tool_names: set[str] | None = None) -> list[dict]:
    path = get_tool_sets_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return normalise_tool_sets(raw, known_tool_names=known_tool_names)


def save_tool_sets(tool_sets: object, *, known_tool_names: set[str]) -> dict:
    sets = normalise_tool_sets(tool_sets, known_tool_names=known_tool_names)
    if not sets:
        raise ValueError("No valid non-empty tool sets were supplied.")
    path = get_tool_sets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sets":         sets,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"path": str(path), "sets": sets}


def resolve_tool_sets(names: object, *, known_tool_names: set[str]) -> dict:
    requested = _normalise_tool_names(names)
    available_sets = {item["name"]: item for item in load_tool_sets()}
    activated: list[str] = []
    selected_sets: list[str] = []
    unknown_sets: list[str] = []
    unavailable_tools: list[str] = []
    for name in requested:
        tool_set = available_sets.get(name.lower())
        if tool_set is None:
            unknown_sets.append(name)
            continue
        selected_sets.append(tool_set["name"])
        for tool_name in tool_set["tools"]:
            if tool_name in known_tool_names:
                if tool_name not in activated:
                    activated.append(tool_name)
            elif tool_name not in unavailable_tools:
                unavailable_tools.append(tool_name)
    return {
        "selected_sets":     selected_sets,
        "tool_names":        activated,
        "unknown_sets":      unknown_sets,
        "unavailable_tools": unavailable_tools,
    }


def related_tool_set(tool_name: str, *, known_tool_names: set[str]) -> list[str]:
    """Return the smallest configured set containing a known tool.

    This supports transparent runtime reactivation.  A direct call to an
    inactive tool should make its closely related operations available too;
    for example, activating ``file_write`` also exposes ``folder_create``.
    """
    requested = str(tool_name or "").strip()
    if not requested:
        return []

    candidates = [
        item["tools"]
        for item in load_tool_sets(known_tool_names=known_tool_names)
        if requested in item["tools"]
    ]
    if not candidates:
        return [requested] if requested in known_tool_names else []
    return list(min(candidates, key=lambda tools: (len(tools), tools)))


def relevant_tool_sets(prompt: str, *, known_tool_names: set[str], limit: int = 2) -> list[dict]:
    """Return the most relevant bounded tool sets for a new task.

    This is deliberately lexical and conservative: it only activates groups
    when the request has a distinctive domain word (for example ``folder`` or
    ``system``), rather than treating generic verbs such as ``write`` as a
    tool-selection signal.
    """
    prompt_terms = {
        _TERM_ALIASES.get(term, term)
        for term in re.findall(r"[a-z0-9]+", str(prompt or "").lower())
    }
    if not prompt_terms:
        return []

    ranked: list[tuple[int, str, dict]] = []
    for tool_set in load_tool_sets(known_tool_names=known_tool_names):
        group_terms = {
            _TERM_ALIASES.get(term, term)
            for term in re.findall(
                r"[a-z0-9]+",
                " ".join([tool_set["name"], tool_set["description"], *tool_set["tools"]]).lower(),
            )
        }
        matched = prompt_terms & group_terms
        distinctive = {term for term in matched if term not in _GENERIC_TERMS}
        if not distinctive:
            continue
        ranked.append((len(distinctive) * 10 + len(matched), tool_set["name"], tool_set))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [item[2] for item in ranked[:max(1, int(limit))]]
