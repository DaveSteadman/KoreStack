# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Server startup for KoreAgent.
#
# Provides run_api_mode(), which is the main entry point called by main.py:
#   - Loads schedules and initialises the task queue
#   - Wires up server.py's push_log_line as the LLM-call log sink
#   - Starts a background scheduler thread that hot-reloads and fires scheduled tasks
#   - Launches uvicorn to serve the FastAPI app
#
# Related modules:
#   - server.py             -- FastAPI app, all endpoints, setup(), push_log_line()
#   - main.py             -- creates config and calls run_api_mode()
#   - scheduler.py        -- task_queue, load_schedules_dir, is_task_due
#   - orchestration.py    -- orchestrate_prompt, OrchestratorConfig
#   - runtime_logger.py   -- SessionLogger, create_log_file_path
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import asyncio
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import llm_client as llm_client
from run_helpers import run_prompt_batch
from input_layer.server import app
from input_layer.server import push_log_line
from input_layer.server import setup as api_setup
from input_layer.server import _load_session
from input_layer.server import _save_session
from input_layer.koreconv_input import start_koreconv_loop
from input_layer.koreconv_input import _get_base_url as _kc_get_base_url
from input_layer.koreconv_input import _http_get as _kc_http_get
from input_layer.koreconv_input import _http_post as _kc_http_post
from orchestration import OrchestratorConfig
from utils.runtime_logger import SessionLogger
from utils.runtime_logger import create_log_file_path
from scheduler.scheduler import initial_last_run
from scheduler.scheduler import is_task_due
from scheduler.scheduler import load_schedules_dir
from scheduler.scheduler import task_queue
from utils.workspace_utils import get_logs_dir
from utils.workspace_utils import get_schedules_dir


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
_SCHEDULES_DIR        = get_schedules_dir()
_LOG_DIR              = get_logs_dir()
_SCHEDULER_POLL_SECS  = 30
_DEFAULT_PORT         = 8000
_DEFAULT_HOST         = "0.0.0.0"


# ====================================================================================================
# MARK: SERVER STARTUP
# ====================================================================================================

def _can_bind(host: str, port: int) -> tuple[bool, str]:
    """Return whether the TCP listen socket can be bound, plus an optional reason."""
    bind_host = "" if host == "0.0.0.0" else host
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((bind_host, int(port)))
        return True, ""
    except OSError as exc:
        if getattr(exc, "winerror", None) == 10048:
            return False, f"Port {port} is already in use."
        return False, str(exc)
    finally:
        sock.close()

def run_api_mode(
    config: OrchestratorConfig,
    logger: SessionLogger,
    log_path: Path,
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
) -> None:
    """Launch the FastAPI server with background scheduler.

    Blocks until a stop signal is received or the process is otherwise terminated.
    All log output is broadcast to connected /logs/stream SSE clients as well
    as written to the log file.
    """
    import uvicorn

    can_bind, bind_reason = _can_bind(host, port)
    if not can_bind:
        message = f"[API] Startup aborted: {bind_reason} Close the existing server or use --agentport <port>."
        print(f"\n{message}", flush=True)
        logger.log_file_only(message)
        return

    shutdown = threading.Event()

    # Wire push_log_line into the LLM call logger so every orchestration log
    # line is also broadcast over the /logs/stream SSE endpoint.
    def _log_sink(text: str) -> None:
        logger.log_file_only(text)
        push_log_line(text)

    llm_client.register_llm_call_logger(_log_sink)

    # Load schedules.
    tasks         = load_schedules_dir(_SCHEDULES_DIR)
    enabled_tasks = [t for t in tasks if t.get("enabled", True)]
    _startup      = datetime.now()
    last_run: dict[str, datetime | None] = {
        t["name"]: initial_last_run(t, _startup)
        for t in enabled_tasks
    }

    # Publish shared state to the API module.
    api_setup(
        config         = config,
        enabled_tasks  = enabled_tasks,
        last_run       = last_run,
        shutdown_event = shutdown,
    )

    # -----------------------------------------------------------------------
    # Background scheduler thread.
    # -----------------------------------------------------------------------
    def _scheduler_loop() -> None:
        while not shutdown.is_set():
            now = datetime.now()
            # Hot-reload schedules from disk on every poll cycle so task CRUD changes
            # (create/delete/enable/disable/reschedule) take effect without a restart.
            # enabled_tasks[:] mutates the shared list in-place so the API endpoints
            # (which reference the same list object via server.py's _enabled_tasks) see
            # the updated view automatically.
            try:
                _all = load_schedules_dir(_SCHEDULES_DIR)
                enabled_tasks[:] = [t for t in _all if t.get("enabled", True)]
            except FileNotFoundError:
                enabled_tasks[:] = []
            for t in enabled_tasks:
                if t["name"] not in last_run:
                    last_run[t["name"]] = initial_last_run(t, now)

            for task in enabled_tasks:
                if shutdown.is_set():
                    break
                name    = task["name"]
                prompts = task.get("prompts", [])
                if not prompts:
                    continue
                if not is_task_due(task, last_run.get(name), now):
                    continue

                output_template = task.get("output_template", "").strip()

                def _run_task(_name=name, _prompts=list(prompts), _when=now, _output_template=output_template) -> None:
                    push_log_line(f"[SCHEDULER] Starting task: {_name}")
                    try:
                        run_log_path = create_log_file_path(log_dir=_LOG_DIR)
                        with SessionLogger(run_log_path) as run_logger:
                            for prompt_text in _prompts:
                                current = prompt_text.get("prompt", "") if isinstance(prompt_text, dict) else str(prompt_text)
                                if current:
                                    push_log_line(f"[SCHEDULER] {_name}: {current[:80]}")

                            # Item 7: Fetch prior run output from KoreChat for continuity.
                            # This lets the model know what it produced last time so it can
                            # build on, update, or cross-reference prior results.
                            kc_base = _kc_get_base_url()
                            prior_context_prefix = ""
                            if kc_base:
                                try:
                                    prior_conv = _kc_http_get(kc_base, f"/conversations/by-external-id/task:{_name}")
                                    if prior_conv and prior_conv.get("id"):
                                        msgs = _kc_http_get(
                                            kc_base,
                                            f"/conversations/{prior_conv['id']}/messages?limit=10",
                                        ) or []
                                        last_out = next(
                                            (m for m in reversed(msgs) if m.get("direction") == "outbound"),
                                            None,
                                        )
                                        if last_out:
                                            ts = (last_out.get("created_at") or "")[:10]
                                            snippet = (last_out.get("content") or "")[:400].strip()
                                            prior_context_prefix = (
                                                f"[Previous run ({ts})]:\n{snippet}\n\n"
                                            )
                                            push_log_line(f"[SCHEDULER] {_name}: loaded prior context ({len(snippet)} chars)")
                                except Exception as exc:
                                    push_log_line(f"[SCHEDULER] {_name}: could not fetch prior context: {exc}")

                            # Prepend prior run context to the first prompt if available.
                            enriched_prompts = list(_prompts)
                            if prior_context_prefix and enriched_prompts:
                                first = enriched_prompts[0]
                                if isinstance(first, dict):
                                    enriched_prompts[0] = dict(first, prompt=prior_context_prefix + first.get("prompt", ""))
                                else:
                                    enriched_prompts[0] = prior_context_prefix + str(first)

                            # Append output_template to every prompt so formatting/save
                            # instructions are separated from the retrieval instructions.
                            if _output_template:
                                enriched_prompts = [
                                    dict(p, prompt=p.get("prompt", "") + "\n\n" + _output_template)
                                    if isinstance(p, dict)
                                    else str(p) + "\n\n" + _output_template
                                    for p in enriched_prompts
                                ]

                            results = run_prompt_batch(
                                enriched_prompts,
                                session_id=f"task_{_name}",
                                persist_path=None,
                                config=config,
                                logger=run_logger,
                                quiet=True,
                                max_turns=10,
                            )
                            for item in results:
                                tps_str = f"{item['tps']:.1f}" if item["tps"] > 0 else "0"
                                push_log_line(f"[SCHEDULER] {_name}: done [{item['prompt_tokens']:,} tok, {tps_str} tok/s]")

                            # Item 3: Post task output to KoreChat so prior runs are available
                            # to future runs and to human review via the KoreChat UI.
                            if kc_base:
                                try:
                                    full_output = "\n\n".join(
                                        r.get("response", "").strip()
                                        for r in results
                                        if r.get("response", "").strip()
                                    )
                                    if full_output:
                                        # Re-fetch (or create) the task's KoreChat conversation.
                                        task_conv = _kc_http_get(kc_base, f"/conversations/by-external-id/task:{_name}")
                                        if not task_conv:
                                            task_conv = _kc_http_post(kc_base, "/conversations", {
                                                "external_id":  f"task:{_name}",
                                                "subject":      f"Scheduled: {_name}",
                                                "channel_type": "scheduled",
                                            })
                                        if task_conv and task_conv.get("id"):
                                            _kc_http_post(
                                                kc_base,
                                                f"/conversations/{task_conv['id']}/messages",
                                                {
                                                    "direction":      "outbound",
                                                    "content":        full_output,
                                                    "sender_display": "agent",
                                                    "status":         "sent",
                                                },
                                            )
                                            push_log_line(f"[SCHEDULER] {_name}: output posted to KoreChat")
                                except Exception as exc:
                                    push_log_line(f"[SCHEDULER] {_name}: could not post to KoreChat: {exc}")

                    except Exception as exc:
                        push_log_line(f"[SCHEDULER] {_name} error: {exc}")
                    last_run[_name] = _when
                    push_log_line(f"[SCHEDULER] Task '{_name}' completed.")

                if task_queue.enqueue(name, "scheduled", _run_task):
                    last_run[name] = now
                    push_log_line(f"[SCHEDULER] Task '{name}' queued.")

            for _ in range(_SCHEDULER_POLL_SECS * 2):
                if shutdown.is_set():
                    break
                time.sleep(0.5)

    sched_thread = threading.Thread(target=_scheduler_loop, daemon=True, name="api-scheduler")
    sched_thread.start()

    start_koreconv_loop(
        config               = config,
        push_log_line        = push_log_line,
        task_queue           = task_queue,
        create_log_file_path = create_log_file_path,
        log_dir              = _LOG_DIR,
        session_logger_cls   = SessionLogger,
        shutdown             = shutdown,
    )

    push_log_line(f"[API] Server starting on http://{host}:{port}")
    print(f"\nKoreAgent - http://{host}:{port}  (send interrupt to stop)", flush=True)
    print(f"Web UI:   http://localhost:{port}/", flush=True)

    uvicorn_config = uvicorn.Config(
        app     = app,
        host    = host,
        port    = port,
        log_level = "warning",  # suppress uvicorn access noise; our own logger handles it
    )
    server = uvicorn.Server(uvicorn_config)

    def _serve_in_current_thread() -> None:
        if sys.platform != "win32":
            server.run()
            return

        loop = asyncio.SelectorEventLoop() if hasattr(asyncio, "SelectorEventLoop") else asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        def _exception_handler(loop_obj: asyncio.AbstractEventLoop, context: dict) -> None:
            exc    = context.get("exception")
            handle = context.get("handle")
            callback = getattr(handle, "_callback", None)
            cb_name  = getattr(callback, "__qualname__", repr(callback))
            if (
                isinstance(exc, ConnectionResetError)
                and getattr(exc, "winerror", None) == 10054
                and "_call_connection_lost" in str(cb_name)
            ):
                return
            loop_obj.default_exception_handler(context)

        loop.set_exception_handler(_exception_handler)
        try:
            loop.run_until_complete(server.serve())
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    try:
        _serve_in_current_thread()
    except KeyboardInterrupt:
        print("\nShutting down...", flush=True)
        logger.log_file_only("[API] Shutdown requested.")
        server.should_exit = True
    finally:
        shutdown.set()
        try:
            task_queue.stop()
        except Exception as exc:
            print(f"[API] Warning: error stopping task queue: {exc}", flush=True)
        server.should_exit = True
        try:
            sched_thread.join(timeout=2)
        except KeyboardInterrupt:
            pass
        print("\nAPI server stopped.", flush=True)
        logger.log("[API] Server stopped.")
