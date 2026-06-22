from contextlib import asynccontextmanager
from contextlib import redirect_stdout
import datetime
from io import StringIO
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.database import add_driver, delete_driver, get_driver, get_status, init_db, list_drivers, update_driver


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title       = "KoreDeviceDriver",
    description = "Boilerplate driver registry and configuration service",
    lifespan    = _lifespan,
)


_DRIVER_RUNTIME_GLOBALS = {
    "__builtins__": __builtins__,
    "datetime":     datetime,
    "json":         json,
    "math":         math,
    "os":           os,
    "Path":         Path,
    "re":           re,
    "shutil":       shutil,
    "statistics":   statistics,
    "subprocess":   subprocess,
}


class DriverCreate(BaseModel):
    name:              str
    display_name:      str | None = None
    vendor:            str | None = None
    protocol:          str | None = None
    transport_address: str | None = None
    poll_interval_sec: int | None = None
    enabled:           bool       = False
    description:       str | None = None
    python_snippet:    str | None = None


class DriverUpdate(BaseModel):
    display_name:      str | None = None
    vendor:            str | None = None
    protocol:          str | None = None
    transport_address: str | None = None
    poll_interval_sec: int | None = None
    enabled:           bool       = False
    description:       str | None = None
    python_snippet:    str | None = None


class DriverRun(BaseModel):
    display_name:      str | None = None
    vendor:            str | None = None
    protocol:          str | None = None
    transport_address: str | None = None
    poll_interval_sec: int | None = None
    enabled:           bool       = False
    description:       str | None = None
    python_snippet:    str | None = None


def _execute_driver(name: str, data: DriverRun) -> dict:
    driver = get_driver(name)
    if driver is None:
        raise ValueError(f"Driver '{name}' not found")

    runtime_driver = {
        **driver,
        "display_name":      data.display_name      if data.display_name      is not None else driver.get("display_name"),
        "vendor":            data.vendor            if data.vendor            is not None else driver.get("vendor"),
        "protocol":          data.protocol          if data.protocol          is not None else driver.get("protocol"),
        "transport_address": data.transport_address if data.transport_address is not None else driver.get("transport_address"),
        "poll_interval_sec": data.poll_interval_sec if data.poll_interval_sec is not None else driver.get("poll_interval_sec"),
        "enabled":           data.enabled,
        "description":       data.description       if data.description       is not None else driver.get("description"),
        "python_snippet":    data.python_snippet    if data.python_snippet    is not None else driver.get("python_snippet"),
    }
    snippet = str(runtime_driver.get("python_snippet") or "").strip()
    if not snippet:
        raise ValueError(f"Driver '{name}' has no python snippet")

    stdout_buffer = StringIO()
    namespace     = dict(_DRIVER_RUNTIME_GLOBALS)
    context       = {
        "driver": runtime_driver,
        "config": {
            "default_protocol":      get_status().get("default_protocol"),
            "default_vendor":        get_status().get("default_vendor"),
            "default_poll_interval": get_status().get("default_poll_interval"),
        },
    }

    try:
        with redirect_stdout(stdout_buffer):
            exec(snippet, namespace, namespace)
            read_driver = namespace.get("read_driver")
            if callable(read_driver):
                result = read_driver(context)
            else:
                result = namespace.get("result")
    except Exception as exc:
        return {
            "ok":      False,
            "driver":  runtime_driver["name"],
            "stdout":  stdout_buffer.getvalue(),
            "error":   f"{exc.__class__.__name__}: {exc}",
            "result":  None,
        }

    return {
        "ok":      True,
        "driver":  runtime_driver["name"],
        "stdout":  stdout_buffer.getvalue(),
        "error":   None,
        "result":  result,
    }


@app.get("/status")
def route_status():
    return get_status()


@app.get("/drivers")
def route_drivers():
    return list_drivers()


@app.get("/drivers/{name:path}")
def route_driver(name: str):
    driver = get_driver(name)
    if driver is None:
        raise HTTPException(status_code=404, detail="Driver not found")
    return driver


@app.post("/drivers", status_code=201)
def route_add_driver(data: DriverCreate):
    try:
        return add_driver(
            name              = data.name,
            display_name      = data.display_name,
            vendor            = data.vendor,
            protocol          = data.protocol,
            transport_address = data.transport_address,
            poll_interval_sec = data.poll_interval_sec,
            enabled           = data.enabled,
            description       = data.description,
            python_snippet    = data.python_snippet,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/drivers/{name:path}")
def route_update_driver(name: str, data: DriverUpdate):
    try:
        return update_driver(
            name              = name,
            display_name      = data.display_name,
            vendor            = data.vendor,
            protocol          = data.protocol,
            transport_address = data.transport_address,
            poll_interval_sec = data.poll_interval_sec,
            enabled           = data.enabled,
            description       = data.description,
            python_snippet    = data.python_snippet,
        )
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/drivers/{name:path}/run")
def route_run_driver(name: str, data: DriverRun):
    try:
        return _execute_driver(name, data)
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/drivers/{name:path}")
def route_delete_driver(name: str):
    try:
        return delete_driver(name)
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
