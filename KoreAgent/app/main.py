# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# CLI entrypoint for KoreAgent.
#
# Starts the FastAPI server with the web UI and background scheduler.
#
# Core orchestration pipeline lives in agent/orchestration/.
# Server startup lives in api/startup.py.
#
# Related modules:
#   - agent/orchestration/engine.py -- OrchestratorConfig, orchestrate_prompt
#   - api/startup.py               -- run_api_mode (FastAPI + uvicorn + scheduler)
#   - llm_client.py              -- server management and LLM calls
#   - skills_catalog_builder.py -- load_skills_payload, tool definitions
#   - utils/runtime_logger.py   -- SessionLogger, create_log_file_path
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import argparse
import ctypes
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _hidden_windows_creation_flags() -> int:
    """Prevent a console flash when re-executing with the project virtualenv."""
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ----------------------------------------------------------------------------------------------------
def _maybe_reexec_into_project_venv() -> None:
    """Prefer the repository virtualenv interpreter when one exists.

    The app is often launched as `python app/main.py`, which depends on whichever
    global `python` happens to be first on PATH. That can diverge from the project's
    `.venv`, causing ambient system info and package resolution to be inconsistent.

    To keep startup automatic and deterministic, launch the repository `.venv`
    interpreter if it exists and we are not already running inside it. The parent
    process stays attached to the terminal and waits for the child so the shell
    does not regain control while the real server process is still running.
    Set MAF_SKIP_AUTO_VENV=1 to bypass.
    """
    if os.environ.get("MAF_SKIP_AUTO_VENV") == "1":
        return

    repo_root = Path(__file__).resolve().parent.parent
    venv_python = (
        repo_root / ".venv" / "Scripts" / "python.exe"
        if sys.platform.startswith("win")
        else repo_root / ".venv" / "bin" / "python"
    )
    if not venv_python.exists():
        return

    try:
        current_python = Path(sys.executable).resolve()
        target_python = venv_python.resolve()
    except Exception:
        return

    if current_python == target_python:
        return

    child_env = dict(os.environ)
    child_env["MAF_SKIP_AUTO_VENV"] = "1"
    cmd = [str(target_python), str(Path(__file__).resolve()), *sys.argv[1:]]

    child = subprocess.Popen(cmd, env=child_env, creationflags=_hidden_windows_creation_flags())

    job_handle = None
    if sys.platform == "win32":
        try:
            job_handle = _attach_child_to_kill_on_close_job(child.pid)
        except Exception:
            job_handle = None

    try:
        raise SystemExit(child.wait())
    except KeyboardInterrupt:
        # If the child received the same terminal interrupt it should exit on its own.
        try:
            raise SystemExit(child.wait(timeout=5))
        except subprocess.TimeoutExpired as exc:
            child.terminate()
            raise SystemExit(child.wait(timeout=5)) from exc
    finally:
        if job_handle is not None:
            ctypes.windll.kernel32.CloseHandle(job_handle)


# ----------------------------------------------------------------------------------------------------
def _attach_child_to_kill_on_close_job(pid: int):
    """On Windows, tie the child process lifetime to this launcher process."""
    kernel32 = ctypes.windll.kernel32

    PROCESS_TERMINATE = 0x0001
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_SET_INFORMATION = 0x0200
    PROCESS_QUERY_INFORMATION = 0x0400
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JobObjectExtendedLimitInformation = 9

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError()

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

    ok = kernel32.SetInformationJobObject(
        job,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        kernel32.CloseHandle(job)
        raise ctypes.WinError()

    process_handle = kernel32.OpenProcess(
        PROCESS_TERMINATE | PROCESS_SET_QUOTA | PROCESS_SET_INFORMATION | PROCESS_QUERY_INFORMATION,
        False,
        int(pid),
    )
    if not process_handle:
        kernel32.CloseHandle(job)
        raise ctypes.WinError()

    try:
        ok = kernel32.AssignProcessToJobObject(job, process_handle)
        if not ok:
            kernel32.CloseHandle(job)
            raise ctypes.WinError()
    finally:
        kernel32.CloseHandle(process_handle)

    return job


_maybe_reexec_into_project_venv()

import llm_client as llm_client
from api.startup import run_api_mode
from api.app import update_startup_state
from KoreCommon.service_logging import configure_service_logging
from llm_client import format_running_model_report
from llm_client import get_llm_timeout
from llm_client import register_llm_call_logger
from agent.orchestration.engine import OrchestratorConfig
from agent.orchestration.engine import resolve_execution_model
from skills_catalog_builder import load_skills_payload
import mcp_client as _mcp_client
from utils.runtime_logger import create_log_file_path
from utils.runtime_logger import SessionLogger
from utils.workspace_utils import get_agent_config_file
from utils.workspace_utils import get_controldata_dir
from utils.workspace_utils import get_logs_dir
from utils.workspace_utils import get_user_data_dir
from utils.workspace_utils import load_runtime_config


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
DEFAULT_NUM_CTX      = 131072
MAX_ITERATIONS       = 25   # safety cap; model exits naturally via native tool calling
SKILLS_CATALOG_PATH  = Path(__file__).resolve().parent / "skills" / "skills_catalog.json"
LOG_DIR              = get_logs_dir()
DEFAULTS_FILE        = get_agent_config_file()

# Keys accepted from the runtime defaults file - must match the argparse dest names exactly.
_DEFAULTS_KEYS = {"model", "ctx", "agentport", "llmhost"}

# All valid keys in the runtime defaults file - superset of _DEFAULTS_KEYS.
# Keys here that are not in _DEFAULTS_KEYS are read directly by skills or slash commands
# and are not passed through argparse.
_KNOWN_KEYS = _DEFAULTS_KEYS | {
    "korechaturl",
    "DataRootFolder",
    "ControlDataFolder",
    "UserDataFolder",
    "mcp_connections",
}


# ====================================================================================================
# MARK: DEFAULTS LOADING
# ====================================================================================================
def _load_defaults() -> dict:
    # Returns only recognised keys from the runtime defaults file.
    # Prints a startup warning listing any keys present in the file but not recognised.
    try:
        raw = load_runtime_config()
        if not isinstance(raw, dict):
            return {}
        accepted  = {k: v for k, v in raw.items() if k in _DEFAULTS_KEYS}
        unknown   = [k for k in raw if k not in _KNOWN_KEYS]
        if unknown:
            known_list = ", ".join(sorted(_KNOWN_KEYS))
            print(
                f"[defaults] Unrecognised key(s) ignored: {', '.join(sorted(unknown))}. "
                f"Recognised keys: {known_list}.",
                flush=True,
            )
        return accepted
    except Exception:
        return {}


# ====================================================================================================
# MARK: CLI
# ====================================================================================================
def parse_main_args() -> argparse.Namespace:
    # Priority: factory defaults < runtime defaults file < command-line args.
    file_defaults = _load_defaults()

    parser = argparse.ArgumentParser(description="KoreAgent - web UI entrypoint.")
    parser.add_argument(
        "--model",
        type=str,
        default="20b",
        help="Ollama model alias or tag to use (e.g. '20b', 'llama3:8b').",
    )
    parser.add_argument(
        "--ctx",
        type=int,
        default=DEFAULT_NUM_CTX,
        help="Context window for LLM calls.",
    )
    parser.add_argument(
        "--agentport",
        type=int,
        default=8000,
        metavar="PORT",
        help="Port for the web UI server (default 8000). Always binds to 0.0.0.0.",
    )
    parser.add_argument(
        "--llmhost",
        type=str,
        default=os.environ.get("LLMHOST", os.environ.get("OLLAMAHOST", llm_client.DEFAULT_OLLAMAHOST)),
        metavar="URL",
        help="Inference server host URL or alias. Aliases: 'lmstudio' (http://localhost:1234), "
             "'local' (http://localhost:11434). Also read from LLMHOST env var.",
    )
    # Apply file defaults between factory defaults and CLI; set_defaults() is overridden
    # by any explicit CLI value but overrides argparse's own default= values.
    if file_defaults:
        parser.set_defaults(**file_defaults)
    return parser.parse_args()


# ====================================================================================================`r`n# MARK: MAIN ENTRYPOINT
# ====================================================================================================
def main() -> None:
    service_log_path = configure_service_logging("koreagent", "INFO")
    service_logger   = logging.getLogger("koreagent.service")
    service_logger.info("starting")
    try:
        args     = parse_main_args()
        log_path = create_log_file_path(log_dir=LOG_DIR)
        with SessionLogger(log_path) as logger:
            try:
                _run(args, logger, log_path)
            finally:
                service_logger.info("stopping log=%s", service_log_path)
    except Exception:
        service_logger.exception("startup failed")
        raise
    finally:
        service_logger.info("shutdown complete")


# ----------------------------------------------------------------------------------------------------
def _run(args, logger, log_path) -> None:
    # When running in batch mode Windows defaults stdout
    # to cp1252, which cannot encode the tick/cross characters printed in the
    # status block.  Reconfigure to UTF-8 early so all print() calls survive.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    register_llm_call_logger(logger.log_file_only)

    # Set the active host once; all subsequent LLM calls use this value.
    llm_client.configure_host(args.llmhost)

    skills_payload = load_skills_payload(SKILLS_CATALOG_PATH)
    catalog_mtime  = SKILLS_CATALOG_PATH.stat().st_mtime if SKILLS_CATALOG_PATH.exists() else 0.0

    config = OrchestratorConfig(
        resolved_model      = args.model,
        num_ctx             = args.ctx,
        max_iterations      = MAX_ITERATIONS,
        skills_payload      = skills_payload,
        skills_catalog_path = SKILLS_CATALOG_PATH,
        catalog_mtime       = catalog_mtime,
    )

    llm_client.register_session_config(config.resolved_model, args.ctx)

    _host_ok      = False
    _model_ok     = False
    _cd = get_controldata_dir()
    _ud = get_user_data_dir()
    _tick = chr(0x2713)
    _cross = chr(0x2717)

    _backend_label = "LM Studio host" if llm_client.get_active_backend() == "lmstudio" else "Ollama host"
    logger.log_section("SYSTEM STATUS")
    logger.log(f"{_backend_label}:   {llm_client.get_active_host()} (pending)")
    logger.log(f"Requested model: {args.model}")
    logger.log(f"Resolved model:  {config.resolved_model} (pending)")
    print(f"Control data:    {_cd} {_tick if _cd.exists() else _cross}", flush=True)
    print(f"User data:       {_ud} {_tick if _ud.exists() else _cross}", flush=True)

    try:
        _raw_defaults  = load_runtime_config()
    except Exception:
        _raw_defaults  = {}
    _koreconv_url = str(_raw_defaults.get("korechaturl", "")).strip().rstrip("/")

    def _service_reachable(base_url: str) -> bool:
        if not str(base_url or "").strip():
            return False
        try:
            import urllib.request as _ur
            _ur.urlopen(f"{base_url}/status", timeout=3)
            return True
        except Exception:
            return False

    update_startup_state(
        service_status = "starting",
        message        = "Launching HTTP server",
        dependencies   = {
            "llm":      {"status": "pending", "detail": f"{_backend_label} warmup pending"},
            "mcp":      {"status": "pending", "detail": "MCP discovery pending"},
            "korechat": {"status": "pending", "detail": "KoreChat reachability pending"},
        },
    )

    def _background_startup() -> None:
        resolved_model = config.resolved_model
        dep_statuses   = {
            "llm":      "pending",
            "mcp":      "pending",
            "korechat": "pending",
        }

        def _service_status() -> str:
            return "degraded" if any(status == "degraded" for status in dep_statuses.values()) else "ready"

        try:
            llm_client.ensure_ollama_running(verbose=True, start_if_needed=False)
            _host_ok = llm_client.is_ollama_running() if llm_client.get_active_backend() == "ollama" else True
            _known   = llm_client.list_ollama_models(start_if_needed=False)
            try:
                resolved_model = resolve_execution_model(args.model)
            except Exception:
                resolved_model = args.model
            config.resolved_model = resolved_model
            llm_client.register_session_config(resolved_model, args.ctx)
            _model_ok = resolved_model in _known
            update_startup_state(
                dependencies = {
                    "llm": {
                        "status": "ready" if _host_ok and _model_ok else "degraded",
                        "detail": f"{resolved_model} on {llm_client.get_active_host()}",
                    }
                }
            )
            dep_statuses["llm"] = "ready" if _host_ok and _model_ok else "degraded"
            logger.log(f"{_backend_label}:   {llm_client.get_active_host()} {_tick if _host_ok else _cross}")
            logger.log(f"Resolved model:  {resolved_model} {_tick if _model_ok else _cross}")
        except RuntimeError as exc:
            dep_statuses["llm"] = "degraded"
            update_startup_state(
                service_status = "degraded",
                dependencies   = {
                    "llm": {
                        "status": "degraded",
                        "detail": str(exc),
                    }
                },
            )
            logger.log(f"{_backend_label}:   {llm_client.get_active_host()} {_cross}")
            logger.log(f"Resolved model:  {resolved_model} {_cross}")
            print(f"Warning: {exc}  LLM calls will fail until the server is reachable.", flush=True)

        try:
            _mcp_client.start(DEFAULTS_FILE)
            _mcp_status = _mcp_client.get_server_status()
            for _srv in _mcp_status:
                _ok_str  = f"({_srv['tool_count']} tool(s))" if _srv["ok"] else "(failed to connect)"
                _purpose = f" - {_srv['purpose']}" if _srv.get("purpose") else ""
                logger.log(f"MCP [{_srv['name']}]: {_srv['url']} {_ok_str} {_tick if _srv['ok'] else _cross}{_purpose}")
            if not _mcp_status:
                logger.log("MCP connections: (none configured)")
            _mcp_ok = any(_srv["ok"] for _srv in _mcp_status) if _mcp_status else True
            dep_statuses["mcp"] = "ready" if _mcp_ok else "degraded"
            update_startup_state(
                service_status = _service_status(),
                dependencies   = {
                    "mcp": {
                        "status": "ready" if _mcp_ok else "degraded",
                        "detail": f"{sum(1 for _srv in _mcp_status if _srv['ok'])}/{len(_mcp_status)} servers connected" if _mcp_status else "No MCP connections configured",
                    }
                },
            )
        except Exception as exc:
            dep_statuses["mcp"] = "degraded"
            update_startup_state(
                service_status = "degraded",
                dependencies   = {
                    "mcp": {
                        "status": "degraded",
                        "detail": str(exc),
                    }
                },
            )
            logger.log(f"MCP startup failed: {exc}")

        _kc_ok = _service_reachable(_koreconv_url)
        logger.log(f"KoreChat:{_koreconv_url or '(not configured)'} {_tick if _kc_ok else _cross}")
        dep_statuses["korechat"] = "ready" if _kc_ok else ("disabled" if not _koreconv_url else "degraded")
        update_startup_state(
            service_status = _service_status(),
            message        = "Ready" if _service_status() == "ready" else "Running with degraded dependencies",
            dependencies   = {
                "korechat": {
                    "status": "ready" if _kc_ok else ("disabled" if not _koreconv_url else "degraded"),
                    "detail": _koreconv_url or "Not configured",
                }
            },
        )
        try:
            logger.log(format_running_model_report(config.resolved_model))
        except Exception as exc:
            logger.log(f"Model runtime status: unavailable ({exc})")

    try:
        run_api_mode(
            config             = config,
            logger             = logger,
            log_path           = log_path,
            host               = "0.0.0.0",
            port               = args.agentport,
            background_startup = _background_startup,
        )
    finally:
        _mcp_client.stop()


# ----------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    main()
