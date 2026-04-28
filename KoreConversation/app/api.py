# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FastAPI application for KoreConversation.
#
# Exposes four resource groups:
#   /conversations  - CRUD for conversation records
#   /messages       - append and update messages within a conversation
#   /events         - event queue with atomic claim for cross-service coordination
#   /ui             - browser debug view: all conversations and their full data
#
# A background reaper thread runs every 60 seconds to release stale event claims
# so a crashed consumer cannot permanently block a conversation.
# ====================================================================================================

import json
import logging
import os
import queue
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Query
from fastapi.responses import FileResponse
from fastapi.responses import Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import database as db

logger = logging.getLogger(__name__)


# ====================================================================================================
# MARK: REAPER
# ====================================================================================================

def _reaper_loop(stop_event: threading.Event) -> None:
    while not stop_event.wait(60):
        try:
            released = db.release_stale_claims()
            if released:
                logger.info("Reaper released %d stale claim(s)", released)
        except Exception as exc:
            logger.warning("Reaper error: %s", exc)
        try:
            cleared = db.clear_stale_outbound_ready()
            if cleared:
                logger.info("Reaper cleared %d stale outbound_ready event(s)", cleared)
        except Exception as exc:
            logger.warning("Reaper outbound_ready cleanup error: %s", exc)


# ====================================================================================================
# MARK: SSE PUSH
# ====================================================================================================
# Lightweight broadcaster: any mutation endpoint calls _kc_push() with a small event dict.
# All connected /stream clients receive it immediately, eliminating the 5-second poll cycle.

_kc_subscribers:      list[queue.Queue] = []
_kc_subscribers_lock: threading.Lock    = threading.Lock()


def _kc_push(event_type: str, conversation_id: int | None = None) -> None:
    """Broadcast a change notification to all connected SSE clients."""
    item = {"type": event_type}
    if conversation_id is not None:
        item["conversation_id"] = conversation_id
    with _kc_subscribers_lock:
        dead = []
        for sub in _kc_subscribers:
            try:
                sub.put_nowait(item)
            except queue.Full:
                dead.append(sub)
        for sub in dead:
            try:
                _kc_subscribers.remove(sub)
            except ValueError:
                pass


# ====================================================================================================
# MARK: LIFESPAN
# ====================================================================================================

_stop_reaper: threading.Event = threading.Event()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    _stop_reaper.clear()
    reaper = threading.Thread(target=_reaper_loop, args=(_stop_reaper,), daemon=True)
    reaper.start()
    yield
    _stop_reaper.set()


# ====================================================================================================
# MARK: APP
# ====================================================================================================

app = FastAPI(title="KoreConversation", lifespan=lifespan)

_UI_DIR = Path(__file__).resolve().parent / "ui"
_UI_ELEMENTS_ASSETS = Path(
    os.environ.get(
        "KORE_UIELEMENTS_ASSETS_DIR",
        str(Path(__file__).resolve().parents[2] / "UIElements" / "assets"),
    )
).resolve()


# ====================================================================================================
# MARK: STATUS
# ====================================================================================================

@app.get("/", include_in_schema=False)
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/ui")


# ----------------------------------------------------------------------------------------------------
@app.get("/status")
def status():
    return {
        "status":        "ok",
        "conversations":  db.conversation_counts(),
        "events":         db.event_counts(),
    }


# ====================================================================================================
# MARK: CONVERSATIONS
# ====================================================================================================

class ConversationCreateRequest(BaseModel):
    channel_type:       str
    subject:            str | None = None
    background_context: str        = ""
    profile:            str | None = None
    external_id:        str | None = None


class ConversationPatchRequest(BaseModel):
    status:             str | None  = None
    subject:            str | None  = None
    thread_summary:     str | None  = None
    scratchpad:         dict | None = None
    background_context: str | None  = None
    token_estimate:     int | None  = None
    turn_count:         int | None  = None


# ----------------------------------------------------------------------------------------------------
@app.post("/conversations", status_code=201)
def create_conversation(req: ConversationCreateRequest):
    result = db.conversation_create(
        channel_type       = req.channel_type,
        subject            = req.subject,
        background_context = req.background_context,
        profile            = req.profile,
        external_id        = req.external_id,
    )
    _kc_push("conv_created", result["id"])
    return result


# ----------------------------------------------------------------------------------------------------
@app.get("/conversations/by-external-id/{external_id}")
def get_conversation_by_external_id(external_id: str):
    """Return the conversation whose external_id matches exactly, or 404."""
    conv = db.conversation_get_by_external_id(external_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


# ----------------------------------------------------------------------------------------------------
@app.get("/conversations/by-external-id/{external_id}/turns")
def get_conversation_turns_by_external_id(external_id: str):
    """Return raw inbound/outbound messages for the conversation - single DB call, no extra data."""
    messages = db.conversation_get_turns_by_external_id(external_id)
    if messages is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"messages": messages}


# ----------------------------------------------------------------------------------------------------
@app.get("/conversations")
def list_conversations(
    status:       str | None = Query(default=None),
    channel_type: str | None = Query(default=None),
    limit:        int        = Query(default=50, ge=1, le=500),
    offset:       int        = Query(default=0, ge=0),
):
    return db.conversation_list(
        status       = status,
        channel_type = channel_type,
        limit        = limit,
        offset       = offset,
    )


# ----------------------------------------------------------------------------------------------------
@app.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int):
    conv = db.conversation_get_with_messages(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


# ----------------------------------------------------------------------------------------------------
@app.get("/conversations/{conversation_id}/detail")
def get_conversation_detail(conversation_id: int):
    """Return conversation + messages + events in a single response for fast UI population."""
    detail = db.conversation_get_detail(conversation_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return detail


# ----------------------------------------------------------------------------------------------------
@app.patch("/conversations/{conversation_id}")
def patch_conversation(conversation_id: int, req: ConversationPatchRequest):
    result = db.conversation_update(
        conversation_id    = conversation_id,
        status             = req.status,
        subject            = req.subject,
        thread_summary     = req.thread_summary,
        scratchpad         = req.scratchpad,
        background_context = req.background_context,
        token_estimate     = req.token_estimate,
        turn_count         = req.turn_count,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _kc_push("conv_updated", conversation_id)
    return result


# ----------------------------------------------------------------------------------------------------
@app.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: int):
    conv = db.conversation_get(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.conversation_delete(conversation_id)
    _kc_push("conv_deleted", conversation_id)
    return Response(status_code=204)


# ====================================================================================================
# MARK: INPUT HISTORY
# ====================================================================================================

_INPUT_HISTORY_MAX = 32


class InputHistoryAppendRequest(BaseModel):
    text: str


# ----------------------------------------------------------------------------------------------------
@app.get("/conversations/{conversation_id}/input-history")
def get_conversation_input_history(conversation_id: int):
    """Return all stored input-history entries for a conversation."""
    conv = db.conversation_get(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"entries": db.conversation_get_input_history(conversation_id)}


# ----------------------------------------------------------------------------------------------------
@app.patch("/conversations/{conversation_id}/input-history")
def patch_conversation_input_history(conversation_id: int, req: InputHistoryAppendRequest):
    """Append one entry to the conversation's input history (dedup, capped at _INPUT_HISTORY_MAX)."""
    conv = db.conversation_get(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text cannot be empty")
    entries = db.conversation_get_input_history(conversation_id)
    # Erase-dups: remove any prior occurrence so each entry appears only once.
    entries = [e for e in entries if e != text]
    entries.append(text)
    if len(entries) > _INPUT_HISTORY_MAX:
        entries = entries[-_INPUT_HISTORY_MAX:]
    db.conversation_set_input_history(conversation_id, entries)
    return {"entries": entries}


# ====================================================================================================
# MARK: EVENTS LIST
# ====================================================================================================

@app.get("/events")
def list_events(
    conversation_id: int | None = Query(default=None),
    status:          str | None = Query(default=None),
    limit:           int        = Query(default=200, ge=1, le=1000),
):
    return db.event_list(
        conversation_id = conversation_id,
        status          = status,
        limit           = limit,
    )


# ====================================================================================================
# MARK: UI
# ====================================================================================================
# Static files are served with Cache-Control: no-store so the browser always fetches the
# current version from disk. FastAPI/Starlette StaticFiles mounts are NOT used here because
# they take routing priority and prevent the no-store header being set per file.

_NO_STORE = {"Cache-Control": "no-store"}


@app.get("/ui", include_in_schema=False)
def serve_ui():
    return FileResponse(str(_UI_DIR / "conversations.html"), headers=_NO_STORE)


@app.get("/ui/conversations.js", include_in_schema=False)
def serve_ui_js():
    return FileResponse(str(_UI_DIR / "conversations.js"), headers=_NO_STORE)


@app.get("/ui/conversations.css", include_in_schema=False)
def serve_ui_css():
    return FileResponse(str(_UI_DIR / "conversations.css"), headers=_NO_STORE)


@app.get("/ui-elements/assets/{asset_path:path}", include_in_schema=False)
def serve_ui_elements_asset(asset_path: str):
    candidate = (_UI_ELEMENTS_ASSETS / asset_path).resolve()
    if candidate != _UI_ELEMENTS_ASSETS and _UI_ELEMENTS_ASSETS not in candidate.parents:
        raise HTTPException(status_code=404, detail="Asset not found")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(str(candidate), headers=_NO_STORE)


# ====================================================================================================
# MARK: SSE STREAM
# ====================================================================================================

@app.get("/stream", include_in_schema=False)
def stream_events():
    """SSE push stream - clients subscribe once and receive change notifications in real time."""
    sub: queue.Queue = queue.Queue(maxsize=64)
    with _kc_subscribers_lock:
        _kc_subscribers.append(sub)

    def generate():
        try:
            while True:
                try:
                    item = sub.get(timeout=20)
                    yield f"data: {json.dumps(item)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            with _kc_subscribers_lock:
                try:
                    _kc_subscribers.remove(sub)
                except ValueError:
                    pass

    return StreamingResponse(
        generate(),
        media_type = "text/event-stream",
        headers    = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ====================================================================================================
# MARK: MESSAGES
# ====================================================================================================

class MessageAppendRequest(BaseModel):
    direction:      str
    content:        str
    sender_display: str = ""
    status:         str = "received"


class MessagePatchRequest(BaseModel):
    status:     str | None = None
    summarised: int | None = None


# ----------------------------------------------------------------------------------------------------
@app.post("/conversations/{conversation_id}/messages", status_code=201)
def append_message(conversation_id: int, req: MessageAppendRequest):
    conv = db.conversation_get(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msg = db.message_append(
        conversation_id = conversation_id,
        direction       = req.direction,
        content         = req.content,
        sender_display  = req.sender_display,
        status          = req.status,
    )
    # Inbound messages need an agent response - create the event so the agent picks it up.
    if req.direction == "inbound":
        db.ensure_response_needed_event(conversation_id)
        db.conversation_update(conversation_id=conversation_id, status="waiting_agent")
    elif req.direction == "outbound":
        db.clear_pending_response_needed_events(conversation_id)
        db.conversation_update(conversation_id=conversation_id, status="active")
    _kc_push("message_added", conversation_id)
    return msg


# ----------------------------------------------------------------------------------------------------
@app.get("/conversations/{conversation_id}/messages")
def list_messages(
    conversation_id: int,
    summarised:      int | None = Query(default=None),
    direction:       str | None = Query(default=None),
    limit:           int        = Query(default=200, ge=1, le=1000),
):
    conv = db.conversation_get(conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return db.message_list(
        conversation_id = conversation_id,
        summarised      = summarised,
        direction       = direction,
        limit           = limit,
    )


# ----------------------------------------------------------------------------------------------------
@app.patch("/messages/{message_id}")
def patch_message(message_id: int, req: MessagePatchRequest):
    result = db.message_update(
        message_id = message_id,
        status     = req.status,
        summarised = req.summarised,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return result


# ====================================================================================================
# MARK: EVENTS
# ====================================================================================================

class EventCreateRequest(BaseModel):
    conversation_id: int | None = None
    event_type:      str
    priority:        int        = 0
    payload:         dict       = {}


class EventCompleteRequest(BaseModel):
    status: str = "completed"


# ----------------------------------------------------------------------------------------------------
@app.post("/events", status_code=201)
def create_event(req: EventCreateRequest):
    if req.conversation_id is not None:
        conv = db.conversation_get(req.conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
    return db.event_create(
        conversation_id = req.conversation_id,
        event_type      = req.event_type,
        priority        = req.priority,
        payload         = req.payload,
    )


# ----------------------------------------------------------------------------------------------------
@app.get("/events/next")
def get_next_event(claimed_by: str = Query(..., description="Identifier of the claiming service")):
    event = db.event_claim_next(claimed_by)
    if event is None:
        return Response(status_code=204)
    # Include the conversation record alongside the event so the consumer gets everything
    # it needs in one call without a follow-up GET /conversations/{id}.
    result = dict(event)
    if result.get("conversation_id"):
        result["conversation"] = db.conversation_get_with_messages(result["conversation_id"])
    return result


# ----------------------------------------------------------------------------------------------------
@app.post("/events/{event_id}/complete")
def complete_event(event_id: int, req: EventCompleteRequest):
    result = db.event_complete(event_id, status=req.status)
    if result is None:
        raise HTTPException(status_code=404, detail="Event not found")
    _kc_push("event_completed", result.get("conversation_id"))
    return result
