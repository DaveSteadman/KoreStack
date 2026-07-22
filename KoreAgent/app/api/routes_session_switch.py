from fastapi import HTTPException
from pydantic import BaseModel


class SessionSwitchRequest(BaseModel):
    name: str = ""
    conversation_id: int | None = None


def register_session_switch_routes(app, *, validate_session_id, set_pending_switch) -> None:
    @app.post("/api/sessions/request-switch", status_code=200)
    @app.post("/sessions/request-switch", status_code=200, include_in_schema=False)
    def post_request_switch(body: SessionSwitchRequest):
        from input_layer.slash_command_handlers_sessions import (
            _display_name,
            _list_all_conversations,
            _session_id_from_external_id,
        )

        conversations = _list_all_conversations()
        conv = None

        if body.conversation_id is not None:
            conv = next((c for c in conversations if int(c.get("id") or 0) == int(body.conversation_id)), None)
            if conv is None:
                raise HTTPException(status_code=404, detail=f"No conversation with id '{body.conversation_id}' found.")
        else:
            target = body.name.strip().lower()
            if not target:
                raise HTTPException(status_code=400, detail="name or conversation_id is required.")
            conv = next((c for c in conversations if _display_name(c).lower() == target), None)
            if conv is None:
                conv = next((c for c in conversations if target in _display_name(c).lower()), None)
            if conv is None:
                raise HTTPException(status_code=404, detail=f"No conversation named '{body.name}' found.")

        external_id = str(conv.get("external_id") or "")
        if external_id.startswith("webchat_"):
            session_id = _session_id_from_external_id(external_id)
        else:
            session_id = f"kc_conv_{conv['id']}"
        name = _display_name(conv)
        validate_session_id(session_id)
        set_pending_switch({"session_id": session_id, "name": name})
        return {"ok": True}
