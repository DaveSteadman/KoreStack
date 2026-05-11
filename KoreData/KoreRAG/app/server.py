# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FastAPI application for KoreRAG — a retrieval-augmented generation chunk store.
#
# Provides REST API for storing and searching text chunks (documents, notes, web pages)
# with FTS5 full-text search and optional metadata tagging.
#
# Key endpoints:
#   GET  /api/chunks           -- paginated chunk listing
#   POST /api/chunks           -- add or update a chunk
#   DELETE /api/chunks/{id}    -- remove a chunk
#   GET  /api/search?q=        -- full-text search with snippet highlights
#   GET  /api/status           -- chunk count and database size
#
# Related modules:
#   - app/database.py    -- all DB operations; get_status()
#   - app/config.py      -- cfg (host, port, data_dir)
#   - CommonCode/dbutil.py  -- fts_build_query
# ====================================================================================================
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from app.config import cfg
from app.database import (
    add_chunk,
    delete_chunk,
    get_chunk,
    get_debate,
    get_debate_speeches,
    get_member_by_id,
    get_member_speeches,
    get_members,
    get_sittings,
    get_sitting_debates,
    get_status,
    init_db,
    list_chunks,
    search_all_dbs,
    search_chunks,
    update_chunk,
)
from app.registry import get_descriptor, list_database_ids, list_databases, reload as _registry_reload

@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Refresh the registry (picks up any .db files dropped into databases/ since process start)
    _registry_reload()
    for db_id in list_database_ids():
        init_db(db=db_id)
    yield


app = FastAPI(
    title="KoreRAG",
    description="Retrieval-augmented generation chunk storage service",
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ChunkCreate(BaseModel):
    content: str
    title: Optional[str] = None
    source: Optional[str] = None
    tags: Optional[str] = None


class ChunkUpdate(BaseModel):
    content: Optional[str] = None
    title: Optional[str] = None
    source: Optional[str] = None
    tags: Optional[str] = None


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@app.get("/status", summary="Service health and stats")
def route_status(db: str = Query("default")):
    try:
        return get_status(db=db)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown database: {db!r}")


# ---------------------------------------------------------------------------
# Chunks CRUD
# ---------------------------------------------------------------------------

@app.get("/databases", summary="List all registered databases")
def route_list_databases():
    return list_databases()


@app.get("/databases/{name}/info", summary="Descriptor + status for a single database")
def route_database_info(name: str):
    desc = get_descriptor(name)
    if desc is None:
        raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")
    try:
        status = get_status(db=name)
    except Exception:
        status = {"total_chunks": None, "db_size_bytes": None}
    return {**desc, **{k: v for k, v in status.items() if k not in ("service",)}}


@app.post("/admin/reload", summary="Re-scan databases/ directory and init any new databases")
def route_admin_reload():
    _registry_reload()
    for db_id in list_database_ids():
        init_db(db=db_id)
    return {"databases": list_database_ids()}


@app.get("/chunks", summary="List all chunks (metadata only)")
def route_list_chunks(limit: int = 100, offset: int = 0, db: str = Query("default")):
    try:
        return list_chunks(limit=limit, offset=offset, db=db)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown database: {db!r}")


@app.get("/chunks/{chunk_id}", summary="Get a single chunk with full content")
def route_get_chunk(chunk_id: int, db: str = Query("default")):
    try:
        chunk = get_chunk(chunk_id, include_content=True, db=db)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown database: {db!r}")
    if chunk is None:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return chunk


@app.post("/chunks", status_code=201, summary="Add a new chunk")
def route_add_chunk(data: ChunkCreate, db: str = Query("default")):
    try:
        return add_chunk(
            content=data.content,
            title=data.title,
            source=data.source,
            tags=data.tags,
            db=db,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown database: {db!r}")


@app.patch("/chunks/{chunk_id}", summary="Update chunk fields")
def route_update_chunk(chunk_id: int, data: ChunkUpdate, db: str = Query("default")):
    try:
        if get_chunk(chunk_id, include_content=False, db=db) is None:
            raise HTTPException(status_code=404, detail="Chunk not found")
        updated = update_chunk(chunk_id, data.model_dump(exclude_none=True), db=db)
    except HTTPException:
        raise
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown database: {db!r}")
    return updated


@app.delete("/chunks/{chunk_id}", summary="Delete a chunk")
def route_delete_chunk(chunk_id: int, db: str = Query("default")):
    try:
        if not delete_chunk(chunk_id, db=db):
            raise HTTPException(status_code=404, detail="Chunk not found")
    except HTTPException:
        raise
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown database: {db!r}")
    return {"deleted": chunk_id}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@app.get("/search", summary="Full-text search across chunks")
def route_search(
    q: str,
    limit: int = 20,
    source: Optional[str] = None,
    tags: Optional[str] = None,
    db: str = Query("default"),
):
    try:
        results = search_chunks(q, limit=limit, source=source, tags=tags, db=db)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown database: {db!r}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return results


@app.get("/search/all", summary="Full-text search across all registered databases")
def route_search_all(
    q: str,
    limit: int = 20,
    source: Optional[str] = None,
    tags: Optional[str] = None,
):
    try:
        return search_all_dbs(q, limit=limit, source=source, tags=tags)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Navigation (Hansard layer 2)
# ---------------------------------------------------------------------------

@app.get("/databases/{name}/sittings", summary="List sitting dates for a Hansard database")
def route_sittings(name: str):
    try:
        return get_sittings(db=name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/databases/{name}/sittings/{date}/debates", summary="Debates for a sitting date")
def route_sitting_debates(name: str, date: str):
    try:
        return get_sitting_debates(date=date, db=name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/databases/{name}/members", summary="Members with speech counts")
def route_members(name: str):
    try:
        return get_members(db=name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/databases/{name}/members/{member_id}", summary="Member metadata and bio")
def route_member(name: str, member_id: int):
    try:
        member = get_member_by_id(member_id=member_id, db=name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if member is None:
        raise HTTPException(status_code=404, detail=f"Member not found: {member_id}")
    return member


@app.get("/databases/{name}/members/{member_id}/speeches", summary="Speeches by a member")
def route_member_speeches(name: str, member_id: int):
    try:
        return get_member_speeches(member_id=member_id, db=name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/databases/{name}/debates/{uuid}", summary="Debate metadata by UUID")
def route_debate(name: str, uuid: str):
    try:
        debate = get_debate(debate_uuid=uuid, db=name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if debate is None:
        raise HTTPException(status_code=404, detail=f"Debate not found: {uuid!r}")
    return debate


@app.get("/databases/{name}/debates/{uuid}/speeches", summary="Speeches for a debate in order")
def route_debate_speeches(name: str, uuid: str):
    try:
        return get_debate_speeches(debate_uuid=uuid, db=name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
