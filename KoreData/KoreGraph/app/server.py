# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FastAPI application for KoreGraph — a SQLite-backed concept / relation graph store.
#
# Schema: 2 tables only — vocab (concept_id + term) and relations (subject/predicate/object concept_ids).
#
# Key endpoints:
#   GET  /status                -- health + concept/vocab/connection counts
#   GET  /ui/vocab              -- vocab management page
#   GET  /ui/connections        -- connections management page
#   GET  /ui/graph              -- graph visualization page
#   GET  /api/vocab             -- list vocab terms
#   POST /api/vocab             -- add vocab term
#   PATCH /api/vocab/{id}       -- rename vocab term
#   GET  /api/vocab/{id}        -- get term detail
#   DELETE /api/vocab/{id}      -- delete term
#   DELETE /api/concepts/{id}   -- delete concept plus touching relations
#   GET  /api/connections       -- list connections (limit/offset)
#   POST /api/connections       -- upsert connection (score accumulates)
#   PATCH /api/connections      -- update state/score
#   DELETE /api/connections     -- delete connection
#   GET  /api/search?q=         -- vocab keyword search
#   GET  /api/expand?concept_id=&depth= -- sub-graph traversal
#
# MCP tools (mounted at /mcp):
#   search_vocab    -- search vocab for concepts by keyword
#   expand_concept  -- expand a concept into its neighbourhood sub-graph
#
# Related modules:
#   - app/config.py     -- cfg (host, port, data_dir)
#   - app/database.py   -- all DB operations
# ====================================================================================================
import json
import os
import subprocess
import threading
import time as _time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from app.config import cfg
from app.database import (
    add_vocab_term,
    count_relations,
    delete_concept,
    delete_connection_by_name,
    delete_relation,
    delete_vocab_term,
    expand_by_term,
    expand_concept,
    get_status,
    get_vocab_detail,
    init_db,
    list_relations,
    list_vocab,
    merge_vocab_term,
    rename_vocab_term,
    unmerge_vocab_term,
    update_relation_state_score,
    upsert_connection_by_name,
    upsert_relation,
)

# ---------------------------------------------------------------------------
# MARK: Setup
# ---------------------------------------------------------------------------

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_UI_ELEMENTS_ASSETS = Path(
    os.environ.get(
        "KORE_UIELEMENTS_ASSETS_DIR",
        str(Path(__file__).resolve().parents[3] / "UIElements" / "assets"),
    )
).resolve()
_STATIC_DIR = (Path(__file__).parent / "static").resolve()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_db()
    async with _mcp.session_manager.run():
        yield


app = FastAPI(
    title="KoreGraph",
    description="Concept / connection knowledge graph store for KoreData",
    lifespan=_lifespan,
)


def _request_ui_prefix(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-prefix") or "").strip()
    if not forwarded:
        return ""
    return forwarded if forwarded.startswith("/") else f"/{forwarded}"


# ---------------------------------------------------------------------------
# MARK: MCP
# ---------------------------------------------------------------------------

_mcp = FastMCP(
    "KoreGraph",
    instructions=(
        "Search and traverse the KoreGraph concept-connection knowledge graph.\n\n"
        "All tools use plain string terms — no integer concept_ids required.\n\n"
        "Canonical workflow:\n"
        "1. Call graph_connection_search_vocab to find terms matching a keyword.\n"
        "2. Call graph_connection_expand_concept_by_term with a string term to retrieve its neighbourhood.\n"
        "3. Call graph_connection_create to add or reinforce a graph connection using three strings.\n\n"
        "State filter: 0=proposed, 1=active, 2=deprecated, 3=rejected."
    ),
    streamable_http_path="/",
    stateless_http=True,
)


@_mcp.tool(description="Search KoreGraph vocab for graph concepts matching a keyword.")
def graph_connection_search_vocab(q: str, limit: int = 20) -> list[dict]:
    """Return matching vocab terms with concept_id, term, alias_count."""
    if not q or not q.strip():
        return []
    return list_vocab(q=q.strip(), limit=min(limit, 100))


@_mcp.tool(description=(
    "Expand a KoreGraph concept into its neighbourhood sub-graph. "
    "Returns {nodes, edges} within the requested depth of hops."
))
def graph_connection_expand_concept(concept_id: int, depth: int = 1, min_score: int = 0) -> dict:
    """Return {nodes: [...], edges: [...]} for the sub-graph around concept_id."""
    depth = max(1, min(depth, 4))
    return expand_concept(concept_id, depth=depth, min_score=min_score)


@_mcp.tool(description=(
    "Expand a concept by string term into its neighbourhood sub-graph. "
    "No concept_id needed — pass the term as a plain string. "
    "Returns {query, matched, nodes, edges} with all names as strings."
))
def graph_connection_expand_concept_by_term(term: str, depth: int = 1, min_score: int = 0) -> dict:
    """String-based expand. Returns {query, matched, nodes, edges}."""
    depth = max(1, min(depth, 4))
    return expand_by_term(term=term, depth=depth, min_score=min_score)


@_mcp.tool(description=(
    "Create or reinforce a graph connection between two concepts using plain string names. "
    "Vocab entries are created automatically if they do not exist. "
    "Repeated calls with the same triple accumulate score (capped at 255)."
))
def graph_connection_create(
    start: str,
    connection: str,
    end: str,
    state: int = 0,
    score: int = 1,
) -> dict:
    """Create/reinforce a (start, connection, end) triple by string name."""
    return upsert_connection_by_name(
        start=start, connection=connection, end=end, state=state, score=score
    )


@_mcp.tool(description=(
    "List KoreGraph graph connections in batches using limit and offset. "
    "Returns {total, items} where each item has start_name, connection_name, end_name, state, score. "
    "Use limit/offset to step through all connections. "
    "Optionally filter by state: 0=proposed, 1=active, 2=deprecated, 3=rejected."
))
def graph_connection_list(
    limit: int = 100,
    offset: int = 0,
    state: Optional[int] = None,
) -> dict:
    """Return a batch of connections with resolved names."""
    limit = max(1, min(500, limit))
    items = list_relations(limit=limit, offset=offset, state=state)
    total = count_relations(state=state)
    return {"total": total, "offset": offset, "limit": limit, "items": items}


@_mcp.tool(description=(
    "Delete a KoreGraph graph connection by the plain string names of its three concepts. "
    "Returns {deleted: true} on success or {deleted: false} if the triple was not found. "
    "Use graph_connection_list first to see current graph connections and their exact names."
))
def graph_connection_delete(start: str, connection: str, end: str) -> dict:
    """Delete a (start, connection, end) triple by string names."""
    ok = delete_connection_by_name(start=start, connection=connection, end=end)
    return {"deleted": ok, "start": start, "connection": connection, "end": end}


@_mcp.tool(description=(
    "Create or reinforce multiple graph connections in a single call. "
    "Each item must have start, connection, end (strings). "
    "state and score are optional per item (defaults: state=0, score=1). "
    "Use this instead of repeated graph_connection_create calls."
))
def graph_connection_create_many(connections: list[dict]) -> dict:
    """Batch create/reinforce (start, connection, end) triples by string name."""
    accepted = []
    errors   = []
    for i, c in enumerate(connections):
        try:
            result = upsert_connection_by_name(
                start=c["start"],
                connection=c["connection"],
                end=c["end"],
                state=c.get("state", 0),
                score=c.get("score", 1),
            )
            accepted.append(result)
        except Exception as exc:
            errors.append({"index": i, "item": c, "error": str(exc)})
    return {"accepted": len(accepted), "errors": errors, "connections": accepted}


app.mount("/mcp", _mcp.streamable_http_app())


# ---------------------------------------------------------------------------
# MARK: Suite config + UIElements static files
# ---------------------------------------------------------------------------

@app.get("/suite-config.js", include_in_schema=False)
def suite_config_js():
    urls = os.environ.get("KORE_SUITE_URLS", "{}")
    return Response(
        content=f"window.__koreSuiteUrls = {urls};",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/ui-elements/assets/{asset_path:path}", include_in_schema=False)
def serve_ui_elements_asset(asset_path: str):
    candidate = (_UI_ELEMENTS_ASSETS / asset_path).resolve()
    if candidate != _UI_ELEMENTS_ASSETS and _UI_ELEMENTS_ASSETS not in candidate.parents:
        raise HTTPException(status_code=404, detail="Asset not found")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(str(candidate), headers={"Cache-Control": "no-store"})


@app.get("/static/{asset_path:path}", include_in_schema=False)
def serve_local_static_asset(asset_path: str):
    candidate = (_STATIC_DIR / asset_path).resolve()
    if candidate != _STATIC_DIR and _STATIC_DIR not in candidate.parents:
        raise HTTPException(status_code=404, detail="Asset not found")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(str(candidate), headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# MARK: Status
# ---------------------------------------------------------------------------

@app.get("/status", summary="Service health and graph statistics")
def route_status():
    return get_status()


# ---------------------------------------------------------------------------
# MARK: Navigation
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def route_root():
    return RedirectResponse("/ui/vocab")


@app.get("/ui", include_in_schema=False)
def route_ui():
    return RedirectResponse("/ui/vocab")


# ---------------------------------------------------------------------------
# MARK: UI — Connections
# ---------------------------------------------------------------------------

_STATE_LABELS = {0: "proposed", 1: "active", 2: "deprecated", 3: "rejected"}


@app.get("/ui/relations", include_in_schema=False)
def route_ui_relations_redirect():
    return RedirectResponse("/ui/connections")


@app.get("/ui/connections", include_in_schema=False)
def route_ui_connections(request: Request, state: Optional[int] = None,
                         concept_id: Optional[int] = None,
                         page: int = 1, page_size: int = 50):
    page = max(1, page)
    page_size = max(10, min(200, page_size))
    offset = (page - 1) * page_size
    total = count_relations(state=state, concept_id=concept_id)
    connections = list_relations(limit=page_size, offset=offset, state=state, concept_id=concept_id)
    total_pages = max(1, (total + page_size - 1) // page_size)
    request_prefix = _request_ui_prefix(request)
    return templates.TemplateResponse(
        request,
        "connections.html",
        {
            "connections": connections,
            "state": state,
            "concept_id": concept_id,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "state_labels": _STATE_LABELS,
            "_pfx": request_prefix,
        },
    )


# ---------------------------------------------------------------------------
# MARK: UI — Graph
# ---------------------------------------------------------------------------

@app.get("/ui/graph", include_in_schema=False)
def route_ui_graph(request: Request):
    request_prefix = _request_ui_prefix(request)
    return templates.TemplateResponse(
        request,
        "graph.html",
        {"_pfx": request_prefix},
    )


# ---------------------------------------------------------------------------
# MARK: UI — Vocab
# ---------------------------------------------------------------------------

@app.get("/ui/vocab", include_in_schema=False)
def route_ui_vocab(request: Request):
    request_prefix = _request_ui_prefix(request)
    return templates.TemplateResponse(
        request,
        "vocab.html",
        {"_pfx": request_prefix},
    )


# ---------------------------------------------------------------------------
# MARK: Pydantic models
# ---------------------------------------------------------------------------

class VocabCreate(BaseModel):
    term: str


class VocabRename(BaseModel):
    term: str


class VocabMergeIn(BaseModel):
    target_id: int


class ConnectionUpsert(BaseModel):
    start_concept_id: int
    connection_concept_id: int
    end_concept_id: int
    state: int = 0
    score: int = 0


class ConnectionPatch(BaseModel):
    start_concept_id: int
    connection_concept_id: int
    end_concept_id: int
    state: Optional[int] = None
    score: Optional[int] = None


class ConnectionKey(BaseModel):
    start_concept_id: int
    connection_concept_id: int
    end_concept_id: int


class ConnectionByName(BaseModel):
    start: str
    connection: str
    end: str
    state: int = 0
    score: int = 1


# ---------------------------------------------------------------------------
# MARK: API — Connections
# ---------------------------------------------------------------------------

@app.get("/api/connections", summary="List connections (limit/offset)")
def api_list_connections(state: Optional[int] = None, concept_id: Optional[int] = None,
                         limit: int = 100, offset: int = 0):
    limit = max(1, min(500, limit))
    return {
        "total": count_relations(state=state, concept_id=concept_id),
        "items": list_relations(limit=limit, offset=offset, state=state, concept_id=concept_id),
    }


@app.post("/api/connections", summary="Upsert a connection (score accumulates on conflict)", status_code=201)
def api_upsert_connection(body: ConnectionUpsert):
    try:
        return upsert_relation(
            subject_concept_id=body.start_concept_id,
            predicate_concept_id=body.connection_concept_id,
            object_concept_id=body.end_concept_id,
            state=body.state,
            score=body.score,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/connections", summary="Update connection state and/or score")
def api_patch_connection(body: ConnectionPatch):
    result = update_relation_state_score(
        subject_concept_id=body.start_concept_id,
        predicate_concept_id=body.connection_concept_id,
        object_concept_id=body.end_concept_id,
        state=body.state,
        score=body.score,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return result


@app.delete("/api/connections", summary="Delete a connection")
def api_delete_connection(body: ConnectionKey):
    if not delete_relation(body.start_concept_id, body.connection_concept_id, body.end_concept_id):
        raise HTTPException(status_code=404, detail="Connection not found")
    return {"ok": True}


@app.post("/api/connections/by-name", summary="Create/reinforce a connection by string name", status_code=201)
def api_upsert_connection_by_name(body: ConnectionByName):
    try:
        return upsert_connection_by_name(
            start=body.start,
            connection=body.connection,
            end=body.end,
            state=body.state,
            score=body.score,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/connections/by-name/batch", summary="Batch create/reinforce connections by string name", status_code=201)
def api_batch_upsert_connection_by_name(body: list[ConnectionByName]):
    accepted = []
    errors   = []
    for i, c in enumerate(body):
        try:
            result = upsert_connection_by_name(
                start=c.start,
                connection=c.connection,
                end=c.end,
                state=c.state,
                score=c.score,
            )
            accepted.append(result)
        except Exception as exc:
            errors.append({"index": i, "item": c.model_dump(), "error": str(exc)})
    return {"accepted": len(accepted), "errors": errors}


# ---------------------------------------------------------------------------
# MARK: API — Search + expand
# ---------------------------------------------------------------------------

@app.get("/api/search", summary="Search vocab by keyword")
def api_search(q: str, limit: int = 20):
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty")
    limit = max(1, min(100, limit))
    return list_vocab(q=q.strip(), limit=limit)


@app.get("/api/expand", summary="Expand concept sub-graph by depth")
def api_expand(concept_id: int, depth: int = 1, min_score: int = 0):
    depth = max(1, min(depth, 4))
    return expand_concept(concept_id, depth=depth, min_score=min_score)


@app.get("/api/expand-by-term", summary="Expand concept neighbourhood by string term")
def api_expand_by_term(q: str, depth: int = 1, min_score: int = 0):
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty")
    depth = max(1, min(depth, 4))
    return expand_by_term(term=q.strip(), depth=depth, min_score=min_score)


# ---------------------------------------------------------------------------
# MARK: API — Vocab
# ---------------------------------------------------------------------------

@app.get("/api/vocab", summary="List vocab terms")
def api_list_vocab(q: Optional[str] = None, limit: int = 100000):
    limit = max(1, min(100000, limit))
    return list_vocab(q=q or None, limit=limit)


@app.post("/api/vocab", summary="Add a vocab term", status_code=201)
def api_add_vocab(body: VocabCreate):
    try:
        return add_vocab_term(body.term)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/vocab/{vocab_id}", summary="Rename a vocab term")
def api_rename_vocab(vocab_id: int, body: VocabRename):
    try:
        result = rename_vocab_term(vocab_id, body.term)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Vocab term not found")
    return result


@app.get("/api/vocab/{vocab_id}", summary="Get vocab term detail")
def api_get_vocab(vocab_id: int):
    d = get_vocab_detail(vocab_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Vocab term not found")
    return d


@app.delete("/api/vocab/{vocab_id}", summary="Delete a vocab term")
def api_delete_vocab(vocab_id: int):
    if not delete_vocab_term(vocab_id):
        raise HTTPException(status_code=404, detail="Vocab term not found")
    return {"ok": True}


@app.delete("/api/concepts/{concept_id}", summary="Delete a concept and every relation that references it")
def api_delete_concept(concept_id: int):
    result = delete_concept(concept_id)
    if not result.get("deleted"):
        raise HTTPException(status_code=404, detail="Concept not found")
    return result


@app.post("/api/vocab/{vocab_id}/merge", summary="Merge term as alias of another concept")
def api_merge_vocab(vocab_id: int, body: VocabMergeIn):
    try:
        result = merge_vocab_term(vocab_id, body.target_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Vocab term not found")
    return result


@app.delete("/api/vocab/{vocab_id}/merge", summary="Split term off from its alias group")
def api_unmerge_vocab(vocab_id: int):
    try:
        result = unmerge_vocab_term(vocab_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Vocab term not found")
    return result


# ---------------------------------------------------------------------------
# MARK: Processing — job runner for datacontrol/KoreGraph/ scripts
# ---------------------------------------------------------------------------

# In-memory single-job tracker (one script at a time).
_proc_job: dict = {
    "process":    None,   # subprocess.Popen | None
    "script_id":  None,
    "script":     None,   # display name
    "log_path":   None,   # str path to log file
    "start_time": None,   # float (time.time())
    "exit_code":  None,   # int | None while running
}
_proc_lock = threading.Lock()


def _get_scripts_dir() -> Path:
    d = Path(cfg.get("scripts_dir", ""))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _list_processing_scripts() -> list[dict]:
    """Discover scripts by scanning scripts_dir for companion .json descriptor files."""
    d = _get_scripts_dir()
    results = []
    for jf in sorted(d.glob("*.json")):
        try:
            with open(jf, encoding="utf-8-sig") as f:
                info = json.load(f)
            script_file = info.get("script", "")
            if not script_file:
                continue
            script_path = (d / script_file).resolve()
            # Ensure script is inside scripts_dir (no traversal)
            if d.resolve() not in script_path.parents and script_path.parent != d.resolve():
                continue
            if not script_path.exists():
                continue
            info.setdefault("id", jf.stem)
            info.setdefault("name", jf.stem)
            info.setdefault("args", [])
            results.append(info)
        except Exception:
            pass
    return results


@app.get("/ui/processing", include_in_schema=False)
def route_ui_processing(request: Request):
    request_prefix = _request_ui_prefix(request)
    return templates.TemplateResponse(
        request,
        "processing.html",
        {"_pfx": request_prefix},
    )


@app.get("/api/processing/scripts", summary="List available processing scripts")
def api_processing_scripts():
    return _list_processing_scripts()


class ProcessingRunBody(BaseModel):
    script_id: str
    args: dict = {}


@app.post("/api/processing/run", summary="Launch a processing script")
def api_processing_run(body: ProcessingRunBody):
    # Validate script_id — only alphanumeric + underscores/hyphens
    import re as _re
    if not _re.match(r'^[\w\-]+$', body.script_id):
        raise HTTPException(status_code=400, detail="Invalid script_id")

    with _proc_lock:
        proc = _proc_job["process"]
        if proc is not None and proc.poll() is None:
            raise HTTPException(status_code=409, detail="A job is already running")

        scripts = _list_processing_scripts()
        script_info = next((s for s in scripts if s["id"] == body.script_id), None)
        if script_info is None:
            raise HTTPException(status_code=404, detail="Script not found")

        d = _get_scripts_dir()
        script_path = (d / script_info["script"]).resolve()
        if d.resolve() not in script_path.parents and script_path.parent != d.resolve():
            raise HTTPException(status_code=400, detail="Invalid script path")
        if not script_path.exists():
            raise HTTPException(status_code=404, detail="Script file not found")

        # Build argv from args dict
        import sys as _sys
        argv = [_sys.executable, str(script_path)]
        for flag, val in body.args.items():
            if not _re.match(r'^--[\w\-]+$', flag):
                continue  # skip malformed flags silently
            if val is True:
                argv.append(flag)
            elif val is not False and val is not None and str(val) != "":
                argv.extend([flag, str(val)])

        # Create log file
        log_dir = d / "logs"
        log_dir.mkdir(exist_ok=True)
        ts = _time.strftime("%Y%m%d_%H%M%S")
        log_path = log_dir / f"{body.script_id}_{ts}.log"

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        with open(log_path, "w", encoding="utf-8") as lf:
            new_proc = subprocess.Popen(
                argv,
                stdout=lf,
                stderr=subprocess.STDOUT,
                env=env,
            )

        _proc_job.update({
            "process":    new_proc,
            "script_id":  body.script_id,
            "script":     script_info.get("name", body.script_id),
            "log_path":   str(log_path),
            "start_time": _time.time(),
            "exit_code":  None,
        })

    # Background thread: write back last_run/status to JSON descriptor on completion
    def _monitor(proc=new_proc, script_id=body.script_id, lp=str(log_path)):
        proc.wait()
        ec = proc.returncode
        _proc_job["exit_code"] = ec
        jf = _get_scripts_dir() / f"{script_id}.json"
        try:
            with open(jf, encoding="utf-8") as f:
                desc = json.load(f)
            desc["last_run"]    = _time.strftime("%Y-%m-%d %H:%M:%S")
            desc["last_status"] = "ok" if ec == 0 else f"error ({ec})"
            _scripts_dir = _get_scripts_dir()
            try:
                desc["last_log"] = str(Path(lp).relative_to(_scripts_dir))
            except ValueError:
                desc["last_log"] = lp
            with open(jf, "w", encoding="utf-8") as f:
                json.dump(desc, f, indent=2)
        except Exception:
            pass

    threading.Thread(target=_monitor, daemon=True).start()

    return {"ok": True, "log_path": str(log_path)}


@app.get("/api/processing/log", summary="Poll for new log lines from running/last job")
def api_processing_log(cursor: int = 0):
    job = _proc_job
    proc = job["process"]

    running = proc is not None and proc.poll() is None
    exit_code = None
    if proc is not None and proc.poll() is not None:
        exit_code = proc.returncode
    if exit_code is None:
        exit_code = job["exit_code"]

    elapsed_str = ""
    if job["start_time"]:
        secs = int(_time.time() - job["start_time"])
        elapsed_str = f"{secs // 60}m{secs % 60:02d}s"

    lines: list[str] = []
    new_cursor = cursor
    if job["log_path"]:
        try:
            with open(job["log_path"], "rb") as f:
                f.seek(cursor)
                chunk = f.read(65536)
            if chunk:
                new_cursor = cursor + len(chunk)
                lines = chunk.decode("utf-8", errors="replace").splitlines()
        except (FileNotFoundError, OSError):
            pass

    return {
        "running":    running,
        "exit_code":  exit_code,
        "script":     job.get("script"),
        "script_id":  job.get("script_id"),
        "elapsed":    elapsed_str,
        "cursor":     new_cursor,
        "lines":      lines,
    }


@app.post("/api/processing/stop", summary="Stop the currently running processing job")
def api_processing_stop():
    proc = _proc_job["process"]
    if proc is None or proc.poll() is not None:
        raise HTTPException(status_code=400, detail="No running job")
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    return {"ok": True}


