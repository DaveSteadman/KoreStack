# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FastAPI application for KoreGraph — a SQLite-backed entity / relation graph store.
#
# Key endpoints:
#   GET  /status                          -- health + entity/relation counts
#   GET  /ui/entities                     -- entity management page
#   GET  /ui/relations                    -- relations management page
#   GET  /ui/vocab                        -- relation-type vocabulary page
#   GET  /api/entities                    -- list entities (paginated, optional search)
#   POST /api/entities                    -- create entity
#   GET  /api/entities/{id}               -- get entity with aliases + relations
#   PUT  /api/entities/{id}               -- update entity
#   DELETE /api/entities/{id}             -- delete entity
#   POST /api/entities/{id}/aliases       -- add alias
#   DELETE /api/aliases/{id}              -- remove alias
#   GET  /api/relation-types             -- list relation types
#   POST /api/relation-types             -- create relation type
#   DELETE /api/relation-types/{id}      -- delete relation type
#   GET  /api/relations                  -- list relations
#   POST /api/relations                  -- upsert relation
#   PATCH /api/relations                 -- update state/score
#   DELETE /api/relations                -- delete relation
#   POST /api/evidence                   -- add evidence for a relation
#   GET  /api/search?q=                  -- entity name/alias search
#   GET  /api/expand?entity_id=&depth=   -- sub-graph traversal
#
# MCP tools (mounted at /mcp):
#   search_entities   -- search for entities by keyword
#   expand_graph      -- expand a named entity into its neighbourhood sub-graph
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
    add_alias,
    add_evidence,
    add_relation_type_alias,
    add_vocab_alias,
    add_vocab_term,
    count_entities,
    count_relations,
    create_entity,
    create_relation_type,
    delete_alias,
    delete_entity,
    delete_relation,
    delete_relation_type,
    delete_relation_type_alias,
    delete_vocab_alias,
    delete_vocab_term,
    expand_entity,
    get_entity,
    get_status,
    get_vocab_detail,
    init_db,
    list_entities,
    list_evidence,
    list_relation_types,
    list_relations,
    list_vocab,
    merge_vocab_terms,
    search_entities,
    update_entity,
    update_relation_state_score,
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
        "Search and traverse the KoreGraph entity-relation knowledge graph.\n\n"
        "Entities are named things (people, organisations, concepts, places, events). "
        "Relations are typed directional or undirected connections between two entities.\n\n"
        "Canonical workflow:\n"
        "1. Call search_entities to find the entity IDs for names you are interested in.\n"
        "2. Call expand_graph with an entity_id to retrieve its neighbourhood (nodes + edges).\n"
        "3. Use the returned graph to answer questions about connections between entities.\n\n"
        "State filter: 0=proposed, 1=active, 2=deprecated, 3=rejected. "
        "By default expand_graph returns all active (state=1) relations only."
    ),
    streamable_http_path="/",
    stateless_http=True,
)


@_mcp.tool(description="Search for entities in KoreGraph by name or alias keyword.")
def mcp_search_entities(q: str, limit: int = 20) -> list[dict]:
    """Return a list of matching entities with id, name, type, description."""
    if not q or not q.strip():
        return []
    return search_entities(q.strip(), limit=min(limit, 100))


@_mcp.tool(description=(
    "Expand a KoreGraph entity into its neighbourhood sub-graph. "
    "Returns {nodes, edges} within the requested depth of hops."
))
def mcp_expand_graph(entity_id: int, depth: int = 1, min_score: int = 0) -> dict:
    """Return {nodes: [...], edges: [...]} for the sub-graph around entity_id."""
    depth = max(1, min(depth, 4))
    return expand_entity(entity_id, depth=depth, min_score=min_score)


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
    return RedirectResponse("/ui/entities")


@app.get("/ui", include_in_schema=False)
def route_ui():
    return RedirectResponse("/ui/entities")


# ---------------------------------------------------------------------------
# MARK: UI — Entities
# ---------------------------------------------------------------------------

@app.get("/ui/entities", include_in_schema=False)
def route_ui_entities(request: Request, q: Optional[str] = None,
                      page: int = 1, page_size: int = 50):
    page = max(1, page)
    page_size = max(10, min(200, page_size))
    offset = (page - 1) * page_size
    total = count_entities(q=q)
    entities = list_entities(limit=page_size, offset=offset, q=q)
    relation_types = list_relation_types()
    total_pages = max(1, (total + page_size - 1) // page_size)
    return templates.TemplateResponse(
        request,
        "entities.html",
        {
            "entities": entities,
            "relation_types": relation_types,
            "q": q or "",
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "_pfx": cfg.get("ui_prefix", ""),
        },
    )


# ---------------------------------------------------------------------------
# MARK: UI — Relations
# ---------------------------------------------------------------------------

_STATE_LABELS = {0: "proposed", 1: "active", 2: "deprecated", 3: "rejected"}


@app.get("/ui/relations", include_in_schema=False)
def route_ui_relations(request: Request, state: Optional[int] = None,
                       entity_id: Optional[int] = None,
                       page: int = 1, page_size: int = 50):
    page = max(1, page)
    page_size = max(10, min(200, page_size))
    offset = (page - 1) * page_size
    total = count_relations(state=state, entity_id=entity_id)
    relations = list_relations(limit=page_size, offset=offset, state=state, entity_id=entity_id)
    relation_types = list_relation_types()
    total_pages = max(1, (total + page_size - 1) // page_size)
    return templates.TemplateResponse(
        request,
        "relations.html",
        {
            "relations": relations,
            "relation_types": relation_types,
            "state": state,
            "entity_id": entity_id,
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

class EntityCreate(BaseModel):
    name: str
    type: Optional[str] = None
    description: Optional[str] = None


class EntityUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    description: Optional[str] = None


class AliasCreate(BaseModel):
    alias: str


class VocabCreate(BaseModel):
    term: str


class RelationTypeCreate(BaseModel):
    label: str
    directed: bool = True


class RelationUpsert(BaseModel):
    source_entity_id: int
    relation_type_id: int
    target_entity_id: int
    state: int = 0
    score: int = 0


class RelationPatch(BaseModel):
    source_entity_id: int
    relation_type_id: int
    target_entity_id: int
    state: Optional[int] = None
    score: Optional[int] = None


class RelationKey(BaseModel):
    source_entity_id: int
    relation_type_id: int
    target_entity_id: int


class EvidenceCreate(BaseModel):
    source_entity_id: int
    relation_type_id: int
    target_entity_id: int
    evidence: str


# ---------------------------------------------------------------------------
# MARK: API — Entities
# ---------------------------------------------------------------------------

@app.get("/api/entities", summary="List entities (paginated)")
def api_list_entities(q: Optional[str] = None, limit: int = 100, offset: int = 0):
    limit = max(1, min(500, limit))
    return {
        "total": count_entities(q=q),
        "items": list_entities(limit=limit, offset=offset, q=q),
    }


@app.post("/api/entities", summary="Create a new entity", status_code=201)
def api_create_entity(body: EntityCreate):
    if not body.name or not body.name.strip():
        raise HTTPException(status_code=400, detail="Entity name must not be empty")
    try:
        return create_entity(body.name, type_=body.type, description=body.description)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/entities/{entity_id}", summary="Get entity with aliases and relations")
def api_get_entity(entity_id: int):
    entity = get_entity(entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity


@app.put("/api/entities/{entity_id}", summary="Update entity fields")
def api_update_entity(entity_id: int, body: EntityUpdate):
    result = update_entity(entity_id, name=body.name, type_=body.type, description=body.description)
    if result is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return result


@app.delete("/api/entities/{entity_id}", summary="Delete entity (cascades to relations)")
def api_delete_entity(entity_id: int):
    if not delete_entity(entity_id):
        raise HTTPException(status_code=404, detail="Entity not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# MARK: API — Aliases
# ---------------------------------------------------------------------------

@app.post("/api/entities/{entity_id}/aliases", summary="Add alias to entity", status_code=201)
def api_add_alias(entity_id: int, body: AliasCreate):
    if not body.alias or not body.alias.strip():
        raise HTTPException(status_code=400, detail="Alias must not be empty")
    # Verify entity exists
    if get_entity(entity_id) is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    try:
        return add_alias(entity_id, body.alias)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.delete("/api/aliases/{alias_id}", summary="Remove an entity alias")
def api_delete_alias(alias_id: int):
    if not delete_alias(alias_id):
        raise HTTPException(status_code=404, detail="Alias not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# MARK: API — Relation types
# ---------------------------------------------------------------------------

@app.get("/api/relation-types", summary="List relation type vocabulary")
def api_list_relation_types():
    return list_relation_types()


@app.post("/api/relation-types", summary="Create a new relation type", status_code=201)
def api_create_relation_type(body: RelationTypeCreate):
    try:
        return create_relation_type(body.label, directed=body.directed)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.delete("/api/relation-types/{rt_id}", summary="Delete a relation type")
def api_delete_relation_type(rt_id: int):
    if not delete_relation_type(rt_id):
        raise HTTPException(status_code=404, detail="Relation type not found")
    return {"ok": True}


@app.post("/api/relation-types/{rt_id}/aliases", summary="Add an alias to a relation type", status_code=201)
def api_add_relation_type_alias(rt_id: int, body: AliasCreate):
    alias = body.alias.strip()
    if not alias:
        raise HTTPException(status_code=400, detail="Alias is required")
    try:
        return add_relation_type_alias(rt_id, alias)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.delete("/api/relation-type-aliases/{alias_id}", summary="Remove a relation type alias")
def api_delete_relation_type_alias(alias_id: int):
    if not delete_relation_type_alias(alias_id):
        raise HTTPException(status_code=404, detail="Alias not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# MARK: API — Relations
# ---------------------------------------------------------------------------

@app.get("/api/relations", summary="List relations (paginated)")
def api_list_relations(state: Optional[int] = None, entity_id: Optional[int] = None,
                       limit: int = 100, offset: int = 0):
    limit = max(1, min(500, limit))
    return {
        "total": count_relations(state=state, entity_id=entity_id),
        "items": list_relations(limit=limit, offset=offset, state=state, entity_id=entity_id),
    }


@app.post("/api/relations", summary="Upsert a relation (create or update state/score)", status_code=201)
def api_upsert_relation(body: RelationUpsert):
    try:
        return upsert_relation(
            source_id=body.source_entity_id,
            relation_type_id=body.relation_type_id,
            target_id=body.target_entity_id,
            state=body.state,
            score=body.score,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/relations", summary="Update relation state and/or score")
def api_patch_relation(body: RelationPatch):
    result = update_relation_state_score(
        source_id=body.source_entity_id,
        relation_type_id=body.relation_type_id,
        target_id=body.target_entity_id,
        state=body.state,
        score=body.score,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Relation not found")
    return result


@app.delete("/api/relations", summary="Delete a relation")
def api_delete_relation(body: RelationKey):
    if not delete_relation(body.source_entity_id, body.relation_type_id, body.target_entity_id):
        raise HTTPException(status_code=404, detail="Relation not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# MARK: API — Evidence
# ---------------------------------------------------------------------------

@app.post("/api/evidence", summary="Add evidence text for a relation", status_code=201)
def api_add_evidence(body: EvidenceCreate):
    if not body.evidence or not body.evidence.strip():
        raise HTTPException(status_code=400, detail="Evidence text must not be empty")
    return add_evidence(body.source_entity_id, body.relation_type_id, body.target_entity_id, body.evidence)


@app.get("/api/evidence", summary="List evidence for a relation")
def api_list_evidence(source_entity_id: int, relation_type_id: int, target_entity_id: int):
    return list_evidence(source_entity_id, relation_type_id, target_entity_id)


# ---------------------------------------------------------------------------
# MARK: API — Search + expand
# ---------------------------------------------------------------------------

@app.get("/api/search", summary="Search entities by name or alias")
def api_search(q: str, limit: int = 20):
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Query must not be empty")
    limit = max(1, min(100, limit))
    return search_entities(q.strip(), limit=limit)


@app.get("/api/expand", summary="Expand entity sub-graph by depth")
def api_expand(entity_id: int, depth: int = 1, min_score: int = 0):
    depth = max(1, min(depth, 4))
    return expand_entity(entity_id, depth=depth, min_score=min_score)


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
def api_add_vocab_alias(vocab_id: int, body: AliasCreate):
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
