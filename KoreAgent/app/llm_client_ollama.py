# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Ollama-specific client: model management, process lifecycle, runtime status, and the
# /api/generate legacy endpoint, and native /api/chat.
#
# All functions that require Ollama-specific APIs (/api/tags, /api/generate, /api/ps, ollama serve)
# live here. The shared OpenAI-compatible call (call_llm_chat) lives in llm_client.py (the facade).
#
# Shared state and utilities are accessed via the llm_client_openai module imported as _core.
# Module-level variables in _core are read at call time, so mutations via configure_host() etc.
# are always reflected without needing to re-import.
# MARK: FUNCTIONS
# Function inventory:
# - _per_request_context_enabled: Implements the  per request context enabled operation for this module.
# - _windows_creation_flags: Implements the  windows creation flags operation for this module.
# - is_ollama_running: Checks whether ollama running is true.
# - start_ollama_server: Starts ollama server for this module.
# - ensure_ollama_running: Ensures ollama running for this module.
# - recover_ollama_runtime: Recovers ollama runtime for this module.
# - list_ollama_models: Lists ollama models for this module.
# - get_ollama_ps_rows: Returns ollama ps rows for this module.
# - _get_ollama_ps_rows_local: Implements the  get ollama ps rows local operation for this module.
# - _get_ollama_ps_rows_remote: Implements the  get ollama ps rows remote operation for this module.
# - get_running_model_row: Returns running model row for this module.
# - format_running_model_report: Formats running model report for this module.
# - stop_model: Stops model for this module.
# - _native_chat_messages: Implements the  native chat messages operation for this module.
# - _openai_tool_calls: Implements the  openai tool calls operation for this module.
# - call_ollama_chat: Implements the call ollama chat operation for this module.
# - call_ollama_extended: Implements the call ollama extended operation for this module.
# - call_ollama: Implements the call ollama operation for this module.
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request

import llm_client_openai as _core
from utils.workspace_utils import trunc


# ====================================================================================================
# MARK: HEALTH CHECK
# ====================================================================================================
# Serialises the check-then-start sequence so concurrent callers cannot both see
# is_ollama_running()==False and both invoke start_ollama_server().
_ollama_start_lock: threading.Lock = threading.Lock()
_ollama_recovery_lock: threading.Lock = threading.Lock()
_ollama_proc: subprocess.Popen | None = None

_RUNNER_RECOVERY_ATTEMPTS: int = 2
_RUNNER_CRASH_MARKERS: tuple[str, ...] = (
    "llama-server process has terminated",
    "rocm error",
    "unspecified launch failure",
    "stack-based buffer",
)


def _cpu_fallback_enabled() -> bool:
    """Return whether repeated local GPU runner crashes may fall back to CPU."""
    configured = str(os.environ.get("KORE_OLLAMA_CPU_FALLBACK", "")).strip().lower()
    return configured not in {"0", "false", "no", "off"}


def _per_request_context_enabled() -> bool:
    """Return whether native requests may replace Ollama's loaded runner context.

    Linux runners must receive the requested context: without it, the server's
    model default silently overrides KoreAgent's context policy.  Windows keeps
    the conservative opt-in default because the Windows/ROCm runner has crashed
    while warming a replacement context.  The environment variable always wins
    on either platform.
    """
    configured = str(os.environ.get("KORE_OLLAMA_PER_REQUEST_CONTEXT", "")).strip().lower()
    if configured:
        return configured in {"1", "true", "yes", "on"}
    return os.name != "nt"


def _windows_creation_flags(*, detach: bool = False) -> int:
    """Return process flags that prevent transient console windows on Windows."""
    if os.name != "nt":
        return 0

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if detach:
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return flags


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

    try:
        _ollama_proc = subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=_windows_creation_flags(detach=True),
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


def _is_runner_crash(detail: str) -> bool:
    normalized = detail.lower()
    return any(marker in normalized for marker in _RUNNER_CRASH_MARKERS)


def recover_ollama_runtime(host: str, *, attempt: int, detail: str) -> None:
    """Recover a failed local Ollama runtime before retrying one interrupted request.

    A runner crash can leave the Ollama HTTP daemon alive, in which case the next
    request reloads the model. If the daemon has also exited, start a replacement
    only for a local host. Recovery is serialised to prevent concurrent requests
    from spawning duplicate daemons.
    """
    _core.invalidate_host_health(host)
    delay_seconds = min(4.0, float(attempt + 1))
    _core.log_to_session(
        f"[Ollama recovery] Attempt {attempt + 1}/{_RUNNER_RECOVERY_ATTEMPTS}: "
        f"{trunc(detail, 180)}"
    )
    if (
        attempt > 0
        and _is_runner_crash(detail)
        and _core._is_local_host(host)
        and _cpu_fallback_enabled()
        and _core.get_ollama_offload_mode() == "autogpu"
    ):
        _core.set_ollama_offload_mode("forcecpu")
        _core.log_to_session(
            "[Ollama recovery] Repeated GPU runner crash; retrying with CPU offload. "
            "Set KORE_OLLAMA_CPU_FALLBACK=0 to disable this fallback."
        )
    time.sleep(delay_seconds)

    with _ollama_recovery_lock:
        if is_ollama_running(host):
            _core.mark_host_healthy(host)
            _core.log_to_session("[Ollama recovery] Daemon is healthy; retrying so Ollama reloads the runner.")
            return
        if not _core._is_local_host(host):
            _core.log_to_session("[Ollama recovery] Remote daemon is unavailable; it cannot be restarted locally.")
            return
        ensure_ollama_running(host=host, start_if_needed=True, wait_seconds=30.0)
        _core.log_to_session("[Ollama recovery] Local Ollama daemon restarted.")


def _retry_after_runtime_failure(
    *,
    host: str,
    detail: str,
    recovery_attempt: int,
) -> bool:
    """Recover a known transient runtime failure and report whether to retry."""
    if recovery_attempt >= _RUNNER_RECOVERY_ATTEMPTS:
        return False
    if not _is_runner_crash(detail) and is_ollama_running(host):
        return False
    recover_ollama_runtime(host, attempt=recovery_attempt, detail=detail)
    return True


# ====================================================================================================
# MARK: MODEL LISTING
# ====================================================================================================
def list_ollama_models(host: str | None = None, *, start_if_needed: bool = True) -> list[str]:
    host = host or _core.get_active_host()
    if start_if_needed:
        ensure_ollama_running(host=host, start_if_needed=True)
    elif not is_ollama_running(host=host):
        return []
    body   = _core._request_json(url=f"{host.rstrip('/')}/api/tags", timeout=10.0)
    models = body.get("models", [])
    return [entry.get("model", "") for entry in models if entry.get("model")]


# ====================================================================================================
# MARK: RUNTIME STATUS
# ====================================================================================================
def get_ollama_ps_rows() -> list[dict[str, str]]:
    """Return currently running models as a list of dicts with at least a 'name' key.

    For both local and remote Ollama hosts, prefer the HTTP /api/ps endpoint so
    passive UI status polling does not shell out to the local CLI.
    """
    return _get_ollama_ps_rows_remote(_core.get_active_host())


def _get_ollama_ps_rows_local() -> list[dict[str, str]]:
    try:
        result = subprocess.run(
            ["ollama", "ps"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            creationflags=_windows_creation_flags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("'ollama ps' did not respond within 10 s - is Ollama running?") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("'ollama' executable not found on PATH.") from exc

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
# MARK: NATIVE CHAT
# ====================================================================================================
def _native_chat_messages(messages: list[dict]) -> list[dict]:
    """Convert the framework's OpenAI-shaped tool history to Ollama's native form."""
    native_messages: list[dict] = []
    for message in messages:
        converted = {key: value for key, value in message.items() if key not in {"tool_call_id", "name"}}
        if converted.get("role") == "tool":
            converted["tool_name"] = str(message.get("name") or "")

        tool_calls = converted.get("tool_calls")
        if isinstance(tool_calls, list):
            converted_calls: list[dict] = []
            for index, tool_call in enumerate(tool_calls):
                function = tool_call.get("function") if isinstance(tool_call, dict) else {}
                function = function if isinstance(function, dict) else {}
                arguments = function.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                converted_calls.append({
                    "type": "function",
                    "function": {
                        "index":       index,
                        "name":        str(function.get("name") or ""),
                        "arguments":   arguments if isinstance(arguments, dict) else {},
                    },
                })
            converted["tool_calls"] = converted_calls
        native_messages.append(converted)
    return native_messages


def _openai_tool_calls(native_calls: object) -> list[dict]:
    """Convert Ollama native tool calls to the framework's existing loop contract."""
    converted: list[dict] = []
    for index, tool_call in enumerate(native_calls if isinstance(native_calls, list) else []):
        function = tool_call.get("function") if isinstance(tool_call, dict) else {}
        function = function if isinstance(function, dict) else {}
        arguments = function.get("arguments", {})
        converted.append({
            "id":   f"ollama-{index}",
            "type": "function",
            "function": {
                "name":      str(function.get("name") or ""),
                "arguments": json.dumps(arguments if isinstance(arguments, dict) else {}, ensure_ascii=False),
            },
        })
    return converted


def call_ollama_chat(
    model_name: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    host: str | None = None,
    num_ctx: int | None = None,
    timeout: int | None = None,
    on_token = None,
    _recovery_attempt: int = 0,
) -> _core.ChatCallResult:
    """Call Ollama's native chat API, preserving per-request runtime options."""
    host = host or _core.get_active_host()
    ensure_ollama_running(host=host, start_if_needed=_core.get_local_ollama_autostart_enabled())

    last_user = next((trunc(message.get("content", ""), 32) for message in reversed(messages) if message.get("role") == "user"), "")
    ctx_str   = f"{num_ctx:,}" if num_ctx is not None and _per_request_context_enabled() else "server default"
    tool_str  = f" | {len(tools)} tools" if tools else ""
    _core.log_to_session(f"[Ollama native chat] {model_name} | ctx={ctx_str}{tool_str} | {last_user!r}")

    payload: dict = {
        "model":    model_name,
        "messages": _native_chat_messages(messages),
        "stream":   on_token is not None,
        # Nemotron exposes a separate reasoning channel.  Leaving its native
        # default enabled makes ordinary chat and tool selection spend hundreds
        # of invisible tokens before it emits either content or a tool call.
        # KoreAgent does not consume that reasoning as an execution input, so
        # request the same direct-answer mode used for responsive chat.
        "think":    False,
    }
    if tools:
        payload["tools"] = tools
    requested_num_ctx = num_ctx if _per_request_context_enabled() else None
    options = _core.get_ollama_request_options(requested_num_ctx)
    if options:
        payload["options"] = options

    effective_timeout = timeout if timeout is not None else _core.get_llm_timeout()
    started           = time.monotonic()
    try:
        if on_token is None:
            body = _core._request_json(
                url     = f"{host.rstrip('/')}/api/chat",
                method  = "POST",
                payload = payload,
                timeout = effective_timeout,
            )
        else:
            request = urllib.request.Request(
                url     = f"{host.rstrip('/')}/api/chat",
                data    = json.dumps(payload).encode("utf-8"),
                headers = {"Content-Type": "application/json"},
                method  = "POST",
            )
            text_parts: list[str] = []
            thinking_parts: list[str] = []
            tool_calls: list[dict] = []
            body: dict = {}
            with urllib.request.urlopen(request, timeout=effective_timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if not isinstance(chunk, dict):
                        continue
                    message = chunk.get("message") if isinstance(chunk.get("message"), dict) else {}
                    content = str(message.get("content") or "")
                    thinking = str(message.get("thinking") or "")
                    if content:
                        text_parts.append(content)
                        on_token(content)
                    if thinking:
                        thinking_parts.append(thinking)
                    chunk_calls = message.get("tool_calls")
                    if isinstance(chunk_calls, list):
                        tool_calls.extend(chunk_calls)
                    body = chunk
            final_message = body.get("message") if isinstance(body.get("message"), dict) else {}
            body["message"] = {
                **final_message,
                "role":       str(final_message.get("role") or "assistant"),
                "content":    "".join(text_parts),
                "thinking":   "".join(thinking_parts),
                "tool_calls": tool_calls,
            }
    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        error.close()
        detail = f"Ollama native chat HTTP error {error.code}: {error_body}"
        if _retry_after_runtime_failure(
            host=host,
            detail=detail,
            recovery_attempt=_recovery_attempt,
        ):
            return call_ollama_chat(
                model_name=model_name,
                messages=messages,
                tools=tools,
                host=host,
                num_ctx=num_ctx,
                timeout=timeout,
                on_token=on_token,
                _recovery_attempt=_recovery_attempt + 1,
            )
        raise RuntimeError(detail) from error
    except urllib.error.URLError as error:
        detail = f"Unable to reach Ollama at {host}: {error.reason}"
        if _retry_after_runtime_failure(
            host=host,
            detail=detail,
            recovery_attempt=_recovery_attempt,
        ):
            return call_ollama_chat(
                model_name=model_name,
                messages=messages,
                tools=tools,
                host=host,
                num_ctx=num_ctx,
                timeout=timeout,
                on_token=on_token,
                _recovery_attempt=_recovery_attempt + 1,
            )
        raise RuntimeError(detail) from error
    except TimeoutError as error:
        raise RuntimeError(f"Ollama native chat timed out after {effective_timeout}s") from error
    except json.JSONDecodeError as error:
        raise RuntimeError("Ollama native chat returned a non-JSON response") from error

    message = body.get("message") if isinstance(body.get("message"), dict) else {}
    if not message:
        raise RuntimeError(f"Ollama native chat response missing message: {body}")
    message = dict(message)
    message["tool_calls"] = _openai_tool_calls(message.get("tool_calls"))
    completion_tokens = int(body.get("eval_count") or 0)
    eval_duration_ns  = int(body.get("eval_duration") or 0)
    elapsed           = time.monotonic() - started
    tokens_per_second = (
        completion_tokens / (eval_duration_ns / 1_000_000_000)
        if completion_tokens > 0 and eval_duration_ns > 0
        else completion_tokens / elapsed if completion_tokens > 0 and elapsed > 0 else 0.0
    )
    return _core.ChatCallResult(
        message           = message,
        finish_reason     = str(body.get("done_reason") or ("tool_calls" if message["tool_calls"] else "stop")),
        prompt_tokens     = int(body.get("prompt_eval_count") or 0),
        completion_tokens = completion_tokens,
        tokens_per_second = tokens_per_second,
    )


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
    # The local Ollama route is manual by default. Auto-start remains opt-in via
    # KORE_OLLAMA_AUTOSTART for environments that still want the old behavior.
    ensure_ollama_running(host=host, start_if_needed=_core.get_local_ollama_autostart_enabled())

    preview = trunc(prompt.replace("\n", " "), 32)
    ctx_str = f"{num_ctx:,}" if num_ctx is not None else "default"
    _core.log_to_session(f"[LLM call] {model_name} | ctx={ctx_str} | {preview!r}")

    options = _core.get_ollama_request_options(num_ctx)

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
                available_models = list_ollama_models(host=host, start_if_needed=False)
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
