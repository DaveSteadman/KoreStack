# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FastAPI application for KoreRAG - a retrieval-augmented generation chunk store.
#
# This file now owns service setup and ingest lifecycle management.
# API endpoints live in app/endpoint_api.py and UI scaffolding lives in app/endpoint_ui.py.
# ====================================================================================================
from contextlib import asynccontextmanager
from datetime import date
from datetime import datetime
from datetime import timedelta
import json
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException

_KORECOMMON_PARENT = next((parent for parent in Path(__file__).resolve().parents if (parent / "KoreCommon").is_dir()), None)
if _KORECOMMON_PARENT is not None and str(_KORECOMMON_PARENT) not in sys.path:
    sys.path.insert(0, str(_KORECOMMON_PARENT))

from KoreCommon.endpoint_manifest import build_endpoint_manifest
from app.config import cfg
from app.endpoint_api import register_rag_api
from app.endpoint_ui import register_rag_ui
from app.registry import get_descriptor, list_database_ids, reload as _registry_reload
from app.database import init_db


_ingest_procs:           dict[str, "subprocess.Popen[bytes]"] = {}
_scheduler_stop_event:   threading.Event  | None = None
_scheduler_thread:       threading.Thread | None = None
_job_handle:             int              | None = None


def _init_job_object() -> None:
    global _job_handle
    if sys.platform != "win32":
        return
    try:
        import ctypes
        import ctypes.wintypes  # noqa: F401
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
        ext.BasicLimitInformation.LimitFlags = 0x2000
        if k32.SetInformationJobObject(job, 9, ctypes.byref(ext), ctypes.sizeof(ext)):
            _job_handle = int(job)
    except Exception:
        pass


def _assign_to_job(proc: "subprocess.Popen[bytes]") -> None:
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
    try:
        descriptor = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        descriptor = {}
    descriptor["sync"] = {**descriptor.get("sync", {}), "status": status}
    json_path.write_text(json.dumps(descriptor, indent=2), encoding="utf-8")


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
    descriptor = get_descriptor(name)
    if descriptor is None:
        raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")

    if descriptor.get("managed_by") != "ingestor" or not descriptor.get("ingestor"):
        raise HTTPException(
            status_code = 409,
            detail      = f"Database {name!r} is not ingestor-managed (managed_by={descriptor.get('managed_by')!r})",
        )

    existing = _ingest_procs.get(name)
    if existing is not None and existing.poll() is None:
        return {"status": "already_running", "db": name, "ingestor": descriptor["ingestor"], "pid": existing.pid}

    ingestor_name = descriptor["ingestor"]
    data_dbs_dir  = Path(cfg["data_dir"]) / "databases"
    ingest_py     = data_dbs_dir / ingestor_name / "ingest.py"

    if not ingest_py.exists():
        raise HTTPException(
            status_code = 400,
            detail      = f"No ingest.py found for ingestor {ingestor_name!r} at {ingest_py}",
        )

    json_path = data_dbs_dir / ingestor_name / f"{ingestor_name}.json"
    if json_path.exists():
        try:
            descriptor_data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            descriptor_data = {}
        descriptor_data["sync"] = {
            **descriptor_data.get("sync", {}),
            "status":   "running",
            "last_run": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        json_path.write_text(json.dumps(descriptor_data, indent=2), encoding="utf-8")
    _registry_reload()

    proc = subprocess.Popen(
        [sys.executable, str(ingest_py)],
        stdout = subprocess.DEVNULL,
        stderr = subprocess.DEVNULL,
    )
    _ingest_procs[name] = proc
    _assign_to_job(proc)
    return {"status": "started", "db": name, "ingestor": ingestor_name, "pid": proc.pid}


def _stop_ingestor(name: str) -> dict:
    descriptor = get_descriptor(name)
    if descriptor is None:
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

    ingestor_name = descriptor.get("ingestor") or name
    data_dbs_dir  = Path(cfg["data_dir"]) / "databases"
    json_path     = data_dbs_dir / ingestor_name / f"{ingestor_name}.json"
    if json_path.exists():
        _write_sync_status(json_path, "stopped")
    _registry_reload()
    return {"status": "stopped", "db": name}


def _run_ingest_scheduler(stop_event: threading.Event) -> None:
    while not stop_event.wait(60):
        try:
            _registry_reload()
            _prune_finished_ingest_processes()
            today = date.today()
            for db_id in list_database_ids():
                descriptor = get_descriptor(db_id)
                if not descriptor or descriptor.get("managed_by") != "ingestor":
                    continue
                sync      = descriptor.get("sync") or {}
                schedule  = _normalize_schedule(descriptor.get("schedule"))
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
    data_dbs_dir = Path(cfg["data_dir"]) / "databases"
    for db_id in list_database_ids():
        descriptor = get_descriptor(db_id)
        if not descriptor:
            continue
        sync = descriptor.get("sync") or {}
        if sync.get("status") != "running":
            continue
        ingestor_name = descriptor.get("ingestor") or db_id
        json_path     = data_dbs_dir / ingestor_name / f"{ingestor_name}.json"
        if json_path.exists():
            _write_sync_status(json_path, "stopped")
    _registry_reload()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _scheduler_stop_event, _scheduler_thread
    _init_job_object()
    _registry_reload()
    for db_id in list_database_ids():
        init_db(db=db_id)
    _reset_stale_running()
    _scheduler_stop_event = threading.Event()
    _scheduler_thread     = threading.Thread(
        target = _run_ingest_scheduler,
        args   = (_scheduler_stop_event,),
        daemon = True,
        name   = "korerag-ingest-scheduler",
    )
    _scheduler_thread.start()
    yield
    if _scheduler_stop_event is not None:
        _scheduler_stop_event.set()
    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        _scheduler_thread.join(timeout=2)
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
    title       = "KoreRAG",
    description = "Retrieval-augmented generation chunk storage service",
    lifespan    = _lifespan,
)

register_rag_api(
    app,
    launch_ingestor    = _launch_ingestor,
    write_sync_status  = _write_sync_status,
    ingest_procs       = _ingest_procs,
)
register_rag_ui(
    app,
    launch_ingestor    = _launch_ingestor,
    stop_ingestor      = _stop_ingestor,
    write_sync_status  = _write_sync_status,
    assign_to_job      = _assign_to_job,
)


@app.get("/__endpoint_manifest", include_in_schema=False)
def endpoint_manifest() -> dict:
    return build_endpoint_manifest(app, service_key="korerag", service_label="KoreRAG")
