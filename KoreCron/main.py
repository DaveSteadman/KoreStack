from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.fastmcp import FastMCP
import uvicorn


ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))
from KoreCommon.service_app import register_suite_shell_routes
from KoreCommon.suite_paths import get_suite_datacontrol_dir


CONFIG       = ROOT / "config" / "korestack_config.json"
STORE_DIR    = get_suite_datacontrol_dir() / "cronprompts"
STORE_FILE   = STORE_DIR / "cronprompts.json"
STATE_FILE   = STORE_DIR / "scheduler_state.json"
UI_ROOT      = ROOT / "KoreUI" / "KoreCron"
UI_ASSETS    = ROOT / "KoreUI" / "UIElements" / "assets"
STOP         = threading.Event()
NAME_RE      = re.compile(r"^[A-Za-z0-9_-]+$")


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


def _definitions() -> list[dict]:
    data = _read(STORE_FILE, {"cronprompts": []})
    return data.get("cronprompts", []) if isinstance(data, dict) else []


def _save(definitions: list[dict]) -> None:
    _write(STORE_FILE, {"cronprompts": definitions})


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
        return {"type": "daily", "time": value}
    minutes = int(value)
    if minutes < 1:
        raise ValueError("Interval must be at least one minute.")
    return {"type": "interval", "minutes": minutes}


def _http(method: str, url: str, body: dict | None = None):
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=payload, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _conversation(definition: dict) -> dict:
    chat_name   = str(definition["chat_name"])
    external_id = f"webchat_cron_{chat_name}"
    base        = _service_url("korechat")
    try:
        return _http("GET", f"{base}/api/conversations/by-external-id/{urllib.parse.quote(external_id, safe='')}")
    except Exception:
        return _http("POST", f"{base}/api/conversations", {"channel_type": "webchat", "subject": chat_name, "external_id": external_id})


def _run(definition: dict) -> None:
    conversation = _conversation(definition)
    base         = _service_url("korechat")
    conversation_id = int(conversation["id"])
    for prompt in definition.get("prompts", []):
        prompt_text = str(prompt.get("prompt", "")) if isinstance(prompt, dict) else str(prompt)
        if not prompt_text.strip():
            continue
        before = _http("GET", f"{base}/api/conversations/{conversation_id}/messages?limit=1000")
        prior_count = len(before) if isinstance(before, list) else 0
        _http("POST", f"{base}/api/conversations/{conversation_id}/messages", {"direction": "inbound", "content": prompt_text, "sender_display": "KoreCron", "status": "received"})
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline and not STOP.is_set():
            messages = _http("GET", f"{base}/api/conversations/{conversation_id}/messages?limit=1000")
            if isinstance(messages, list) and len(messages) > prior_count and messages[-1].get("direction") == "outbound":
                break
            time.sleep(1)


def _due(definition: dict, last_run: str | None, now: datetime) -> bool:
    schedule = definition.get("schedule", {})
    if schedule.get("type") == "daily":
        return now.strftime("%H:%M") == str(schedule.get("time")) and (not last_run or not last_run.startswith(now.date().isoformat()))
    if not last_run:
        return True
    try:
        return (now - datetime.fromisoformat(last_run)).total_seconds() >= int(schedule.get("minutes", 60)) * 60
    except ValueError:
        return True


def _next_fire(definition: dict, last_run: str | None, now: datetime) -> str:
    schedule = definition.get("schedule", {})
    if schedule.get("type") == "daily":
        hour, minute = (int(part) for part in str(schedule.get("time", "00:00")).split(":"))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now: target += timedelta(days=1)
        return target.isoformat(timespec="seconds")
    try:
        base = datetime.fromisoformat(last_run) if last_run else now
    except ValueError:
        base = now
    return (base + timedelta(minutes=int(schedule.get("minutes", 60)))).isoformat(timespec="seconds")


def _scheduler() -> None:
    while not STOP.is_set():
        state = _read(STATE_FILE, {})
        now = datetime.now()
        for definition in _definitions():
            name = str(definition.get("name", ""))
            if definition.get("enabled", True) and _due(definition, state.get(name), now):
                state[name] = now.isoformat(timespec="seconds")
                _write(STATE_FILE, state)
                try:
                    _run(definition)
                except Exception:
                    pass
        STOP.wait(20)


@asynccontextmanager
async def lifespan(_app):
    STOP.clear()
    threading.Thread(target=_scheduler, daemon=True, name="korecron-scheduler").start()
    yield
    STOP.set()


app = FastAPI(title="KoreCron", lifespan=lifespan)
register_suite_shell_routes(app, service_key="korecron", service_label="KoreCron", ui_elements_assets_dir=UI_ASSETS)
app.mount("/static", StaticFiles(directory=str(UI_ROOT / "static")), name="korecron-static")
_mcp = FastMCP("KoreCron")


@app.get("/status")
def status(): return {"ok": True, "service": "KoreCron"}


@app.get("/api/cronprompts")
def list_cronprompts():
    state = _read(STATE_FILE, {})
    return {"cronprompts": [{**item, "schedule_text": _schedule_text(item.get("schedule", {})), "last_run": state.get(item.get("name"))} for item in _definitions()]}


@app.get("/api/timeline")
def timeline():
    now = datetime.now()
    state = _read(STATE_FILE, {})
    items = [
        {"name": item.get("name"), "chat_name": item.get("chat_name"), "next_fire": _next_fire(item, state.get(item.get("name")), now), "schedule_text": _schedule_text(item.get("schedule", {}))}
        for item in _definitions() if item.get("enabled", True)
    ]
    return {"items": sorted(items, key=lambda item: item["next_fire"]), "now": now.isoformat(timespec="seconds")}


@app.post("/api/cronprompts")
def create_cronprompt(payload: dict):
    name = str(payload.get("name", "")).strip()
    if not NAME_RE.fullmatch(name): raise HTTPException(400, "Name must use letters, digits, hyphens, or underscores.")
    definitions = _definitions()
    if any(str(item.get("name", "")).lower() == name.lower() for item in definitions): raise HTTPException(409, "CronPrompt already exists.")
    try: schedule = _parse_schedule(str(payload.get("schedule", "")))
    except (ValueError, TypeError): raise HTTPException(400, "Schedule must be minutes or HH:MM.")
    chat_name = str(payload.get("chat_name") or name).strip()
    if not chat_name:
        raise HTTPException(400, "Chat name is required.")
    prompt_items = payload.get("prompts", [])
    if not isinstance(prompt_items, list):
        raise HTTPException(400, "Prompts must be an ordered list.")
    prompts = []
    for item in prompt_items:
        text = str(item.get("prompt", "")) if isinstance(item, dict) else str(item)
        if text.strip():
            prompts.append({"prompt": text.strip()})
    if not prompts:
        raise HTTPException(400, "At least one non-empty prompt is required.")
    definition = {"name": name, "chat_name": chat_name, "enabled": True, "schedule": schedule, "prompts": prompts}
    definitions.append(definition); _save(definitions)
    return definition


@app.post("/api/cronprompts/{name}/run")
def run_cronprompt(name: str):
    definition = next((item for item in _definitions() if item.get("name", "").lower() == name.lower()), None)
    if not definition: raise HTTPException(404, "CronPrompt not found.")
    threading.Thread(target=_run, args=(definition,), daemon=True).start()
    return {"queued": True, "name": definition["name"], "chat_name": definition["chat_name"]}


@app.get("/ui", include_in_schema=False)
def ui() -> FileResponse: return FileResponse(UI_ROOT / "static" / "cron" / "index.html")

@_mcp.tool()
def cron_list() -> dict: return list_cronprompts()

@_mcp.tool()
def cron_run(name: str) -> dict: return run_cronprompt(name)

app.mount("/mcp", _mcp.streamable_http_app())

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(_config()["services"]["korecron"]["port"]))
