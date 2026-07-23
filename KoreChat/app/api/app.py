import sys
from pathlib import Path

from fastapi import FastAPI

_KORECOMMON_PARENT = next((parent for parent in Path(__file__).resolve().parents if (parent / "KoreCommon").is_dir()), None)
if _KORECOMMON_PARENT is not None and str(_KORECOMMON_PARENT) not in sys.path:
    sys.path.insert(0, str(_KORECOMMON_PARENT))

from KoreCommon.service_app import register_endpoint_manifest
from app import database as db
from app.api.routes_conversations import router as conversations_router
from app.api.routes_events import router as events_router
from app.api.routes_messages import router as messages_router
from app.api.routes_ui import router as ui_router
from app.api.startup import lifespan


app = FastAPI(title="KoreChat", lifespan=lifespan)
register_endpoint_manifest(app, service_key="korechat", service_label="KoreChat")


@app.get("/status")
def status():
    return {
        "status": "ok",
        "conversations": db.conversation_counts(),
        "events": db.event_counts(),
    }


app.include_router(conversations_router)
app.include_router(messages_router)
app.include_router(events_router)
app.include_router(ui_router)
