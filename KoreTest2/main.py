from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn


ROOT      = Path(__file__).resolve().parents[1]
UI_ROOT   = ROOT / "KoreUI" / "KoreTest2"
UI_ASSETS = ROOT / "KoreUI" / "UIElements" / "assets"
CONFIG    = ROOT / "config" / "korestack_config.json"
sys.path.insert(0, str(ROOT))

from KoreCommon.service_app import register_suite_shell_routes
from KoreTest2 import service


def _config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


app = FastAPI(title="KoreTest2")
register_suite_shell_routes(app, service_key="koretest2", service_label="KoreTest2", ui_elements_assets_dir=UI_ASSETS)
app.mount("/static", StaticFiles(directory=str(UI_ROOT / "static")), name="koretest2-static")


@app.get("/status")
def status() -> dict:
    summary = service.summary()
    return {"ok": True, "available": True, "service": "KoreTest2", **summary}


@app.post("/api/sessions")
def start_session() -> dict:
    return service.start_session()


@app.get("/api/grid")
def grid() -> dict:
    return service.grid()


@app.get("/api/runs/{test_id}")
def run_detail(test_id: str, build_id: str | None = None) -> dict:
    return service.run_detail(test_id, build_id)


@app.get("/ui")
def ui() -> FileResponse:
    return FileResponse(UI_ROOT / "static" / "koretest2" / "index.html")


if __name__ == "__main__":
    config = _config()
    host   = config.get("network", {}).get("host", "127.0.0.1")
    port   = int(config["services"]["koretest2"]["port"])
    uvicorn.run(app, host=host, port=port)
