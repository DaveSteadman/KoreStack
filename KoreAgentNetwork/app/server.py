from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from KoreCommon.service_app import register_suite_shell_routes

from .config import SERVICE_KEY, SERVICE_LABEL
from .runtime import run_network, run_node
from .store import create_network, delete_network, list_networks, load_network, save_network, validate_node


ROOT        = Path(__file__).resolve().parents[2]
UI_ROOT     = ROOT / "KoreUI" / "KoreAgentNetwork"
UI_ASSETS   = ROOT / "KoreUI" / "UIElements" / "assets"
app = FastAPI(title=SERVICE_LABEL, description="Visual, executable KoreStack processing networks")
register_suite_shell_routes(app, service_key=SERVICE_KEY, service_label=SERVICE_LABEL, ui_elements_assets_dir=UI_ASSETS)
app.mount("/static", StaticFiles(directory=str(UI_ROOT / "static")), name="koreagentnetwork-static")


class NetworkCreate(BaseModel):
    title: str = "Untitled network"


class NetworkBody(BaseModel):
    network: dict


class NodeRunBody(BaseModel):
    node: dict
    inputs: dict = {}


@app.get("/status")
def status() -> dict:
    return {"ok": True, "available": True, "service": SERVICE_LABEL, "networks": len(list_networks())}


@app.get("/")
@app.get("/ui")
def ui() -> FileResponse:
    return FileResponse(UI_ROOT / "static" / "koreagentnetwork" / "index.html")


@app.get("/api/networks")
def api_list_networks() -> dict:
    return {"networks": list_networks()}


@app.post("/api/networks")
def api_create_network(body: NetworkCreate) -> dict:
    return {"network": create_network(body.title)}


@app.get("/api/networks/{network_id}")
def api_get_network(network_id: str) -> dict:
    try:
        return {"network": load_network(network_id)}
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Network not found")


@app.put("/api/networks/{network_id}")
def api_save_network(network_id: str, body: NetworkBody) -> dict:
    network = dict(body.network)
    network["id"] = network_id
    try:
        return {"network": save_network(network)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.delete("/api/networks/{network_id}")
def api_delete_network(network_id: str) -> dict:
    try:
        delete_network(network_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Network not found")
    return {"deleted": network_id}


@app.post("/api/networks/{network_id}/run")
def api_run_network(network_id: str) -> dict:
    try:
        return {"run": run_network(load_network(network_id))}
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/api/run-node")
def api_run_node(body: NodeRunBody) -> dict:
    try:
        node = validate_node(body.node)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"result": run_node(node, body.inputs)}
