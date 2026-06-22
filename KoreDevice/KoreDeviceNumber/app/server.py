from contextlib import asynccontextmanager
from pathlib import Path
import sys

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

_KORECOMMON_PARENT = next((parent for parent in Path(__file__).resolve().parents if (parent / "KoreCommon").is_dir()), None)
if _KORECOMMON_PARENT is not None and str(_KORECOMMON_PARENT) not in sys.path:
    sys.path.insert(0, str(_KORECOMMON_PARENT))

from KoreCommon.endpoint_manifest import build_endpoint_manifest
from app.database import get_signal, get_status, init_db, list_signals, record_sample


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title       = "KoreDeviceNumber",
    description = "Numeric signal capture and simple analysis service",
    lifespan    = _lifespan,
)


@app.get("/__endpoint_manifest", include_in_schema=False)
def endpoint_manifest() -> dict:
    return build_endpoint_manifest(app, service_key="koredevicenumber", service_label="KoreDeviceNumber")


class SampleCreate(BaseModel):
    name:         str
    value:        float
    observed_at:  str | None = None
    display_name: str | None = None
    unit:         str | None = None
    source:       str | None = None
    note:         str | None = None
    normal_min:   float | None = None
    normal_max:   float | None = None


class SignalSampleCreate(BaseModel):
    value:        float
    observed_at:  str | None = None
    display_name: str | None = None
    unit:         str | None = None
    source:       str | None = None
    note:         str | None = None
    normal_min:   float | None = None
    normal_max:   float | None = None


@app.get("/status")
def route_status():
    return get_status()


@app.get("/api/signals")
@app.get("/signals", include_in_schema=False)
def route_signals(limit: int = 200):
    return list_signals(limit=limit)


@app.get("/api/signals/{name:path}")
@app.get("/signals/{name:path}", include_in_schema=False)
def route_signal(name: str, sample_limit: int = 100):
    signal = get_signal(name, sample_limit=sample_limit)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    return signal


@app.post("/api/samples", status_code=201)
@app.post("/samples", status_code=201, include_in_schema=False)
def route_record_sample(data: SampleCreate):
    try:
        return record_sample(
            name         = data.name,
            value        = data.value,
            observed_at  = data.observed_at,
            display_name = data.display_name,
            unit         = data.unit,
            source       = data.source,
            note         = data.note,
            normal_min   = data.normal_min,
            normal_max   = data.normal_max,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/signals/{name:path}/samples", status_code=201)
@app.post("/signals/{name:path}/samples", status_code=201, include_in_schema=False)
def route_record_signal_sample(name: str, data: SignalSampleCreate):
    try:
        return record_sample(
            name         = name,
            value        = data.value,
            observed_at  = data.observed_at,
            display_name = data.display_name,
            unit         = data.unit,
            source       = data.source,
            note         = data.note,
            normal_min   = data.normal_min,
            normal_max   = data.normal_max,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
