# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreCron is a pure trigger service. It holds named schedules, each pointing at a target (the KoreTest2
# system tests, the KoreUnitTest suite, or a named KoreAgentNetwork network), and fires that target's
# own API when the schedule is due. It performs none of the work itself.
# ====================================================================================================

from __future__ import annotations

import json
import re
import sys
import threading
import urllib.parse
import urllib.request
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from KoreCommon.service_app import register_suite_shell_routes
from KoreCommon.skill_registration import start_manifest_registration
from KoreCommon.skill_service import register_skill_invocation_routes
from KoreCommon.suite_paths import get_suite_datacontrol_dir


CONFIG           = ROOT / "config" / "korestack_config.json"
STORE_DIR        = get_suite_datacontrol_dir() / "korecron"
STORE_FILE       = STORE_DIR / "triggers.json"
STATE_FILE       = STORE_DIR / "scheduler_state.json"
RUN_HISTORY_FILE = STORE_DIR / "scheduler_run_history.json"
UI_ROOT          = ROOT / "KoreUI" / "KoreCron"
UI_ASSETS        = ROOT / "KoreUI" / "UIElements" / "assets"
STOP             = threading.Event()
NAME_RE          = re.compile(r"^(?=.{1,120}$)[A-Za-z0-9][A-Za-z0-9 _-]*$")
RUN_LOCK         = threading.Lock()
RUN_STATUSES: dict[str, dict] = {}
TARGET_LABELS    = {"system_test": "System tests", "unit_test": "Unit tests", "network": "Network"}


def _config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _service_url(name: str) -> str:
    cfg = _config()
    return f"http://{cfg.get('network', {}).get('host', '127.0.0.1')}:{cfg['services'][name]['port']}"


def _read(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _triggers() -> list[dict]:
    data = _read(STORE_FILE, {"triggers": []})
    return data.get("triggers", []) if isinstance(data, dict) else []


def _save(triggers: list[dict]) -> None:
    _write(STORE_FILE, {"triggers": triggers})


def _schedule_text(schedule: dict) -> str:
    if schedule.get("type") == "daily":
        return f"daily @ {schedule.get('time', '00:00')}"
    return f"every {schedule.get('minutes', 60)} min"


def _parse_schedule(value: str) -> dict:
    value = value.strip()
    if re.fullmatch(r"\d{1,2}:\d{2}", value):
        hour, minute = (int(part) for part in value.split(":"))
        if hour > 23 or minute > 59:
            raise ValueError("Daily time must be HH:MM.")
        return {"type": "daily", "time": f"{hour:02d}:{minute:02d}"}
    minutes = int(value)
    if minutes < 1:
        raise ValueError("Interval must be at least one minute.")
    return {"type": "interval", "minutes": minutes}


def _http(method: str, url: str, body: dict | None = None, timeout: int = 20):
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=payload, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8").strip()
        return json.loads(raw) if raw else None


def _networks() -> list[dict]:
    try:
        return _http("GET", f"{_service_url('koreagentnetwork')}/api/networks").get("networks", [])
    except Exception:
        return []


# ----------------------------------------------------------------------------------------------------
# MARK: RUNNING
# ----------------------------------------------------------------------------------------------------

def _start_status(trigger: dict) -> bool:
    now = datetime.now().isoformat(timespec="seconds")
    with RUN_LOCK:
        if RUN_STATUSES.get(trigger["id"], {}).get("status") == "running":
            return False
        RUN_STATUSES[trigger["id"]] = {"status": "running", "started_at": now, "detail": "Triggered."}
    return True


def _finish_status(trigger: dict, *, error: str = "", detail: str = "") -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with RUN_LOCK:
        RUN_STATUSES[trigger["id"]].update({
            "status":      "failed" if error else "succeeded",
            "finished_at": now,
            "detail":      error or detail or "Completed.",
        })


def _record_run(trigger: dict, *, attempted_at: datetime, error: str = "") -> None:
    history = _read(RUN_HISTORY_FILE, {})
    if not isinstance(history, dict):
        history = {}
    entries = history.get(trigger["id"], [])
    entries.append({"attempted_at": attempted_at.isoformat(timespec="seconds"), "succeeded": not error, "error": error})
    history[trigger["id"]] = entries[-50:]
    _write(RUN_HISTORY_FILE, history)


def _fire(trigger: dict) -> str:
    """Call the target's own API; returns a short detail string."""
    target = trigger.get("target")
    if target in ("system_test", "unit_test"):
        service = "koretest2" if target == "system_test" else "koreunittest"
        started = _http("POST", f"{_service_url(service)}/api/sessions")
        if not (started or {}).get("started"):
            raise RuntimeError(str((started or {}).get("detail") or "A session is already running."))
        return "Session started."
    if target == "network":
        network_id = urllib.parse.quote(str(trigger.get("network_id") or ""), safe="")
        run = _http("POST", f"{_service_url('koreagentnetwork')}/api/networks/{network_id}/run", timeout=3600)
        nodes = ((run or {}).get("run") or {}).get("nodes", {})
        failed = [node_id for node_id, result in nodes.items() if result.get("status") != "completed"]
        if failed:
            raise RuntimeError(f"{len(failed)} block(s) failed: {', '.join(failed)}")
        return f"Network ran {len(nodes)} block(s)."
    raise RuntimeError(f"Unknown target '{target}'.")


def _run(trigger: dict) -> None:
    attempted_at = datetime.now()
    try:
        detail = _fire(trigger)
        _finish_status(trigger, detail=detail)
        _record_run(trigger, attempted_at=attempted_at)
    except Exception as error:
        _finish_status(trigger, error=str(error))
        _record_run(trigger, attempted_at=attempted_at, error=str(error))
        print(f"[KoreCron] {trigger.get('name')} failed: {error}", flush=True)


def _dispatch(trigger: dict) -> bool:
    if not _start_status(trigger):
        return False
    threading.Thread(target=_run, args=(trigger,), daemon=True, name=f"korecron-{trigger['id']}").start()
    return True


# ----------------------------------------------------------------------------------------------------
# MARK: SCHEDULING
# ----------------------------------------------------------------------------------------------------

def _due(trigger: dict, last_run: str | None, now: datetime) -> bool:
    schedule = trigger.get("schedule", {})
    if schedule.get("type") == "daily":
        return now.strftime("%H:%M") == str(schedule.get("time")) and (not last_run or not last_run.startswith(now.date().isoformat()))
    if not last_run:
        return True
    try:
        return (now - datetime.fromisoformat(last_run)).total_seconds() >= int(schedule.get("minutes", 60)) * 60
    except ValueError:
        return True


def _next_fire(trigger: dict, last_run: str | None, now: datetime) -> str:
    schedule = trigger.get("schedule", {})
    if schedule.get("type") == "daily":
        hour, minute = (int(part) for part in str(schedule.get("time", "00:00")).split(":"))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target.isoformat(timespec="seconds")
    try:
        base = datetime.fromisoformat(last_run) if last_run else now
    except ValueError:
        base = now
    return (base + timedelta(minutes=int(schedule.get("minutes", 60)))).isoformat(timespec="seconds")


def _scheduler() -> None:
    while not STOP.is_set():
        state = _read(STATE_FILE, {})
        now   = datetime.now()
        for trigger in _triggers():
            if trigger.get("enabled", True) and _due(trigger, state.get(trigger["id"]), now):
                state[trigger["id"]] = now.isoformat(timespec="seconds")
                _write(STATE_FILE, state)
                _dispatch(trigger)
        STOP.wait(20)


@asynccontextmanager
async def lifespan(_app):
    STOP.clear()
    threading.Thread(target=_scheduler, daemon=True, name="korecron-scheduler").start()
    start_manifest_registration(
        ROOT / "KoreCron" / "skill_registration.json",
        service_base_url=_service_url("korecron"),
        logger_name=__name__,
    )
    yield
    STOP.set()


app = FastAPI(title="KoreCron", lifespan=lifespan)
register_suite_shell_routes(app, service_key="korecron", service_label="KoreCron", ui_elements_assets_dir=UI_ASSETS)
app.mount("/static", StaticFiles(directory=str(UI_ROOT / "static")), name="korecron-static")


@app.get("/status")
def status(): return {"ok": True, "service": "KoreCron"}


@app.get("/api/targets")
def list_targets():
    return {
        "targets":  [{"key": key, "label": label} for key, label in TARGET_LABELS.items() if key != "network"],
        "networks": [{"id": n["id"], "title": n["title"]} for n in _networks()],
    }


@app.get("/api/triggers")
def list_triggers():
    state   = _read(STATE_FILE, {})
    history = _read(RUN_HISTORY_FILE, {})
    now     = datetime.now()
    titles  = {n["id"]: n["title"] for n in _networks()}
    return {"triggers": [{
        **item,
        "schedule_text": _schedule_text(item.get("schedule", {})),
        "target_text":   f"Network: {titles.get(item.get('network_id'), item.get('network_id'))}" if item.get("target") == "network" else TARGET_LABELS.get(item.get("target"), item.get("target")),
        "last_run":      state.get(item["id"]),
        "next_fire":     _next_fire(item, state.get(item["id"]), now) if item.get("enabled", True) else None,
        "last_outcome":  (history.get(item["id"]) or [None])[-1],
        "run_status":    dict(RUN_STATUSES.get(item["id"], {"status": "idle"})),
    } for item in _triggers()]}


def _definition(payload: dict, trigger_id: str | None = None) -> dict:
    name = str(payload.get("name", "")).strip()
    if not NAME_RE.fullmatch(name):
        raise HTTPException(400, "Name must begin with a letter or digit and use up to 120 letters, digits, spaces, hyphens, or underscores.")
    try:
        schedule = _parse_schedule(str(payload.get("schedule", "")))
    except (ValueError, TypeError):
        raise HTTPException(400, "Schedule must be minutes or HH:MM.")
    target = str(payload.get("target", ""))
    if target not in TARGET_LABELS:
        raise HTTPException(400, "Target must be system_test, unit_test or network.")
    network_id = str(payload.get("network_id") or "")
    if target == "network" and not network_id:
        raise HTTPException(400, "Choose a network.")
    return {
        "id":         trigger_id or f"trigger_{uuid.uuid4().hex[:10]}",
        "name":       name,
        "enabled":    bool(payload.get("enabled", True)),
        "target":     target,
        "network_id": network_id if target == "network" else "",
        "schedule":   schedule,
    }


def _find(triggers: list[dict], key: str) -> int | None:
    return next((i for i, t in enumerate(triggers) if t["id"] == key or t.get("name", "").casefold() == key.casefold()), None)


@app.post("/api/triggers")
def create_trigger(payload: dict):
    definition = _definition(payload)
    triggers   = _triggers()
    if any(t.get("name", "").casefold() == definition["name"].casefold() for t in triggers):
        raise HTTPException(409, "A trigger with this name already exists.")
    triggers.append(definition)
    _save(triggers)
    return definition


@app.put("/api/triggers/{trigger_id}")
def update_trigger(trigger_id: str, payload: dict):
    triggers = _triggers()
    index    = _find(triggers, trigger_id)
    if index is None:
        raise HTTPException(404, "Trigger not found.")
    definition = _definition({**triggers[index], "schedule": _schedule_text_to_input(triggers[index]), **payload}, triggers[index]["id"])
    if any(i != index and t.get("name", "").casefold() == definition["name"].casefold() for i, t in enumerate(triggers)):
        raise HTTPException(409, "A trigger with this name already exists.")
    triggers[index] = definition
    _save(triggers)
    return definition


def _schedule_text_to_input(trigger: dict) -> str:
    schedule = trigger.get("schedule", {})
    return str(schedule.get("time")) if schedule.get("type") == "daily" else str(schedule.get("minutes", 60))


@app.delete("/api/triggers/{trigger_id}", status_code=204)
def delete_trigger(trigger_id: str):
    triggers = _triggers()
    index    = _find(triggers, trigger_id)
    if index is None:
        raise HTTPException(404, "Trigger not found.")
    removed = triggers.pop(index)
    _save(triggers)
    state = _read(STATE_FILE, {})
    state.pop(removed["id"], None)
    _write(STATE_FILE, state)
    return None


@app.post("/api/triggers/{trigger_id}/run")
def run_trigger(trigger_id: str):
    triggers = _triggers()
    index    = _find(triggers, trigger_id)
    if index is None:
        raise HTTPException(404, "Trigger not found.")
    if not _dispatch(triggers[index]):
        raise HTTPException(409, "Trigger is already running.")
    return {"queued": True, "name": triggers[index]["name"]}


@app.get("/ui", include_in_schema=False)
def ui() -> FileResponse: return FileResponse(UI_ROOT / "static" / "cron" / "index.html")

def cron_list() -> dict: return list_triggers()

def cron_run(name: str) -> dict: return run_trigger(name)

register_skill_invocation_routes(app, {"cron_list": cron_list, "cron_run": cron_run})

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(_config()["services"]["korecron"]["port"]))