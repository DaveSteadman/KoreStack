# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FastAPI application for KoreRAG — a retrieval-augmented generation chunk store.
#
# Provides REST API for storing and searching text chunks (documents, notes, web pages)
# with FTS5 full-text search and optional metadata tagging.
#
# Key endpoints:
#   GET  /api/chunks           -- chunk listing (limit/offset)
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
from datetime import date
from datetime import datetime
from datetime import timedelta
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

_KORECOMMON_PARENT = next((parent for parent in Path(__file__).resolve().parents if (parent / "KoreCommon").is_dir()), None)
if _KORECOMMON_PARENT is not None and str(_KORECOMMON_PARENT) not in sys.path:
    sys.path.insert(0, str(_KORECOMMON_PARENT))

from KoreCommon.endpoint_manifest import build_endpoint_manifest
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

# Code-side databases folder — kept for reference but ingestors now live in data dir
_CODE_DBS_DIR = Path(__file__).parent.parent / "databases"

# ---------------------------------------------------------------------------
# Ingest process lifecycle management
# ---------------------------------------------------------------------------

# Active ingest subprocesses keyed by database name.
_ingest_procs: dict[str, "subprocess.Popen[bytes]"] = {}
_scheduler_stop_event: threading.Event | None = None
_scheduler_thread:     threading.Thread | None = None

# Windows Job Object handle.  All ingest processes are assigned to this job so
# they are killed automatically if KoreRAG exits for *any* reason — including a
# hard kill from the command line.  The OS closes the job handle when our
# process dies and immediately terminates every process in the job.
_job_handle: int | None = None


def _init_job_object() -> None:
    """Create a Windows Job Object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE."""
    global _job_handle
    if sys.platform != "win32":
        return
    try:
        import ctypes
        import ctypes.wintypes  # noqa: F401 — needed to populate wintypes namespace
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return

        class _BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit",     ctypes.c_int64),
                ("LimitFlags",              ctypes.c_uint32),
                ("MinimumWorkingSetSize",   ctypes.c_size_t),
                ("MaximumWorkingSetSize",   ctypes.c_size_t),
                ("ActiveProcessLimit",      ctypes.c_uint32),
                ("Affinity",                ctypes.c_size_t),
                ("PriorityClass",           ctypes.c_uint32),
                ("SchedulingClass",         ctypes.c_uint32),
            ]

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount",  ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount",   ctypes.c_uint64),
                ("WriteTransferCount",  ctypes.c_uint64),
                ("OtherTransferCount",  ctypes.c_uint64),
            ]

        class _ExtLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimit),
                ("IoInfo",                _IoCounters),
                ("ProcessMemoryLimit",    ctypes.c_size_t),
                ("JobMemoryLimit",        ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed",     ctypes.c_size_t),
            ]

        ext = _ExtLimit()
        ext.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if k32.SetInformationJobObject(job, 9, ctypes.byref(ext), ctypes.sizeof(ext)):
            _job_handle = int(job)
    except Exception:
        pass


def _assign_to_job(proc: "subprocess.Popen[bytes]") -> None:
    """Add a spawned ingest process to the kill-on-close job (Windows, best-effort)."""
    if _job_handle is None or sys.platform != "win32":
        return
    try:
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.AssignProcessToJobObject(
            ctypes.c_void_p(_job_handle),
            ctypes.c_void_p(int(proc._handle)),  # type: ignore[attr-defined]
        )
    except Exception:
        pass


def _write_sync_status(json_path: Path, status: str) -> None:
    """Patch only the sync.status field of a descriptor JSON, preserving other keys."""
    try:
        d = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    d["sync"] = {**d.get("sync", {}), "status": status}
    json_path.write_text(json.dumps(d, indent=2), encoding="utf-8")


def _normalize_schedule(value: object) -> str:
    schedule = str(value or "").strip().lower()
    return schedule if schedule in {"manual", "daily", "weekly", "monthly"} else "manual"


def _parse_last_run_date(value: object) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None


def _add_months(value: date, months: int) -> date:
    year  = value.year + ((value.month - 1 + months) // 12)
    month = ((value.month - 1 + months) % 12) + 1
    day   = value.day
    while day > 28:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1
    return date(year, month, day)


def _is_schedule_due(schedule: str, last_run: date | None, today: date) -> bool:
    normalized = _normalize_schedule(schedule)
    if normalized == "manual":
        return False
    if last_run is None:
        return True
    if normalized == "daily":
        return today >= last_run + timedelta(days=1)
    if normalized == "weekly":
        return today >= last_run + timedelta(days=7)
    if normalized == "monthly":
        return today >= _add_months(last_run, 1)
    return False


def _prune_finished_ingest_processes() -> None:
    for name, proc in list(_ingest_procs.items()):
        if proc.poll() is not None:
            _ingest_procs.pop(name, None)


def _launch_ingestor(name: str) -> dict:
    desc = get_descriptor(name)
    if desc is None:
        raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")

    if desc.get("managed_by") != "ingestor" or not desc.get("ingestor"):
        raise HTTPException(
            status_code=409,
            detail=f"Database {name!r} is not ingestor-managed (managed_by={desc.get('managed_by')!r})",
        )

    existing = _ingest_procs.get(name)
    if existing is not None and existing.poll() is None:
        return {"status": "already_running", "db": name, "ingestor": desc["ingestor"], "pid": existing.pid}

    ingestor_name = desc["ingestor"]
    data_dbs_dir  = Path(cfg["data_dir"]) / "databases"
    ingest_py     = data_dbs_dir / ingestor_name / "ingest.py"

    if not ingest_py.exists():
        raise HTTPException(
            status_code=400,
            detail=f"No ingest.py found for ingestor {ingestor_name!r} at {ingest_py}",
        )

    json_path = data_dbs_dir / ingestor_name / f"{ingestor_name}.json"
    if json_path.exists():
        try:
            d = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            d = {}
        d["sync"] = {**d.get("sync", {}), "status": "running", "last_run": date.today().isoformat()}
        json_path.write_text(json.dumps(d, indent=2), encoding="utf-8")
    _registry_reload()

    proc = subprocess.Popen(
        [sys.executable, str(ingest_py)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _ingest_procs[name] = proc
    _assign_to_job(proc)
    return {"status": "started", "db": name, "ingestor": ingestor_name, "pid": proc.pid}


def _run_ingest_scheduler(stop_event: threading.Event) -> None:
    while not stop_event.wait(60):
        try:
            _registry_reload()
            _prune_finished_ingest_processes()
            today = date.today()
            for db_id in list_database_ids():
                desc = get_descriptor(db_id)
                if not desc or desc.get("managed_by") != "ingestor":
                    continue
                sync      = desc.get("sync") or {}
                schedule  = _normalize_schedule(desc.get("schedule"))
                last_run  = _parse_last_run_date(sync.get("last_run"))
                if not _is_schedule_due(schedule, last_run, today):
                    continue
                if sync.get("status") == "running":
                    continue
                try:
                    _launch_ingestor(db_id)
                except Exception:
                    continue
        except Exception:
            continue


def _reset_stale_running() -> None:
    """On startup, any descriptor with status='running' is stale (ingest died with
    the previous server instance).  Reset those to 'stopped' so the UI is accurate."""
    data_dbs_dir = Path(cfg["data_dir"]) / "databases"
    for db_id in list_database_ids():
        desc = get_descriptor(db_id)
        if not desc:
            continue
        sync = desc.get("sync") or {}
        if sync.get("status") != "running":
            continue
        ingestor = desc.get("ingestor") or db_id
        json_path = data_dbs_dir / ingestor / f"{ingestor}.json"
        if json_path.exists():
            _write_sync_status(json_path, "stopped")
    _registry_reload()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _scheduler_stop_event, _scheduler_thread
    # Bind all future ingest subprocesses to a kill-on-close Windows Job Object
    # so they die automatically whenever this process exits (graceful or forced).
    _init_job_object()
    # Initialise databases and reset any stale 'running' statuses from a
    # previous server instance that was killed without graceful shutdown.
    _registry_reload()
    for db_id in list_database_ids():
        init_db(db=db_id)
    _reset_stale_running()
    _scheduler_stop_event = threading.Event()
    _scheduler_thread     = threading.Thread(
        target=_run_ingest_scheduler,
        args=( _scheduler_stop_event, ),
        daemon=True,
        name="korerag-ingest-scheduler",
    )
    _scheduler_thread.start()
    yield
    if _scheduler_stop_event is not None:
        _scheduler_stop_event.set()
    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        _scheduler_thread.join(timeout=2)
    # Graceful shutdown: explicitly terminate any tracked ingest processes.
    for _name, _proc in list(_ingest_procs.items()):
        if _proc.poll() is None:
            try:
                _proc.terminate()
                _proc.wait(timeout=5)
            except Exception:
                try:
                    _proc.kill()
                except Exception:
                    pass


app = FastAPI(
    title="KoreRAG",
    description="Retrieval-augmented generation chunk storage service",
    lifespan=_lifespan,
)


@app.get("/__endpoint_manifest", include_in_schema=False)
def endpoint_manifest() -> dict:
    return build_endpoint_manifest(app, service_key="korerag", service_label="KoreRAG")


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
def route_status(db: Optional[str] = Query(None)):
    # No db specified — return aggregate stats across all registered databases.
    if db is None:
        db_ids = list_database_ids()
        total_chunks = 0
        total_bytes  = 0
        for db_id in db_ids:
            try:
                s = get_status(db=db_id)
                total_chunks += s.get("total_chunks", 0)
                total_bytes  += s.get("db_size_bytes", 0)
            except Exception:
                pass
        return {"ok": True, "databases": len(db_ids),
                "total_chunks": total_chunks, "db_size_bytes": total_bytes}
    try:
        return get_status(db=db)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown database: {db!r}")


# ---------------------------------------------------------------------------
# Chunks CRUD
# ---------------------------------------------------------------------------

@app.get("/api/databases", summary="List all registered databases")
@app.get("/databases", include_in_schema=False)
def route_list_databases():
    _registry_reload()
    return list_databases()


@app.get("/api/databases/{name}/info", summary="Descriptor + status for a single database")
@app.get("/databases/{name}/info", include_in_schema=False)
def route_database_info(name: str):
    _registry_reload()
    desc = get_descriptor(name)
    if desc is None:
        raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")
    # Read sync status fresh from disk so polling always reflects what the
    # ingest subprocess has written, bypassing the in-memory registry cache.
    ingestor = desc.get("ingestor") or name
    json_path = Path(cfg["data_dir"]) / "databases" / ingestor / f"{ingestor}.json"
    if json_path.exists():
        try:
            d = json.loads(json_path.read_text(encoding="utf-8"))
            if "sync" in d:
                desc = {**desc, "sync": d["sync"]}
        except Exception:
            pass
    try:
        status = get_status(db=name)
    except Exception:
        status = {"total_chunks": None, "db_size_bytes": None}
    return {**desc, **{k: v for k, v in status.items() if k not in ("service",)}}


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
            content=data.content,
            title=data.title,
            source=data.source,
            tags=data.tags,
            db=db,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown database: {db!r}")


@app.patch("/api/chunks/{chunk_id}", summary="Update chunk fields")
@app.patch("/chunks/{chunk_id}", include_in_schema=False)
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


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@app.get("/api/search", summary="Full-text search across chunks")
@app.get("/search", include_in_schema=False)
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


@app.get("/api/search/all", summary="Full-text search across all registered databases")
@app.get("/search/all", include_in_schema=False)
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

@app.get("/api/databases/{name}/sittings", summary="List sitting dates for a Hansard database")
@app.get("/databases/{name}/sittings", include_in_schema=False)
def route_sittings(name: str):
    try:
        return get_sittings(db=name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/databases/{name}/sittings/{date}/debates", summary="Debates for a sitting date")
@app.get("/databases/{name}/sittings/{date}/debates", include_in_schema=False)
def route_sitting_debates(name: str, date: str):
    try:
        return get_sitting_debates(date=date, db=name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/databases/{name}/members", summary="Members with speech counts")
@app.get("/databases/{name}/members", include_in_schema=False)
def route_members(name: str):
    try:
        return get_members(db=name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/databases/{name}/members/{member_id}", summary="Member metadata and bio")
@app.get("/databases/{name}/members/{member_id}", include_in_schema=False)
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


@app.get("/api/databases/{name}/members/{member_id}/speeches", summary="Speeches by a member")
@app.get("/databases/{name}/members/{member_id}/speeches", include_in_schema=False)
def route_member_speeches(name: str, member_id: int):
    try:
        return get_member_speeches(member_id=member_id, db=name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/databases/{name}/debates/{uuid}", summary="Debate metadata by UUID")
@app.get("/databases/{name}/debates/{uuid}", include_in_schema=False)
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


@app.get("/api/databases/{name}/debates/{uuid}/speeches", summary="Speeches for a debate in order")
@app.get("/databases/{name}/debates/{uuid}/speeches", include_in_schema=False)
def route_debate_speeches(name: str, uuid: str):
    try:
        return get_debate_speeches(debate_uuid=uuid, db=name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Database creation
# ---------------------------------------------------------------------------

class DatabaseCreate(BaseModel):
    name: str                          # becomes the db id / filename stem
    display_name: Optional[str] = None
    description:  Optional[str] = None

_DB_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

@app.post("/api/databases", status_code=201, summary="Create a new user-managed database")
@app.post("/databases", status_code=201, include_in_schema=False)
def route_create_database(data: DatabaseCreate):
    db_id = data.name.strip().lower().replace(" ", "_")
    if not _DB_ID_RE.match(db_id):
        raise HTTPException(
            status_code=422,
            detail="Database name must start with a letter and contain only a-z, 0-9, _",
        )
    if db_id in list_database_ids():
        raise HTTPException(status_code=409, detail=f"Database {db_id!r} already exists")

    dbs_dir = Path(cfg["data_dir"]) / "databases"
    db_subdir = dbs_dir / db_id
    db_subdir.mkdir(parents=True, exist_ok=True)
    json_path = db_subdir / f"{db_id}.json"
    descriptor = {
        "id":           db_id,
        "display_name": data.display_name or db_id.replace("_", " ").title(),
        "description":  data.description or None,
        "managed_by":   "user",
    }
    json_path.write_text(json.dumps(descriptor, indent=2), encoding="utf-8")

    # Register and initialise the database (creates the .db file with tables)
    _registry_reload()
    init_db(db=db_id)

    return get_descriptor(db_id)


@app.delete("/api/databases/{name}", status_code=200, summary="Delete a database and all its data")
@app.delete("/databases/{name}", status_code=200, include_in_schema=False)
def route_delete_database(name: str):
    """Delete a database's stored content.

    For user-managed databases, remove the whole database folder.
    For ingestor-managed databases, preserve the ingest scripts/descriptor and delete only
    generated runtime artifacts such as the database file and logs.
    """
    desc = get_descriptor(name)
    if desc is None:
        raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")

    # Stop any running ingest process for this database.
    proc = _ingest_procs.pop(name, None)
    if proc is not None and proc.poll() is None:
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
            descriptor = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            descriptor = {}
        descriptor["schedule"] = str(descriptor.get("schedule") or "manual").strip().lower() or "manual"
        descriptor["sync"]     = {"status": "not_started"}
        json_path.write_text(json.dumps(descriptor, indent=2), encoding="utf-8")

    # Delete runtime data, preserving ingestor scripts where applicable.
    dbs_dir = Path(cfg["data_dir"]) / "databases"
    subdir = dbs_dir / name
    is_ingestor_managed = desc.get("managed_by") == "ingestor"
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
            # Legacy flat layout — remove .db and .json individually.
            for ext in (".db", ".json"):
                f = dbs_dir / (name + ext)
                if f.exists():
                    f.unlink()

    _registry_reload()
    return {"deleted": name}


# ---------------------------------------------------------------------------
# Ingestor sync
# ---------------------------------------------------------------------------

@app.post("/api/databases/{name}/sync", summary="Launch the ingestor for a managed database")
@app.post("/databases/{name}/sync", include_in_schema=False)
def route_sync(name: str):
    """Fire-and-forget: spawns the database's ingest.py as a subprocess and returns
    immediately.  The ingest process writes its own progress to the _meta table inside
    the database, so progress can be tracked via GET /databases/{name}/info.

    Returns 404 if the database is unknown, 409 if it is not ingestor-managed, or
    400 if the ingest.py script cannot be located.
    """
    return _launch_ingestor(name)


@app.post("/api/databases/{name}/stop", summary="Stop a running ingest process")
@app.post("/databases/{name}/stop", include_in_schema=False)
def route_stop(name: str):
    """Terminate a running ingest subprocess and mark the descriptor as 'stopped'.

    Safe to call even if the process has already finished — in that case it just
    writes the status without attempting to kill anything.
    """
    desc = get_descriptor(name)
    if desc is None:
        raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")

    proc = _ingest_procs.pop(name, None)
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    ingestor_name = desc.get("ingestor") or name
    data_dbs_dir = Path(cfg["data_dir"]) / "databases"
    json_path = data_dbs_dir / ingestor_name / f"{ingestor_name}.json"
    if json_path.exists():
        _write_sync_status(json_path, "stopped")
    _registry_reload()
    return {"status": "stopped", "db": name}
