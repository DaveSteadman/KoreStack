# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Local Ollama lifecycle and state adapter for the KoreStack landing page. KoreStack owns the server
# process it starts; runtime model settings are read from KoreAgent's public status endpoint.
# ====================================================================================================
"""Managed local Ollama process control and compact runtime-state retrieval."""


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
REQUEST_TIMEOUT     = 1.5


# ====================================================================================================
# MARK: PROCESS HELPERS
# ====================================================================================================
def _hidden_windows_creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _read_json(url: str, *, timeout: float = REQUEST_TIMEOUT) -> dict[str, Any] | None:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _local_ollama_host(host: str) -> bool:
    parsed = urllib.parse.urlparse(host)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _ollama_api_url(host: str, path: str) -> str:
    return urllib.parse.urljoin(host.rstrip("/") + "/", path.lstrip("/"))


def _ollama_executable() -> str | None:
    discovered = shutil.which("ollama")
    if discovered:
        return discovered
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    candidate = Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe"
    return str(candidate) if candidate.exists() else None


def _sampling_summary(sampling: dict[str, Any]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(sampling.items()))


# ====================================================================================================
# MARK: OLLAMA LIFECYCLE (PUBLIC)
# ====================================================================================================
class OllamaControl:
    """Own a local ``ollama serve`` process and expose its live state to the dashboard."""

    def __init__(self, agent_status_url: str, log_dir: Path) -> None:
        self._agent_status_url = agent_status_url
        self._log_dir          = log_dir
        self._proc: subprocess.Popen[bytes] | None = None
        self._log_handle: Any = None
        self._lock = threading.RLock()

    def snapshot(self) -> dict[str, Any]:
        agent     = _read_json(self._agent_status_url) or {}
        host      = str(agent.get("host") or DEFAULT_OLLAMA_HOST).strip()
        backend   = str(agent.get("backend") or "ollama").strip().lower()
        api_state = _read_json(_ollama_api_url(host, "/api/ps")) if backend == "ollama" else None
        rows      = api_state.get("models", []) if isinstance(api_state, dict) else []
        loaded_models = [
            str(row.get("name") or "").strip()
            for row in rows
            if isinstance(row, dict) and str(row.get("name") or "").strip()
        ]
        with self._lock:
            owned_running    = self._proc is not None and self._proc.poll() is None
            owned_pid        = self._proc.pid if owned_running and self._proc is not None else None
            owned_returncode = self._proc.returncode if self._proc is not None and not owned_running else None

        server_ready   = api_state is not None
        server_running = server_ready or owned_running
        management     = "KoreStack" if owned_running else "external" if server_ready else "none"
        sampling       = agent.get("sampling") if isinstance(agent.get("sampling"), dict) else {}
        return {
            "server_running":   server_running,
            "server_ready":     server_ready,
            "management":       management,
            "pid":              owned_pid,
            "returncode":       owned_returncode,
            "host":             host,
            "backend":          backend,
            "configured_model": str(agent.get("model") or "").strip(),
            "loaded_models":    loaded_models,
            "num_ctx":          agent.get("num_ctx"),
            "max_predict":      agent.get("max_predict"),
            "sampling":         sampling,
            "sampling_summary": _sampling_summary(sampling),
            "offload_mode":     str(agent.get("offload_mode") or "").strip(),
            "llm_running":      bool(agent.get("llm_running")),
            "agent_available":  bool(agent),
            "controllable":     backend == "ollama" and _local_ollama_host(host),
        }

    def start(self) -> dict[str, Any]:
        state = self.snapshot()
        if state["backend"] != "ollama":
            raise RuntimeError("Ollama controls are unavailable while KoreAgent uses another backend.")
        if not state["controllable"]:
            raise RuntimeError("KoreStack can manage Ollama only on a local http://127.0.0.1:11434-style host.")
        if state["server_running"]:
            message = "Ollama is already managed by KoreStack." if state["management"] == "KoreStack" else "Ollama is already running outside KoreStack. Stop it here once, then start it here."
            return {"changed": False, "message": message, "state": state}

        executable = _ollama_executable()
        if not executable:
            raise RuntimeError("Could not find the Ollama executable on PATH or in the standard Windows install location.")

        self._log_dir.mkdir(parents=True, exist_ok=True)
        log_path   = self._log_dir / "ollama.log"
        log_handle = open(log_path, "ab")  # noqa: SIM115
        try:
            process = subprocess.Popen(
                [executable, "serve"],
                stdout        = log_handle,
                stderr        = subprocess.STDOUT,
                creationflags = _hidden_windows_creation_flags(),
            )
        except Exception:
            log_handle.close()
            raise

        with self._lock:
            self._close_log_handle()
            self._proc       = process
            self._log_handle = log_handle
        return {"changed": True, "message": "Ollama start requested.", "state": self.snapshot()}

    def stop(self, *, include_external: bool = True) -> dict[str, Any]:
        with self._lock:
            process = self._proc
        if process is not None and process.poll() is None:
            self._stop_process_tree(process)
            with self._lock:
                self._close_log_handle()
            return {"changed": True, "message": "KoreStack-managed Ollama stopped.", "state": self.snapshot()}

        if include_external and os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/IM", "ollama.exe", "/T", "/F"],
                stdout        = subprocess.DEVNULL,
                stderr        = subprocess.DEVNULL,
                check         = False,
                creationflags = _hidden_windows_creation_flags(),
            )
            changed = result.returncode == 0
            return {
                "changed": changed,
                "message": "External Ollama stopped." if changed else "No external Ollama server was running.",
                "state":   self.snapshot(),
            }
        return {"changed": False, "message": "No KoreStack-managed Ollama server is running.", "state": self.snapshot()}

    def close(self) -> None:
        self.stop(include_external=False)

    def _close_log_handle(self) -> None:
        if self._log_handle is None:
            return
        try:
            self._log_handle.close()
        except OSError:
            pass
        self._log_handle = None

    @staticmethod
    def _stop_process_tree(process: subprocess.Popen[bytes]) -> None:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout        = subprocess.DEVNULL,
                stderr        = subprocess.DEVNULL,
                check         = False,
                creationflags = _hidden_windows_creation_flags(),
            )
            if result.returncode == 0:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                return
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
