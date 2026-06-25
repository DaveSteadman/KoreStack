from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Task korechat helpers for KoreAgent/app.
# Provides the focused helpers and module-level behaviour grouped into this file.
# ====================================================================================================

import urllib.parse

from input_layer.koreconv_input import _get_base_url
from input_layer.koreconv_input import _http_get
from input_layer.koreconv_input import _http_patch
from input_layer.koreconv_input import _http_post


def _task_external_id(task_name: str) -> str:
    return f"task:{task_name}"


def _lookup_task_conversation(base: str, task_name: str) -> dict | None:
    external_id = urllib.parse.quote(_task_external_id(task_name), safe="")
    try:
        existing = _http_get(base, f"/api/conversations/by-external-id/{external_id}")
    except RuntimeError as exc:
        if "KC HTTP 404" in str(exc):
            return None
        raise
    return existing if isinstance(existing, dict) else None


def ensure_task_conversation(task_name: str) -> dict:
    base = _get_base_url()
    if not base:
        raise RuntimeError("KoreChat is not configured")

    existing = _lookup_task_conversation(base, task_name)
    if existing is not None:
        return existing

    created = _http_post(
        base,
        "/api/conversations",
        {
            "external_id":  _task_external_id(task_name),
            "subject":      task_name,
            "channel_type": "scheduled",
        },
    )
    if not isinstance(created, dict):
        raise RuntimeError("Failed to create task conversation")
    return created


def load_task_turns(task_name: str) -> list[dict]:
    try:
        base = _get_base_url()
        if not base:
            return []
        conv = ensure_task_conversation(task_name)
        result = _http_get(base, f"/api/conversations/{conv['id']}/messages?limit=1000") or []
    except Exception:
        return []

    if not isinstance(result, list):
        return []

    turns: list[dict] = []
    pending_prompt: str | None = None
    for message in result:
        direction = message.get("direction")
        content   = (message.get("content") or "").strip()
        if not content:
            continue
        if direction == "inbound":
            pending_prompt = content
            continue
        if direction == "outbound" and pending_prompt is not None:
            turns.append({"role": "user",      "content": pending_prompt})
            turns.append({"role": "assistant", "content": content})
            pending_prompt = None
    return turns


def save_task_turn(task_name: str, user_text: str, agent_text: str) -> None:
    try:
        base = _get_base_url()
        if not base:
            return
        conv       = ensure_task_conversation(task_name)
        conv_id    = conv["id"]
        turn_count = int(conv.get("turn_count") or 0) + 1

        _http_post(
            base,
            f"/api/conversations/{conv_id}/messages",
            {
                "direction":      "inbound",
                "content":        user_text,
                "sender_display": task_name,
                "status":         "received",
            },
        )
        _http_post(
            base,
            f"/api/conversations/{conv_id}/messages",
            {
                "direction":      "outbound",
                "content":        agent_text,
                "sender_display": "agent",
                "status":         "sent",
            },
        )
        _http_patch(
            base,
            f"/api/conversations/{conv_id}",
            {
                "subject":    task_name,
                "turn_count": turn_count,
                "status":     "active",
            },
        )
    except Exception:
        return
