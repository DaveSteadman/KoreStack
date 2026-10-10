from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from KoreCommon.service_app import register_suite_shell_routes

from .config import SERVICE_KEY, SERVICE_LABEL
from .runtime import NetworkBusy, fire_trigger, is_locked, run_network, run_node
from .templates import add_template, delete_template, list_templates
from .store import NetworkConflict, count_networks, create_network, delete_network, duplicate_network, list_networks, load_network, load_run_state, save_network, save_run_state, validate_node


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
    base_updated_at: str | None = None


class RunStateBody(BaseModel):
    run: dict | None = None
    edits: dict = {}


class TemplateBody(BaseModel):
    node: dict
    name: str = ""
    description: str = ""


class NodeRunBody(BaseModel):
    node: dict
    inputs: dict = {}
    network_id: str | None = None
    outputs: dict = {}


class TriggerBody(BaseModel):
    token: str
    name: str
    payload: dict = {}


def _refuse_if_locked(network_id: str) -> None:
    if is_locked(network_id):
        raise HTTPException(status_code=409, detail="This network is running (run mode). It cannot be changed or deleted until the run finishes.")


@app.get("/status")
def status() -> dict:
    return {"ok": True, "available": True, "service": SERVICE_LABEL, "networks": count_networks()}


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
    _refuse_if_locked(network_id)
    network = dict(body.network)
    network["id"] = network_id
    try:
        return {"network": save_network(network, body.base_updated_at)}
    except NetworkConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/api/templates")
def api_list_templates() -> dict:
    return {"templates": list_templates()}


@app.post("/api/templates")
def api_add_template(body: TemplateBody) -> dict:
    try:
        return {"template": add_template(body.node, body.name, body.description)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.delete("/api/templates/{template_id}")
def api_delete_template(template_id: str) -> dict:
    try:
        delete_template(template_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Template not found")
    return {"deleted": template_id}


@app.post("/api/networks/{network_id}/duplicate")
def api_duplicate_network(network_id: str) -> dict:
    try:
        return {"network": duplicate_network(network_id)}
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Network not found")


@app.delete("/api/networks/{network_id}")
def api_delete_network(network_id: str) -> dict:
    _refuse_if_locked(network_id)
    try:
        delete_network(network_id)
    except NetworkConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Network not found")
    return {"deleted": network_id}


@app.get("/api/networks/{network_id}/state")
def api_get_run_state(network_id: str) -> dict:
    try:
        return load_run_state(network_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Network not found")


@app.put("/api/networks/{network_id}/state")
def api_save_run_state(network_id: str, body: RunStateBody) -> dict:
    try:
        save_run_state(network_id, body.run, body.edits)
    except ValueError:
        raise HTTPException(status_code=404, detail="Network not found")
    return {"saved": network_id}


@app.post("/api/networks/{network_id}/run")
def api_run_network(network_id: str) -> dict:
    try:
        run = run_network(load_network(network_id))
        save_run_state(network_id, run, {})
        return {"run": run}
    except NetworkBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/api/run-node")
def api_run_node(body: NodeRunBody) -> dict:
    try:
        node = validate_node(body.node)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    network = None
    if body.network_id:
        try:
            network = load_network(body.network_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="Network not found")
    try:
        return {"result": run_node(node, body.inputs, network, body.outputs)}
    except NetworkBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/api/internal/trigger")
def api_internal_trigger(body: TriggerBody) -> dict:
    try:
        return fire_trigger(body.token, body.name, body.payload)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/api/networks/{network_id}/locked")
def api_network_locked(network_id: str) -> dict:
    return {"locked": is_locked(network_id)}
