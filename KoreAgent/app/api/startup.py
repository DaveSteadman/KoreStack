# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Server startup for KoreAgent.
#
# Provides run_api_mode(), which is the main entry point called by main.py:
#   - Initialises the shared task queue used by interactive Agent work
#   - Wires up server.py's push_log_line as the LLM-call log sink
#   - Launches uvicorn to serve the FastAPI app
#
# Related modules:
#   - api/app.py            -- FastAPI app, all endpoints, setup(), push_log_line()
#   - main.py             -- creates config and calls run_api_mode()
#   - execution_queue.py  -- task_queue
#   - agent/orchestration/engine.py -- orchestrate_prompt, OrchestratorConfig
#   - runtime_logger.py   -- SessionLogger, create_log_file_path
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import asyncio
import logging
import socket
import sys
import threading
from datetime import datetime
from pathlib import Path

import llm_client as llm_client
from api.app import app
from api.app import get_startup_state_snapshot
from api.app import push_log_line
from api.app import setup as api_setup
from api.app import set_startup_state_snapshot
from api.app import update_startup_state
from input_layer.koreconv_input import start_koreconv_loop
from agent.orchestration.engine import OrchestratorConfig
from utils.runtime_logger import SessionLogger
from utils.runtime_logger import create_log_file_path
from execution_queue import task_queue
from utils.workspace_utils import get_logs_dir


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
_LOG_DIR              = get_logs_dir()
_DEFAULT_PORT         = 8000
_DEFAULT_HOST         = "0.0.0.0"
_SERVICE_LOG          = logging.getLogger("koreagent.service")
_LLM_HEALTH_INTERVAL_S  = 15.0


# ====================================================================================================
# MARK: SERVER STARTUP
# ====================================================================================================

def _monitor_llm_dependency(
    *,
    shutdown: threading.Event,
    get_active_backend,
    get_active_host,
    get_active_model,
    is_ollama_running,
    list_ollama_models,
) -> None:
    """Keep the LLM dependency status current after the one-off startup check."""
    consecutive_failures = 0
    last_ready_at        = None

    while not shutdown.is_set():
        checked_at = datetime.now().isoformat(timespec="seconds")
        backend    = get_active_backend()
        host       = get_active_host()
        model      = get_active_model()

        if backend != "ollama":
            update_startup_state(
                dependencies = {"llm": {
                    "status":               "ready",
                    "detail":               f"{backend} backend at {host}",
                    "last_checked_at":      checked_at,
                    "consecutive_failures": 0,
                }}
            )
            shutdown.wait(_LLM_HEALTH_INTERVAL_S)
            continue

        try:
            if not is_ollama_running(host):
                raise RuntimeError(f"Ollama is not reachable at {host}")
            models = list_ollama_models(host, start_if_needed=False)
            if model and model not in models:
                raise RuntimeError(f"Configured model '{model}' is not available at {host}")
        except Exception as exc:
            consecutive_failures += 1
            update_startup_state(
                dependencies = {"llm": {
                    "status":               "degraded",
                    "detail":               str(exc),
                    "last_checked_at":      checked_at,
                    "last_ready_at":        last_ready_at,
                    "consecutive_failures": consecutive_failures,
                }}
            )
        else:
            consecutive_failures = 0
            last_ready_at        = checked_at
            update_startup_state(
                dependencies = {"llm": {
                    "status":               "ready",
                    "detail":               f"{model} on {host}",
                    "last_checked_at":      checked_at,
                    "last_ready_at":        last_ready_at,
                    "consecutive_failures": 0,
                }}
            )

        shutdown.wait(_LLM_HEALTH_INTERVAL_S)

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
    """Launch the FastAPI server for interactive Agent work.

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
        _SERVICE_LOG.error(message)
        return

    shutdown = threading.Event()

    # Wire push_log_line into the LLM call logger so every orchestration log
    # line is also broadcast over the /logs/stream SSE endpoint.
    def _log_sink(text: str) -> None:
        logger.log_file_only(text)
        push_log_line(text)

    llm_client.register_llm_call_logger(_log_sink)

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
        shutdown_event = shutdown,
    )

    start_koreconv_loop(
        config               = config,
        push_log_line        = push_log_line,
        task_queue           = task_queue,
        create_log_file_path = create_log_file_path,
        log_dir              = _LOG_DIR,
        session_logger_cls   = SessionLogger,
        shutdown             = shutdown,
    )

    llm_monitor_thread = threading.Thread(
        target = _monitor_llm_dependency,
        kwargs = {
            "shutdown":           shutdown,
            "get_active_backend": llm_client.get_active_backend,
            "get_active_host":    llm_client.get_active_host,
            "get_active_model":   llm_client.get_active_model,
            "is_ollama_running":  llm_client.is_ollama_running,
            "list_ollama_models": llm_client.list_ollama_models,
        },
        daemon = True,
        name   = "llm-dependency-monitor",
    )
    llm_monitor_thread.start()

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
    _SERVICE_LOG.info("startup host=%s port=%s", host, port)
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
        _SERVICE_LOG.info("shutdown requested")
        server.should_exit = True
    finally:
        shutdown.set()
        try:
            task_queue.stop()
        except Exception as exc:
            print(f"[API] Warning: error stopping task queue: {exc}", flush=True)
        server.should_exit = True
        if background_thread is not None:
            try:
                background_thread.join(timeout=1)
            except KeyboardInterrupt:
                pass
        print("\nAPI server stopped.", flush=True)
        logger.log("[API] Server stopped.")
        _SERVICE_LOG.info("stopped")
