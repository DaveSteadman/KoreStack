# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Ollama-specific client: model management, process lifecycle, runtime status, and the
# /api/generate legacy endpoint.
#
# All functions that require Ollama-specific APIs (/api/tags, /api/generate, /api/ps, ollama serve)
# live here. The shared OpenAI-compatible call (call_llm_chat) lives in llm_client.py (the facade).
#
# Shared state and utilities are accessed via the llm_client_openai module imported as _core.
# Module-level variables in _core are read at call time, so mutations via configure_host() etc.
# are always reflected without needing to re-import.
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import json
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request

import KoreAgent.llm_client_openai as _core
from KoreAgent.utils.workspace_utils import trunc


# ====================================================================================================
# MARK: HEALTH CHECK
# ====================================================================================================
# Serialises the check-then-start sequence so concurrent callers cannot both see
# is_ollama_running()==False and both invoke start_ollama_server().
_ollama_start_lock: threading.Lock = threading.Lock()
_ollama_proc: subprocess.Popen | None = None


def is_ollama_running(host: str | None = None) -> bool:
    host = host or _core.get_active_host()
    try:
        _core._request_json(url=f"{host.rstrip('/')}/api/tags", timeout=3.0)
        return True
    except Exception:
        return False


# ----------------------------------------------------------------------------------------------------
def start_ollama_server() -> None:
    global _ollama_proc
    # Build platform-specific flags to fully detach the server process from this parent.
    creation_flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        creation_flags |= subprocess.DETACHED_PROCESS
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creation_flags |= subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        _ollama_proc = subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "'ollama' executable not found on PATH. "
            "Please install Ollama (https://ollama.com) and ensure it is on your PATH."
        ) from None


# ----------------------------------------------------------------------------------------------------
def ensure_ollama_running(
    host: str | None = None,
    start_if_needed: bool = True,
    wait_seconds: float = 20.0,
    verbose: bool = False,
) -> None:
    host = host or _core.get_active_host()

    # Skip the health-check HTTP round-trip if this host was confirmed healthy recently.
    if _core.is_host_health_cached(host):
        return

    with _ollama_start_lock:
        # Re-check inside the lock - another thread may have just started it.
        if is_ollama_running(host=host):
            _core.mark_host_healthy(host)
            return

        if not start_if_needed or not _core._is_local_host(host):
            raise RuntimeError(f"Ollama is not reachable at {host}")

        if verbose:
            print(f"Starting Ollama at {host}...", flush=True)
        start_ollama_server()

    # Poll outside the lock so other threads can proceed with their own health checks.
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if is_ollama_running(host=host):
            _core.mark_host_healthy(host)
            if verbose:
                print("Ollama is ready.", flush=True)
            return
        time.sleep(0.5)

    raise RuntimeError(f"Ollama did not become ready at {host} within {wait_seconds:.0f}s")


# ====================================================================================================
# MARK: MODEL LISTING
# ====================================================================================================
def list_ollama_models(host: str | None = None) -> list[str]:
    host = host or _core.get_active_host()
    ensure_ollama_running(host=host, start_if_needed=True)
    body   = _core._request_json(url=f"{host.rstrip('/')}/api/tags", timeout=10.0)
    models = body.get("models", [])
    return [entry.get("model", "") for entry in models if entry.get("model")]


# ====================================================================================================
# MARK: RUNTIME STATUS
# ====================================================================================================
def get_ollama_ps_rows() -> list[dict[str, str]]:
    """Return currently running models as a list of dicts with at least a 'name' key.

    For local Ollama: parses `ollama ps` subprocess output.
    For remote/cloud Ollama: calls the /api/ps REST endpoint instead.
    """
    if _core._is_local_host(_core.get_active_host()):
        return _get_ollama_ps_rows_local()
    return _get_ollama_ps_rows_remote(_core.get_active_host())


def _get_ollama_ps_rows_local() -> list[dict[str, str]]:
    try:
        result = subprocess.run(
            ["ollama", "ps"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("'ollama ps' did not respond within 10 s - is Ollama running?")
    except FileNotFoundError:
        raise RuntimeError("'ollama' executable not found on PATH.")

    if result.returncode != 0:
        raise RuntimeError(f"Failed to run 'ollama ps': {result.stderr.strip()}")

    lines = [line.rstrip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return []

    # Parse the header line to derive column names, then split each data row by the same spacing.
    columns = [column.lower() for column in re.split(r"\s{2,}", lines[0].strip())]
    rows    = []

    for line in lines[1:]:
        values = re.split(r"\s{2,}", line.strip(), maxsplit=max(0, len(columns) - 1))
        if len(values) < len(columns):
            values += [""] * (len(columns) - len(values))

        row = dict(zip(columns, values))
        rows.append(row)

    return rows


def _get_ollama_ps_rows_remote(host: str) -> list[dict[str, str]]:
    """Call /api/ps on a remote Ollama host and normalise the response into the same row shape."""
    try:
        data   = _core._request_json(f"{host.rstrip('/')}/api/ps", timeout=10.0)
        models = data.get("models") or []
    except Exception:
        return []

    rows = []
    for m in models:
        details    = m.get("details") or {}
        size_bytes = m.get("size", 0)
        size_gb    = f"{size_bytes / 1_073_741_824:.1f} GB" if size_bytes else ""
        size_vram  = m.get("size_vram", 0)
        vram_gb    = f"{size_vram / 1_073_741_824:.1f} GB" if size_vram else "0 B"
        rows.append({
            "name":       m.get("name", ""),
            "id":         m.get("digest", "")[:12],
            "size":       size_gb,
            "processor":  "100% GPU" if size_vram else "100% CPU",
            "vram":       vram_gb,
            "until":      m.get("expires_at", ""),
            "param_size": details.get("parameter_size", ""),
        })

    return rows


# ----------------------------------------------------------------------------------------------------
def get_running_model_row(model_name: str) -> dict[str, str] | None:
    rows             = get_ollama_ps_rows()
    running_names    = [row.get("name", "") for row in rows if row.get("name")]
    resolved_running = _core.resolve_model_name(model_name, running_names)

    if not resolved_running:
        return None

    for row in rows:
        if row.get("name", "").lower() == resolved_running.lower():
            return row

    return None


# ----------------------------------------------------------------------------------------------------
def format_running_model_report(model_name: str) -> str:
    row = get_running_model_row(model_name)
    if row is None:
        return f"Model runtime status: {model_name} not currently loaded (ollama ps)."

    size      = row.get("size", "unknown")
    processor = row.get("processor", "unknown")
    context   = row.get("context", row.get("param_size", "unknown"))
    until     = row.get("until", "unknown")
    running   = row.get("name", model_name)

    return (
        f"Model runtime status: {running} | size={size} | processor={processor} "
        f"| context={context} | until={until}"
    )


# ====================================================================================================
# MARK: MODEL UNLOAD
# ====================================================================================================
def stop_model(
    model_name: str,
    host: str | None = None,
) -> None:
    """Unload a model from VRAM immediately by sending keep_alive=0 to the generate endpoint.

    Ollama interprets a generate request with keep_alive=0 as an instruction to evict the
    model from memory as soon as the (empty) call completes.  Raises RuntimeError on failure.
    """
    host    = host or _core.get_active_host()
    payload = {
        "model":      model_name,
        "prompt":     "",
        "keep_alive": 0,
        "stream":     False,
    }
    try:
        _core._request_json(
            url=f"{host.rstrip('/')}/api/generate",
            method="POST",
            payload=payload,
            timeout=30.0,
        )
    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama HTTP error {error.code} stopping model: {error_body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Unable to reach Ollama at {host}: {error.reason}") from error


# ====================================================================================================
# MARK: GENERATE
# ====================================================================================================
def call_ollama_extended(
    model_name: str,
    prompt: str,
    host: str | None = None,
    num_ctx: int | None = None,
    timeout: int | None = None,
) -> _core.OllamaCallResult:
    """Call the Ollama generate endpoint and return the response with token usage counts.

    timeout defaults to the module-level _DEFAULT_LLM_TIMEOUT (set via set_llm_timeout()).
    """
    host = host or _core.get_active_host()
    # Check/start Ollama before each call; auto-start is suppressed for remote and Cloud hosts.
    ensure_ollama_running(host=host, start_if_needed=True)

    preview = trunc(prompt.replace("\n", " "), 32)
    ctx_str = f"{num_ctx:,}" if num_ctx is not None else "default"
    _core.log_to_session(f"[LLM call] {model_name} | ctx={ctx_str} | {preview!r}")

    options = {}
    if num_ctx is not None:
        options["num_ctx"] = num_ctx

    payload = {
        "model":  model_name,
        "prompt": prompt,
        "stream": False,
    }
    if options:
        payload["options"] = options

    effective_timeout = timeout if timeout is not None else _core.get_llm_timeout()
    try:
        body = _core._request_json(
            url=f"{host.rstrip('/')}/api/generate",
            method="POST",
            payload=payload,
            timeout=effective_timeout,
        )
    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        # Provide a helpful message listing installed models when the requested model is absent.
        if error.code == 404 and "not found" in error_body.lower():
            available_models = []
            try:
                available_models = list_ollama_models(host=host)
            except Exception:
                pass

            if available_models:
                available_text = ", ".join(available_models)
                raise RuntimeError(
                    f"Model '{model_name}' not found. Installed models: {available_text}"
                ) from error

        raise RuntimeError(f"Ollama HTTP error {error.code}: {error_body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Unable to reach Ollama at {host}: {error.reason}") from error
    except TimeoutError as error:
        raise RuntimeError(f"Ollama call timed out after {effective_timeout}s") from error
    except json.JSONDecodeError as error:
        raise RuntimeError("Ollama returned a non-JSON response") from error

    if "response" not in body:
        raise RuntimeError(f"Ollama response missing 'response' field: {body}")

    prompt_tokens           = body.get("prompt_eval_count", 0)
    completion_tokens       = body.get("eval_count", 0)
    eval_duration_ns        = body.get("eval_duration", 0)
    prompt_eval_duration_ns = body.get("prompt_eval_duration", 0)
    return _core.OllamaCallResult(
        response=body["response"],
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        eval_duration_ns=eval_duration_ns,
        prompt_eval_duration_ns=prompt_eval_duration_ns,
    )


# ----------------------------------------------------------------------------------------------------
def call_ollama(
    model_name: str,
    prompt: str,
    host: str | None = None,
    num_ctx: int | None = None,
) -> str:
    """Convenience wrapper - returns the response text only. See call_ollama_extended for token counts."""
    return call_ollama_extended(model_name=model_name, prompt=prompt, host=host, num_ctx=num_ctx).response
