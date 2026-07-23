from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Query

from app import database as db
from app.models.api_models import MessageAppendRequest
from app.models.api_models import MessagePatchRequest
from app.models.api_models import TurnAppendRequest
from app.runtime.stream import push_event


router = APIRouter()


def _require_conversation(conversation_id: int) -> None:
    if db.conversation_get(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")


@router.post("/api/conversations/{conversation_id}/messages", status_code=201)
@router.post("/conversations/{conversation_id}/messages", status_code=201, include_in_schema=False)
def append_message(conversation_id: int, req: MessageAppendRequest):
    _require_conversation(conversation_id)
    message = db.message_append(
        conversation_id = conversation_id,
        direction       = req.direction,
        content         = req.content,
        sender_display  = req.sender_display,
        status          = req.status,
    )
    if req.direction == "inbound":
        db.ensure_response_needed_event(conversation_id, payload=req.response_payload or {})
        db.conversation_update(conversation_id=conversation_id, status="waiting_agent")
    elif req.direction == "outbound":
        db.clear_pending_response_needed_events(conversation_id)
        db.conversation_update(conversation_id=conversation_id, status="active")
    push_event("message_added", conversation_id)
    return message


@router.post("/api/conversations/{conversation_id}/turns")
@router.post("/conversations/{conversation_id}/turns", include_in_schema=False)
def append_turn(conversation_id: int, req: TurnAppendRequest):
    _require_conversation(conversation_id)
    inbound_content = str(req.inbound_content or "").strip()
    outbound_content = str(req.outbound_content or "").strip()
    if not inbound_content or not outbound_content:
        raise HTTPException(status_code=400, detail="Both inbound_content and outbound_content are required")
    result = db.conversation_append_turn(
        conversation_id  = conversation_id,
        inbound_content  = inbound_content,
        outbound_content = outbound_content,
        inbound_sender   = req.inbound_sender,
        outbound_sender  = req.outbound_sender,
        token_estimate   = req.token_estimate,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    push_event("message_added", conversation_id)
    push_event("conv_updated", conversation_id)
    return result


@router.get("/api/conversations/{conversation_id}/messages")
@router.get("/conversations/{conversation_id}/messages", include_in_schema=False)
def list_messages(
    conversation_id: int,
    summarised: int | None = Query(default=None),
    direction: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    _require_conversation(conversation_id)
    return db.message_list(
        conversation_id = conversation_id,
        summarised      = summarised,
        direction       = direction,
        limit           = limit,
    )


@router.patch("/api/messages/{message_id}")
@router.patch("/messages/{message_id}", include_in_schema=False)
def patch_message(message_id: int, req: MessagePatchRequest):
    result = db.message_update(
        message_id = message_id,
        status     = req.status,
        summarised = req.summarised,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return result
