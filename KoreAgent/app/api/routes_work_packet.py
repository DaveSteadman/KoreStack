# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Registers the stateless work-packet endpoint. The endpoint validates caller-supplied JSON and
# forwards the packet to the active model without persisting a conversation.
#
# Public API:
#   - register_work_packet_routes() -- binds the work-packet route to an application instance.
#
# The nested submit_work_packet route is intentionally scoped to registration because it closes
# over the injected LLM and active-model providers.
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
from __future__ import annotations

import json

from fastapi import HTTPException
from pydantic import BaseModel


# ====================================================================================================
# MARK: REQUEST MODEL
# ====================================================================================================
class WorkPacketRequest(BaseModel):
    json_text: str


# ====================================================================================================
# MARK: ROUTE REGISTRATION (PUBLIC)
# ====================================================================================================
def register_work_packet_routes(
    app,
    *,
    call_llm_chat,
    get_active_model,
    get_active_num_ctx,
    call_system_one=None,
    get_active_system_one_model=None,
) -> None:
    """Register the deliberately thin, stateless JSON-to-LLM endpoint.

    A top-level ``"route": "system_one"`` sends the packet to the System One decision API
    (``state``, ``questions``, optional ``images`` and ``model``); anything else goes to chat.
    Chat packets may optionally provide ``prompt`` and ``model`` to override the default prompt text
    and active chat model.
    """

    @app.post("/api/work-packet")
    def submit_work_packet(body: WorkPacketRequest) -> dict:
        packet_text = body.json_text.strip()
        if not packet_text:
            raise HTTPException(status_code=400, detail="Work packet cannot be empty")
        try:
            packet = json.loads(packet_text)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}",
            ) from exc

        route = str(packet.get("route") or "").strip().lower().replace("-", "_") if isinstance(packet, dict) else ""
        if route in {"system_one", "systemone"}:
            if call_system_one is None:
                raise HTTPException(status_code=501, detail="System One is not available")
            if "state" not in packet or "questions" not in packet:
                raise HTTPException(status_code=400, detail="System One packets require 'state' and 'questions'")
            model = str(packet.get("model") or (get_active_system_one_model() if get_active_system_one_model else "")).strip()
            try:
                result = call_system_one(
                    state=packet["state"],
                    questions=packet["questions"],
                    images=packet.get("images"),
                    model_name=model or None,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            return {
                "response": json.dumps(result.answers, indent=2),
                "model": result.model,
                "finish_reason": "stop",
                "prompt_tokens": result.input_tokens,
                "completion_tokens": result.output_tokens,
                "tokens_per_second": 0,
            }

        model = str(packet.get("model") or get_active_model()).strip() if isinstance(packet, dict) else str(get_active_model()).strip()
        if not model:
            raise HTTPException(status_code=503, detail="No model is configured")
        prompt_text = str(packet.get("prompt") or "") if isinstance(packet, dict) and "prompt" in packet else packet_text

        try:
            result = call_llm_chat(
                model_name=model,
                messages=[{"role": "user", "content": prompt_text}],
                tools=None,
                num_ctx=get_active_num_ctx(),
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        return {
            "response": result.response,
            "model": model,
            "finish_reason": result.finish_reason,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "tokens_per_second": result.tokens_per_second,
        }
