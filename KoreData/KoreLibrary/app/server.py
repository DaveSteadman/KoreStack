# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FastAPI application for KoreLibrary — a book catalog service.
#
# This file now owns only service setup and route registration.
# UI endpoints live in app/endpoint_ui.py and API endpoints live in app/endpoint_api.py.
# MARK: FUNCTIONS
# Function inventory:
# - _lifespan: Implements the  lifespan operation for this module.
# - _warm_library: Implements the  warm library operation for this module.
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

from KoreCommon.service_app import register_endpoint_manifest
from KoreCommon.skill_registration import start_manifest_registration
from KoreCommon.skill_service import register_skill_invocation_routes
from app.chroma_index import migrate_legacy_catalog_stores
from app.config import cfg
from app.database import init_db, list_books, search_books
from app.endpoint_api import register_library_api
from app.endpoint_ui import register_library_ui


def korelibrary_search(q: str, limit: int = 50, catalog: str | None = None) -> list[dict]:
    return search_books(q=q, limit=limit, catalog=catalog or None)


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
    start_manifest_registration(
        Path(__file__).resolve().parent.parent / "skill_registration.json",
        service_base_url=f"http://{cfg['host']}:{cfg['port']}",
        logger_name=__name__,
    )
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
register_endpoint_manifest(app, service_key="korelibrary", service_label="KoreLibrary")
register_skill_invocation_routes(
    app,
    {
        "korelibrary_search": korelibrary_search,
        "korelibrary_books_list": list_books,
    },
)
