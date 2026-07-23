import json
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote as _urlquote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader
from markupsafe import escape
from KoreCommon.service_app import register_suite_config_js
from KoreCommon.service_app import register_ui_elements_assets

from app.config import cfg
from app.database import (
    add_chunk,
    delete_chunk,
    get_chunk,
    get_status,
    init_db,
    list_chunks,
    search_chunks,
    update_chunk,
)
from app.navigation import (
    get_navigation_type,
    has_navigation,
    provider_attribute,
    provider_call,
    provider_supports,
)
from app.registry import get_descriptor, list_database_ids, list_databases, reload as _registry_reload


_RAG_UI_ROOT = Path(
    os.environ.get(
        "KORE_KORERAG_UI_DIR",
        str(Path(__file__).resolve().parents[3] / "KoreUI" / "KoreData" / "KoreRAG"),
    )
).resolve()
TEMPLATES_DIR = Path(
    os.environ.get(
        "KORE_KORERAG_TEMPLATES_DIR",
        str(_RAG_UI_ROOT / "templates"),
    )
).resolve()
_SHARED_TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "KoreUI" / "KoreData" / "KoreDataGateway" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.loader = ChoiceLoader([
    FileSystemLoader(str(TEMPLATES_DIR)),
    FileSystemLoader(str(_SHARED_TEMPLATES_DIR)),
])

_UI_ELEMENTS_ASSETS = Path(
    os.environ.get(
        "KORE_UIELEMENTS_ASSETS_DIR",
        str(Path(__file__).resolve().parents[3] / "UIElements" / "assets"),
    )
).resolve()

_RAG_SCRIPT_SCHEDULE_VALUES: set[str] = {"manual", "daily", "weekly", "monthly"}
_rag_processing_jobs:        dict[str, subprocess.Popen] = {}
_rag_processing_jobs_lock = threading.Lock()
_ingest_procs_ref:          dict[str, object]           = {}

_DB_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def _rag_runtime_root() -> Path:
    return Path(cfg["data_dir"]) / "databases"


def _suite_redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=302)


def _named_process_running(name: str) -> bool:
    candidates = [str(name or "").strip()]
    descriptor = get_descriptor(name) or {}
    ingestor   = str(descriptor.get("ingestor") or "").strip()
    if ingestor and ingestor not in candidates:
        candidates.append(ingestor)

    for candidate in candidates:
        proc = _ingest_procs_ref.get(candidate)
        if proc is not None and proc.poll() is None:
            return True

    with _rag_processing_jobs_lock:
        for candidate in candidates:
            proc = _rag_processing_jobs.get(candidate)
            if proc is not None and proc.poll() is None:
                return True
    return False


def _resolved_sync_status(sync: dict[str, Any]) -> str:
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


def _database_info(db_id: str) -> dict[str, Any]:
    descriptor = get_descriptor(db_id)
    if descriptor is None:
        raise HTTPException(status_code=404, detail=f"Unknown database: {db_id!r}")

    ingestor_name = descriptor.get("ingestor") or db_id
    json_path     = _rag_runtime_root() / ingestor_name / f"{ingestor_name}.json"
    if json_path.exists():
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            if "sync" in payload:
                descriptor = {**descriptor, "sync": payload["sync"]}
        except Exception:
            pass

    try:
        status = get_status(db=db_id)
    except Exception:
        status = {"total_chunks": None, "db_size_bytes": None}

    navigation = descriptor.get("navigation")
    if not navigation:
        try:
            if has_navigation(db_id):
                nav_type = get_navigation_type(db_id)
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

    if sync.get("status") == "running":
        if not _named_process_running(db_id):
            sync = {
                **sync,
                "status": _resolved_sync_status(sync),
            }

    return {
        **descriptor,
        **{key: value for key, value in status.items() if key != "service"},
        "navigation": navigation,
        "sync":       sync or None,
    }


def _rag_databases_enriched() -> list[dict[str, Any]]:
    _registry_reload()
    results: list[dict[str, Any]] = []
    for item in list_databases():
        db_id = str(item.get("id") or "").strip()
        if not db_id:
            results.append(item)
            continue
        try:
            enriched = {**item, **_database_info(db_id)}
        except HTTPException:
            enriched = item
        results.append(_rag_database_with_local_fallbacks(enriched))
    return results


def _rag_processing_descriptor_path(script_id: str) -> Path:
    return _rag_runtime_root() / script_id / f"{script_id}.json"


def _rag_database_file_path(db_id: str) -> Path:
    return _rag_runtime_root() / db_id / f"{db_id}.db"


def _rag_database_size_bytes(db_id: str) -> int | None:
    db_path = _rag_database_file_path(db_id)
    if not db_path.exists():
        return None
    try:
        return db_path.stat().st_size
    except OSError:
        return None


def _rag_database_with_local_fallbacks(entry: dict[str, Any]) -> dict[str, Any]:
    db_id = str(entry.get("id") or "").strip()
    if not db_id:
        return entry
    if entry.get("db_size_bytes") is None:
        size_bytes = _rag_database_size_bytes(db_id)
        if size_bytes is not None:
            entry = {**entry, "db_size_bytes": size_bytes}
    return entry


def _read_rag_processing_descriptor(script_id: str) -> dict:
    descriptor_path = _rag_processing_descriptor_path(script_id)
    if not descriptor_path.exists():
        return {}
    try:
        return json.loads(descriptor_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_rag_processing_descriptor(script_id: str, descriptor: dict) -> None:
    descriptor_path = _rag_processing_descriptor_path(script_id)
    descriptor_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor_path.write_text(json.dumps(descriptor, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _normalize_rag_processing_schedule(value: Any) -> str:
    schedule = str(value or "").strip().lower()
    return schedule if schedule in _RAG_SCRIPT_SCHEDULE_VALUES else "manual"


def _rag_processing_is_running(script_id: str) -> bool:
    with _rag_processing_jobs_lock:
        proc = _rag_processing_jobs.get(script_id)
        if proc is None:
            return False
        if proc.poll() is None:
            return True
        _rag_processing_jobs.pop(script_id, None)
        return False


def _rag_processing_scripts(database_ids: set[str]) -> list[dict[str, Any]]:
    runtime_root = _rag_runtime_root()
    results: list[dict[str, Any]] = []
    seen:    set[str]             = set()
    if not runtime_root.exists():
        return results

    for subdir in sorted(path for path in runtime_root.iterdir() if path.is_dir()):
        script_path     = subdir / "ingest.py"
        descriptor_path = subdir / f"{subdir.name}.json"
        if not script_path.exists() or not descriptor_path.exists():
            continue
        script_id = subdir.name
        if script_id in seen:
            continue
        seen.add(script_id)
        descriptor = _read_rag_processing_descriptor(script_id)
        sync       = descriptor.get("sync") or {}
        results.append({
            "id":           script_id,
            "display_name": descriptor.get("display_name") or script_id.replace("_", " ").title(),
            "description":  descriptor.get("description") or "Database builder script.",
            "managed_by":   descriptor.get("managed_by") or "ingestor",
            "ingestor":     descriptor.get("ingestor") or script_id,
            "schedule":     _normalize_rag_processing_schedule(descriptor.get("schedule")),
            "has_database": script_id in database_ids,
            "running":      _rag_processing_is_running(script_id),
            "source_path":  str(subdir),
            "log_exists":   (subdir / "processing.log").exists(),
            "last_run":     sync.get("last_run"),
            "sync_status":  sync.get("status"),
        })
    return results


def _find_rag_processing_script(script_id: str) -> dict[str, Any] | None:
    for script in _rag_processing_scripts(set()):
        if script.get("id") == script_id:
            return script
    return None


def _db_info_from_list(databases: list[dict[str, Any]], db_id: str) -> dict[str, Any]:
    for item in databases:
        if str(item.get("id") or "") == str(db_id):
            return item
    return {}


def _provider_payload(db_id: str, builder_name: str, **builder_kwargs: Any) -> dict[str, Any]:
    databases = _rag_databases_enriched()
    return provider_call(
        db_id,
        builder_name,
        db_id,
        databases = databases,
        db_info   = _db_info_from_list(databases, db_id),
        **builder_kwargs,
    )


def _rag_explore_payload(db_id: str) -> dict[str, Any]:
    databases = _rag_databases_enriched()
    return {
        "db_id":     db_id,
        "sittings":  [],
        "members":   [],
        "databases": databases,
        "db_info":   _db_info_from_list(databases, db_id),
        "errors":    [],
        "timings":   [],
    }


def _rag_explore_sitting_payload(db_id: str, date: str) -> dict[str, Any]:
    return _provider_payload(db_id, "build_sitting_payload", date=date)


def _rag_explore_debate_payload(db_id: str, uuid: str) -> dict[str, Any]:
    return _provider_payload(db_id, "build_debate_payload", uuid=uuid)


def _rag_explore_member_payload(db_id: str, member_id: int) -> dict[str, Any]:
    return _provider_payload(db_id, "build_member_payload", member_id=member_id)


def _rag_navigation_explore_payload(db_id: str) -> dict[str, Any]:
    return _provider_payload(db_id, "build_explore_payload")


def _rag_navigation_category_payload(db_id: str, category_id: str) -> dict[str, Any]:
    return _provider_payload(db_id, "build_category_payload", category_id=category_id)


def register_rag_ui(
    app: FastAPI,
    *,
    launch_ingestor,
    ingest_procs,
    stop_ingestor,
    write_sync_status,
    assign_to_job,
) -> None:
    global _ingest_procs_ref
    _ingest_procs_ref = ingest_procs
    register_suite_config_js(app)
    register_ui_elements_assets(app, _UI_ELEMENTS_ASSETS)

    @app.get("/", include_in_schema=False)
    def route_root():
        return _suite_redirect("/ui/rag/databases")

    @app.get("/ui", include_in_schema=False)
    def route_ui():
        return _suite_redirect("/ui/rag/databases")

    @app.get("/ui/rag", response_class=HTMLResponse, include_in_schema=False)
    def rag_index(request: Request, limit: int = 100, offset: int = 0, db: str = "default", view: str = ""):
        if "db" not in request.query_params:
            databases = list_databases()
            if databases:
                best       = databases[0]["id"]
                best_count = 0
                for item in databases:
                    db_id = item["id"]
                    try:
                        count = int(get_status(db=db_id).get("total_chunks", 0) or 0)
                    except Exception:
                        count = 0
                    if count > best_count:
                        best_count = count
                        best       = db_id
                best_db = next((item for item in databases if item["id"] == best), None)
                if best_db and best_db.get("navigation"):
                    return _suite_redirect(f"/ui/rag/explore/{best}")
                params     = dict(request.query_params)
                params["db"] = best
                qs         = "&".join(f"{key}={value}" for key, value in params.items())
                return _suite_redirect(f"/ui/rag?{qs}")

        db_info = _database_info(db)
        if db_info.get("navigation") and str(view or "").strip().lower() != "chunks":
            return _suite_redirect(f"/ui/rag/explore/{db}")

        chunks    = list_chunks(limit=limit, offset=offset, db=db)
        status    = get_status(db=db)
        databases = list_databases()
        return templates.TemplateResponse(
            request,
            "rag_index.html",
            {
                "chunks":    chunks,
                "total":     status.get("total_chunks", len(chunks)),
                "limit":     limit,
                "offset":    offset,
                "db":        db,
                "view":      view,
                "databases": databases,
                "has_nav":   bool(db_info.get("navigation")),
            },
        )

    @app.get("/ui/rag/databases/json", include_in_schema=False)
    def rag_databases_json():
        databases = _rag_databases_enriched()
        return {
            "databases":          databases,
            "processing_scripts": _rag_processing_scripts({item.get("id") for item in databases if item.get("id")}),
        }

    @app.get("/ui/rag/databases", response_class=HTMLResponse, include_in_schema=False)
    def rag_databases(request: Request):
        databases  = _rag_databases_enriched()
        processing = _rag_processing_scripts({item.get("id") for item in databases if item.get("id")})
        return templates.TemplateResponse(
            request,
            "rag_databases.html",
            {"databases": databases, "processing_scripts": processing},
        )

    @app.post("/ui/rag/databases/{name}/sync", include_in_schema=False)
    def rag_database_sync(name: str):
        launch_ingestor(name)
        return RedirectResponse("/ui/rag/databases", status_code=303)

    @app.post("/ui/rag/processing/{script_id}/run", include_in_schema=False)
    def rag_processing_run(script_id: str, reset: int = Form(0)):
        script = _find_rag_processing_script(script_id)
        if script is None:
            raise HTTPException(status_code=404, detail=f"Unknown processing script: {script_id!r}")

        descriptor = _read_rag_processing_descriptor(script_id)
        sync       = descriptor.get("sync") or {}
        if str(sync.get("status") or "").strip().lower() == "running":
            return RedirectResponse("/ui/rag/databases", status_code=303)

        with _rag_processing_jobs_lock:
            existing = _rag_processing_jobs.get(script_id)
            if existing is not None and existing.poll() is None:
                return RedirectResponse("/ui/rag/databases", status_code=303)
            if existing is not None and existing.poll() is not None:
                _rag_processing_jobs.pop(script_id, None)

        script_dir  = Path(script["source_path"])
        script_path = script_dir / "ingest.py"
        if not script_path.exists():
            raise HTTPException(status_code=404, detail=f"Missing ingest.py for {script_id!r}")

        argv = [os.sys.executable, str(script_path)]
        if reset:
            argv.append("--reset")

        log_path   = script_dir / "processing.log"
        try:
            log_handle = open(log_path, "ab")
        except PermissionError:
            return RedirectResponse("/ui/rag/databases", status_code=303)
        try:
            proc = subprocess.Popen(
                argv,
                cwd    = str(script_dir),
                stdout = log_handle,
                stderr = subprocess.STDOUT,
                env    = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"},
            )
        finally:
            log_handle.close()

        assign_to_job(proc)
        with _rag_processing_jobs_lock:
            _rag_processing_jobs[script_id] = proc
        return RedirectResponse("/ui/rag/databases", status_code=303)

    @app.post("/ui/rag/processing/{script_id}/schedule", include_in_schema=False)
    def rag_processing_schedule(script_id: str, schedule: str = Form("")):
        script = _find_rag_processing_script(script_id)
        if script is None:
            raise HTTPException(status_code=404, detail=f"Unknown processing script: {script_id!r}")

        descriptor             = _read_rag_processing_descriptor(script_id)
        descriptor["schedule"] = _normalize_rag_processing_schedule(schedule)
        _write_rag_processing_descriptor(script_id, descriptor)
        return RedirectResponse("/ui/rag/databases", status_code=303)

    @app.get("/ui/rag/processing/{script_id}/log", response_class=HTMLResponse, include_in_schema=False)
    def rag_processing_log(script_id: str):
        script = _find_rag_processing_script(script_id)
        if script is None:
            raise HTTPException(status_code=404, detail=f"Unknown processing script: {script_id!r}")

        script_dir = Path(script["source_path"])
        log_path   = script_dir / "processing.log"
        if not log_path.exists():
            raise HTTPException(status_code=404, detail=f"No processing log found for {script_id!r}")
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not read processing log: {exc}") from exc

        return HTMLResponse(
            f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(script_id)} processing log</title>
  <link rel="stylesheet" href="/ui-elements/assets/css/chrome.css">
</head>
<body class="kcui-shell-bg">
  <main class="kcui-page kcui-page--narrow kcui-stack">
    <section class="kcui-panel">
      <div class="kcui-panel-header">
        <span>{escape(script.get("display_name") or script_id)} processing log</span>
        <a class="kcui-tag kcui-tag--muted" href="/ui/rag/databases" style="margin-left:auto;">BACK</a>
      </div>
      <pre class="kcui-panel-body kcui-panel-body--mono kcui-panel-body--scroll" style="max-height:75vh; white-space:pre-wrap;">{escape(text)}</pre>
    </section>
  </main>
</body>
</html>"""
        )

    @app.post("/ui/rag/databases/{name}/stop", include_in_schema=False)
    def rag_database_stop(name: str):
        stop_ingestor(name)
        return RedirectResponse("/ui/rag/databases", status_code=303)

    @app.post("/ui/rag/databases/{name}/delete", include_in_schema=False)
    def rag_database_delete(name: str):
        descriptor = get_descriptor(name)
        if descriptor is None:
            raise HTTPException(status_code=404, detail=f"Unknown database: {name!r}")

        stop_ingestor(name)

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

        dbs_dir             = _rag_runtime_root()
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
        return RedirectResponse("/ui/rag/databases", status_code=303)

    @app.post("/ui/rag/databases", response_class=HTMLResponse, include_in_schema=False)
    def rag_database_create(
        request:      Request,
        name:         str           = Form(...),
        display_name: Optional[str] = Form(None),
        description:  Optional[str] = Form(None),
    ):
        db_id = name.strip().lower().replace(" ", "_")
        if not _DB_ID_RE.match(db_id):
            databases  = _rag_databases_enriched()
            processing = _rag_processing_scripts({item.get("id") for item in databases if item.get("id")})
            return templates.TemplateResponse(
                request,
                "rag_databases.html",
                {
                    "databases":          databases,
                    "processing_scripts": processing,
                    "create_error":       "Database name must start with a letter and contain only a-z, 0-9, _",
                },
                status_code = 422,
            )
        if db_id in list_database_ids():
            databases  = _rag_databases_enriched()
            processing = _rag_processing_scripts({item.get("id") for item in databases if item.get("id")})
            return templates.TemplateResponse(
                request,
                "rag_databases.html",
                {
                    "databases":          databases,
                    "processing_scripts": processing,
                    "create_error":       f"Database {db_id!r} already exists",
                },
                status_code = 409,
            )

        dbs_dir   = _rag_runtime_root()
        db_subdir = dbs_dir / db_id
        db_subdir.mkdir(parents=True, exist_ok=True)
        db_path   = db_subdir / f"{db_id}.db"
        json_path = db_subdir / f"{db_id}.json"
        descriptor = {
            "id":           db_id,
            "display_name": display_name or db_id.replace("_", " ").title(),
            "description":  description or None,
            "managed_by":   "user",
        }
        json_path.write_text(json.dumps(descriptor, indent=2), encoding="utf-8")
        db_path.touch(exist_ok=True)
        _registry_reload()
        init_db(db=db_id)
        return RedirectResponse("/ui/rag/databases", status_code=303)

    @app.get("/ui/rag/search", response_class=HTMLResponse, include_in_schema=False)
    def rag_search(
        request: Request,
        q:       Optional[str] = None,
        source:  Optional[str] = None,
        tags:    Optional[str] = None,
        limit:   int           = 20,
        db:      str           = "default",
    ):
        searched = bool(q)
        results  = search_chunks(q, limit=limit, source=source, tags=tags, db=db) if searched else []
        return templates.TemplateResponse(
            request,
            "rag_search.html",
            {
                "results":   results,
                "searched":  searched,
                "q":         q or "",
                "source":    source or "",
                "tags":      tags or "",
                "limit":     limit,
                "db":        db,
                "databases": list_databases(),
            },
        )

    @app.get("/ui/rag/insert", response_class=HTMLResponse, include_in_schema=False)
    def rag_insert(request: Request, db: str = "default"):
        return templates.TemplateResponse(
            request,
            "rag_insert.html",
            {"error": None, "success": None, "db": db, "databases": list_databases()},
        )

    @app.post("/ui/rag/insert", response_class=HTMLResponse, include_in_schema=False)
    def rag_insert_post(
        request: Request,
        content: str           = Form(...),
        title:   Optional[str] = Form(None),
        source:  Optional[str] = Form(None),
        tags:    Optional[str] = Form(None),
        db:      str           = Form("default"),
    ):
        try:
            chunk = add_chunk(content=content, title=title, source=source, tags=tags, db=db)
        except Exception as exc:
            return templates.TemplateResponse(
                request,
                "rag_insert.html",
                {"error": str(exc), "success": None, "db": db, "databases": list_databases()},
                status_code = 400,
            )
        return RedirectResponse(url=f"/ui/rag/{chunk.get('id')}?db={db}", status_code=303)

    @app.get("/ui/rag/{chunk_id}", response_class=HTMLResponse, include_in_schema=False)
    def rag_chunk(request: Request, chunk_id: int, db: str = "default"):
        chunk = get_chunk(chunk_id, include_content=True, db=db)
        if chunk is None:
            raise HTTPException(status_code=404, detail="Chunk not found")
        return templates.TemplateResponse(request, "rag_chunk.html", {"chunk": chunk, "db": db})

    @app.post("/ui/rag/{chunk_id}/edit", include_in_schema=False)
    def rag_chunk_edit(
        chunk_id: int,
        db:       str = "default",
        title:    str = Form(""),
        source:   str = Form(""),
        tags:     str = Form(""),
        content:  str = Form(""),
    ):
        payload = {"title": title, "source": source, "tags": tags, "content": content}
        update_chunk(chunk_id, payload, db=db)
        return RedirectResponse(url=f"/ui/rag/{chunk_id}?db={db}", status_code=303)

    @app.post("/ui/rag/{chunk_id}/delete", include_in_schema=False)
    def rag_chunk_delete(chunk_id: int, db: str = "default"):
        if not delete_chunk(chunk_id, db=db):
            raise HTTPException(status_code=404, detail="Chunk not found")
        return RedirectResponse(url=f"/ui/rag?db={db}", status_code=303)

    @app.get("/ui/rag/explore/{db_id}/json", include_in_schema=False)
    def rag_explore_json(db_id: str):
        if provider_supports(db_id, "build_explore_payload"):
            return JSONResponse(_rag_navigation_explore_payload(db_id))
        return JSONResponse(_rag_explore_payload(db_id))

    @app.get("/ui/rag/explore/{db_id}", response_class=HTMLResponse, include_in_schema=False)
    def rag_explore(request: Request, db_id: str):
        if provider_supports(db_id, "build_explore_payload"):
            template_name = provider_attribute(db_id, "EXPLORE_TEMPLATE", "rag_explore.html")
            context       = {"db_id": db_id, **(provider_attribute(db_id, "EXPLORE_CONTEXT", {}) or {})}
            return templates.TemplateResponse(
                request,
                template_name,
                context,
            )
        return templates.TemplateResponse(
            request,
            "rag_explore.html",
            {
                "db_id":     db_id,
                "sittings":  [],
                "members":   [],
                "databases": [],
                "db_info":   {},
                "errors":    [],
                "timings":   [],
            },
        )

    @app.get("/ui/rag/explore/{db_id}/sitting/{date}/json", include_in_schema=False)
    def rag_explore_sitting_json(db_id: str, date: str):
        if not provider_supports(db_id, "build_sitting_payload"):
            raise HTTPException(status_code=404, detail="Sitting navigation not supported for this database")
        return JSONResponse(_rag_explore_sitting_payload(db_id, date))

    @app.get("/ui/rag/explore/{db_id}/sitting/{date}", response_class=HTMLResponse, include_in_schema=False)
    def rag_explore_sitting(request: Request, db_id: str, date: str):
        if not provider_supports(db_id, "build_sitting_payload"):
            raise HTTPException(status_code=404, detail="Sitting navigation not supported for this database")
        return templates.TemplateResponse(
            request,
            provider_attribute(db_id, "SITTING_TEMPLATE", "rag_explore_sitting.html"),
            {"db_id": db_id, "date": date, **(provider_attribute(db_id, "SITTING_CONTEXT", {}) or {})},
        )

    @app.get("/ui/rag/explore/{db_id}/debate/{uuid}/json", include_in_schema=False)
    def rag_explore_debate_json(db_id: str, uuid: str):
        if not provider_supports(db_id, "build_debate_payload"):
            raise HTTPException(status_code=404, detail="Debate navigation not supported for this database")
        return JSONResponse(_rag_explore_debate_payload(db_id, uuid))

    @app.get("/ui/rag/explore/{db_id}/debate/{uuid}", response_class=HTMLResponse, include_in_schema=False)
    def rag_explore_debate(request: Request, db_id: str, uuid: str):
        if not provider_supports(db_id, "build_debate_payload"):
            raise HTTPException(status_code=404, detail="Debate navigation not supported for this database")
        return templates.TemplateResponse(
            request,
            provider_attribute(db_id, "DEBATE_TEMPLATE", "rag_explore_debate.html"),
            {"db_id": db_id, "uuid": uuid, **(provider_attribute(db_id, "DEBATE_CONTEXT", {}) or {})},
        )

    @app.get("/ui/rag/explore/{db_id}/member/{member_id}/json", include_in_schema=False)
    def rag_explore_member_json(db_id: str, member_id: int):
        if not provider_supports(db_id, "build_member_payload"):
            raise HTTPException(status_code=404, detail="Member navigation not supported for this database")
        return JSONResponse(_rag_explore_member_payload(db_id, member_id))

    @app.get("/ui/rag/explore/{db_id}/member/{member_id}", response_class=HTMLResponse, include_in_schema=False)
    def rag_explore_member(request: Request, db_id: str, member_id: int):
        if not provider_supports(db_id, "build_member_payload"):
            raise HTTPException(status_code=404, detail="Member navigation not supported for this database")
        return templates.TemplateResponse(
            request,
            provider_attribute(db_id, "MEMBER_TEMPLATE", "rag_explore_member.html"),
            {"db_id": db_id, "member_id": member_id, **(provider_attribute(db_id, "MEMBER_CONTEXT", {}) or {})},
        )

    @app.get("/ui/rag/explore/{db_id}/category/{category_id}/json", include_in_schema=False)
    def rag_explore_category_json(db_id: str, category_id: str):
        if not provider_supports(db_id, "build_category_payload"):
            raise HTTPException(status_code=404, detail="Category navigation not supported for this database")
        return JSONResponse(_rag_navigation_category_payload(db_id, category_id))

    @app.get("/ui/rag/explore/{db_id}/category/{category_id}", response_class=HTMLResponse, include_in_schema=False)
    def rag_explore_category(request: Request, db_id: str, category_id: str):
        if not provider_supports(db_id, "build_category_payload"):
            raise HTTPException(status_code=404, detail="Category navigation not supported for this database")
        return templates.TemplateResponse(
            request,
            provider_attribute(db_id, "CATEGORY_TEMPLATE"),
            {"db_id": db_id, "category_id": category_id, **(provider_attribute(db_id, "CATEGORY_CONTEXT", {}) or {})},
        )

    @app.get("/ui/rag/explore/{db_id}/item/{page_code}/{anchor_id}", include_in_schema=False)
    def rag_explore_item_redirect(db_id: str, page_code: str, anchor_id: str):
        if not provider_supports(db_id, "resolve_item_chunk_id"):
            raise HTTPException(status_code=404, detail="Item navigation not supported for this database")
        chunk_id = provider_call(db_id, "resolve_item_chunk_id", db_id, page_code, anchor_id)
        if not chunk_id:
            raise HTTPException(status_code=404, detail="Downloaded content chunk not found")

        return RedirectResponse(url=f"/ui/rag/{int(chunk_id)}?db={_urlquote(db_id)}", status_code=302)
