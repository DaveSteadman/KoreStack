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
import time
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
WARMUP_TIMEOUT      = 600.0
SERVER_START_TIMEOUT = 60.0


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


def _post_json(url: str, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama warmup request failed with HTTP {error.code}: {detail}") from error
    except (OSError, urllib.error.URLError) as error:
        raise RuntimeError(f"Ollama warmup request failed: {error}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError("Ollama warmup request returned invalid JSON") from error
    if not isinstance(body, dict):
        raise RuntimeError("Ollama warmup request returned a non-object response")
    return body


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


def _same_model(name: str, wanted: str) -> bool:
    name, wanted = name.strip().lower(), wanted.strip().lower()
    return name == wanted or name == f"{wanted}:latest" or wanted == f"{name}:latest"


def _format_bytes(value: Any) -> str:
    if not isinstance(value, (int, float)) or value <= 0:
        return ""
    return f"{value / (1024 ** 3):.1f} GB"


def _system_one_stats(row: dict[str, Any] | None, model: str) -> str:
    if not model:
        return "Not configured"
    if row is None:
        return "Not loaded"
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    size, vram = row.get("size"), row.get("size_vram")
    parts = [_format_bytes(size) and f"size {_format_bytes(size)}"]
    if isinstance(size, (int, float)) and size > 0 and isinstance(vram, (int, float)):
        parts.append(f"GPU {round(100 * vram / size)}%")
    if row.get("context_length"):
        parts.append(f"ctx {row['context_length']}")
    parts.append(str(details.get("parameter_size") or ""))
    parts.append(str(details.get("quantization_level") or ""))
    parts.append(f"expires {row['expires_at']}" if row.get("expires_at") else "")
    return ", ".join(part for part in parts if part) or "Loaded"


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
        self._snapshot_cache = self._initial_snapshot()
        self._refresh_stop = threading.Event()
        self._refresh_thread: threading.Thread | None = None
        self._warmup_thread: threading.Thread | None = None
        self._warmup_status = "idle"
        self._warmup_detail = ""
        self._warmup_models: list[str] = []

    def snapshot(self) -> dict[str, Any]:
        """Return the last state refreshed by the worker without network I/O."""
        with self._lock:
            return self._snapshot_cache

    def start_refreshing(self) -> None:
        with self._lock:
            if self._refresh_thread is not None:
                return
            self._refresh_thread = threading.Thread(target=self._refresh_loop, daemon=True)
            self._refresh_thread.start()

    def refresh(self) -> dict[str, Any]:
        """Synchronously refresh state for a user-initiated lifecycle action."""
        state = self._compute_snapshot()
        with self._lock:
            self._snapshot_cache = state
        return state

    def _refresh_loop(self) -> None:
        while not self._refresh_stop.is_set():
            try:
                self.refresh()
            finally:
                self._refresh_stop.wait(3)

    @staticmethod
    def _initial_snapshot() -> dict[str, Any]:
        return {
            "server_running": False,
            "server_ready": False,
            "management": "none",
            "pid": None,
            "returncode": None,
            "host": DEFAULT_OLLAMA_HOST,
            "backend": "ollama",
            "version": "",
            "configured_model": "",
            "loaded_models": [],
            "system_one_model": "",
            "system_one_loaded": False,
            "system_one_stats": "Not configured",
            "num_ctx": None,
            "max_predict": None,
            "sampling": {},
            "sampling_summary": "",
            "offload_mode": "",
            "llm_running": False,
            "agent_available": False,
            "controllable": True,
            "warmup_status": "idle",
            "warmup_detail": "",
            "warmup_models": [],
        }

    def _compute_snapshot(self) -> dict[str, Any]:
        agent     = _read_json(self._agent_status_url) or {}
        host      = str(agent.get("host") or DEFAULT_OLLAMA_HOST).strip()
        backend   = str(agent.get("backend") or "ollama").strip().lower()
        api_state = _read_json(_ollama_api_url(host, "/api/ps")) if backend == "ollama" else None
        rows      = api_state.get("models", []) if isinstance(api_state, dict) else []
        version_state = _read_json(_ollama_api_url(host, "/api/version")) if backend == "ollama" else None
        version       = str((version_state or {}).get("version") or "").strip()
        loaded_models = [
            str(row.get("name") or "").strip()
            for row in rows
            if isinstance(row, dict) and str(row.get("name") or "").strip()
        ]
        system_one_model = str(agent.get("system_one_model") or "").strip()
        system_one_row   = next(
            (row for row in rows if isinstance(row, dict) and system_one_model
             and _same_model(str(row.get("name") or row.get("model") or ""), system_one_model)),
            None,
        )
        with self._lock:
            owned_running    = self._proc is not None and self._proc.poll() is None
            owned_pid        = self._proc.pid if owned_running and self._proc is not None else None
            owned_returncode = self._proc.returncode if self._proc is not None and not owned_running else None
            warmup_status    = self._warmup_status
            warmup_detail    = self._warmup_detail
            warmup_models    = list(self._warmup_models)

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
            "version":          version,
            "configured_model": str(agent.get("model") or "").strip(),
            "loaded_models":    loaded_models,
            "system_one_model": system_one_model,
            "system_one_loaded": system_one_row is not None,
            "system_one_stats": _system_one_stats(system_one_row, system_one_model),
            "num_ctx":          agent.get("num_ctx"),
            "max_predict":      agent.get("max_predict"),
            "sampling":         sampling,
            "sampling_summary": _sampling_summary(sampling),
            "offload_mode":     str(agent.get("offload_mode") or "").strip(),
            "llm_running":      bool(agent.get("llm_running")),
            "agent_available":  bool(agent),
            "controllable":     backend == "ollama" and _local_ollama_host(host),
            "warmup_status":    warmup_status,
            "warmup_detail":    warmup_detail,
            "warmup_models":    warmup_models,
        }

    def start(self) -> dict[str, Any]:
        state = self.refresh()
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
            self._set_warmup_state("pending", "Waiting for Ollama before loading configured models.")
            self._warmup_thread = threading.Thread(
                target=self._preload_configured_models,
                args=(process,),
                daemon=True,
                name="ollama-model-warmup",
            )
            self._warmup_thread.start()
        return {
            "changed": True,
            "message": "Ollama start requested; configured models are warming in the background.",
            "state": self.refresh(),
        }

    def stop(self, *, include_external: bool = True) -> dict[str, Any]:
        with self._lock:
            process = self._proc
        if process is not None and process.poll() is None:
            self._stop_process_tree(process)
            with self._lock:
                self._close_log_handle()
            return {"changed": True, "message": "KoreStack-managed Ollama stopped.", "state": self.refresh()}

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
                "state":   self.refresh(),
            }
        return {"changed": False, "message": "No KoreStack-managed Ollama server is running.", "state": self.refresh()}

    def close(self) -> None:
        self._refresh_stop.set()
        if self._refresh_thread is not None:
            self._refresh_thread.join(timeout=2)
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

    def _preload_configured_models(self, process: subprocess.Popen[bytes]) -> None:
        deadline = time.monotonic() + SERVER_START_TIMEOUT
        while time.monotonic() < deadline:
            with self._lock:
                still_owned = self._proc is process and process.poll() is None
            if not still_owned:
                return
            if _read_json(_ollama_api_url(DEFAULT_OLLAMA_HOST, "/api/version")) is not None:
                break
            time.sleep(0.5)
        else:
            self._set_warmup_failure("Ollama did not become ready before the model warmup timeout.")
            return

        agent = _read_json(self._agent_status_url)
        if not agent:
            self._set_warmup_failure("KoreAgent configuration was unavailable; configured models were not preloaded.")
            return

        host = str(agent.get("host") or DEFAULT_OLLAMA_HOST).strip().rstrip("/")
        chat_model = str(agent.get("model") or "").strip()
        system_one_model = str(agent.get("system_one_model") or "").strip()
        num_ctx = agent.get("num_ctx")
        chat_payload: dict[str, Any] = {"stream": False, "keep_alive": -1}
        if isinstance(num_ctx, int) and num_ctx > 0:
            chat_payload["options"] = {"num_ctx": num_ctx}

        self._set_warmup_state("warming", "Loading configured models.")

        loaded_models: list[str] = []
        try:
            if chat_model:
                _post_json(
                    _ollama_api_url(host, "/api/generate"),
                    {"model": chat_model, **chat_payload},
                    timeout=WARMUP_TIMEOUT,
                )
                loaded_models.append(chat_model)
            if system_one_model:
                _post_json(
                    _ollama_api_url(host, "/v1/systemone"),
                    {
                        "model": system_one_model,
                        "state": "System One warmup.",
                        "questions": {
                            "ready": {
                                "type": "noul",
                                "instructions": "Is the System One decision runtime available?",
                            }
                        },
                        "keep_alive": -1,
                    },
                    timeout=WARMUP_TIMEOUT,
                )
                loaded_models.append(system_one_model)
        except RuntimeError as error:
            self._set_warmup_failure(str(error), loaded_models)
            return

        if not loaded_models:
            self._set_warmup_failure("No configured Ollama models were available to preload.")
            return
        self._set_warmup_state("ready", "Configured models are loaded and retained.", loaded_models)

    def _set_warmup_failure(self, detail: str, loaded_models: list[str] | None = None) -> None:
        self._set_warmup_state("failed", detail, loaded_models)

    def _set_warmup_state(self, status: str, detail: str, models: list[str] | None = None) -> None:
        with self._lock:
            self._warmup_status = status
            self._warmup_detail = detail
            self._warmup_models = list(models or [])
            self._snapshot_cache = {
                **self._snapshot_cache,
                "warmup_status": self._warmup_status,
                "warmup_detail": self._warmup_detail,
                "warmup_models": list(self._warmup_models),
            }
