# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FastAPI application exposing the KoreAgent engine as a REST + SSE service.
#
# Endpoints:
#   GET  /                             serve static web UI (index.html)
#   GET  /version                      return framework version string
#   GET  /status/ollama                current 'ollama ps' model status
#   GET  /settings/sandbox             return current sandbox enabled state
#   POST /settings/sandbox?enabled=    set sandbox enabled state
#   GET  /settings/webskills           return current web skills enabled state
#   POST /settings/webskills?enabled=  set web skills enabled state
#   GET  /completions                  tab-completion candidates (sessions, test files, tasks, models)
#   GET  /tasks                        enabled scheduled tasks with last-run and next-fire times
#   GET  /timeline                     minute-resolution task timeline centred on now
#   GET  /queue                        current task queue state
#   GET  /sessions/{id}/input-history  last 20 input history entries for the session
#   POST /sessions/{id}/input-history  append an entry to session input history
#   GET  /sessions/{id}/history        full conversation history for a session
#   POST /sessions/{id}/prompt         submit a new prompt (enqueues on task_queue)
#   GET  /logs                         list all log directories and files
#   GET  /logs/latest                  path of the most recently written log file
#   GET  /logs/stream                  SSE: tail all new log lines across all log files
#   GET  /logs/file?path=<path>        SSE: tail a specific log file (used for per-run view)
#   GET  /logs/{date}/{filename}       serve a specific log file
#   GET  /runs/{id}/stream             SSE: stream events for a specific enqueued run
#
# SSE events are plain text/event-stream with a "data: <json>\n\n" envelope.
# CORS is restricted to localhost origins only - requests from external sites are blocked.
#
# Instantiated once in modes/api_mode.py, then served by uvicorn.
#
# Related modules:
#   - modes/api_mode.py       -- constructs and starts this app
#   - scheduler.py            -- task_queue singleton, load_schedules_dir, is_task_due
#   - orchestration.py        -- orchestrate_prompt, OrchestratorConfig, ConversationHistory
#   - runtime_logger.py       -- SessionLogger, create_log_file_path
#   - llm_client.py        -- get_ollama_ps_rows, get_active_host
#   - slash_commands.py       -- SlashCommandContext, handle; /session commands manage named sessions
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
# sys.path must be configured before any project imports.
import sys
import os
from pathlib import Path

_code_dir = str(Path(__file__).resolve().parent.parent)
if _code_dir not in sys.path:
    sys.path.insert(0, _code_dir)

import json
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from datetime import timedelta

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import RedirectResponse
from fastapi.responses import Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from llm_client import get_active_backend
from llm_client import get_active_host
from llm_client import get_active_model
from llm_client import get_active_num_ctx
from llm_client import get_ollama_ps_rows
from llm_client import list_ollama_models
from llm_client import call_llm_chat
from orchestration import ConversationHistory
from orchestration import OrchestratorConfig
from orchestration import SessionContext
from orchestration import get_sandbox_enabled
from orchestration import get_web_skills_enabled
from orchestration import orchestrate_prompt
from orchestration import request_stop
from orchestration import set_sandbox_enabled
from orchestration import set_web_skills_enabled
from scratchpad import get_store as get_scratch_store
from scratchpad import scratch_clear
from scratchpad import scratch_save as scratch_restore_key
from input_layer.api_routes_logs import register_log_routes
from input_layer.api_routes_sessions import register_session_routes
from input_layer.api_routes_status import register_status_routes
from input_layer.api_routes_tasks import register_task_routes
from utils.runtime_logger import SessionLogger
from utils.runtime_logger import create_log_file_path
from scheduler.scheduler import is_task_due
from scheduler.scheduler import task_queue
from input_layer.slash_commands import handle as handle_slash
from input_layer.slash_command_context import SlashCommandContext
from utils.workspace_utils import get_logs_dir
from utils.workspace_utils import get_test_prompts_dir
from utils.suite_version import SUITE_VERSION
import koreconv_client as _kc_client


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Run-event streaming patterns - compiled once and reused across all prompt runs.
_LOG_FILE_RE      = re.compile(r"^Log file:\s*(.+)$")
_TURN_AGENT_RE    = re.compile(r"^\[TURN\s+(\d+)\]\s+Agent:\s*(.*)$")
_TURN_METRICS_RE  = re.compile(r"^\[TURN\s+(\d+)\]\s+tokens=(\d+)\s+tps=([0-9.]+)$")
_TEST_COMPLETE_RE = re.compile(r"^\[(TEST COMPLETE|ALL TESTS COMPLETE)\]\s+(.+)$")

_LOG_DIR             = get_logs_dir()
_WEB_DIR             = Path(__file__).resolve().parent / "ui"
_UI_ELEMENTS_ASSETS  = Path(
    os.environ.get(
        "KORE_UIELEMENTS_ASSETS_DIR",
        str(Path(__file__).resolve().parents[4] / "UIElements" / "assets"),
    )
).resolve()
_COMPACT_FILL_PCT    = 0.65  # compact when prompt-token fill reaches this fraction of num_ctx
_QUEUE_PREVIEW_LIMIT = 10
_LOG_POLL_SECS       = 1.0      # how often the log-tail SSE generator checks for new lines
_LOG_TAIL_LINES      = 200      # how many historic lines to send on first connect


# ====================================================================================================
# MARK: GLOBAL STATE
# ====================================================================================================
# These are set once by api_mode.py before uvicorn starts.
_config:         OrchestratorConfig | None = None
_last_run:       dict[str, datetime | None] = {}
_enabled_tasks:  list[dict] = []
_shutdown_event: threading.Event = threading.Event()

# Per-run event queues: run_id -> queue.Queue[dict | None]
# None sentinel signals the stream is finished.
_run_event_queues: dict[str, queue.Queue] = {}
_run_queues_lock:  threading.Lock = threading.Lock()

_latest_log_path: str | None = None


# ====================================================================================================
# MARK: SETUP FUNCTIONS
# ====================================================================================================

def setup(
    config: OrchestratorConfig,
    enabled_tasks: list[dict],
    last_run: dict[str, datetime | None],
    shutdown_event: threading.Event,
) -> None:
    """Called once by api_mode.py before serving. Stores shared state."""
    global _config, _enabled_tasks, _last_run, _shutdown_event
    _config         = config
    _enabled_tasks  = enabled_tasks
    _last_run       = last_run
    _shutdown_event = shutdown_event


def _set_latest_log_path(path: str | Path | None) -> None:
    global _latest_log_path
    _latest_log_path = str(path) if path else None


def _make_run_event_queue(run_id: str) -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=2000)
    with _run_queues_lock:
        _run_event_queues[run_id] = q
    return q


def _queue_run_event(run_q: queue.Queue, event: dict | None, priority: bool = False) -> None:
    try:
        run_q.put_nowait(event)
        return
    except queue.Full:
        if not priority:
            return

    while True:
        try:
            dropped = run_q.get_nowait()
            if dropped is None:
                # Sentinel marks stream completion - reinsert it and stop draining.
                # Discarding it would cause the SSE consumer to wait forever.
                try:
                    run_q.put_nowait(None)
                except queue.Full:
                    pass
                return
        except queue.Empty:
            return

        try:
            run_q.put_nowait(event)
            return
        except queue.Full:
            continue


def _validate_session_id(session_id: str) -> None:
    """Raise HTTP 400 if session_id contains characters that could form a path traversal."""
    if not _SESSION_ID_RE.match(session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id: use only letters, digits, hyphens and underscores")


def finish_run_event_queue(run_id: str) -> None:
    """Signal that a run is complete by sending None sentinel to the queue.

    Does NOT pop the queue entry here - stream_run's _generate() does that when it receives
    the sentinel. This is critical: fast runs (e.g. slash commands completing in milliseconds)
    must keep the queue entry alive so the SSE client can still connect and read all events.
    Uses the same priority-drain logic as _queue_run_event so the sentinel is never silently lost.
    """
    with _run_queues_lock:
        q = _run_event_queues.get(run_id)
    if q:
        _queue_run_event(q, None, priority=True)


# ====================================================================================================
# MARK: FASTAPI APP
# ====================================================================================================
app = FastAPI(title="KoreAgent API", version=SUITE_VERSION)

# Restrict CORS to localhost only. External pages cannot trigger prompt or history endpoints.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)


# ====================================================================================================
# MARK: STATIC FILES
# ====================================================================================================
# All static UI files are served with Cache-Control: no-store so the browser always fetches
# the current version from disk. Do NOT use StaticFiles mount for these - Starlette mounts
# take routing priority over explicit handlers, which prevents the no-store header being set.

def _get_korechat_base_url() -> str | None:
    # Prefer the live sidecar setting, but fall back to default.json so the UI link
    # still works when KoreChat is configured but managed externally.
    base_url = _kc_client.get_base_url()
    if base_url:
        return base_url.rstrip("/")

    defaults_path = _WEB_DIR.parent.parent.parent / "default.json"
    try:
        raw = json.loads(defaults_path.read_text(encoding="utf-8")) if defaults_path.exists() else {}
    except Exception:
        return "http://localhost:8700"

    configured = str(raw.get("koreconvurl", "")).strip().rstrip("/")
    return configured or "http://localhost:8700"

@app.get("/", include_in_schema=False)
def serve_index():
    index = _WEB_DIR / "index.html"
    if not index.exists():
        return {"error": "Web UI not found"}
    return FileResponse(str(index), headers={"Cache-Control": "no-store"})


@app.get("/suite-config.js", include_in_schema=False)
def suite_config_js():
    urls = os.environ.get("KORE_SUITE_URLS", "{}")
    return Response(content=f"window.__koreSuiteUrls = {urls};", media_type="application/javascript", headers={"Cache-Control": "no-store"})


@app.get("/static/app.js", include_in_schema=False)
def serve_app_js():
    return FileResponse(str(_WEB_DIR / "app.js"), headers={"Cache-Control": "no-store"})


@app.get("/static/style.css", include_in_schema=False)
def serve_style_css():
    return FileResponse(str(_WEB_DIR / "style.css"), headers={"Cache-Control": "no-store"})


@app.get("/ui-elements/assets/{asset_path:path}", include_in_schema=False)
def serve_ui_elements_asset(asset_path: str):
    candidate = (_UI_ELEMENTS_ASSETS / asset_path).resolve()
    if candidate != _UI_ELEMENTS_ASSETS and _UI_ELEMENTS_ASSETS not in candidate.parents:
        raise HTTPException(status_code=404, detail="Asset not found")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(str(candidate), headers={"Cache-Control": "no-store"})


@app.get("/conversations", include_in_schema=False)
def redirect_to_korechat_ui():
    base_url = _get_korechat_base_url()
    return RedirectResponse(url=f"{base_url}/ui", status_code=307)


@app.get("/favicon.ico", include_in_schema=False)
def serve_favicon():
    ico = _WEB_DIR / "favicon.ico"
    if not ico.exists():
        from starlette.responses import Response
        return Response(status_code=404)
    return FileResponse(str(ico), media_type="image/x-icon", headers={"Cache-Control": "no-cache"})


@app.get("/README.md", include_in_schema=False)
def serve_readme():
    import markdown
    from starlette.responses import HTMLResponse
    readme = _WEB_DIR.parent.parent.parent / "README.md"
    if not readme.exists():
        from starlette.responses import Response
        return Response(status_code=404)
    md_text = readme.read_text(encoding="utf-8")
    body    = markdown.markdown(md_text, extensions=["tables", "fenced_code", "toc"])
    html    = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>README</title>"
        "<style>"
        "body{font-family:sans-serif;max-width:860px;margin:40px auto;padding:0 20px;line-height:1.6;color:#ccc;background:#1a1a1a}"
        "h1,h2,h3{color:#e8e8e8;border-bottom:1px solid #333;padding-bottom:4px}"
        "a{color:#6ab0f5}"
        "code{background:#2a2a2a;padding:2px 5px;border-radius:3px;font-size:0.9em}"
        "pre{background:#2a2a2a;padding:12px;border-radius:4px;overflow-x:auto}"
        "pre code{background:none;padding:0}"
        "table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #444;padding:6px 12px;text-align:left}"
        "th{background:#2a2a2a}"
        "blockquote{border-left:3px solid #555;margin:0;padding-left:16px;color:#999}"
        "</style></head><body>"
        + body
        + "</body></html>"
    )
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


register_status_routes(
    app,
    get_active_host=get_active_host,
    get_active_model=get_active_model,
    get_active_num_ctx=get_active_num_ctx,
    get_active_backend=get_active_backend,
    get_ollama_ps_rows=get_ollama_ps_rows,
    version=SUITE_VERSION,
)


# ====================================================================================================
# MARK: COMPLETIONS ENDPOINT
# ====================================================================================================

@app.get("/completions")
def get_completions():
    """Return tab-completion candidates grouped by type for the UI tab-complete feature."""
    sessions = []
    try:
        kc_sessions = _kc_get("/conversations?channel_type=webchat&limit=500") or []
        if isinstance(kc_sessions, list):
            for item in kc_sessions:
                external_id = str(item.get("external_id") or "")
                if not external_id.startswith("webchat_"):
                    continue
                name = (item.get("subject") or "").strip() or external_id.removeprefix("webchat_")
                if name and name not in sessions:
                    sessions.append(name)
    except HTTPException:
        pass

    test_dir   = get_test_prompts_dir()
    test_files = []
    if test_dir.exists():
        test_files = sorted(p.stem for p in test_dir.glob("*.json"))

    task_names = [t.get("name", "") for t in _enabled_tasks if t.get("name")]

    try:
        models = list_ollama_models()
    except Exception:
        models = []

    return {
        "sessions":   sessions,
        "test_files": test_files,
        "task_names": task_names,
        "models":     models,
    }


# ====================================================================================================
# MARK: SETTINGS ENDPOINTS
# ====================================================================================================

@app.get("/settings/sandbox")
def settings_sandbox_get():
    """Return the current Python execution sandbox state."""
    return {"sandbox": get_sandbox_enabled()}


@app.post("/settings/sandbox")
def settings_sandbox_post(enabled: bool):
    """Set the Python execution sandbox state."""
    set_sandbox_enabled(enabled)
    return {"sandbox": get_sandbox_enabled()}


@app.get("/settings/webskills")
def settings_webskills_get():
    """Return the current web skills enabled state."""
    return {"webskills": get_web_skills_enabled()}


@app.post("/settings/webskills")
def settings_webskills_post(enabled: bool):
    """Set the web skills enabled state."""
    set_web_skills_enabled(enabled)
    return {"webskills": get_web_skills_enabled()}


# ----------------------------------------------------------------------------------------------------
# LLM Direct: bypass orchestration, tool loop, and slash handling - straight to call_llm_chat.
_llm_direct_enabled: bool = False


def get_llm_direct_enabled() -> bool:
    return _llm_direct_enabled


def set_llm_direct_enabled(enabled: bool) -> None:
    global _llm_direct_enabled
    _llm_direct_enabled = bool(enabled)


@app.get("/settings/llmdirect")
def settings_llmdirect_get():
    """Return the current LLM Direct mode state."""
    return {"llmdirect": get_llm_direct_enabled()}


@app.post("/settings/llmdirect")
def settings_llmdirect_post(enabled: bool):
    """Set the LLM Direct mode state."""
    set_llm_direct_enabled(enabled)
    return {"llmdirect": get_llm_direct_enabled()}


register_task_routes(
    app,
    get_enabled_tasks=lambda: _enabled_tasks,
    get_last_run=lambda: _last_run,
    is_task_due=is_task_due,
    task_queue=task_queue,
    queue_preview_limit=_QUEUE_PREVIEW_LIMIT,
)


# ====================================================================================================
# MARK: INPUT HISTORY ENDPOINTS
# ====================================================================================================

_HISTORY_LIMIT = 20


class HistoryAppendRequest(BaseModel):
    text: str


@app.get("/sessions/{session_id}/input-history")
def get_session_input_history(session_id: str):
    """Return the last _HISTORY_LIMIT input history entries for the session's conversation."""
    _validate_session_id(session_id)
    conv = _kc_get_conversation_for_session(session_id)
    if conv is None:
        return {"entries": []}
    try:
        result  = _kc_get(f"/conversations/{conv['id']}/input-history")
        entries = result.get("entries", []) if isinstance(result, dict) else []
    except HTTPException:
        entries = []
    return {"entries": entries[-_HISTORY_LIMIT:]}


@app.post("/sessions/{session_id}/input-history")
def post_session_input_history(session_id: str, body: HistoryAppendRequest):
    """Append one entry to the session's per-conversation input history."""
    _validate_session_id(session_id)
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text cannot be empty")
    conv = _kc_ensure_conversation(session_id)
    if conv is None:
        return {"entries": [text]}
    try:
        result  = _kc_patch(f"/conversations/{conv['id']}/input-history", {"text": text})
        entries = result.get("entries", []) if isinstance(result, dict) else []
    except HTTPException:
        entries = [text]
    return {"entries": entries[-_HISTORY_LIMIT:]}


# timeline route is registered by register_task_routes()


# ====================================================================================================
# MARK: STOPRUN
# ====================================================================================================
# /stoprun is the only slash command that bypasses the task queue entirely.
# It executes immediately inside post_prompt so it can act on the currently-running
# LLM call without waiting for it to finish.  Two things happen:
#   1. request_stop() sets a threading.Event that orchestrate_prompt() checks between
#      rounds - the current HTTP call to Ollama finishes naturally, then the loop exits.
#   2. clear_pending() drains every not-yet-started item from the task queue and returns
#      their run_ids so we can push cancellation events to their SSE clients.

def _handle_stoprun_immediate(run_id: str, run_q: "queue.Queue") -> None:
    """Immediately signal the active run to stop and cancel all pending queue items."""
    # Signal the orchestration loop to exit after its current LLM round.
    request_stop()

    # Drain all pending items from the task queue.
    cancelled_ids = task_queue.clear_pending()

    # Push a cancellation response + sentinel to each pending run's SSE client.
    cancel_msg = "Cancelled by /stoprun."
    for rid in cancelled_ids:
        with _run_queues_lock:
            q = _run_event_queues.get(rid)
        if q is None:
            continue
        _queue_run_event(q, {"type": "response", "run_id": rid, "response": cancel_msg, "tokens": 0, "tps": "0"}, priority=True)
        _queue_run_event(q, None, priority=True)

    n = len(cancelled_ids)
    active_note = "Active run will halt after its current LLM round. "
    summary = (
        f"{active_note}{n} pending prompt{'s' if n != 1 else ''} cancelled."
        if n else
        f"{active_note}No prompts were queued."
    )
    _queue_run_event(run_q, {"type": "response", "run_id": run_id, "response": summary, "tokens": 0, "tps": "0"}, priority=True)
    finish_run_event_queue(run_id)


# ====================================================================================================
# MARK: SESSION HELPERS
# ====================================================================================================
def _kc_external_id_for_session(session_id: str) -> str:
    return f"webchat_{session_id}"


# In-memory cache: session_id -> KC conversation dict (avoids repeated GET lookups per prompt).
# Invalidated on session delete. Thread-safe for read-heavy access since dict reads are atomic
# in CPython, but the cache is best-effort - a miss just does a fresh GET.
_kc_conv_cache: dict[str, dict] = {}

# Pending display names set via /newchat <name>. Consumed on first conversation create.
_kc_session_names: dict[str, str] = {}


def _kc_set_session_name(session_id: str, name: str) -> None:
    if name:
        _kc_session_names[session_id] = name
    else:
        _kc_session_names.pop(session_id, None)


def _kc_get_conversation_for_session(session_id: str) -> dict | None:
    if session_id in _kc_conv_cache:
        return _kc_conv_cache[session_id]
    external_id = _kc_external_id_for_session(session_id)
    try:
        result = _kc_get(f"/conversations/by-external-id/{urllib.parse.quote(external_id, safe='')}")
    except HTTPException as exc:
        if exc.status_code in {404, 503}:
            return None
        raise
    conv = result if isinstance(result, dict) else None
    if conv is not None:
        _kc_conv_cache[session_id] = conv
    return conv


def _get_session_turns(session_id: str) -> list[dict]:
    # Single KC HTTP call - returns paired inbound/outbound messages as turns.
    external_id = _kc_external_id_for_session(session_id)
    try:
        result = _kc_get(f"/conversations/by-external-id/{urllib.parse.quote(external_id, safe='')}/turns")
    except HTTPException as exc:
        if exc.status_code in {404, 503}:
            return []
        raise
    if not isinstance(result, dict):
        return []
    messages = result.get("messages") or []
    turns: list[dict] = []
    pending_prompt: str | None = None
    for message in messages:
        direction = message.get("direction")
        content   = (message.get("content") or "").strip()
        if not content:
            continue
        if direction == "inbound":
            pending_prompt = content
        elif direction == "outbound" and pending_prompt is not None:
            turns.append({"role": "user",      "content": pending_prompt})
            turns.append({"role": "assistant", "content": content})
            pending_prompt = None
    return turns


def _kc_ensure_conversation(session_id: str) -> dict | None:
    """Return the KC conversation for session_id, creating it if absent."""
    conv = _kc_get_conversation_for_session(session_id)
    if conv is not None:
        return conv
    try:
        external_id = _kc_external_id_for_session(session_id)
        # Use any pending display name set by /newchat <name>, fall back to default.
        pending_name = _kc_session_names.pop(session_id, None)
        subject = pending_name or f"Webchat {session_id}"
        conv = _kc_post("/conversations", {
            "channel_type": "webchat",
            "subject":      subject,
            "protected":    bool(pending_name),
            "external_id":  external_id,
        })
    except Exception:
        return None
    if isinstance(conv, dict):
        _kc_conv_cache[session_id] = conv
        return conv
    return None


def _kc_save_turn(session_id: str, user_text: str, agent_text: str, token_estimate: int | None = None) -> None:
    """Write a user + agent turn to the KC conversation as inbound + outbound messages."""
    conv = _kc_ensure_conversation(session_id)
    if conv is None:
        return
    conv_id    = conv["id"]
    turn_count = (conv.get("turn_count") or 0) + 1
    try:
        _kc_post(f"/conversations/{conv_id}/messages", {
            "direction":      "inbound",
            "content":        user_text,
            "sender_display": session_id,
            "status":         "received",
        })
        _kc_post(f"/conversations/{conv_id}/messages", {
            "direction":      "outbound",
            "content":        agent_text,
            "sender_display": "agent",
            "status":         "sent",
        })
    except Exception:
        pass
    try:
        patch: dict = {"turn_count": turn_count}
        if token_estimate is not None:
            patch["token_estimate"] = token_estimate
        _kc_patch(f"/conversations/{conv_id}", patch)
        # Invalidate cached conv so the next call sees the updated turn_count.
        _kc_conv_cache.pop(session_id, None)
    except Exception:
        pass


def _load_session(session_id: str) -> tuple["ConversationHistory", list[dict]]:
    """Load conversation history and scratchpad from KoreChat when present."""
    history   = ConversationHistory()
    summaries: list[dict] = []
    conv = _kc_get_conversation_for_session(session_id)
    if conv is None:
        return history, summaries

    scratch_clear(session_id)
    scratchpad = conv.get("scratchpad") or {}
    if isinstance(scratchpad, str):
        try:
            scratchpad = json.loads(scratchpad)
        except Exception:
            scratchpad = {}
    for key, value in scratchpad.items():
        try:
            scratch_restore_key(key, str(value), session_id)
        except Exception as exc:
            print(f"[session] Warning: could not restore scratchpad key '{key}': {exc}", flush=True)

    thread_summary = (conv.get("thread_summary") or "").strip()
    if thread_summary:
        summaries = [{"text": thread_summary, "turn_range": [1, 1]}]

    try:
        messages = _kc_get(f"/conversations/{conv['id']}/messages?summarised=0&limit=1000") or []
    except HTTPException:
        messages = []

    pending_prompt: str | None = None
    for message in messages:
        direction = message.get("direction")
        content = (message.get("content") or "").strip()
        if not content:
            continue
        if direction == "inbound":
            pending_prompt = content
            continue
        if direction == "outbound" and pending_prompt is not None:
            history.add(pending_prompt, content)
            pending_prompt = None

    return history, summaries


def _compact_old_turns(turns: list[dict], summaries: list[dict], batch_size: int) -> tuple[list[dict], list[dict]]:
    """Compress the oldest batch_size turns into a summary block via an isolated LLM call.

    Returns (remaining_turns, updated_summaries).  Returns inputs unchanged on any error
    (no model loaded, LLM failure) so the session is never corrupted by a failed compaction.
    """
    from llm_client import call_llm_chat
    from llm_client import get_active_model
    from llm_client import get_active_num_ctx

    model   = get_active_model()
    num_ctx = get_active_num_ctx()
    if not model:
        return turns, summaries

    batch     = turns[:batch_size]
    remaining = turns[batch_size:]
    batch_text = "\n\n".join(
        f"User: {t['user_prompt']}\nAssistant: {t['assistant_response']}"
        for t in batch
    )

    messages = [
        {
            "role":    "system",
            "content": (
                "You are a precise conversation summariser. "
                "Compress the following conversation exchanges into one compact paragraph. "
                "Preserve all specific facts, decisions, code, URLs, names, and conclusions reached. "
                "Write in third person (e.g. 'The user asked about X; the assistant explained Y and provided Z.'). "
                "Do not interpret, evaluate, or add information not present in the exchanges."
            ),
        },
        {
            "role":    "user",
            "content": f"Conversation to summarise:\n\n{batch_text}",
        },
    ]

    try:
        result       = call_llm_chat(model_name=model, messages=messages, tools=None, num_ctx=num_ctx)
        summary_text = (result.response or "").strip()
    except Exception as exc:
        print(f"[session] Warning: history compaction LLM call failed: {exc}", flush=True)
        return turns, summaries

    if not summary_text:
        return turns, summaries

    prior_end   = summaries[-1]["turn_range"][1] if summaries else 0
    new_summary = {
        "text":       summary_text,
        "turn_range": [prior_end + 1, prior_end + len(batch)],
    }
    return remaining, summaries + [new_summary]


def _build_summary_block(summaries: list[dict]) -> str:
    """Format summary blocks into a single string for injection into the system prompt."""
    if not summaries:
        return ""
    parts = [
        f"[Turns {s['turn_range'][0]}-{s['turn_range'][1]}] {s['text']}"
        for s in summaries
    ]
    return "\n\n".join(parts)


def _save_session(
    session_id:    str,
    history:       "ConversationHistory",
    summaries:     list[dict],
    prompt_tokens: int,
    num_ctx:       int,
) -> list[dict]:
    """Persist session state and compact old history when context fill is high."""
    _flush_scratch_to_session(session_id)

    # Compact old turns with LLM summarization when the last prompt consumed a large
    # share of the context window.  This keeps future turns from growing unboundedly
    # and is the primary mechanism that makes "endless chat" sustainable over many turns.
    _KEEP_RECENT_TURNS = 4   # always keep the N most-recent turns untouched
    _BATCH_SIZE        = 4   # turns to compress per compaction pass

    if (
        num_ctx > 0
        and prompt_tokens > 0
        and (prompt_tokens / num_ctx) >= _COMPACT_FILL_PCT
        and len(history) >= _KEEP_RECENT_TURNS + _BATCH_SIZE
    ):
        raw = history.as_list()
        turn_dicts: list[dict] = []
        for i in range(0, len(raw) - 1, 2):
            if raw[i]["role"] == "user" and raw[i + 1]["role"] == "assistant":
                turn_dicts.append({
                    "user_prompt":        raw[i]["content"],
                    "assistant_response": raw[i + 1]["content"],
                })
        if len(turn_dicts) >= _KEEP_RECENT_TURNS + _BATCH_SIZE:
            compactable  = turn_dicts[: len(turn_dicts) - _KEEP_RECENT_TURNS]
            recent_turns = turn_dicts[len(turn_dicts) - _KEEP_RECENT_TURNS :]
            remaining_dicts, updated_summaries = _compact_old_turns(compactable, summaries, _BATCH_SIZE)
            if len(updated_summaries) > len(summaries):
                # Compaction produced a new summary - rebuild in-memory history and persist.
                history.clear()
                for t in remaining_dicts + recent_turns:
                    history.add(t["user_prompt"], t["assistant_response"])
                summaries = updated_summaries
                conv = _kc_get_conversation_for_session(session_id)
                if conv:
                    try:
                        combined = _build_summary_block(summaries)
                        _kc_patch(f"/conversations/{conv['id']}", {"thread_summary": combined})
                    except Exception as exc:
                        print(f"[session] Warning: could not store thread_summary: {exc}", flush=True)

    return summaries


def _flush_scratch_to_session(session_id: str) -> None:
    # Runtime scratch now syncs to KoreChat instead of chatsessions JSON files.
    conv = _kc_get_conversation_for_session(session_id)
    if conv is None:
        return
    try:
        named_scratch = {k: v for k, v in get_scratch_store(session_id).items() if not k.startswith("_tc_")}
        _kc_patch(f"/conversations/{conv['id']}", {"scratchpad": named_scratch})
    except Exception as exc:
        print(f"[session] Warning: could not flush scratchpad to KoreChat for session '{session_id}': {exc}", flush=True)


def _delete_session_state(session_id: str) -> None:
    scratch_clear(session_id)
    _kc_conv_cache.pop(session_id, None)
    conv = _kc_get_conversation_for_session(session_id)
    if conv is None:
        return
    try:
        _kc_delete(f"/conversations/{conv['id']}")
    except HTTPException as exc:
        if exc.status_code != 404:
            raise


# ====================================================================================================
# MARK: SSE HELPER
# ====================================================================================================

def _sse(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data)}\n\n"


# ====================================================================================================
# MARK: LOG BROADCAST
# ====================================================================================================
# Fan-out: each SSE client gets its own subscriber queue fed by push_log_line.
_log_subscribers:      list[queue.Queue] = []
_log_subscribers_lock: threading.Lock    = threading.Lock()


def push_log_line(line: str) -> None:
    """Push a log line to all active log-stream SSE subscribers."""
    item = {
        "type": "log",
        "text": line,
        "ts":   datetime.now().isoformat(timespec="seconds"),
        "path": _latest_log_path,
    }
    with _log_subscribers_lock:
        for sub in list(_log_subscribers):
            try:
                sub.put_nowait(item)
            except queue.Full:
                pass


def _get_log_backfill() -> list[dict]:
    """Return the last _LOG_TAIL_LINES lines from the most recent log file."""
    latest = _get_latest_log_file()
    if latest is None:
        return []
    try:
        lines = latest.read_text(encoding="utf-8", errors="replace").splitlines()
        tail  = lines[-_LOG_TAIL_LINES:]
        _set_latest_log_path(latest)
        return [{"type": "log", "text": ln, "ts": "", "path": str(latest)} for ln in tail]
    except Exception:
        return []


def _get_latest_log_file() -> Path | None:
    if not _LOG_DIR.exists():
        return None
    day_dirs = sorted(_LOG_DIR.iterdir(), reverse=True)
    for day_dir in day_dirs:
        if not day_dir.is_dir():
            continue
        files = sorted(day_dir.glob("*.txt"), reverse=True)
        if files:
            return files[0]
    return None


register_session_routes(
    app,
    config_getter=lambda: _config,
    validate_session_id=_validate_session_id,
    make_run_event_queue=_make_run_event_queue,
    queue_run_event=_queue_run_event,
    finish_run_event_queue=finish_run_event_queue,
    handle_stoprun_immediate=_handle_stoprun_immediate,
    load_session=_load_session,
    save_session=_save_session,
    flush_scratch_session=_flush_scratch_to_session,
    build_summary_block=_build_summary_block,
    create_session_context=SessionContext,
    clear_session_scratch=scratch_clear,
    make_slash_context=SlashCommandContext,
    handle_slash=handle_slash,
    push_log_line=push_log_line,
    log_file_re=_LOG_FILE_RE,
    turn_agent_re=_TURN_AGENT_RE,
    turn_metrics_re=_TURN_METRICS_RE,
    test_complete_re=_TEST_COMPLETE_RE,
    set_latest_log_path=_set_latest_log_path,
    log_dir=_LOG_DIR,
    create_log_file_path=create_log_file_path,
    session_logger_cls=SessionLogger,
    orchestrate_prompt=orchestrate_prompt,
    get_active_num_ctx=get_active_num_ctx,
    task_queue=task_queue,
    run_queues=_run_event_queues,
    run_queues_lock=_run_queues_lock,
    sse=lambda data: _sse(data),
    delete_session_state=_delete_session_state,
    kc_save_turn=_kc_save_turn,
    get_session_turns=_get_session_turns,
    get_session_conversation=_kc_get_conversation_for_session,
    kc_set_session_name=_kc_set_session_name,
    get_llm_direct_enabled=get_llm_direct_enabled,
    call_llm_chat=call_llm_chat,
)

register_log_routes(
    app,
    log_dir=_LOG_DIR,
    shutdown_event_getter=lambda: _shutdown_event,
    log_poll_secs=_LOG_POLL_SECS,
    sse=lambda data: _sse(data),
    set_latest_log_path=_set_latest_log_path,
    get_latest_log_file=_get_latest_log_file,
    get_log_backfill=_get_log_backfill,
    log_subscribers=_log_subscribers,
    log_subscribers_lock=_log_subscribers_lock,
)


# ====================================================================================================
# MARK: KORECHAT PROXY ENDPOINTS
# ====================================================================================================
# These endpoints expose the KoreChat service to the web UI so the browser
# always talks to a single origin (port 8000). They proxy create/send/read operations
# to the KC service at its own port.
#
# POST /kc/send   - create or find the KC conversation for a session, append an inbound
#                   message. Returns {conv_id, msg_id}. The KoreChat message
#                   append endpoint is the sole source of response_needed events.
# GET  /kc/conversations/{conv_id}/messages - proxy the KC message list to the browser.

_KC_TIMEOUT = 8


def _kc_get(path: str) -> dict | list | None:
    """Proxy a GET request to the KoreChat service."""
    base = _kc_client.get_base_url()
    if not base:
        raise HTTPException(status_code=503, detail="KoreChat not configured")
    url = f"{base}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_KC_TIMEOUT) as resp:
            if resp.status == 204:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=exc.code, detail=exc.read().decode("utf-8", errors="replace")[:200]) from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=503, detail=f"KoreChat unreachable: {exc.reason}") from exc


def _kc_post(path: str, payload: dict) -> dict | None:
    """Proxy a POST request to the KoreChat service."""
    base = _kc_client.get_base_url()
    if not base:
        raise HTTPException(status_code=503, detail="KoreChat not configured")
    url  = f"{base}{path}"
    body = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        url,
        data    = body,
        headers = {"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_KC_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8").strip()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=exc.code, detail=exc.read().decode("utf-8", errors="replace")[:200]) from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=503, detail=f"KoreChat unreachable: {exc.reason}") from exc


def _kc_patch(path: str, payload: dict) -> dict | None:
    """Proxy a PATCH request to the KoreChat service."""
    base = _kc_client.get_base_url()
    if not base:
        raise HTTPException(status_code=503, detail="KoreChat not configured")
    url  = f"{base}{path}"
    body = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        url,
        data    = body,
        method  = "PATCH",
        headers = {"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_KC_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8").strip()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=exc.code, detail=exc.read().decode("utf-8", errors="replace")[:200]) from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=503, detail=f"KoreChat unreachable: {exc.reason}") from exc


def _kc_delete(path: str) -> None:
    """Proxy a DELETE request to the KoreChat service."""
    base = _kc_client.get_base_url()
    if not base:
        raise HTTPException(status_code=503, detail="KoreChat not configured")
    req = urllib.request.Request(f"{base}{path}", method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=_KC_TIMEOUT):
            return None
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=exc.code, detail=exc.read().decode("utf-8", errors="replace")[:200]) from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=503, detail=f"KoreChat unreachable: {exc.reason}") from exc


class KcSendRequest(BaseModel):
    session_id: str
    content:    str


@app.post("/kc/send", status_code=201)
def kc_send(body: KcSendRequest):
    """Append an inbound chat message to KoreChat.

    Finds or creates the KC conversation for the given session_id (mapped via external_id).
    Returns {conv_id, msg_id} so the browser can poll for the outbound reply.
    """
    _validate_session_id(body.session_id)
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content cannot be empty")

    external_id = f"webchat_{body.session_id}"

    # Find existing conversation by external_id; create if absent.
    conv_id: int | None = None
    try:
        existing = _kc_get(f"/conversations/by-external-id/{urllib.parse.quote(external_id, safe='')}")
        if isinstance(existing, dict) and existing.get("id"):
            conv_id = int(existing["id"])
    except HTTPException as exc:
        if exc.status_code != 404:
            raise

    if conv_id is None:
        # Create a new webchat conversation tied to this session.
        new_conv = _kc_post("/conversations", {
            "channel_type": "webchat",
            "subject":      f"Webchat {body.session_id}",
            "protected":    False,
            "external_id":  external_id,
        })
        if not new_conv:
            raise HTTPException(status_code=502, detail="Failed to create KC conversation")
        conv_id = new_conv["id"]

    # Append inbound message.
    msg = _kc_post(f"/conversations/{conv_id}/messages", {
        "direction":      "inbound",
        "content":        content,
        "sender_display": body.session_id,
        "status":         "received",
    })
    if not msg:
        raise HTTPException(status_code=502, detail="Failed to append message to KC conversation")

    return {"conv_id": conv_id, "msg_id": msg.get("id")}


@app.get("/kc/conversations/{conv_id}/messages")
def kc_get_messages(conv_id: int, limit: int = 100):
    """Proxy the message list for a KC conversation to the browser."""
    return _kc_get(f"/conversations/{conv_id}/messages?limit={limit}") or []


@app.get("/kc/conversations/{conv_id}")
def kc_get_conversation(conv_id: int):
    """Proxy a KC conversation record to the browser."""
    result = _kc_get(f"/conversations/{conv_id}")
    if result is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return result
