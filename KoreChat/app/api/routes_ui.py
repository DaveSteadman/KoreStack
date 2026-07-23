from fastapi import APIRouter
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.responses import RedirectResponse

from KoreCommon.service_app import register_suite_config_js
from KoreCommon.service_app import register_ui_elements_assets
from app.api.state import NO_STORE_HEADERS
from app.api.state import UI_DIR
from app.api.state import UI_ELEMENTS_ASSETS
from app.runtime.stream import event_stream_response


router = APIRouter()
register_suite_config_js(router)
register_ui_elements_assets(router, UI_ELEMENTS_ASSETS)


@router.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/ui")


@router.get("/ui", include_in_schema=False)
def serve_ui():
    return FileResponse(str(UI_DIR / "conversations.html"), headers=NO_STORE_HEADERS)


@router.get("/ui/conversations.js", include_in_schema=False)
def serve_ui_js():
    return FileResponse(str(UI_DIR / "conversations.js"), headers=NO_STORE_HEADERS)


@router.get("/ui/conversations.css", include_in_schema=False)
def serve_ui_css():
    return FileResponse(str(UI_DIR / "conversations.css"), headers=NO_STORE_HEADERS)


@router.get("/ui/modules/{module_path:path}", include_in_schema=False)
def serve_ui_module(module_path: str):
    candidate = (UI_DIR / "modules" / module_path).resolve()
    modules_dir = (UI_DIR / "modules").resolve()
    if candidate != modules_dir and modules_dir not in candidate.parents:
        raise HTTPException(status_code=404, detail="Asset not found")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(str(candidate), headers=NO_STORE_HEADERS, media_type="application/javascript")


@router.get("/stream", include_in_schema=False)
def stream_events():
    return event_stream_response()
