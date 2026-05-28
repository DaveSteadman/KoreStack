import json


_BACKGROUND_CONTEXT_VERSION = 1
_MAX_PERSISTED_TURNS = 3
_MAX_PERSISTED_SKILL_OUTPUTS = 4
_REQUIRED_TURN_KEYS = ("turn", "user_prompt", "assistant_response", "skill_outputs")


def estimate_next_turn_tokens(prompt_tokens: int | None, completion_tokens: int | None) -> int:
    return max(0, int(prompt_tokens or 0)) + max(0, int(completion_tokens or 0))


def estimate_summary_tokens(summary_text: str | None) -> int:
    return max(0, len((summary_text or "").strip()) // 4)


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