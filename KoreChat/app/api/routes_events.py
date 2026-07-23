from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Query
from fastapi.responses import Response

from app import database as db
from app.models.api_models import EventCompleteRequest
from app.models.api_models import EventCreateRequest
from app.runtime.stream import push_event


router = APIRouter()


@router.get("/api/events")
@router.get("/events", include_in_schema=False)
def list_events(
    conversation_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    return db.event_list(
        conversation_id = conversation_id,
        status          = status,
        limit           = limit,
    )


@router.post("/api/events", status_code=201)
@router.post("/events", status_code=201, include_in_schema=False)
def create_event(req: EventCreateRequest):
    if req.conversation_id is not None and db.conversation_get(req.conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return db.event_create(
        conversation_id = req.conversation_id,
        event_type      = req.event_type,
        priority        = req.priority,
        payload         = req.payload,
    )


@router.get("/api/events/next")
@router.get("/events/next", include_in_schema=False)
def get_next_event(claimed_by: str = Query(..., description="Identifier of the claiming service")):
    event = db.event_claim_next(claimed_by)
    if event is None:
        return Response(status_code=204)
    result = dict(event)
    if result.get("conversation_id"):
        result["conversation"] = db.conversation_get_with_messages(result["conversation_id"])
    return result


@router.post("/api/events/{event_id}/complete")
@router.post("/events/{event_id}/complete", include_in_schema=False)
def complete_event(event_id: int, req: EventCompleteRequest):
    result = db.event_complete(event_id, status=req.status)
    if result is None:
        raise HTTPException(status_code=404, detail="Event not found")
    push_event("event_completed", result.get("conversation_id"))
    return result
