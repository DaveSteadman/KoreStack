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
import socket
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import llm_client as llm_client
from run_helpers import run_prompt_batch
from task_korechat import load_task_turns
from task_korechat import save_task_turn
from input_layer.server import app
from input_layer.server import get_startup_state_snapshot
from input_layer.server import push_log_line
from input_layer.server import setup as api_setup
from input_layer.server import set_startup_state_snapshot
from input_layer.server import update_startup_state
from input_layer.koreconv_input import start_koreconv_loop
from orchestration import OrchestratorConfig
from utils.runtime_logger import SessionLogger
from utils.runtime_logger import create_log_file_path
from scheduler.scheduler import initial_last_run
from scheduler.scheduler import is_task_due
from scheduler.scheduler import load_schedules_dir
from scheduler.scheduler import task_queue
from scheduler.shared_state import SchedulerSharedState
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
    background_startup: object | None = None,
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
    scheduler_state = SchedulerSharedState(enabled_tasks=enabled_tasks, last_run=last_run)
    set_startup_state_snapshot(
        {
            **get_startup_state_snapshot(),
            "service_status": "starting",
            "message":        "HTTP server starting",
            "started_at":     datetime.now().isoformat(timespec="seconds"),
        }
    )

    # Publish shared state to the API module.
    api_setup(
        config         = config,
        enabled_tasks  = enabled_tasks,
        last_run       = last_run,
        shutdown_event = shutdown,
        scheduler_state = scheduler_state,
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
                scheduler_state.replace_enabled_tasks([t for t in _all if t.get("enabled", True)])
            except FileNotFoundError:
                scheduler_state.replace_enabled_tasks([])

            enabled_tasks, last_run = scheduler_state.snapshot()
            for t in enabled_tasks:
                scheduler_state.ensure_last_run(t["name"], initial_last_run(t, now))

            enabled_tasks, last_run = scheduler_state.snapshot()

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

                def _run_task(_name=name, _prompts=tuple(prompts), _when=now, _output_template=output_template) -> None:
                    push_log_line(f"[SCHEDULER] Starting task: {_name}")
                    try:
                        run_log_path = create_log_file_path(log_dir=_LOG_DIR)
                        with SessionLogger(run_log_path) as run_logger:
                            for prompt_text in _prompts:
                                current = prompt_text.get("prompt", "") if isinstance(prompt_text, dict) else str(prompt_text)
                                if current:
                                    push_log_line(f"[SCHEDULER] {_name}: {current[:80]}")

                            enriched_prompts = list(_prompts)

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
                                session_id   = f"task_{_name}",
                                persist_path=None,
                                config       = config,
                                logger       = run_logger,
                                quiet        = True,
                                max_turns    = 10,
                                seeded_turns = load_task_turns(_name),
                                save_turn_fn = lambda user_text, agent_text, _task_name=_name: save_task_turn(_task_name, user_text, agent_text),
                            )
                            for item in results:
                                tps_str = f"{item['tps']:.1f}" if item["tps"] > 0 else "0"
                                push_log_line(f"[SCHEDULER] {_name}: done [{item['prompt_tokens']:,} tok, {tps_str} tok/s]")

                    except Exception as exc:
                        push_log_line(f"[SCHEDULER] {_name} error: {exc}")
                    scheduler_state.set_last_run(_name, _when)
                    push_log_line(f"[SCHEDULER] Task '{_name}' completed.")

                if task_queue.enqueue(name, "scheduled", _run_task):
                    scheduler_state.set_last_run(name, now)
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

    background_thread: threading.Thread | None = None
    if callable(background_startup):
        def _run_background_startup() -> None:
            try:
                background_startup()
            except Exception as exc:
                message = f"[API] Background startup failed: {exc}"
                logger.log_file_only(message)
                push_log_line(message)
                update_startup_state(
                    service_status = "degraded",
                    message        = "Background startup failed",
                )

        background_thread = threading.Thread(
            target = _run_background_startup,
            daemon = True,
            name   = "api-background-startup",
        )
        background_thread.start()

    push_log_line(f"[API] Server starting on http://{host}:{port}")
    update_startup_state(
        service_status = "starting",
        message        = "HTTP server accepting requests; dependency warmup continues in background",
    )
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
        if background_thread is not None:
            try:
                background_thread.join(timeout=1)
            except KeyboardInterrupt:
                pass
        print("\nAPI server stopped.", flush=True)
        logger.log("[API] Server stopped.")
