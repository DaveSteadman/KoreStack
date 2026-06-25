from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Run executor helpers for KoreCode/app.
# Provides the focused helpers and module-level behaviour grouped into this file.
# ====================================================================================================

import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MAX_AGENT_TOOL_TURNS   = 3
POLL_INTERVAL_SECONDS  = 0.9
DEFAULT_WAIT_TIMEOUT_S = 180.0

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


@dataclass(slots=True)
class ChatRunRequest:
    run_id:                    str
    mode:                      str
    user_text:                 str
    thread_path:               str
    active_path:               str
    selection:                 str | None
    cursor:                    dict[str, Any] | None
    prompt:                    str
    workspace_root:            Path
    workspace_context_enabled: bool
    conversation_external_id:  str | None = None


@dataclass(slots=True)
class ContinueRunRequest:
    run_id:                    str
    thread_path:               str
    active_path:               str
    prefix:                    str
    suffix:                    str
    offset:                    int
    prompt:                    str
    workspace_root:            Path
    workspace_context_enabled: bool
    conversation_external_id:  str | None = None


@dataclass(slots=True)
class AgentRunServices:
    append_visible_message_for_conversation: Any
    append_internal_followup:                Any
    get_thread:                              Any
    build_tool_followup_prompt:              Any
    execute_tool_requests:                   Any
    append_tool_call:                        Any
    append_model_response:                   Any
    set_run_output:                          Any
    update_run:                              Any


def build_continue_prompt(prefix: str, suffix: str) -> str:
    system_note = " ".join(
        [
            "You are a code completion assistant.",
            "You will be given code before and after a cursor position.",
            "Reply with ONLY the code to insert at the cursor so that it fits naturally between the prefix and suffix.",
            "Do not repeat any of the provided code.",
            "Do not include markdown fences, explanations, or commentary.",
            "Output only the raw code to insert at the cursor.",
        ]
    )
    before = str(prefix or "")
    after  = str(suffix or "")
    if after.strip():
        return (
            f"{system_note}\n\n"
            f"[CODE BEFORE CURSOR]\n```\n{before}\n```\n\n"
            f"[CODE AFTER CURSOR]\n```\n{after}\n```"
        )
    return f"{system_note}\n\n```\n{before}\n```"


def extract_agent_envelope(text: str) -> dict[str, Any] | None:
    raw_text = str(text or "")
    if not raw_text.strip():
        return None

    candidates: list[str] = []
    for match in _FENCED_JSON_RE.finditer(raw_text):
        candidate = str(match.group(1) or "").strip()
        if candidate:
            candidates.append(candidate)
    candidates.append(raw_text.strip())

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("kind"), str):
            return parsed
    return None


def start_background_run(target, *args) -> None:
    worker = threading.Thread(target=target, args=args, daemon=True)
    worker.start()


def execute_chat_run(request: ChatRunRequest, services: AgentRunServices) -> None:
    last_reply = ""
    try:
        services.update_run(
            request.run_id,
            status       = "queued",
            event_type   = "prompt_ready",
            event_payload = {
                "mode":        request.mode,
                "thread_path": request.thread_path,
                "active_path": request.active_path,
            },
        )

        started = services.append_visible_message_for_conversation(
            request.workspace_root,
            request.thread_path,
            request.user_text,
            request.prompt,
            conversation_external_id   = request.conversation_external_id,
            workspace_context_enabled  = request.workspace_context_enabled,
        )
        services.update_run(
            request.run_id,
            status                   = "waiting_agent",
            conversation_external_id = started.get("external_id"),
            conversation_id          = started.get("conversation_id"),
            event_type               = "conversation_append_completed",
            event_payload            = {
                "pending_response": bool(started.get("pending_response")),
            },
        )

        previous_signature = _assistant_signature(started.get("last_assistant"))
        conversation_id    = str(started.get("external_id") or request.conversation_external_id or "").strip() or None

        for turn_index in range(MAX_AGENT_TOOL_TURNS + 1):
            settled = _wait_for_agent_turn(
                workspace_root            = request.workspace_root,
                thread_path               = request.thread_path,
                conversation_external_id  = conversation_id,
                workspace_context_enabled = request.workspace_context_enabled,
                get_thread_fn             = services.get_thread,
                previous_signature        = previous_signature,
            )

            conversation_id    = str(settled.get("external_id") or conversation_id or "").strip() or None
            previous_signature = _assistant_signature(settled.get("last_assistant"))
            last_reply         = str((settled.get("last_assistant") or {}).get("content") or "")

            services.append_model_response(
                request.run_id,
                role     = "assistant",
                content  = last_reply,
                metadata = {
                    "message_id":   (settled.get("last_assistant") or {}).get("id"),
                    "created_at":   (settled.get("last_assistant") or {}).get("created_at"),
                    "turn_index":   turn_index,
                    "thread_path":  request.thread_path,
                    "active_path":  request.active_path,
                },
            )

            envelope         = extract_agent_envelope(last_reply)
            requested_tools  = list(envelope.get("tool_requests") or []) if isinstance(envelope, dict) else []
            should_continue  = (
                isinstance(envelope, dict)
                and str(envelope.get("kind") or "") == "tool_requests"
                and str(envelope.get("next") or "") == "continue"
                and bool(requested_tools)
            )
            if not should_continue:
                services.set_run_output(
                    request.run_id,
                    output_text = last_reply,
                    output_kind = "assistant_text",
                    metadata    = {
                        "thread_path":              request.thread_path,
                        "active_path":              request.active_path,
                        "conversation_external_id": conversation_id,
                    },
                )
                services.update_run(
                    request.run_id,
                    status                   = "completed",
                    conversation_external_id = conversation_id,
                    event_type               = "agent_run_completed",
                    event_payload            = {
                        "turn_count": turn_index + 1,
                    },
                )
                return

            if turn_index >= MAX_AGENT_TOOL_TURNS:
                raise RuntimeError("Agent exceeded maximum tool-followup rounds")

            services.update_run(
                request.run_id,
                status       = "running_tools",
                event_type   = "tool_requests_received",
                event_payload = {
                    "turn_index":     turn_index,
                    "request_count":  len(requested_tools),
                },
            )

            tool_results = services.execute_tool_requests(
                tool_requests             = requested_tools,
                active_path               = request.active_path if request.active_path not in {"", "."} else None,
                workspace_context_enabled = request.workspace_context_enabled,
            )
            _append_tool_results(request.run_id, requested_tools, tool_results, services)

            followup_prompt = services.build_tool_followup_prompt(
                mode              = request.mode,
                path              = request.active_path,
                user_text         = request.user_text,
                previous_response = last_reply,
                tool_results      = tool_results,
            )
            followed = services.append_internal_followup(
                request.workspace_root,
                request.thread_path,
                followup_prompt,
                request.user_text,
                conversation_external_id  = conversation_id,
                outbound_sender_display   = "agent",
                workspace_context_enabled = request.workspace_context_enabled,
            )
            conversation_id = str(followed.get("external_id") or conversation_id or "").strip() or None
            services.update_run(
                request.run_id,
                status                   = "waiting_agent",
                conversation_external_id = conversation_id,
                conversation_id          = followed.get("conversation_id"),
                event_type               = "tool_followup_appended",
                event_payload            = {
                    "turn_index": turn_index,
                },
            )
            previous_signature = _assistant_signature(followed.get("last_assistant"))

        raise RuntimeError("Agent loop terminated unexpectedly")
    except Exception as exc:
        if last_reply:
            services.set_run_output(
                request.run_id,
                output_text = last_reply,
                output_kind = "assistant_text",
                metadata    = {
                    "thread_path": request.thread_path,
                    "active_path": request.active_path,
                    "partial":     True,
                },
            )
        services.update_run(
            request.run_id,
            status       = "failed",
            error        = {"message": str(exc)},
            event_type   = "agent_run_failed",
            event_payload = {
                "thread_path": request.thread_path,
                "active_path": request.active_path,
            },
        )


def execute_continue_run(request: ContinueRunRequest, services: AgentRunServices) -> None:
    try:
        services.update_run(
            request.run_id,
            status       = "queued",
            event_type   = "continue_prompt_ready",
            event_payload = {
                "thread_path": request.thread_path,
                "active_path": request.active_path,
                "offset":      request.offset,
            },
        )

        started = services.append_internal_followup(
            request.workspace_root,
            request.thread_path,
            request.prompt,
            "",
            conversation_external_id  = request.conversation_external_id,
            outbound_sender_display   = "__korecode_internal__",
            workspace_context_enabled = request.workspace_context_enabled,
        )
        services.update_run(
            request.run_id,
            status                   = "waiting_agent",
            conversation_external_id = started.get("external_id"),
            conversation_id          = started.get("conversation_id"),
            event_type               = "continue_append_completed",
            event_payload            = {
                "pending_response": bool(started.get("pending_response")),
            },
        )

        settled = _wait_for_agent_turn(
            workspace_root            = request.workspace_root,
            thread_path               = request.thread_path,
            conversation_external_id  = str(started.get("external_id") or request.conversation_external_id or "").strip() or None,
            workspace_context_enabled = request.workspace_context_enabled,
            get_thread_fn             = services.get_thread,
            previous_signature        = _assistant_signature(started.get("last_assistant")),
        )
        reply = str((settled.get("last_assistant") or {}).get("content") or "")
        services.append_model_response(
            request.run_id,
            role     = "assistant",
            content  = reply,
            metadata = {
                "message_id":   (settled.get("last_assistant") or {}).get("id"),
                "created_at":   (settled.get("last_assistant") or {}).get("created_at"),
                "thread_path":  request.thread_path,
                "active_path":  request.active_path,
            },
        )
        insertion = reply.replace("\r\n", "\n").lstrip("\n")
        if not insertion.strip():
            raise RuntimeError("Continue returned no content")

        services.set_run_output(
            request.run_id,
            output_text = insertion,
            output_kind = "continue_insert",
            metadata    = {
                "thread_path":              request.thread_path,
                "active_path":              request.active_path,
                "offset":                   request.offset,
                "line_count":               len(insertion.split("\n")),
                "conversation_external_id": settled.get("external_id"),
            },
        )
        services.update_run(
            request.run_id,
            status                   = "completed",
            conversation_external_id = settled.get("external_id"),
            event_type               = "continue_run_completed",
            event_payload            = {
                "offset":     request.offset,
                "line_count": len(insertion.split("\n")),
            },
        )
    except Exception as exc:
        services.update_run(
            request.run_id,
            status       = "failed",
            error        = {"message": str(exc)},
            event_type   = "continue_run_failed",
            event_payload = {
                "thread_path": request.thread_path,
                "active_path": request.active_path,
                "offset":      request.offset,
            },
        )


def _append_tool_results(
    run_id: str,
    tool_requests: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
    services: AgentRunServices,
) -> None:
    for item in tool_results:
        index = int(item.get("request_index") or 0)
        args  = tool_requests[index].get("args", {}) if 0 <= index < len(tool_requests) else {}
        services.append_tool_call(
            run_id,
            tool_name     = str(item.get("tool") or ""),
            request_index = index,
            ok            = bool(item.get("ok")),
            request_args  = args if isinstance(args, dict) else {},
            result        = item.get("result") if item.get("ok") else None,
            error         = item.get("error") if not item.get("ok") else None,
        )


def _assistant_signature(message: dict[str, Any] | None) -> str | None:
    if not isinstance(message, dict):
        return None
    parts = [
        str(message.get("id") or "").strip(),
        str(message.get("created_at") or "").strip(),
        str(message.get("content") or ""),
    ]
    signature = "|".join(parts).strip("|")
    return signature or None


def _wait_for_agent_turn(
    *,
    workspace_root: Path,
    thread_path: str,
    conversation_external_id: str | None,
    workspace_context_enabled: bool,
    get_thread_fn,
    previous_signature: str | None,
    timeout_seconds: float = DEFAULT_WAIT_TIMEOUT_S,
) -> dict[str, Any]:
    started_at = time.monotonic()
    while True:
        payload = get_thread_fn(
            workspace_root,
            thread_path,
            create                    = False,
            conversation_external_id  = conversation_external_id,
            workspace_context_enabled = workspace_context_enabled,
        )
        if not bool(payload.get("pending_response")):
            current_signature = _assistant_signature(payload.get("last_assistant"))
            if current_signature and current_signature != previous_signature:
                return payload
        if (time.monotonic() - started_at) >= timeout_seconds:
            raise TimeoutError(f"Timed out waiting for agent reply after {timeout_seconds:.0f}s")
        time.sleep(POLL_INTERVAL_SECONDS)
