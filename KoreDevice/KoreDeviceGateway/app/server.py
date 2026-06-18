import asyncio
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import cfg
from config import get_koredevice_dir

_BASE = Path(__file__).parent.parent.parent
_DATA = get_koredevice_dir()

_SERVICES = [
    (_BASE / "KoreDeviceNumber", "KoreDeviceNumber", _DATA / "Numbers"),
]

_children: list[tuple[subprocess.Popen, str, object]] = []
_number_client: httpx.AsyncClient | None = None
_job_handle: int | None = None

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates     = Jinja2Templates(directory=str(TEMPLATES_DIR))
_UI_ASSETS    = Path(
    os.environ.get(
        "KORE_UIELEMENTS_ASSETS_DIR",
        str(Path(__file__).resolve().parents[3] / "UIElements" / "assets"),
    )
).resolve()


def _display_path(path: Path) -> Path:
    try:
        return path.relative_to(_BASE.parent)
    except ValueError:
        return path


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


def _assign_to_job(proc: subprocess.Popen) -> None:
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


def _start_children() -> None:
    for service_dir, label, data_dir in _SERVICES:
        log_path = data_dir / "service.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
        proc = subprocess.Popen(
            [sys.executable, "main.py"],
            cwd    = service_dir,
            stdout = log_file,
            stderr = log_file,
            env    = dict(os.environ),
        )
        _assign_to_job(proc)
        _children.append((proc, label, log_file))
        print(f"  > {label} starting  (pid {proc.pid})  log -> {_display_path(log_path)}")


def _stop_children() -> None:
    for proc, label, log_file in reversed(_children):
        if proc.poll() is not None:
            continue
        print(f"  [stop] Stopping {label}  (pid {proc.pid})")
        proc.terminate()
    for proc, label, log_file in reversed(_children):
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            print(f"  [kill] Force-killing {label}")
            proc.kill()
        try:
            log_file.close()
        except Exception:
            pass


async def _wait_for(client: httpx.AsyncClient, label: str, timeout: float = 20.0) -> None:
    loop = asyncio.get_running_loop()
    end  = loop.time() + timeout
    while loop.time() < end:
        try:
            response = await client.get("/status", timeout=2.0)
            if response.status_code == 200:
                print(f"  [ok] {label} ready")
                return
        except Exception:
            pass
        await asyncio.sleep(0.5)
    print(f"  [!] {label} did not respond within {timeout:.0f}s - continuing anyway")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _number_client
    _init_job_object()
    print("\n  KoreDeviceGateway - starting child services")
    _start_children()
    _number_client = httpx.AsyncClient(base_url=cfg["koredevicenumber_url"], timeout=15.0)
    await _wait_for(_number_client, "KoreDeviceNumber", timeout=40.0)
    print("  All services ready\n")
    yield
    print("\n  KoreDeviceGateway - shutting down child services")
    if _number_client is not None:
        await _number_client.aclose()
    _stop_children()


app = FastAPI(
    title       = "KoreDeviceGateway",
    description = "Central web UI for KoreDevice services",
    lifespan    = _lifespan,
)


@app.get("/ui-elements/assets/{asset_path:path}", include_in_schema=False)
def serve_ui_elements_asset(asset_path: str):
    candidate = (_UI_ASSETS / asset_path).resolve()
    if candidate != _UI_ASSETS and _UI_ASSETS not in candidate.parents:
        raise HTTPException(status_code=404, detail="Asset not found")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(str(candidate), headers={"Cache-Control": "no-store"})


async def _number_status() -> dict:
    if _number_client is None:
        return {"ok": False}
    try:
        response = await _number_client.get("/status")
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return {"ok": False}


def _svc_ui(status: dict, label: str, slug: str, url: str, icon_key: str, description: str) -> dict:
    return {
        "label":       label,
        "slug":        slug,
        "url":         url,
        "icon_key":    icon_key,
        "description": description,
        "healthy":     bool(status.get("ok")),
        "stats":       status,
    }


@app.get("/status")
async def route_status():
    number_status = await _number_status()
    return {
        "ok": True,
        "service": "KoreDeviceGateway",
        "children": {
            "numbers": {
                "healthy":        bool(number_status.get("ok")),
                "total_signals":  number_status.get("total_signals", 0),
                "total_samples":  number_status.get("total_samples", 0),
                "last_sample_at": number_status.get("last_sample_at"),
            }
        },
    }


@app.get("/", response_class=HTMLResponse)
@app.get("/ui", response_class=HTMLResponse)
async def home(request: Request):
    number_status = await _number_status()
    services = [
        _svc_ui(
            number_status,
            "KoreDeviceNumber",
            "numbers",
            "/ui/numbers",
            "koredevicenumber",
            "Named numeric signals, sample history, simple trend detection, and notices.",
        )
    ]
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "services":      services,
            "number_status": number_status,
        },
    )


@app.get("/ui/numbers", response_class=HTMLResponse)
async def numbers_page(request: Request):
    if _number_client is None:
        raise HTTPException(status_code=503, detail="KoreDeviceNumber unavailable")

    signals_r, status_r = await asyncio.gather(
        _number_client.get("/signals"),
        _number_client.get("/status"),
    )
    signals = signals_r.json() if signals_r.status_code == 200 else []
    status  = status_r.json()  if status_r.status_code == 200  else {"ok": False}
    return templates.TemplateResponse(
        request,
        "numbers.html",
        {
            "signals": signals,
            "status":  status,
        },
    )


@app.post("/ui/numbers/ingest")
async def numbers_ingest(
    signal_name:  str         = Form(...),
    value:        float       = Form(...),
    observed_at:  str | None  = Form(None),
    display_name: str | None  = Form(None),
    unit:         str | None  = Form(None),
    source:       str | None  = Form(None),
    note:         str | None  = Form(None),
    normal_min:   float | None = Form(None),
    normal_max:   float | None = Form(None),
):
    if _number_client is None:
        raise HTTPException(status_code=503, detail="KoreDeviceNumber unavailable")

    payload = {
        "name":         signal_name,
        "value":        value,
        "observed_at":  observed_at or None,
        "display_name": display_name or None,
        "unit":         unit or None,
        "source":       source or None,
        "note":         note or None,
        "normal_min":   normal_min,
        "normal_max":   normal_max,
    }
    response = await _number_client.post("/samples", json=payload)
    if response.status_code not in (200, 201):
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return RedirectResponse("/ui/numbers", status_code=303)


@app.get("/api/numbers/signals")
async def api_numbers_signals():
    if _number_client is None:
        raise HTTPException(status_code=503, detail="KoreDeviceNumber unavailable")
    response = await _number_client.get("/signals")
    return JSONResponse(content=response.json(), status_code=response.status_code)


@app.get("/api/numbers/signals/{signal_name:path}")
async def api_numbers_signal(signal_name: str):
    if _number_client is None:
        raise HTTPException(status_code=503, detail="KoreDeviceNumber unavailable")
    response = await _number_client.get(f"/signals/{signal_name}")
    return JSONResponse(content=response.json(), status_code=response.status_code)
