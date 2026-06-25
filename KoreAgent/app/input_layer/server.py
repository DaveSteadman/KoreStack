# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FastAPI application exposing the KoreAgent engine as a REST + SSE service.
#
# Endpoints:
#   GET  /                              serve static web UI (index.html)
#   GET  /suite-config.js               service URL map injected as window.__koreSuiteUrls
#   GET  /ui-elements/assets/{path}     shared UIElements static assets
#   GET  /version                       return framework version string
#   GET  /status/ollama                 current 'ollama ps' model status
#   GET  /settings/sandbox              return current sandbox enabled state
#   POST /settings/sandbox?enabled=     set sandbox enabled state
#   GET  /settings/webskills            return current web skills enabled state
#   POST /settings/webskills?enabled=   set web skills enabled state
#   GET  /settings/llmdirect            LLM Direct mode state (bypass orchestration, tools, slash)
#   POST /settings/llmdirect?enabled=   set LLM Direct mode state
#   GET  /completions                   tab-completion candidates (sessions, test files, tasks, models)
#   GET  /tasks                         enabled scheduled tasks with last-run and next-fire times
#   GET  /timeline                      minute-resolution task timeline centred on now
#   GET  /queue                         current task queue state
#   GET  /sessions/{id}/input-history   last 20 input history entries for the session
#   POST /sessions/{id}/input-history   append an entry to session input history
#   GET  /sessions/{id}/history         full conversation history for a session
#   POST /sessions/{id}/prompt          submit a new prompt (enqueues on task_queue)
#   POST /sessions/request-switch       request the active session be switched (consumed by /queue)
#   GET  /logs                          list all log directories and files
#   GET  /logs/latest                   path of the most recently written log file
#   GET  /logs/stream                   SSE: tail all new log lines across all log files
#   GET  /logs/file?path=<path>         SSE: tail a specific log file (used for per-run view)
#   GET  /logs/{date}/{filename}        serve a specific log file
#   GET  /runs/{id}/stream              SSE: stream events for a specific enqueued run
#   POST /kc/send                       append inbound message to KoreChat conversation
#   GET  /kc/conversations/{id}/messages  proxy KC message list to browser
#   GET  /kc/conversations/{id}           proxy KC conversation record to browser
#
# SSE events are plain text/event-stream with a "data: <json>\n\n" envelope.
# CORS is restricted to localhost origins only - requests from external sites are blocked.
#
# Instantiated once in server_startup.py, then served by uvicorn.
#
# Related modules:
#   - server_startup.py       -- constructs and starts this app
#   - scheduler.py            -- task_queue singleton, load_schedules_dir, is_task_due
#   - orchestration.py        -- orchestrate_prompt, OrchestratorConfig, ConversationHistory
#   - runtime_logger.py       -- SessionLogger, create_log_file_path
#   - llm_client.py           -- get_ollama_ps_rows, get_active_host
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
_KORECOMMON_PARENT = next((parent for parent in Path(__file__).resolve().parents if (parent / "KoreCommon").is_dir()), None)
if _KORECOMMON_PARENT is not None and str(_KORECOMMON_PARENT) not in sys.path:
    sys.path.insert(0, str(_KORECOMMON_PARENT))

import json
import queue
import re
import threading
from datetime import datetime

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from KoreCommon.endpoint_manifest import build_endpoint_manifest
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
from datasets import build_persisted_scratchpad_payload
from datasets import delete_session_datasets as delete_persisted_session_datasets
from datasets import get_persisted_datasets_payload
from datasets import hydrate_session_state
from input_layer.korechat_proxy_routes import register_korechat_proxy_routes
from scratchpad import get_store as get_scratch_store
from scratchpad import scratch_clear
from scratchpad import scratch_save as scratch_restore_key
from input_layer.server_static import register_static_routes
from input_layer.session_service import SessionService
from input_layer.routes_logs import register_log_routes
from input_layer.routes_sessions import register_session_routes
from input_layer.routes_status import register_status_routes
from input_layer.routes_tasks import register_task_routes
from utils.runtime_logger import SessionLogger
from utils.runtime_logger import create_log_file_path
from scheduler.scheduler import is_task_due
from scheduler.scheduler import task_queue
from scheduler.shared_state import SchedulerSharedState
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
        str(Path(__file__).resolve().parents[3] / "UIElements" / "assets"),
    )
).resolve()
_COMPACT_FILL_PCT    = 0.65  # compact when prompt-token fill reaches this fraction of num_ctx
_QUEUE_PREVIEW_LIMIT = 10
_LOG_POLL_SECS       = 1.0      # how often the log-tail SSE generator checks for new lines
_LOG_TAIL_LINES      = 200      # how many historic lines to send on first connect


# ====================================================================================================
# MARK: GLOBAL STATE
# ====================================================================================================
# These are set once by server_startup.py before uvicorn starts.
_config:         OrchestratorConfig | None = None
_last_run:       dict[str, datetime | None] = {}
_enabled_tasks:  list[dict] = []
_scheduler_state: SchedulerSharedState | None = None
_shutdown_event: threading.Event = threading.Event()

# Per-run event queues: run_id -> queue.Queue[dict | None]
# None sentinel signals the stream is finished.
_run_event_queues: dict[str, queue.Queue] = {}
_run_queues_lock:  threading.Lock = threading.Lock()

_latest_log_path: str | None = None

_pending_switch:      dict | None   = None
_pending_switch_lock: threading.Lock = threading.Lock()


# ====================================================================================================
# MARK: SETUP FUNCTIONS
# ====================================================================================================

def setup(
    config: OrchestratorConfig,
    enabled_tasks: list[dict],
    last_run: dict[str, datetime | None],
    shutdown_event: threading.Event,
    scheduler_state: SchedulerSharedState | None = None,
) -> None:
    """Called once by server_startup.py before serving. Stores shared state."""
    global _config, _enabled_tasks, _last_run, _shutdown_event, _scheduler_state
    _config         = config
    _enabled_tasks  = enabled_tasks
    _last_run       = last_run
    _scheduler_state = scheduler_state
    _shutdown_event = shutdown_event


def _get_scheduler_snapshot() -> tuple[list[dict], dict[str, datetime | None]]:
    if _scheduler_state is not None:
        return _scheduler_state.snapshot()
    return list(_enabled_tasks), dict(_last_run)


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


@app.get("/__endpoint_manifest", include_in_schema=False)
def endpoint_manifest() -> dict:
    return build_endpoint_manifest(app, service_key="koreagent", service_label="KoreAgent")

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
    # Prefer the live sidecar setting, but fall back to the suite config so the UI link
    # still works when KoreChat is configured but managed externally.
    base_url = _kc_client.get_base_url()
    if base_url:
        return base_url.rstrip("/")

    defaults_path = _WEB_DIR.parent.parent.parent / "config" / "korestack_config.json"
    try:
        raw = json.loads(defaults_path.read_text(encoding="utf-8")) if defaults_path.exists() else {}
    except Exception:
        return None

    configured = str(raw.get("korechaturl", "")).strip().rstrip("/")
    if configured:
        return configured

    network = raw.get("network") if isinstance(raw.get("network"), dict) else {}
    services = raw.get("services") if isinstance(raw.get("services"), dict) else {}
    korechat = services.get("korechat") if isinstance(services.get("korechat"), dict) else {}
    port = korechat.get("port")
    if port is None:
        return None
    host = str(network.get("host") or "127.0.0.1").strip() or "127.0.0.1"
    return f"http://{host}:{int(port)}"

register_static_routes(
    app,
    web_dir               = _WEB_DIR,
    ui_elements_assets    = _UI_ELEMENTS_ASSETS,
    get_korechat_base_url = _get_korechat_base_url,
)


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

@app.get("/api/completions")
@app.get("/completions", include_in_schema=False)
def get_completions():
    """Return tab-completion candidates grouped by type for the UI tab-complete feature."""
    sessions = []
    try:
        kc_sessions = _session_service.kc_get("/api/conversations?channel_type=webchat&limit=500") or []
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

    enabled_tasks, _ = _get_scheduler_snapshot()
    task_names = [t.get("name", "") for t in enabled_tasks if t.get("name")]

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

@app.get("/api/settings/sandbox")
@app.get("/settings/sandbox", include_in_schema=False)
def settings_sandbox_get():
    """Return the current Python execution sandbox state."""
    return {"sandbox": get_sandbox_enabled()}


@app.post("/api/settings/sandbox")
@app.post("/settings/sandbox", include_in_schema=False)
def settings_sandbox_post(enabled: bool):
    """Set the Python execution sandbox state."""
    set_sandbox_enabled(enabled)
    return {"sandbox": get_sandbox_enabled()}


@app.get("/api/settings/webskills")
@app.get("/settings/webskills", include_in_schema=False)
def settings_webskills_get():
    """Return the current web skills enabled state."""
    return {"webskills": get_web_skills_enabled()}


@app.post("/api/settings/webskills")
@app.post("/settings/webskills", include_in_schema=False)
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


@app.get("/api/settings/llmdirect")
@app.get("/settings/llmdirect", include_in_schema=False)
def settings_llmdirect_get():
    """Return the current LLM Direct mode state."""
    return {"llmdirect": get_llm_direct_enabled()}


@app.post("/api/settings/llmdirect")
@app.post("/settings/llmdirect", include_in_schema=False)
def settings_llmdirect_post(enabled: bool):
    """Set the LLM Direct mode state."""
    set_llm_direct_enabled(enabled)
    return {"llmdirect": get_llm_direct_enabled()}


# ====================================================================================================
# MARK: SESSION SWITCH REQUEST
# ====================================================================================================
# Allows other services (e.g. KoreChat UI) to request that the KoreAgent switches its
# active session.  The pending switch is stored here and returned once via /queue, where
# the KoreAgent browser UI picks it up on its regular poll cycle.

class SessionSwitchRequest(BaseModel):
    name: str


def _pop_pending_switch() -> dict | None:
    global _pending_switch
    with _pending_switch_lock:
        sw = _pending_switch
        _pending_switch = None
    return sw


@app.post("/api/sessions/request-switch", status_code=200)
@app.post("/sessions/request-switch", status_code=200, include_in_schema=False)
def post_request_switch(body: SessionSwitchRequest):
    # Use the same name/lookup helpers as the slash command handler but search ALL
    # conversation types (not just webchat) so KoreComms and other channels work too.
    from input_layer.slash_command_handlers_sessions import (
        _list_all_conversations,
        _session_id_from_external_id,
        _display_name,
    )
    global _pending_switch
    target = body.name.strip().lower()
    conversations = _list_all_conversations()
    conv = next((c for c in conversations if _display_name(c).lower() == target), None)
    if conv is None:
        conv = next((c for c in conversations if target in _display_name(c).lower()), None)
    if conv is None:
        raise HTTPException(status_code=404, detail=f"No conversation named '{body.name}' found.")
    external_id  = str(conv.get("external_id") or "")
    channel_type = str(conv.get("channel_type") or "")
    if external_id.startswith("webchat_") or channel_type == "webchat":
        session_id = _session_id_from_external_id(external_id)
    else:
        session_id = f"kc_conv_{conv['id']}"
    name = _display_name(conv)
    _validate_session_id(session_id)
    with _pending_switch_lock:
        _pending_switch = {"session_id": session_id, "name": name}
    return {"ok": True}


register_task_routes(
    app,
    get_enabled_tasks=lambda: _get_scheduler_snapshot()[0],
    get_last_run=lambda: _get_scheduler_snapshot()[1],
    get_scheduler_snapshot=_get_scheduler_snapshot,
    is_task_due=is_task_due,
    task_queue=task_queue,
    queue_preview_limit=_QUEUE_PREVIEW_LIMIT,
    get_pending_switch=_pop_pending_switch,
)


# ====================================================================================================
# MARK: INPUT HISTORY ENDPOINTS
# ====================================================================================================

_HISTORY_LIMIT = 20


class HistoryAppendRequest(BaseModel):
    text: str


@app.get("/api/sessions/{session_id}/input-history")
@app.get("/sessions/{session_id}/input-history", include_in_schema=False)
def get_session_input_history(session_id: str):
    """Return the last _HISTORY_LIMIT input history entries for the session's conversation."""
    _validate_session_id(session_id)
    conv = _session_service.kc_get_conversation_for_session(session_id)
    if conv is None:
        return {"entries": []}
    try:
        result  = _session_service.kc_get(f"/api/conversations/{conv['id']}/input-history")
        entries = result.get("entries", []) if isinstance(result, dict) else []
    except HTTPException:
        entries = []
    return {"entries": entries[-_HISTORY_LIMIT:]}


@app.post("/api/sessions/{session_id}/input-history")
@app.post("/sessions/{session_id}/input-history", include_in_schema=False)
def post_session_input_history(session_id: str, body: HistoryAppendRequest):
    """Append one entry to the session's per-conversation input history."""
    _validate_session_id(session_id)
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text cannot be empty")
    conv = _session_service.kc_ensure_conversation(session_id)
    if conv is None:
        return {"entries": [text]}
    try:
        result  = _session_service.kc_patch(f"/api/conversations/{conv['id']}/input-history", {"text": text})
        entries = result.get("entries", []) if isinstance(result, dict) else []
    except HTTPException:
        entries = [text]
    return {"entries": entries[-_HISTORY_LIMIT:]}


# timeline route is registered by register_task_routes()


_session_service = SessionService(
    compact_fill_pct                    = _COMPACT_FILL_PCT,
    kc_client                           = _kc_client,
    conversation_history_cls            = ConversationHistory,
    session_context_cls                 = SessionContext,
    hydrate_session_state               = hydrate_session_state,
    scratch_clear                       = scratch_clear,
    scratch_restore_key                 = scratch_restore_key,
    get_scratch_store                   = get_scratch_store,
    build_persisted_scratchpad_payload  = build_persisted_scratchpad_payload,
    get_persisted_datasets_payload      = get_persisted_datasets_payload,
    delete_persisted_session_datasets   = delete_persisted_session_datasets,
    request_stop                        = request_stop,
    task_queue                          = task_queue,
    run_event_queues                    = _run_event_queues,
    run_queues_lock                     = _run_queues_lock,
    queue_run_event                     = _queue_run_event,
    finish_run_event_queue              = finish_run_event_queue,
)


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
    handle_stoprun_immediate=_session_service.handle_stoprun_immediate,
    load_session=_session_service.load_session,
    save_session=_session_service.save_session,
    flush_scratch_session=_session_service.flush_scratch_to_session,
    create_session_context=_session_service.create_session_context,
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
    delete_session_state=_session_service.delete_session_state,
    kc_save_turn=_session_service.kc_save_turn,
    get_session_turns=_session_service.get_session_turns,
    get_session_conversation=_session_service.kc_get_conversation_for_session,
    kc_set_session_name=_session_service.kc_set_session_name,
    get_llm_direct_enabled=get_llm_direct_enabled,
    call_llm_chat=call_llm_chat,
)


_load_session           = _session_service.load_session
_save_session           = _session_service.save_session
_create_session_context = _session_service.create_session_context


def _kc_get(path: str):
    return _session_service.kc_get(path)


def _kc_patch(path: str, payload: dict):
    return _session_service.kc_patch(path, payload)


def _kc_delete(path: str) -> None:
    return _session_service.kc_delete(path)


def _kc_conversation_id_for_session(session_id: str) -> int | None:
    return _session_service.kc_conversation_id_for_session(session_id)


def _kc_get_conversation_for_session(session_id: str) -> dict | None:
    conv_id = _kc_conversation_id_for_session(session_id)
    if conv_id is not None:
        result = _kc_get(f"/conversations/{conv_id}")
        return result if isinstance(result, dict) else None
    return _session_service.kc_get_conversation_for_session(session_id)


def _delete_session_state(session_id: str) -> None:
    scratch_clear(session_id)
    delete_persisted_session_datasets(session_id)
    conv = _kc_get_conversation_for_session(session_id)
    if conv is not None:
        _kc_delete(f"/conversations/{conv['id']}")

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


register_korechat_proxy_routes(
    app,
    validate_session_id = _validate_session_id,
    kc_get_async        = _session_service.kc_get_async,
    kc_post_async       = _session_service.kc_post_async,
)
