# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FastAPI application exposing the KoreAgent engine as a REST + SSE service.
# The app module now acts primarily as composition glue: shared state lives in
# api/state.py, log fan-out helpers in api/log_state.py, and auxiliary route
# groups in dedicated route modules.
# ====================================================================================================

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
import re

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware

from KoreCommon.endpoint_manifest import build_endpoint_manifest
from agent.orchestration.engine import ConversationHistory
from agent.orchestration.engine import OrchestratorConfig
from agent.orchestration.engine import SessionContext
from agent.orchestration.engine import _filter_web_skills
from agent.orchestration.engine import get_sandbox_enabled
from agent.orchestration.engine import get_web_skills_enabled
from agent.orchestration.engine import orchestrate_prompt
from agent.orchestration.engine import request_stop
from agent.orchestration.engine import set_sandbox_enabled
from agent.orchestration.engine import set_web_skills_enabled
from api.log_state import get_latest_log_file as _get_latest_log_file
from api.log_state import get_log_backfill as _get_log_backfill
from api.log_state import get_log_subscribers
from api.log_state import get_log_subscribers_lock
from api.log_state import push_log_line as _push_log_line
from api.routes_input_history import register_input_history_routes
from api.routes_session_switch import register_session_switch_routes
from api.routes_skills import register_skills_routes
from api.state import finish_run_event_queue
from api.state import format_sse as _sse
from api.state import get_config as _get_config
from api.state import get_latest_log_path
from api.state import get_llm_direct_enabled
from api.state import get_run_event_queues
from api.state import get_run_queues_lock
from api.state import get_scheduler_snapshot as _get_scheduler_snapshot
from api.state import get_shutdown_event
from api.state import get_startup_state_snapshot
from api.state import make_run_event_queue
from api.state import pop_pending_switch as _pop_pending_switch
from api.state import queue_run_event
from api.state import set_latest_log_path as _set_latest_log_path
from api.state import set_llm_direct_enabled
from api.state import set_pending_switch as _set_pending_switch
from api.state import set_startup_state_snapshot
from api.state import setup
from api.state import update_startup_state
from api.state import validate_session_id as _validate_session_id
from datasets_pkg.hydration import build_persisted_scratchpad_payload
from datasets_pkg.hydration import get_persisted_datasets_payload
from datasets_pkg.hydration import hydrate_session_state
from datasets_pkg.service import delete_session_datasets as delete_persisted_session_datasets
from input_layer.korechat_proxy_routes import register_korechat_proxy_routes
from input_layer.routes_logs import register_log_routes
from input_layer.routes_sessions import register_session_routes
from input_layer.routes_status import register_status_routes
from input_layer.routes_tasks import register_task_routes
from input_layer.server_static import register_static_routes
from input_layer.slash_command_context import SlashCommandContext
from input_layer.slash_commands import handle as handle_slash
from llm_client import call_llm_chat
from llm_client import get_active_backend
from llm_client import get_active_host
from llm_client import get_active_model
from llm_client import get_active_num_ctx
from llm_client import get_ollama_ps_rows
from llm_client import list_ollama_models
import mcp_client
from scheduler.scheduler import is_task_due
from scheduler.scheduler import task_queue
from scratchpad import get_store as get_scratchpad_store
from scratchpad import scratchpad_clear
from scratchpad import scratchpad_save as scratchpad_restore_key
from sessions.service import SessionService
from sessions.tool_selection import ALWAYS_ON_TOOL_NAMES
from sessions.tool_selection import clear_session_tools_active
from sessions.tool_selection import get_selected_tools
from sessions.tool_selection import set_selected_tools
from skill_executor import build_catalog_gates
from skill_executor import execute_tool_call
from skills_catalog_builder import build_tool_definitions
from utils.runtime_logger import SessionLogger
from utils.runtime_logger import create_log_file_path
from utils.suite_version import SUITE_VERSION
from utils.workspace_utils import get_logs_dir
from utils.workspace_utils import get_test_prompts_dir
import sessions.korechat_client as _kc_client
from web_tools_state import WEB_TOOL_NAMES
from web_tools_state import filter_mcp_tool_defs
from web_tools_state import filter_mcp_tool_index


_LOG_FILE_RE = re.compile(r"^Log file:\s*(.+)$")
_TURN_AGENT_RE = re.compile(r"^\[TURN\s+(\d+)\]\s+Agent:\s*(.*)$")
_TURN_METRICS_RE = re.compile(r"^\[TURN\s+(\d+)\]\s+tokens=(\d+)\s+tps=([0-9.]+)$")
_TEST_COMPLETE_RE = re.compile(r"^\[(TEST COMPLETE|ALL TESTS COMPLETE|TEST RUN STOPPED)\]\s+(.+)$")

_LOG_DIR = get_logs_dir()
_WEB_DIR = Path(
    os.environ.get(
        "KORE_KOREAGENT_UI_DIR",
        str(Path(__file__).resolve().parents[3] / "KoreUI" / "KoreAgent" / "ui"),
    )
).resolve()
_UI_ELEMENTS_ASSETS = Path(
    os.environ.get(
        "KORE_UIELEMENTS_ASSETS_DIR",
        str(Path(__file__).resolve().parents[3] / "UIElements" / "assets"),
    )
).resolve()
_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
_COMPACT_FILL_PCT = 0.65
_QUEUE_PREVIEW_LIMIT = 10
_LOG_POLL_SECS = 1.0
_LOG_TAIL_LINES = 200


app = FastAPI(title="KoreAgent API", version=SUITE_VERSION)


@app.get("/__endpoint_manifest", include_in_schema=False)
def endpoint_manifest() -> dict:
    return build_endpoint_manifest(app, service_key="koreagent", service_label="KoreAgent")


app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_korechat_base_url() -> str | None:
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
    web_dir=_WEB_DIR,
    ui_elements_assets=_UI_ELEMENTS_ASSETS,
    get_korechat_base_url=_get_korechat_base_url,
)


register_status_routes(
    app,
    get_active_host=get_active_host,
    get_active_model=get_active_model,
    get_active_num_ctx=get_active_num_ctx,
    get_active_backend=get_active_backend,
    get_ollama_ps_rows=get_ollama_ps_rows,
    get_startup_state=get_startup_state_snapshot,
    version=SUITE_VERSION,
)


@app.get("/api/completions")
@app.get("/completions", include_in_schema=False)
def get_completions():
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

    test_dir = get_test_prompts_dir()
    test_files = sorted(p.stem for p in test_dir.glob("*.json")) if test_dir.exists() else []
    enabled_tasks, _ = _get_scheduler_snapshot()
    task_names = [t.get("name", "") for t in enabled_tasks if t.get("name")]
    try:
        models = list_ollama_models()
    except Exception:
        models = []
    return {
        "sessions": sessions,
        "test_files": test_files,
        "task_names": task_names,
        "models": models,
    }


@app.get("/api/settings/sandbox")
@app.get("/settings/sandbox", include_in_schema=False)
def settings_sandbox_get():
    return {"sandbox": get_sandbox_enabled()}


@app.post("/api/settings/sandbox")
@app.post("/settings/sandbox", include_in_schema=False)
def settings_sandbox_post(enabled: bool):
    set_sandbox_enabled(enabled)
    return {"sandbox": get_sandbox_enabled()}


@app.get("/api/settings/webskills")
@app.get("/settings/webskills", include_in_schema=False)
def settings_webskills_get():
    return {"webskills": get_web_skills_enabled()}


@app.post("/api/settings/webskills")
@app.post("/settings/webskills", include_in_schema=False)
def settings_webskills_post(enabled: bool):
    set_web_skills_enabled(enabled)
    if not enabled:
        current = get_selected_tools()
        filtered = [name for name in current if name not in WEB_TOOL_NAMES]
        if filtered != current:
            set_selected_tools(filtered)
    return {"webskills": get_web_skills_enabled()}


@app.get("/api/settings/llmdirect")
@app.get("/settings/llmdirect", include_in_schema=False)
def settings_llmdirect_get():
    return {"llmdirect": get_llm_direct_enabled()}


@app.post("/api/settings/llmdirect")
@app.post("/settings/llmdirect", include_in_schema=False)
def settings_llmdirect_post(enabled: bool):
    set_llm_direct_enabled(enabled)
    return {"llmdirect": get_llm_direct_enabled()}


_session_service = SessionService(
    compact_fill_pct=_COMPACT_FILL_PCT,
    kc_client=_kc_client,
    conversation_history_cls=ConversationHistory,
    session_context_cls=SessionContext,
    hydrate_session_state=hydrate_session_state,
    scratchpad_clear=scratchpad_clear,
    scratchpad_restore_key=scratchpad_restore_key,
    get_scratchpad_store=get_scratchpad_store,
    build_persisted_scratchpad_payload=build_persisted_scratchpad_payload,
    get_persisted_datasets_payload=get_persisted_datasets_payload,
    delete_persisted_session_datasets=delete_persisted_session_datasets,
    request_stop=request_stop,
    task_queue=task_queue,
    run_event_queues=get_run_event_queues(),
    run_queues_lock=get_run_queues_lock(),
    queue_run_event=queue_run_event,
    finish_run_event_queue=finish_run_event_queue,
)


_skills_routes = register_skills_routes(
    app,
    config_getter=_get_config,
    workspace_root=_WORKSPACE_ROOT,
    get_web_skills_enabled=get_web_skills_enabled,
    filter_web_skills=_filter_web_skills,
    build_tool_definitions=build_tool_definitions,
    get_selected_tools=get_selected_tools,
    always_on_tool_names=ALWAYS_ON_TOOL_NAMES,
    filter_mcp_tool_defs=filter_mcp_tool_defs,
    filter_mcp_tool_index=filter_mcp_tool_index,
    mcp_client_module=mcp_client,
    build_catalog_gates=build_catalog_gates,
    execute_tool_call=execute_tool_call,
)

skills_catalog_get = _skills_routes["skills_catalog_get"]
skills_source_get  = _skills_routes["skills_source_get"]
skills_invoke_post = _skills_routes["skills_invoke_post"]


register_session_switch_routes(
    app,
    validate_session_id=_validate_session_id,
    set_pending_switch=_set_pending_switch,
)


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


register_input_history_routes(
    app,
    validate_session_id=_validate_session_id,
    session_service=_session_service,
    history_limit=20,
)


register_session_routes(
    app,
    config_getter=_get_config,
    validate_session_id=_validate_session_id,
    make_run_event_queue=make_run_event_queue,
    queue_run_event=queue_run_event,
    finish_run_event_queue=finish_run_event_queue,
    handle_stoprun_immediate=_session_service.handle_stoprun_immediate,
    load_session=_session_service.load_session,
    save_session=_session_service.save_session,
    flush_scratch_session=_session_service.flush_scratch_to_session,
    create_session_context=_session_service.create_session_context,
    clear_session_scratch=scratchpad_clear,
    make_slash_context=SlashCommandContext,
    handle_slash=handle_slash,
    push_log_line=lambda line: push_log_line(line),
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
    run_queues=get_run_event_queues(),
    run_queues_lock=get_run_queues_lock(),
    sse=lambda data: _sse(data),
    delete_session_state=_session_service.delete_session_state,
    kc_save_turn=_session_service.kc_save_turn,
    get_session_turns=_session_service.get_session_turns,
    get_session_conversation=_session_service.kc_get_conversation_for_session,
    kc_set_session_name=_session_service.kc_set_session_name,
    get_llm_direct_enabled=get_llm_direct_enabled,
    call_llm_chat=call_llm_chat,
)


_load_session = _session_service.load_session
_save_session = _session_service.save_session
_create_session_context = _session_service.create_session_context


def push_log_line(line: str) -> None:
    _push_log_line(line, latest_log_path_getter=get_latest_log_path)


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
    scratchpad_clear(session_id)
    delete_persisted_session_datasets(session_id)
    clear_session_tools_active(session_id)
    conv = _kc_get_conversation_for_session(session_id)
    if conv is not None:
        _kc_delete(f"/conversations/{conv['id']}")


register_log_routes(
    app,
    log_dir=_LOG_DIR,
    shutdown_event_getter=get_shutdown_event,
    log_poll_secs=_LOG_POLL_SECS,
    sse=lambda data: _sse(data),
    set_latest_log_path=_set_latest_log_path,
    get_latest_log_file=lambda: _get_latest_log_file(_LOG_DIR),
    get_log_backfill=lambda: _get_log_backfill(log_dir=_LOG_DIR, tail_lines=_LOG_TAIL_LINES, set_latest_log_path=_set_latest_log_path),
    log_subscribers=get_log_subscribers(),
    log_subscribers_lock=get_log_subscribers_lock(),
)


register_korechat_proxy_routes(
    app,
    validate_session_id=_validate_session_id,
    kc_get_async=_session_service.kc_get_async,
    kc_post_async=_session_service.kc_post_async,
)
