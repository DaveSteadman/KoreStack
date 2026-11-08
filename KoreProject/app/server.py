from __future__ import annotations

import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from KoreCommon.service_app import register_suite_shell_routes
from KoreCommon.skill_registration import start_manifest_registration
from KoreCommon.skill_service import register_skill_invocation_routes
from .config import SERVICE_KEY, SERVICE_LABEL, data_dir, service_config
from .store import ProjectError, ProjectStore

ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = ROOT / "KoreUI" / "KoreProject"
Kind = Literal["project", "statement", "ticket", "board", "lane", "section"]
LinkKind = Literal["statement", "ticket"]
Relation = Literal["satisfies", "refines", "addresses", "depends_on", "relates_to"]
LOG = logging.getLogger("koreproject")


class SaveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: dict
    project_id: str = ""
    expected_version: int | None = Field(default=None, ge=1)
    request_id: str = Field(default="", max_length=100)
    confirm_children: bool = False
    generation: str = ""


class CommentBody(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)
    generation: str = ""


class LinkBody(BaseModel):
    source_kind: LinkKind
    source_id: str
    target_kind: LinkKind
    target_id: str
    relation: Relation
    note: str = Field(default="", max_length=4000)
    generation: str = ""


class ResetBody(BaseModel):
    confirmation: str
    generation: str


class DeleteProjectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    confirmation: str


class DeleteSectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


def create_app(root: Path | None = None, *, register: bool = True) -> FastAPI:
    store = ProjectStore(root or data_dir())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.initialize()
        if register:
            config = service_config()
            start_manifest_registration(
                ROOT / "KoreProject" / "skill_registration.json",
                service_base_url=f"http://{config['host']}:{config['port']}",
                logger_name="koreproject",
            )
        yield

    app = FastAPI(title=SERVICE_LABEL, version="0.1.0", lifespan=lifespan)
    app.state.store = store
    register_suite_shell_routes(
        app, service_key=SERVICE_KEY, service_label=SERVICE_LABEL,
        ui_elements_assets_dir=ROOT / "KoreUI" / "UIElements" / "assets",
    )
    app.mount("/static", StaticFiles(directory=UI_ROOT / "static"), name="koreproject-static")

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            if origin.rstrip("/") != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "Cross-origin writes are not allowed"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(ProjectError)
    async def project_error(request: Request, exc: ProjectError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.exception_handler(sqlite3.IntegrityError)
    async def integrity_error(request: Request, exc: sqlite3.IntegrityError):
        LOG.warning("Rejected conflicting project operation: %s", exc)
        return JSONResponse({"detail": "Duplicate reference or invalid relationship"}, status_code=409)

    @app.exception_handler(sqlite3.DatabaseError)
    async def database_error(request: Request, exc: sqlite3.DatabaseError):
        LOG.exception("Project storage failure")
        return JSONResponse({"detail": "Storage unavailable. See the KoreProject service log."}, status_code=503)

    @app.get("/")
    @app.get("/ui")
    @app.get("/ui/tickets")
    @app.get("/ui/boards")
    @app.get("/ui/settings")
    def ui(request: Request):
        if request.url.path == "/ui/tickets" and request.query_params.get("view") == "boards":
            return RedirectResponse(str(request.url.replace(path="/ui/boards").remove_query_params("view")))
        return FileResponse(UI_ROOT / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/ui/requirements")
    @app.get("/ui/design")
    def requirements_ui():
        return FileResponse(UI_ROOT / "requirements.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/statement-links")
    def statement_links(project_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
        return store.statement_links(project_id, offset, limit)

    @app.get("/status")
    def status():
        store.list("project", limit=1)
        try:
            generation = store.ticket_generation()
        except (ProjectError, sqlite3.DatabaseError) as exc:
            return JSONResponse({"service": SERVICE_LABEL, "status": "degraded",
                                 "version": "0.1.0", "detail": str(exc)}, status_code=503)
        return {"service": SERVICE_LABEL, "status": "ok", "version": "0.1.0", "generation": generation}

    @app.get("/api/tickets/generation")
    def generation():
        return {"generation": store.ticket_generation()}

    @app.post("/api/tickets/reset")
    def reset(body: ResetBody):
        return store.reset_tickets(body.confirmation, body.generation)

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: str, body: DeleteProjectBody):
        return store.delete_project(project_id, body.expected_version, body.confirmation)

    @app.delete("/api/records/section/{section_id}")
    def delete_section(section_id: str, body: DeleteSectionBody):
        return store.delete_section(section_id, body.expected_version)

    @app.get("/api/records/{kind}")
    def records(kind: Kind, project_id: str = "", q: str = "",
                offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
        return store.list(kind, project_id, q, offset, limit)

    @app.get("/api/records/{kind}/{record_id}")
    def detail(kind: Kind, record_id: str):
        return store.detail(kind, record_id)

    @app.post("/api/records/{kind}", status_code=201)
    def create(kind: Kind, body: SaveBody):
        return store.save(kind, **body.model_dump())

    @app.put("/api/records/{kind}/{record_id}")
    def update(kind: Kind, record_id: str, body: SaveBody):
        return store.save(kind, record_id=record_id, **body.model_dump())

    @app.post("/api/records/{kind}/{record_id}/comments", status_code=201)
    def comment(kind: Kind, record_id: str, body: CommentBody):
        return store.comment(kind, record_id, body.body, "operator", body.generation)

    @app.post("/api/links", status_code=201)
    def link(body: LinkBody):
        return store.link(**body.model_dump())

    @app.delete("/api/links/{store_kind}/{link_id}")
    def unlink(store_kind: Literal["statement", "ticket"], link_id: str, generation: str = ""):
        store.unlink(store_kind, link_id, "operator", generation)
        return {"deleted": link_id}

    def project_read(kind: Kind = "project", project_id: str = "", record_id: str = "",
                     q: str = "", offset: int = 0, limit: int = 100) -> dict:
        result = store.detail(kind, record_id) if record_id else store.list(kind, project_id, q, offset, limit)
        try:
            result["generation"] = store.ticket_generation()
        except (ProjectError, sqlite3.DatabaseError) as exc:
            result["ticket_store_error"] = str(exc)
        return result

    def project_save(kind: Kind, data: dict, project_id: str = "", record_id: str = "",
                     expected_version: int | None = None, request_id: str = "",
                     generation: str = "", confirm_children: bool = False) -> dict:
        body = SaveBody(data=data, project_id=project_id, expected_version=expected_version,
                        request_id=request_id, generation=generation, confirm_children=confirm_children)
        return store.save(kind, record_id=record_id, actor="agent", **body.model_dump())

    def project_link(source_kind: LinkKind, source_id: str, target_kind: LinkKind, target_id: str,
                     relation: Relation, note: str = "", generation: str = "") -> dict:
        body = LinkBody(source_kind=source_kind, source_id=source_id, target_kind=target_kind,
                        target_id=target_id, relation=relation, note=note, generation=generation)
        return store.link(actor="agent", **body.model_dump())

    def project_comment(kind: Kind, record_id: str, body: str, generation: str = "") -> dict:
        return store.comment(kind, record_id, body, "agent", generation)

    register_skill_invocation_routes(app, {
        "project_read": project_read, "project_save": project_save,
        "project_link": project_link, "project_comment": project_comment,
    })
    return app


app = create_app()
