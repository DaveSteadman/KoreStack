# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# main module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Function inventory:
# - _config: Implements the  config operation for this module.
# - _service_url: Implements the  service url operation for this module.
# - _read: Implements the  read operation for this module.
# - _write: Implements the  write operation for this module.
# - _definitions: Implements the  definitions operation for this module.
# - _save: Implements the  save operation for this module.
# - _schedule_text: Implements the  schedule text operation for this module.
# - _parse_schedule: Implements the  parse schedule operation for this module.
# - _http: Implements the  http operation for this module.
# - _cron_session_key: Implements the  cron session key operation for this module.
# - _cron_external_id: Implements the  cron external id operation for this module.
# - _conversation: Implements the  conversation operation for this module.
# - _fresh_conversation: Deletes prior chats with the configured name and creates a new one.
# - _reply_error: Returns a central-agent error reported by an outbound message.
# - _await_outbound_reply: Implements the  await outbound reply operation for this module.
# - _run: Implements the  run operation for this module.
# - _due: Implements the  due operation for this module.
# - _next_fire: Implements the  next fire operation for this module.
# - _scheduler: Implements the  scheduler operation for this module.
# - lifespan: Implements the lifespan operation for this module.
# - status: Implements the status operation for this module.
# - list_cronprompts: Lists cronprompts for this module.
# - timeline: Implements the timeline operation for this module.
# - _cronprompt_definition: Implements the  cronprompt definition operation for this module.
# - create_cronprompt: Creates cronprompt for this module.
# - update_cronprompt: Updates cronprompt for this module.
# - delete_cronprompt: Deletes cronprompt for this module.
# - run_cronprompt: Runs cronprompt for this module.
# - resume_cronprompt_agent: Implements the resume cronprompt agent operation for this module.
# - ui: Implements the ui operation for this module.
# - cron_list: Implements the cron list operation for this module.
# - cron_run: Implements the cron run operation for this module.
# ====================================================================================================

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
NAME_RE      = re.compile(r"^(?=.{1,120}$)[A-Za-z0-9][A-Za-z0-9 _-]*$")
SESSION_KEY_RE = re.compile(r"[^A-Za-z0-9_-]+")


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
        raw = response.read().decode("utf-8").strip()
        return json.loads(raw) if raw else None


def _cron_session_key(chat_name: str) -> str:
    normalized = SESSION_KEY_RE.sub("_", str(chat_name or "").strip())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return f"cron_{normalized or 'chat'}"


def _cron_external_id(chat_name: str) -> str:
    return f"webchat_{_cron_session_key(chat_name)}"


def _conversation(definition: dict) -> dict:
    chat_name   = str(definition["chat_name"])
    external_id = _cron_external_id(chat_name)
    base        = _service_url("korechat")
    try:
        return _http("GET", f"{base}/api/conversations/by-external-id/{urllib.parse.quote(external_id, safe='')}")
    except Exception:
        return _http("POST", f"{base}/api/conversations", {"channel_type": "webchat", "subject": chat_name, "external_id": external_id})


def _fresh_conversation(definition: dict) -> dict:
    """Replace every existing conversation with this CronPrompt's chat name."""
    chat_name   = str(definition["chat_name"]).strip()
    external_id = _cron_external_id(chat_name)
    base        = _service_url("korechat")

    conversations: list[dict] = []
    offset = 0
    while True:
        page = _http("GET", f"{base}/api/conversations?limit=500&offset={offset}")
        if not isinstance(page, list):
            raise RuntimeError("KoreChat returned an invalid conversation list")
        conversations.extend(item for item in page if isinstance(item, dict))
        if len(page) < 500:
            break
        offset += len(page)

    matching_ids = {
        int(item["id"])
        for item in conversations
        if item.get("id") is not None
        and (
            str(item.get("subject") or "").strip().casefold() == chat_name.casefold()
            or str(item.get("external_id") or "") == external_id
        )
    }
    for conversation_id in sorted(matching_ids):
        _http("DELETE", f"{base}/api/conversations/{conversation_id}")

    created = _http("POST", f"{base}/api/conversations", {
        "channel_type": "webchat",
        "subject":      chat_name,
        "external_id":  external_id,
    })
    if not isinstance(created, dict) or not created.get("id"):
        raise RuntimeError("KoreChat did not return the fresh conversation")
    return created


def _reply_error(message: dict) -> str | None:
    """Return a canonical agent execution error without reinterpreting model output."""
    tags = {str(tag).strip().casefold() for tag in message.get("tags") or []}
    if "agent_error" in tags:
        return "agent could not produce a valid response"
    return None


def _await_outbound_reply(base: str, conversation_id: int, prior_count: int, timeout_seconds: int = 1800) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline and not STOP.is_set():
        messages = _http("GET", f"{base}/api/conversations/{conversation_id}/messages?limit=1000")
        if isinstance(messages, list) and len(messages) > prior_count:
            latest = messages[-1]
            if latest.get("direction") == "outbound":
                return latest
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for outbound reply in conversation {conversation_id}")


def _run(definition: dict) -> None:
    conversation = _fresh_conversation(definition)
    base         = _service_url("korechat")
    conversation_id = int(conversation["id"])
    for prompt in definition.get("prompts", []):
        prompt_text = str(prompt.get("prompt", "")) if isinstance(prompt, dict) else str(prompt)
        if not prompt_text.strip():
            continue
        before = _http("GET", f"{base}/api/conversations/{conversation_id}/messages?limit=1000")
        prior_count = len(before) if isinstance(before, list) else 0
        _http("POST", f"{base}/api/conversations/{conversation_id}/messages", {"direction": "inbound", "content": prompt_text, "sender_display": "KoreCron", "status": "received"})
        reply = _await_outbound_reply(base, conversation_id, prior_count)
        reply_error = _reply_error(reply)
        if reply_error:
            raise RuntimeError(
                f"CronPrompt '{definition.get('name', '')}' aborted after prompt {prompt_text[:80]!r}: {reply_error}"
            )


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


def _cronprompt_definition(payload: dict) -> dict:
    name = str(payload.get("name", "")).strip()
    if not NAME_RE.fullmatch(name):
        raise HTTPException(400, "Name must begin with a letter or digit and use up to 120 letters, digits, spaces, hyphens, or underscores.")
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
    return {
        "name":               name,
        "chat_name":          chat_name,
        "enabled":            bool(payload.get("enabled", True)),
        "schedule":           schedule,
        "prompts":            prompts,
    }


@app.post("/api/cronprompts")
def create_cronprompt(payload: dict):
    definition  = _cronprompt_definition(payload)
    definitions = _definitions()
    if any(str(item.get("name", "")).casefold() == definition["name"].casefold() for item in definitions):
        raise HTTPException(409, "CronPrompt already exists.")
    definitions.append(definition); _save(definitions)
    return definition


@app.put("/api/cronprompts/{name}")
def update_cronprompt(name: str, payload: dict):
    definitions = _definitions()
    index = next((
        i for i, item in enumerate(definitions)
        if str(item.get("name", "")).casefold() == name.casefold()
    ), None)
    if index is None:
        raise HTTPException(404, "CronPrompt not found.")
    definition = _cronprompt_definition(payload)
    if any(
        i != index and str(item.get("name", "")).casefold() == definition["name"].casefold()
        for i, item in enumerate(definitions)
    ):
        raise HTTPException(409, "CronPrompt already exists.")
    old_name = str(definitions[index]["name"])
    definitions[index] = definition
    _save(definitions)
    if old_name != definition["name"]:
        state = _read(STATE_FILE, {})
        if old_name in state:
            state[definition["name"]] = state.pop(old_name)
            _write(STATE_FILE, state)
    return definition


@app.delete("/api/cronprompts/{name}", status_code=204)
def delete_cronprompt(name: str):
    definitions = _definitions()
    definition = next((
        item for item in definitions
        if str(item.get("name", "")).casefold() == name.casefold()
    ), None)
    if definition is None:
        raise HTTPException(404, "CronPrompt not found.")
    remaining = [
        item for item in definitions
        if str(item.get("name", "")).casefold() != name.casefold()
    ]
    _save(remaining)
    state = _read(STATE_FILE, {})
    state.pop(str(definition["name"]), None)
    _write(STATE_FILE, state)
    return None


@app.post("/api/cronprompts/{name}/run")
def run_cronprompt(name: str):
    definition = next((item for item in _definitions() if item.get("name", "").lower() == name.lower()), None)
    if not definition: raise HTTPException(404, "CronPrompt not found.")
    threading.Thread(target=_run, args=(definition,), daemon=True).start()
    return {"queued": True, "name": definition["name"], "chat_name": definition["chat_name"]}


@app.post("/api/cronprompts/{name}/agent-resume")
def resume_cronprompt_agent(name: str):
    definition = next((item for item in _definitions() if item.get("name", "").lower() == name.lower()), None)
    if not definition:
        raise HTTPException(404, "CronPrompt not found.")

    chat_name = str(definition.get("chat_name") or "").strip()
    if not chat_name:
        raise HTTPException(400, "CronPrompt has no chat name.")

    agent_base = _service_url("koreagent")
    try:
        conversation = _conversation(definition)
    except Exception as exc:
        raise HTTPException(502, "Unable to create or load the KoreChat conversation.") from exc

    try:
        _http("POST", f"{agent_base}/sessions/request-switch", {
            "name": chat_name,
            "conversation_id": int(conversation.get("id") or 0),
        })
    except Exception as exc:
        raise HTTPException(502, "KoreAgent refused the resume request.") from exc

    external_id = str(conversation.get("external_id") or "")
    if external_id.startswith("webchat_"):
        session_id = external_id[len("webchat_"):]
    else:
        session_id = f"kc_conv_{int(conversation.get('id') or 0)}"
    resume_name = str(conversation.get("subject") or chat_name).strip() or chat_name
    redirect_url = f"{agent_base}/?session_id={urllib.parse.quote(session_id, safe='')}&name={urllib.parse.quote(resume_name, safe='')}"
    return {"ok": True, "agent_url": agent_base, "session_id": session_id, "name": resume_name, "redirect_url": redirect_url}


@app.get("/ui", include_in_schema=False)
def ui() -> FileResponse: return FileResponse(UI_ROOT / "static" / "cron" / "index.html")

@_mcp.tool()
def cron_list() -> dict: return list_cronprompts()

@_mcp.tool()
def cron_run(name: str) -> dict: return run_cronprompt(name)

app.mount("/mcp", _mcp.streamable_http_app())

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(_config()["services"]["korecron"]["port"]))
