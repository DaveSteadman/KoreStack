from fastapi import HTTPException
from pydantic import BaseModel


class HistoryAppendRequest(BaseModel):
    text: str


def register_input_history_routes(app, *, validate_session_id, session_service, history_limit: int = 20) -> None:
    @app.get("/api/sessions/{session_id}/input-history")
    @app.get("/sessions/{session_id}/input-history", include_in_schema=False)
    def get_session_input_history(session_id: str):
        validate_session_id(session_id)
        conv = session_service.kc_get_conversation_for_session(session_id)
        if conv is None:
            return {"entries": []}
        try:
            result = session_service.kc_get(f"/api/conversations/{conv['id']}/input-history")
            entries = result.get("entries", []) if isinstance(result, dict) else []
        except HTTPException:
            entries = []
        return {"entries": entries[-history_limit:]}

    @app.post("/api/sessions/{session_id}/input-history")
    @app.post("/sessions/{session_id}/input-history", include_in_schema=False)
    def post_session_input_history(session_id: str, body: HistoryAppendRequest):
        validate_session_id(session_id)
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text cannot be empty")
        conv = session_service.kc_ensure_conversation(session_id)
        if conv is None:
            return {"entries": [text]}
        try:
            result = session_service.kc_patch(f"/api/conversations/{conv['id']}/input-history", {"text": text})
            entries = result.get("entries", []) if isinstance(result, dict) else []
        except HTTPException:
            entries = [text]
        return {"entries": entries[-history_limit:]}
