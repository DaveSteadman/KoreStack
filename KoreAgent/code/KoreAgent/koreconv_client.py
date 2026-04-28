# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Sidecar lifecycle manager for KoreConversation.
#
# start() prefers the suite-level KoreConversation/main.py entrypoint and falls back
# to the legacy embedded code/KoreConversation/main.py shim when the top-level
# launcher is not present. The subprocess uses the same Python interpreter as the
# parent process (guaranteed to be the project venv after
# _maybe_reexec_into_project_venv() runs in main.py).
#
# Stop/start semantics:
#   - If koreconvurl is not configured in default.json, start() is a no-op.
#   - If the service is already reachable before start() is called (externally managed),
#     start() logs the fact and leaves proc ownership as None so stop() does nothing.
#   - If start() launches the subprocess, stop() terminates it cleanly (SIGTERM then SIGKILL
#     after a brief grace period).
#
# Configuration (default.json):
#   "koreconvurl": "http://localhost:8700"
#
# Related modules:
#   - main.py            -- calls start() before run_api_mode, stop() in finally
#   - workspace_utils.py -- get_workspace_root() / get_suite_root() for locating the entrypoint
# ====================================================================================================

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from KoreAgent.utils.workspace_utils import get_workspace_root
from KoreAgent.utils.workspace_utils import get_suite_root
from KoreAgent.utils.workspace_utils import load_runtime_config


# ====================================================================================================
# MARK: STATE
# ====================================================================================================

_proc:    subprocess.Popen | None = None  # set only when WE launched the subprocess
_base_url: str | None              = None  # cached from start()

_DEFAULT_BASE_URL = "http://localhost:8700"
_STARTUP_TIMEOUT = 15   # seconds to wait for the service to become reachable
_POLL_INTERVAL   = 0.5  # seconds between reachability polls
_SHUTDOWN_GRACE  = 5    # seconds to wait for clean exit before SIGKILL


# ====================================================================================================
# MARK: HELPERS
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def _reachable(url: str) -> bool:
    try:
        urllib.request.urlopen(f"{url}/status", timeout=3)
        return True
    except Exception:
        return False


# ====================================================================================================
# MARK: LIFECYCLE
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def start(defaults_path: Path) -> None:
    """Start KoreConversation as a managed subprocess.

    Reads koreconvurl from defaults_path. If absent, falls back to the built-in
    default service URL.
    If the service is already reachable, records the URL but skips launching.
    Otherwise launches the suite KoreConversation entrypoint (or the legacy
    embedded shim) and waits up to
    STARTUP_TIMEOUT seconds for it to respond at /status.
    """
    global _proc, _base_url

    # Read config
    try:
        raw = load_runtime_config()
    except Exception as exc:
        print(f"[koreconv] Warning: could not read runtime config: {exc}", flush=True)
        raw = {}

    url = str(raw.get("koreconvurl", "")).strip().rstrip("/") or _DEFAULT_BASE_URL

    _base_url = url

    # Already reachable - externally managed, don't spawn
    if _reachable(url):
        print(f"[koreconv] Already running at {url} (external)", flush=True)
        return

    # Locate KoreConversation entry point
    suite_entry = get_suite_root() / "KoreConversation" / "main.py"
    entry = suite_entry if suite_entry.exists() else (get_workspace_root() / "code" / "KoreConversation" / "main.py")
    if not entry.exists():
        print(f"[koreconv] Entry point not found: {entry} - skipping launch", flush=True)
        return

    # Use the same interpreter as the parent (project venv)
    cmd = [sys.executable, str(entry)]

    # On Windows, CREATE_NEW_PROCESS_GROUP lets us send Ctrl-C / terminate cleanly
    creation_flags = 0
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

    print(f"[koreconv] Starting {entry.name} ...", flush=True)

    _proc = subprocess.Popen(
        cmd,
        cwd          = str(entry.parent),
        stdout       = subprocess.DEVNULL,
        stderr       = subprocess.DEVNULL,
        stdin        = subprocess.DEVNULL,
        creationflags= creation_flags,
    )

    # Wait for the service to become reachable
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if _reachable(url):
            print(f"[koreconv] Ready at {url}", flush=True)
            return
        if _proc.poll() is not None:
            print(f"[koreconv] Process exited early (rc={_proc.returncode})", flush=True)
            _proc = None
            return
        time.sleep(_POLL_INTERVAL)

    print(f"[koreconv] Service did not respond within {_STARTUP_TIMEOUT}s - continuing anyway", flush=True)


# ----------------------------------------------------------------------------------------------------
def stop() -> None:
    """Terminate the KoreConversation subprocess if we own it."""
    global _proc

    if _proc is None:
        return

    if _proc.poll() is not None:
        _proc = None
        return

    print("[koreconv] Stopping ...", flush=True)
    _proc.terminate()

    try:
        _proc.wait(timeout=_SHUTDOWN_GRACE)
    except subprocess.TimeoutExpired:
        print("[koreconv] Grace period expired - killing process", flush=True)
        _proc.kill()
        try:
            _proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass

    print("[koreconv] Stopped.", flush=True)
    _proc = None


# ====================================================================================================
# MARK: STATUS QUERY
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def is_reachable() -> bool:
    """Return True if the configured KoreConversation service responds at /status."""
    if not _base_url:
        return False
    return _reachable(_base_url)


# ----------------------------------------------------------------------------------------------------
def get_base_url() -> str | None:
    """Return the configured KoreConversation base URL, or None if not set."""
    return _base_url
