# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Conversation state helpers for KoreAgent/app.
# Provides the focused helpers and module-level behaviour grouped into this file.
# ====================================================================================================

import json
import re


_BACKGROUND_CONTEXT_VERSION = 1
_MAX_PERSISTED_TURNS = 8
_MAX_PERSISTED_SKILL_OUTPUTS = 4
_REQUIRED_TURN_KEYS = ("turn", "user_prompt", "assistant_response", "skill_outputs")
_MEMORY_KEY_RE = re.compile(r"[^a-z0-9]+")


def estimate_next_turn_tokens(prompt_tokens: int | None, completion_tokens: int | None) -> int:
    return max(0, int(prompt_tokens or 0)) + max(0, int(completion_tokens or 0))


def estimate_summary_tokens(summary_text: str | None) -> int:
    return max(0, len((summary_text or "").strip()) // 4)


def _memory_key(label: str, fallback: str) -> str:
    cleaned = _MEMORY_KEY_RE.sub("_", label.strip().lower()).strip("_")
    return cleaned[:40] or fallback


def _normalize_turn(turn: object) -> dict | None:
    if not isinstance(turn, dict):
        return None
    if not all(key in turn for key in _REQUIRED_TURN_KEYS):
        return None
    skill_outputs = turn.get("skill_outputs")
    if not isinstance(skill_outputs, list):
        return None
    return {
        "turn": turn.get("turn"),
        "user_prompt": str(turn.get("user_prompt") or ""),
        "assistant_response": str(turn.get("assistant_response") or ""),
        "skill_outputs": [dict(item) for item in skill_outputs if isinstance(item, dict)],
    }


def decode_background_context(background_context: str | None) -> tuple[list[dict], str | None]:
    raw = (background_context or "").strip()
    if not raw:
        return [], None
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        return [], f"background_context JSON decode failed: {exc}"

    turns_payload: object = parsed
    if isinstance(parsed, dict):
        version = int(parsed.get("version") or 0)
        if version != _BACKGROUND_CONTEXT_VERSION:
            return [], f"background_context version {version} is unsupported"
        turns_payload = parsed.get("turns")

    if not isinstance(turns_payload, list):
        return [], "background_context payload is not a turn list"

    normalized = [_normalize_turn(turn) for turn in turns_payload]
    valid_turns = [turn for turn in normalized if turn is not None]
    if not valid_turns:
        return [], "background_context contained no valid turns"
    if len(valid_turns) != len(turns_payload):
        return valid_turns, "background_context contained invalid turn entries"
    return valid_turns, None


def encode_background_context(turns: list[dict], existing_background_context: str | None = "") -> str:
    if not turns:
        return (existing_background_context or "").strip()

    compact_turns: list[dict] = []
    for turn in turns[-_MAX_PERSISTED_TURNS:]:
        compact_turns.append(
            {
                "turn": turn.get("turn"),
                "user_prompt": str(turn.get("user_prompt") or "")[:150],
                "assistant_response": str(turn.get("assistant_response") or "")[:300],
                "skill_outputs": [
                    {
                        "skill": item.get("skill", "?"),
                        "summary": str(item.get("summary") or "")[:100],
                    }
                    for item in (turn.get("skill_outputs") or [])[:_MAX_PERSISTED_SKILL_OUTPUTS]
                    if isinstance(item, dict)
                ],
            }
        )

    return json.dumps(
        {
            "version": _BACKGROUND_CONTEXT_VERSION,
            "turns": compact_turns,
        },
        ensure_ascii=False,
    )


def build_background_turn(
    *,
    turn: int | None,
    user_prompt: str,
    assistant_response: str,
    skill_outputs: list[dict] | None = None,
) -> dict:
    return {
        "turn":               turn,
        "user_prompt":        str(user_prompt or ""),
        "assistant_response": str(assistant_response or ""),
        "skill_outputs": [
            dict(item)
            for item in (skill_outputs or [])
            if isinstance(item, dict)
        ],
    }


def merge_background_turns(
    existing_background_context: str | None,
    new_turns: list[dict],
) -> str:
    existing_turns, _warning = decode_background_context(existing_background_context)
    merged: list[dict]       = list(existing_turns)
    next_turn                = len(merged)

    for turn in new_turns:
        normalized = _normalize_turn(turn)
        if normalized is None:
            continue
        next_turn += 1
        normalized["turn"] = next_turn
        merged.append(normalized)

    return encode_background_context(merged)


def extract_named_items(user_prompt: str, assistant_response: str = "") -> dict[str, str]:
    text = f"{user_prompt}\n{assistant_response}".strip()
    if not text:
        return {}

    named: dict[str, str] = {}

    for match in re.finditer(r"\bmy\s+([a-z][a-z0-9 _-]{1,40})\s+is\s+([^.\n!?]+)", text, re.IGNORECASE):
        label = match.group(1).strip()
        value = match.group(2).strip(" \t:;,.")
        if value:
            named[f"memory_{_memory_key(label, 'fact')}"] = value[:160]

    preference_match = re.search(r"\bi\s+(?:always\s+)?prefer\s+([^.\n!?]+)", text, re.IGNORECASE)
    if preference_match:
        named.setdefault("memory_preference", preference_match.group(1).strip(" \t:;,.")[:160])

    remember_match = re.search(r"\bremember\s+this\s*:\s*([^.\n!?]+)", text, re.IGNORECASE)
    if remember_match:
        remembered = remember_match.group(1).strip(" \t:;,.")
        if remembered:
            named.setdefault("memory_remembered", remembered[:160])

    return {key: value for key, value in named.items() if value}
