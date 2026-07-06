# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FastAPI application for KoreLibrary — a book catalog service.
#
# This file now owns only service setup and route registration.
# UI endpoints live in app/endpoint_ui.py and API endpoints live in app/endpoint_api.py.
# ====================================================================================================
import sys
from contextlib import asynccontextmanager
from pathlib import Path
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

_KORECOMMON_PARENT = next((parent for parent in Path(__file__).resolve().parents if (parent / "KoreCommon").is_dir()), None)
if _KORECOMMON_PARENT is not None and str(_KORECOMMON_PARENT) not in sys.path:
    sys.path.insert(0, str(_KORECOMMON_PARENT))

from KoreCommon.endpoint_manifest import build_endpoint_manifest
from app.chroma_index import migrate_legacy_catalog_stores
from app.database import init_db
from app.endpoint_api import register_library_api
from app.endpoint_ui import register_library_ui


@asynccontextmanager
async def _lifespan(app: FastAPI):
    def _warm_library() -> None:
        init_db()
        try:
            migrate_legacy_catalog_stores(batch_size=250)
        except Exception:
            pass

    threading.Thread(
        target = _warm_library,
        daemon = True,
        name   = "korelibrary-startup-warm",
    ).start()
    yield


app = FastAPI(
    title       = "KoreLibrary",
    description = "Long-form text storage and retrieval service",
    lifespan    = _lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = False,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

register_library_ui(app)
register_library_api(app)


@app.get("/__endpoint_manifest", include_in_schema=False)
def endpoint_manifest() -> dict:
    return build_endpoint_manifest(app, service_key="korelibrary", service_label="KoreLibrary")
