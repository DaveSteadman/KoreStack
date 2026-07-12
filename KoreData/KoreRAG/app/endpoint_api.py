import json
import re
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from app.config import cfg
from app.database import (
    add_chunk,
    delete_chunk,
    get_chunk,
    get_status,
    init_db,
    list_chunks,
    search_all_dbs,
    search_chunks,
    update_chunk,
)
from app.navigation import get_navigation_type, has_navigation, provider_call, provider_supports
from app.registry import get_descriptor, list_database_ids, list_databases, reload as _registry_reload


class ChunkCreate(BaseModel):
    content: str
    title:   Optional[str] = None
    source:  Optional[str] = None
    tags:    Optional[str] = None


class ChunkUpdate(BaseModel):
    content: Optional[str] = None
    title:   Optional[str] = None
    source:  Optional[str] = None
    tags:    Optional[str] = None


class DatabaseCreate(BaseModel):
    name:         str
    display_name: Optional[str] = None
    description:  Optional[str] = None


_DB_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def _api_named_process_running(name: str, ingest_procs: dict[str, object]) -> bool:
    candidates = [str(name or "").strip()]
    descriptor = get_descriptor(name) or {}
    ingestor   = str(descriptor.get("ingestor") or "").strip()
    if ingestor and ingestor not in candidates:
        candidates.append(ingestor)

    for candidate in candidates:
        proc = ingest_procs.get(candidate)
        if proc is not None and getattr(proc, "poll")() is None:
            return True
    return False


def _api_resolved_sync_status(sync: dict) -> str:
    status = str(sync.get("status") or "").strip().lower()
    if status != "running":
        return status
    last_run       = str(sync.get("last_run") or "").strip()
    last_completed = str(sync.get("last_ingest_completed_at") or "").strip()
    if last_run and (not last_completed or last_run > last_completed):
        return "failed"
    if last_completed:
        return "complete"
    return "idle"


def register_rag_api(
    app: FastAPI,
    *,
    launch_ingestor,
    write_sync_status,
    ingest_procs: dict[str, object],
) -> None:
    @app.get("/status", summary="Service health and stats")
    def route_status(db: Optional[str] = Query(None)):
        if db is None:
            db_ids        = list_database_ids()
            total_chunks  = 0
            total_bytes   = 0
            for db_id in db_ids:
                try:
                    status        = get_status(db=db_id)
                    total_chunks += status.get("total_chunks", 0)
                    total_bytes  += status.get("db_size_bytes", 0)
                except Exception:
                    pass
            return {
                "ok":            True,
                "databases":     len(db_ids),
                "total_chunks":  total_chunks,
                "db_size_bytes": total_bytes,
            }
        try:
            return get_status(db=db)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown database: {db!r}")

    @app.get("/api/databases", summary="List all registered databases")
    @app.get("/databases", include_in_schema=False)
    def route_list_databases():
        _registry_reload()
        return list_databases()

    @app.get("/api/databases/{name}/info", summary="Descriptor + status for a single database")
    @app.get("/databases/{name}/info", include_in_schema=False)
    def route_database_info(name: str):
        _registry_reload()
        descriptor = get_descriptor(name)
        if descriptor is None:
            raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")

        ingestor_name = descriptor.get("ingestor") or name
        json_path     = Path(cfg["data_dir"]) / "databases" / ingestor_name / f"{ingestor_name}.json"
        if json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                if "sync" in data:
                    descriptor = {**descriptor, "sync": data["sync"]}
            except Exception:
                pass

        try:
            status = get_status(db=name)
        except Exception:
            status = {"total_chunks": None, "db_size_bytes": None}

        navigation = descriptor.get("navigation")
        if not navigation:
            try:
                if has_navigation(name):
                    nav_type = get_navigation_type(name)
                    if nav_type:
                        navigation = {"type": nav_type}
            except Exception:
                navigation = descriptor.get("navigation")

        sync = descriptor.get("sync") or {}
        if status.get("total_chunks") is not None:
            sync = {
                **sync,
                "current_total_chunks": status.get("total_chunks"),
            }

        if sync.get("status") == "running" and not _api_named_process_running(name, ingest_procs):
            sync = {
                **sync,
                "status": _api_resolved_sync_status(sync),
            }

        return {
            **descriptor,
            **{key: value for key, value in status.items() if key != "service"},
            "navigation": navigation,
            "sync":       sync or None,
        }

    @app.post("/api/admin/reload", summary="Re-scan databases/ directory and init any new databases")
    @app.post("/admin/reload", include_in_schema=False)
    def route_admin_reload():
        _registry_reload()
        for db_id in list_database_ids():
            init_db(db=db_id)
        return {"databases": list_database_ids()}

    @app.get("/api/chunks", summary="List all chunks (metadata only)")
    @app.get("/chunks", include_in_schema=False)
    def route_list_chunks(limit: int = 100, offset: int = 0, db: str = Query("default")):
        try:
            return list_chunks(limit=limit, offset=offset, db=db)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown database: {db!r}")

    @app.get("/api/chunks/{chunk_id}", summary="Get a single chunk with full content")
    @app.get("/chunks/{chunk_id}", include_in_schema=False)
    def route_get_chunk(chunk_id: int, db: str = Query("default")):
        try:
            chunk = get_chunk(chunk_id, include_content=True, db=db)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown database: {db!r}")
        if chunk is None:
            raise HTTPException(status_code=404, detail="Chunk not found")
        return chunk

    @app.post("/api/chunks", status_code=201, summary="Add a new chunk")
    @app.post("/chunks", status_code=201, include_in_schema=False)
    def route_add_chunk(data: ChunkCreate, db: str = Query("default")):
        try:
            return add_chunk(
                content = data.content,
                title   = data.title,
                source  = data.source,
                tags    = data.tags,
                db      = db,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown database: {db!r}")

    @app.patch("/api/chunks/{chunk_id}", summary="Update chunk fields")
    @app.patch("/chunks/{chunk_id}", include_in_schema=False)
    def route_update_chunk(chunk_id: int, data: ChunkUpdate, db: str = Query("default")):
        try:
            if get_chunk(chunk_id, include_content=False, db=db) is None:
                raise HTTPException(status_code=404, detail="Chunk not found")
            return update_chunk(chunk_id, data.model_dump(exclude_none=True), db=db)
        except HTTPException:
            raise
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown database: {db!r}")

    @app.delete("/api/chunks/{chunk_id}", summary="Delete a chunk")
    @app.delete("/chunks/{chunk_id}", include_in_schema=False)
    def route_delete_chunk(chunk_id: int, db: str = Query("default")):
        try:
            if not delete_chunk(chunk_id, db=db):
                raise HTTPException(status_code=404, detail="Chunk not found")
        except HTTPException:
            raise
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown database: {db!r}")
        return {"deleted": chunk_id}

    @app.get("/api/search", summary="Full-text search across chunks")
    @app.get("/search", include_in_schema=False)
    def route_search(
        q:      str,
        limit:  int           = 20,
        source: Optional[str] = None,
        tags:   Optional[str] = None,
        db:     str           = Query("default"),
    ):
        try:
            return search_chunks(q, limit=limit, source=source, tags=tags, db=db)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown database: {db!r}")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/search/all", summary="Full-text search across all registered databases")
    @app.get("/search/all", include_in_schema=False)
    def route_search_all(
        q:      str,
        limit:  int           = 20,
        source: Optional[str] = None,
        tags:   Optional[str] = None,
    ):
        try:
            return search_all_dbs(q, limit=limit, source=source, tags=tags)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/databases/{name}/sittings", summary="List sitting dates for a navigation-capable database")
    @app.get("/databases/{name}/sittings", include_in_schema=False)
    def route_sittings(name: str):
        if not provider_supports(name, "get_sittings"):
            raise HTTPException(status_code=404, detail=f"Sittings not supported for database: {name!r}")
        try:
            return provider_call(name, "get_sittings", db=name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/databases/{name}/sittings/{date}/debates", summary="Debates for a sitting date")
    @app.get("/databases/{name}/sittings/{date}/debates", include_in_schema=False)
    def route_sitting_debates(name: str, date: str):
        if not provider_supports(name, "get_sitting_debates"):
            raise HTTPException(status_code=404, detail=f"Sitting debates not supported for database: {name!r}")
        try:
            return provider_call(name, "get_sitting_debates", date=date, db=name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/databases/{name}/members", summary="Members with speech counts")
    @app.get("/databases/{name}/members", include_in_schema=False)
    def route_members(name: str):
        if not provider_supports(name, "get_members"):
            raise HTTPException(status_code=404, detail=f"Members not supported for database: {name!r}")
        try:
            return provider_call(name, "get_members", db=name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/databases/{name}/members/{member_id}", summary="Member metadata and bio")
    @app.get("/databases/{name}/members/{member_id}", include_in_schema=False)
    def route_member(name: str, member_id: int):
        if not provider_supports(name, "get_member_by_id"):
            raise HTTPException(status_code=404, detail=f"Members not supported for database: {name!r}")
        try:
            member = provider_call(name, "get_member_by_id", member_id=member_id, db=name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if member is None:
            raise HTTPException(status_code=404, detail=f"Member not found: {member_id}")
        return member

    @app.get("/api/databases/{name}/members/{member_id}/speeches", summary="Speeches by a member")
    @app.get("/databases/{name}/members/{member_id}/speeches", include_in_schema=False)
    def route_member_speeches(name: str, member_id: int):
        if not provider_supports(name, "get_member_speeches"):
            raise HTTPException(status_code=404, detail=f"Member speeches not supported for database: {name!r}")
        try:
            return provider_call(name, "get_member_speeches", member_id=member_id, db=name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/databases/{name}/debates/{uuid}", summary="Debate metadata by UUID")
    @app.get("/databases/{name}/debates/{uuid}", include_in_schema=False)
    def route_debate(name: str, uuid: str):
        if not provider_supports(name, "get_debate"):
            raise HTTPException(status_code=404, detail=f"Debates not supported for database: {name!r}")
        try:
            debate = provider_call(name, "get_debate", debate_uuid=uuid, db=name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if debate is None:
            raise HTTPException(status_code=404, detail=f"Debate not found: {uuid!r}")
        return debate

    @app.get("/api/databases/{name}/debates/{uuid}/speeches", summary="Speeches for a debate in order")
    @app.get("/databases/{name}/debates/{uuid}/speeches", include_in_schema=False)
    def route_debate_speeches(name: str, uuid: str):
        if not provider_supports(name, "get_debate_speeches"):
            raise HTTPException(status_code=404, detail=f"Debate speeches not supported for database: {name!r}")
        try:
            return provider_call(name, "get_debate_speeches", debate_uuid=uuid, db=name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/databases", status_code=201, summary="Create a new user-managed database")
    @app.post("/databases", status_code=201, include_in_schema=False)
    def route_create_database(data: DatabaseCreate):
        db_id = data.name.strip().lower().replace(" ", "_")
        if not _DB_ID_RE.match(db_id):
            raise HTTPException(
                status_code = 422,
                detail      = "Database name must start with a letter and contain only a-z, 0-9, _",
            )
        if db_id in list_database_ids():
            raise HTTPException(status_code=409, detail=f"Database {db_id!r} already exists")

        dbs_dir   = Path(cfg["data_dir"]) / "databases"
        db_subdir = dbs_dir / db_id
        db_subdir.mkdir(parents=True, exist_ok=True)
        db_path   = db_subdir / f"{db_id}.db"
        json_path = db_subdir / f"{db_id}.json"
        descriptor = {
            "id":           db_id,
            "display_name": data.display_name or db_id.replace("_", " ").title(),
            "description":  data.description or None,
            "managed_by":   "user",
        }
        json_path.write_text(json.dumps(descriptor, indent=2), encoding="utf-8")
        db_path.touch(exist_ok=True)
        _registry_reload()
        init_db(db=db_id)
        return get_descriptor(db_id)

    @app.delete("/api/databases/{name}", status_code=200, summary="Delete a database and all its data")
    @app.delete("/databases/{name}", status_code=200, include_in_schema=False)
    def route_delete_database(name: str):
        descriptor = get_descriptor(name)
        if descriptor is None:
            raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")

        proc = ingest_procs.pop(name, None)
        if proc is not None and getattr(proc, "poll")() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        def _delete_runtime_artifacts(base_dir: Path, db_id: str) -> None:
            for candidate in (
                base_dir / f"{db_id}.db",
                base_dir / f"{db_id}.db-shm",
                base_dir / f"{db_id}.db-wal",
                base_dir / "processing.log",
            ):
                if candidate.exists():
                    candidate.unlink()

        def _reset_ingestor_descriptor(base_dir: Path, db_id: str) -> None:
            json_path = base_dir / f"{db_id}.json"
            if not json_path.exists():
                return
            try:
                descriptor_data = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                descriptor_data = {}
            descriptor_data["schedule"] = str(descriptor_data.get("schedule") or "manual").strip().lower() or "manual"
            descriptor_data["sync"]     = {"status": "not_started"}
            json_path.write_text(json.dumps(descriptor_data, indent=2), encoding="utf-8")

        dbs_dir             = Path(cfg["data_dir"]) / "databases"
        subdir              = dbs_dir / name
        is_ingestor_managed = descriptor.get("managed_by") == "ingestor"
        if subdir.is_dir():
            import shutil
            if is_ingestor_managed:
                _delete_runtime_artifacts(subdir, name)
                _reset_ingestor_descriptor(subdir, name)
            else:
                shutil.rmtree(subdir)
        else:
            if is_ingestor_managed:
                _delete_runtime_artifacts(dbs_dir, name)
                _reset_ingestor_descriptor(dbs_dir, name)
            else:
                for ext in (".db", ".json"):
                    candidate = dbs_dir / (name + ext)
                    if candidate.exists():
                        candidate.unlink()

        _registry_reload()
        return {"deleted": name}

    @app.post("/api/databases/{name}/sync", summary="Launch the ingestor for a managed database")
    @app.post("/databases/{name}/sync", include_in_schema=False)
    def route_sync(name: str):
        return launch_ingestor(name)

    @app.post("/api/databases/{name}/stop", summary="Stop a running ingest process")
    @app.post("/databases/{name}/stop", include_in_schema=False)
    def route_stop(name: str):
        descriptor = get_descriptor(name)
        if descriptor is None:
            raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")

        proc = ingest_procs.pop(name, None)
        if proc is not None and getattr(proc, "poll")() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        ingestor_name = descriptor.get("ingestor") or name
        data_dbs_dir  = Path(cfg["data_dir"]) / "databases"
        json_path     = data_dbs_dir / ingestor_name / f"{ingestor_name}.json"
        if json_path.exists():
            write_sync_status(json_path, "stopped")
        _registry_reload()
        return {"status": "stopped", "db": name}
