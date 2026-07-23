from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Query
from fastapi.responses import Response

from app import database as db
from app.api.state import INPUT_HISTORY_MAX
from app.models.api_models import ConversationCreateRequest
from app.models.api_models import ConversationPatchRequest
from app.models.api_models import DefaultChatCullRequest
from app.models.api_models import InputHistoryAppendRequest
from app.runtime.stream import push_event


router = APIRouter()


def _require_conversation(conversation_id: int) -> dict:
    conversation = db.conversation_get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.post("/api/conversations", status_code=201)
@router.post("/conversations", status_code=201, include_in_schema=False)
def create_conversation(req: ConversationCreateRequest):
    result = db.conversation_create(
        channel_type       = req.channel_type,
        subject            = req.subject,
        protected          = req.protected,
        background_context = req.background_context,
        profile            = req.profile,
        external_id        = req.external_id,
        tools_active       = req.tools_active,
    )
    push_event("conv_created", result["id"])
    return result


@router.get("/api/conversations/by-external-id/{external_id}")
@router.get("/conversations/by-external-id/{external_id}", include_in_schema=False)
def get_conversation_by_external_id(external_id: str):
    conversation = db.conversation_get_by_external_id(external_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.get("/api/conversations/by-external-id/{external_id}/turns")
@router.get("/conversations/by-external-id/{external_id}/turns", include_in_schema=False)
def get_conversation_turns_by_external_id(external_id: str):
    messages = db.conversation_get_turns_by_external_id(external_id)
    if messages is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"messages": messages}


@router.get("/api/conversations")
@router.get("/conversations", include_in_schema=False)
def list_conversations(
    status: str | None = Query(default=None),
    channel_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    return db.conversation_list(
        status       = status,
        channel_type = channel_type,
        limit        = limit,
        offset       = offset,
    )


@router.get("/api/conversations/{conversation_id}")
@router.get("/conversations/{conversation_id}", include_in_schema=False)
def get_conversation(conversation_id: int):
    conversation = db.conversation_get_with_messages(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.get("/api/conversations/{conversation_id}/detail")
@router.get("/conversations/{conversation_id}/detail", include_in_schema=False)
def get_conversation_detail(conversation_id: int):
    detail = db.conversation_get_detail(conversation_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return detail


@router.patch("/api/conversations/{conversation_id}")
@router.patch("/conversations/{conversation_id}", include_in_schema=False)
def patch_conversation(conversation_id: int, req: ConversationPatchRequest):
    result = db.conversation_update(
        conversation_id    = conversation_id,
        status             = req.status,
        subject            = req.subject,
        protected          = req.protected,
        thread_summary     = req.thread_summary,
        scratchpad         = req.scratchpad,
        datasets           = req.datasets,
        tools_active       = req.tools_active,
        background_context = req.background_context,
        token_estimate     = req.token_estimate,
        turn_count         = req.turn_count,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    push_event("conv_updated", conversation_id)
    return result


@router.delete("/api/conversations/{conversation_id}", status_code=204)
@router.delete("/conversations/{conversation_id}", status_code=204, include_in_schema=False)
def delete_conversation(conversation_id: int):
    _require_conversation(conversation_id)
    db.conversation_delete(conversation_id)
    push_event("conv_deleted", conversation_id)
    return Response(status_code=204)


@router.post("/api/maintenance/default-chat-cull")
@router.post("/maintenance/default-chat-cull", include_in_schema=False)
def cull_default_chats(req: DefaultChatCullRequest):
    if req.max_default_chat_age_days not in (1, 3, 7, 30):
        raise HTTPException(status_code=400, detail="max_default_chat_age_days must be one of 1, 3, 7, 30")
    deleted_ids = db.conversation_cull_default_inactive(req.max_default_chat_age_days)
    for conversation_id in deleted_ids:
        push_event("conv_deleted", conversation_id)
    return {
        "max_default_chat_age_days": req.max_default_chat_age_days,
        "deleted_count": len(deleted_ids),
        "deleted_ids": deleted_ids,
    }


@router.get("/api/conversations/{conversation_id}/input-history")
@router.get("/conversations/{conversation_id}/input-history", include_in_schema=False)
def get_conversation_input_history(conversation_id: int):
    _require_conversation(conversation_id)
    return {"entries": db.conversation_get_input_history(conversation_id)}


@router.patch("/api/conversations/{conversation_id}/input-history")
@router.patch("/conversations/{conversation_id}/input-history", include_in_schema=False)
def patch_conversation_input_history(conversation_id: int, req: InputHistoryAppendRequest):
    _require_conversation(conversation_id)
    text = str(req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text cannot be empty")
    entries = db.conversation_append_input_history(conversation_id, text, INPUT_HISTORY_MAX)
    return {"entries": entries}
