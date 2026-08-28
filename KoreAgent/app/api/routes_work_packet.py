from __future__ import annotations

import json

from fastapi import HTTPException
from pydantic import BaseModel


class WorkPacketRequest(BaseModel):
    json_text: str


def register_work_packet_routes(
    app,
    *,
    call_llm_chat,
    get_active_model,
    get_active_num_ctx,
) -> None:
    """Register the deliberately thin, stateless JSON-to-LLM endpoint."""

    @app.post("/api/work-packet")
    def submit_work_packet(body: WorkPacketRequest) -> dict:
        packet_text = body.json_text.strip()
        if not packet_text:
            raise HTTPException(status_code=400, detail="Work packet cannot be empty")
        try:
            json.loads(packet_text)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}",
            ) from exc

        model = get_active_model()
        if not model:
            raise HTTPException(status_code=503, detail="No model is configured")

        try:
            result = call_llm_chat(
                model_name=model,
                messages=[{"role": "user", "content": packet_text}],
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
