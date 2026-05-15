# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FastAPI application for KoreGraph — a SQLite-backed concept / relation graph store.
#
# Schema: 2 tables only — vocab (concept_id + term) and relations (subject/predicate/object concept_ids).
#
# Key endpoints:
#   GET  /status                                -- health + concept/vocab/relation counts
#   GET  /ui/vocab                              -- vocab management page
#   GET  /ui/relations                          -- relations management page
#   GET  /api/vocab                             -- list vocab terms
#   POST /api/vocab                             -- add vocab term
#   GET  /api/vocab/{id}                        -- get term detail with aliases
#   DELETE /api/vocab/{id}                      -- delete term
#   POST /api/vocab/{id}/aliases                -- add alias
#   DELETE /api/vocab-aliases/{id}              -- remove alias
#   POST /api/vocab/{canonical_id}/merge/{id}   -- merge term into canonical
#   GET  /api/relations                         -- list relations (paginated)
#   POST /api/relations                         -- upsert relation
#   PATCH /api/relations                        -- update state/score
#   DELETE /api/relations                       -- delete relation
#   GET  /api/search?q=                         -- vocab keyword search
#   GET  /api/expand?concept_id=&depth=         -- sub-graph traversal
#
# MCP tools (mounted at /mcp):
#   search_vocab    -- search vocab for concepts by keyword
#   expand_concept  -- expand a concept into its neighbourhood sub-graph
#
# Related modules:
#   - app/config.py     -- cfg (host, port, data_dir)
#   - app/database.py   -- all DB operations
# ====================================================================================================
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from app.config import cfg
from app.database import (
    add_vocab_alias,
    add_vocab_term,
    count_relations,
    delete_relation,
    delete_vocab_alias,
    delete_vocab_term,
    expand_concept,
    get_status,
    get_vocab_detail,
    init_db,
    list_relations,
    list_vocab,
    merge_vocab_terms,
    update_relation_state_score,
    upsert_relation,
    _get_or_create_vocab_term,
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


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="KoreGraph",
    description="Entity and relation graph store for KoreData",
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# MARK: MCP
# ---------------------------------------------------------------------------

_mcp = FastMCP(
    "KoreGraph",
    instructions=(
        "Search and traverse the KoreGraph concept-relation knowledge graph.\n\n"
        "Concepts are vocabulary terms (people, organisations, topics, places). "
        "Relations are typed triples: (subject_concept_id, predicate_concept_id, object_concept_id).\n\n"
        "Canonical workflow:\n"
        "1. Call search_vocab to find concept_ids for the terms you care about.\n"
        "2. Call expand_concept with a concept_id to retrieve its neighbourhood (nodes + edges).\n"
        "3. Use the returned graph to answer questions about connections between concepts.\n\n"
        "State filter: 0=proposed, 1=active, 2=deprecated, 3=rejected."
    ),
    streamable_http_path="/",
    stateless_http=True,
)


@_mcp.tool(description="Search KoreGraph vocab for concepts matching a keyword.")
def mcp_search_vocab(q: str, limit: int = 20) -> list[dict]:
    """Return matching vocab terms with concept_id, term, alias_count."""
    if not q or not q.strip():
        return []
    return list_vocab(q=q.strip(), limit=min(limit, 100))


@_mcp.tool(description=(
    "Expand a KoreGraph concept into its neighbourhood sub-graph. "
    "Returns {nodes, edges} within the requested depth of hops."
))
def mcp_expand_concept(concept_id: int, depth: int = 1, min_score: int = 0) -> dict:
    """Return {nodes: [...], edges: [...]} for the sub-graph around concept_id."""
    depth = max(1, min(depth, 4))
    return expand_concept(concept_id, depth=depth, min_score=min_score)


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
# MARK: UI — Entities (redirects to vocab)
# ---------------------------------------------------------------------------

@app.get("/ui/entities", include_in_schema=False)
def route_ui_entities():
    return RedirectResponse("/ui/vocab")


# ---------------------------------------------------------------------------
# MARK: UI — Relations
# ---------------------------------------------------------------------------

_STATE_LABELS = {0: "proposed", 1: "active", 2: "deprecated", 3: "rejected"}


@app.get("/ui/relations", include_in_schema=False)
def route_ui_relations(request: Request, state: Optional[int] = None,
                       concept_id: Optional[int] = None,
                       page: int = 1, page_size: int = 50):
    page = max(1, page)
    page_size = max(10, min(200, page_size))
    offset = (page - 1) * page_size
    total = count_relations(state=state, concept_id=concept_id)
    relations = list_relations(limit=page_size, offset=offset, state=state, concept_id=concept_id)
    total_pages = max(1, (total + page_size - 1) // page_size)
    return templates.TemplateResponse(
        request,
        "relations.html",
        {
            "relations": relations,
            "state": state,
            "concept_id": concept_id,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "state_labels": _STATE_LABELS,
            "_pfx": cfg.get("ui_prefix", ""),
        },
    )


# ---------------------------------------------------------------------------
# MARK: UI — Vocab
# ---------------------------------------------------------------------------

@app.get("/ui/vocab", include_in_schema=False)
def route_ui_vocab(request: Request):
    return templates.TemplateResponse(
        request,
        "vocab.html",
        {"_pfx": cfg.get("ui_prefix", "")},
    )


# ---------------------------------------------------------------------------
# MARK: Pydantic models
# ---------------------------------------------------------------------------

class VocabCreate(BaseModel):
    term: str


class RelationUpsert(BaseModel):
    subject_concept_id: int
    predicate_concept_id: int
    object_concept_id: int
    state: int = 0
    score: int = 0


class RelationPatch(BaseModel):
    subject_concept_id: int
    predicate_concept_id: int
    object_concept_id: int
    state: Optional[int] = None
    score: Optional[int] = None


class RelationKey(BaseModel):
    subject_concept_id: int
    predicate_concept_id: int
    object_concept_id: int


class VocabAliasCreate(BaseModel):
    alias: str


# ---------------------------------------------------------------------------
# MARK: API — Relations
# ---------------------------------------------------------------------------

@app.get("/api/relations", summary="List relations (paginated)")
def api_list_relations(state: Optional[int] = None, concept_id: Optional[int] = None,
                       limit: int = 100, offset: int = 0):
    limit = max(1, min(500, limit))
    return {
        "total": count_relations(state=state, concept_id=concept_id),
        "items": list_relations(limit=limit, offset=offset, state=state, concept_id=concept_id),
    }


@app.post("/api/relations", summary="Upsert a relation", status_code=201)
def api_upsert_relation(body: RelationUpsert):
    try:
        return upsert_relation(
            subject_concept_id=body.subject_concept_id,
            predicate_concept_id=body.predicate_concept_id,
            object_concept_id=body.object_concept_id,
            state=body.state,
            score=body.score,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/relations", summary="Update relation state and/or score")
def api_patch_relation(body: RelationPatch):
    result = update_relation_state_score(
        subject_concept_id=body.subject_concept_id,
        predicate_concept_id=body.predicate_concept_id,
        object_concept_id=body.object_concept_id,
        state=body.state,
        score=body.score,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Relation not found")
    return result


@app.delete("/api/relations", summary="Delete a relation")
def api_delete_relation(body: RelationKey):
    if not delete_relation(body.subject_concept_id, body.predicate_concept_id, body.object_concept_id):
        raise HTTPException(status_code=404, detail="Relation not found")
    return {"ok": True}


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


# ---------------------------------------------------------------------------
# MARK: API — Vocab
# ---------------------------------------------------------------------------

@app.get("/api/vocab", summary="List vocab terms")
def api_list_vocab(q: Optional[str] = None, limit: int = 500):
    limit = max(1, min(2000, limit))
    return list_vocab(q=q or None, limit=limit)


@app.post("/api/vocab", summary="Add a vocab term", status_code=201)
def api_add_vocab(body: VocabCreate):
    try:
        return add_vocab_term(body.term)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/vocab/{vocab_id}", summary="Get vocab term detail with aliases")
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


@app.post("/api/vocab/{vocab_id}/aliases", summary="Add alias to vocab term", status_code=201)
def api_add_vocab_alias(vocab_id: int, body: VocabAliasCreate):
    try:
        return add_vocab_alias(vocab_id, body.alias)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/vocab-aliases/{alias_id}", summary="Delete a vocab alias")
def api_delete_vocab_alias(alias_id: int):
    if not delete_vocab_alias(alias_id):
        raise HTTPException(status_code=404, detail="Alias not found")
    return {"ok": True}


@app.post("/api/vocab/{canonical_id}/merge/{merge_id}", summary="Merge vocab term into canonical")
def api_merge_vocab(canonical_id: int, merge_id: int):
    try:
        return merge_vocab_terms(canonical_id, merge_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
