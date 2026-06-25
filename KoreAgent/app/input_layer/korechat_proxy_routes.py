from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Narrow KoreChat proxy routes exposed by the input layer.
#
# These endpoints provide the web UI with just enough conversation/message access
# to interact with KoreChat without embedding the full KoreChat API surface into
# the input-layer server module.
# ====================================================================================================

import urllib.parse

from fastapi import HTTPException
from pydantic import BaseModel


class KcSendRequest(BaseModel):
    session_id: str
    content:    str


def register_korechat_proxy_routes(
    app,
    *,
    validate_session_id,
    kc_get_async,
    kc_post_async,
) -> None:
    @app.post("/api/kc/send", status_code=201)
    @app.post("/kc/send", status_code=201, include_in_schema=False)
    async def kc_send(body: KcSendRequest):
        validate_session_id(body.session_id)
        content = (body.content or "").strip()
        if not content:
            raise HTTPException(status_code=400, detail="content cannot be empty")

        external_id = f"webchat_{body.session_id}"
        conv_id: int | None = None
        try:
            existing = await kc_get_async(f"/conversations/by-external-id/{urllib.parse.quote(external_id, safe='')}")
            if isinstance(existing, dict) and existing.get("id"):
                conv_id = int(existing["id"])
        except HTTPException as exc:
            if exc.status_code != 404:
                raise

        if conv_id is None:
            new_conv = await kc_post_async("/conversations", {
                "channel_type": "webchat",
                "subject":      f"Webchat {body.session_id}",
                "protected":    False,
                "external_id":  external_id,
            })
            if not new_conv:
                raise HTTPException(status_code=502, detail="Failed to create KC conversation")
            conv_id = new_conv["id"]

        msg = await kc_post_async(f"/conversations/{conv_id}/messages", {
            "direction":      "inbound",
            "content":        content,
            "sender_display": body.session_id,
            "status":         "received",
        })
        if not msg:
            raise HTTPException(status_code=502, detail="Failed to append message to KC conversation")

        return {"conv_id": conv_id, "msg_id": msg.get("id")}

    @app.get("/api/kc/conversations/{conv_id}/messages")
    @app.get("/kc/conversations/{conv_id}/messages", include_in_schema=False)
    async def kc_get_messages(conv_id: int, limit: int = 100):
        return await kc_get_async(f"/conversations/{conv_id}/messages?limit={limit}") or []

    @app.get("/api/kc/conversations/{conv_id}")
    @app.get("/kc/conversations/{conv_id}", include_in_schema=False)
    async def kc_get_conversation(conv_id: int):
        result = await kc_get_async(f"/conversations/{conv_id}")
        if result is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return result
