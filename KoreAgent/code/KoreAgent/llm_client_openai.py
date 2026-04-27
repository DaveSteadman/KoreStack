# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Shared state and OpenAI-compatible core for the llm_client_*.py sub-modules.
#
# Contains everything that is not Ollama-proprietary:
#   - Module-level connection state and all accessor/mutator functions.
#   - Host configuration and backend detection utilities, including configure_server() for
#     explicit backend targeting.
#   - LM Studio health check, /v1/models listing, and model report.
#   - Health-check cache helpers used by both backends.
#   - The _request_json HTTP helper (thread-safe, hard timeout enforcement).
#   - OllamaCallResult and ChatCallResult data structures.
#   - Model name resolution utilities (resolve_model_name, is_explicit_model_name).
#
# Related modules:
#   - llm_client_ollama.py -- Ollama-specific: model management, process lifecycle, /api/generate
#   - llm_client.py        -- Routing facade: re-exports all public names + call_llm_chat
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from KoreAgent.utils.workspace_utils import trunc


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
DEFAULT_OLLAMAHOST    = "http://localhost:11434"
OLLAMA_CLOUD_HOST     = "https://api.ollama.com"
DEFAULT_LMSTUDIO_HOST = "http://localhost:1234"
_DEFAULT_LLM_TIMEOUT: int = 600   # seconds; updated at runtime by /timeout slash command

# Active host and backend - set once at startup via configure_host() or configure_server().
# Default to local Ollama; overridden by --llmhost / LLMHOST env var.
# backend is "ollama" or "lmstudio".
_active_host:    str = DEFAULT_OLLAMAHOST
_active_backend: str = "ollama"

# Active session model and context window - set once at startup via register_session_config().
# Skills use get_active_model() / get_active_num_ctx() instead of accepting these as parameters.
_active_model:   str = ""
_active_num_ctx: int = 131072

# Cache of last successful server health-check time per host.
# Avoids an HTTP round-trip on every LLM call (many calls/prompt = unnecessary health hits).
_ollama_health_cache: dict[str, float] = {}  # host -> monotonic time of last healthy check
_ollama_health_lock:  threading.Lock   = threading.Lock()
_OLLAMA_HEALTH_TTL_S: float = 30.0           # re-check if not confirmed healthy within this window


# ====================================================================================================
# MARK: TIMEOUT
# ====================================================================================================
def get_llm_timeout() -> int:
    """Return the current default LLM generation timeout in seconds."""
    return _DEFAULT_LLM_TIMEOUT


def set_llm_timeout(seconds: int) -> None:
    """Update the default LLM generation timeout used by all LLM call functions."""
    global _DEFAULT_LLM_TIMEOUT
    _DEFAULT_LLM_TIMEOUT = seconds


# ====================================================================================================
# MARK: LOGGING
# ====================================================================================================
_llm_call_log_fn = None   # optional (str) -> None; set via register_llm_call_logger


def register_llm_call_logger(fn) -> None:
    """Register a callback invoked before every LLM call.

    The callback receives a single formatted string describing the call so it can
    be written to whatever log sink the caller controls.
    """
    global _llm_call_log_fn
    _llm_call_log_fn = fn


# ----------------------------------------------------------------------------------------------------
def log_to_session(message: str) -> None:
    """Write a message to the active session log sink (if one is registered).

    Skills and other non-UI code should use this instead of print() so that output
    is routed to the log file rather than stdout, which would corrupt the TUI.
    If no logger has been registered the message is written to stderr so useful
    diagnostic output is not silently discarded during startup or in non-interactive runs.
    """
    if _llm_call_log_fn is not None:
        try:
            _llm_call_log_fn(message)
        except Exception as exc:
            import sys
            print(f"[log_to_session] Logger callback failed: {exc} | msg: {message}", file=sys.stderr)
    else:
        import sys
        print(message, file=sys.stderr)


# ====================================================================================================
# MARK: SESSION CONFIG
# ====================================================================================================
def register_session_config(model: str, num_ctx: int) -> None:
    """Register the active session model and context window.

    Called once at startup (and again whenever /llmserverconfig model or ctx changes them) so that
    thick skills can read the ambient values without needing them passed as parameters.
    """
    global _active_model, _active_num_ctx
    _active_model   = model
    _active_num_ctx = num_ctx


def get_active_model() -> str:
    """Return the currently active session model name."""
    return _active_model


def get_active_num_ctx() -> int:
    """Return the currently active session context window in tokens."""
    return _active_num_ctx


# ====================================================================================================
# MARK: HEALTH CACHE
# ====================================================================================================
def mark_host_healthy(host: str) -> None:
    """Record that host was reachable and responding at the current monotonic time."""
    with _ollama_health_lock:
        _ollama_health_cache[host] = time.monotonic()


def is_host_health_cached(host: str) -> bool:
    """Return True when host was confirmed healthy within the cache TTL window."""
    with _ollama_health_lock:
        return time.monotonic() - _ollama_health_cache.get(host, 0.0) < _OLLAMA_HEALTH_TTL_S


# ====================================================================================================
# MARK: CONFIGURATION
# ====================================================================================================

# Well-known host aliases accepted by configure_host() and the --llmhost CLI flag.
HOST_ALIASES: dict[str, str] = {
    "local":      DEFAULT_OLLAMAHOST,
    "localhost":  DEFAULT_OLLAMAHOST,
    "lmstudio":   DEFAULT_LMSTUDIO_HOST,
}


def configure_host(host: str) -> None:
    """Set the active host and backend for all subsequent LLM calls.

    Accepts well-known aliases ('local', 'localhost', 'lmstudio') and bare hostnames/IPs;
    bare values (no '://') are expanded to http://<host>:11434 automatically.
    The 'lmstudio' alias resolves to http://localhost:1234 and selects the LM Studio backend.

    Stored as module-level state; mirrors the pattern used by set_llm_timeout().
    """
    global _active_host, _active_backend
    resolved = HOST_ALIASES.get(host.strip().lower(), host.strip())
    if "://" not in resolved:
        resolved = f"http://{resolved}:11434"
    _active_host    = resolved.rstrip("/")
    _active_backend = "lmstudio" if _is_lmstudio_host(_active_host) else "ollama"


# ----------------------------------------------------------------------------------------------------
def configure_server(backend: str, host: str | None = None) -> None:
    """Configure the active server with an explicit backend type and optional host override.

    backend: "ollama" or "lmstudio"
    host:    optional URL or bare hostname; defaults to the backend's standard local address.
             Bare hostnames (no '://') are expanded using the backend's default port.
    """
    global _active_host, _active_backend
    backend = backend.lower().strip()
    if backend not in ("ollama", "lmstudio"):
        raise ValueError(f"Unknown backend '{backend}'. Use 'ollama' or 'lmstudio'.")
    if host is None:
        resolved = DEFAULT_LMSTUDIO_HOST if backend == "lmstudio" else DEFAULT_OLLAMAHOST
    else:
        host = host.strip()
        if "://" not in host:
            # Only append the default port when no port is already present.
            # "MONTBLANC:1234" already has a port; "MONTBLANC" does not.
            if ":" not in host:
                default_port = "1234" if backend == "lmstudio" else "11434"
                resolved     = f"http://{host}:{default_port}"
            else:
                resolved = f"http://{host}"
        else:
            resolved = host
    _active_host    = resolved.rstrip("/")
    _active_backend = backend


# ----------------------------------------------------------------------------------------------------
def get_active_host() -> str:
    """Return the currently configured server host URL."""
    return _active_host


def get_active_backend() -> str:
    """Return the currently configured backend: 'ollama' or 'lmstudio'."""
    return _active_backend


# ----------------------------------------------------------------------------------------------------
def _is_local_host(host: str) -> bool:
    return "localhost" in host or "127.0.0.1" in host or "0.0.0.0" in host


def _is_lmstudio_host(host: str) -> bool:
    # Detected by port 1234 - LM Studio's default and conventional port.
    return ":1234" in host


# ====================================================================================================
# MARK: DATA TYPES
# ====================================================================================================
@dataclass
class OllamaCallResult:
    """Structured return from call_ollama_extended, including token usage and throughput."""
    response:                str
    prompt_tokens:           int
    completion_tokens:       int
    total_tokens:            int
    eval_duration_ns:        int = 0   # nanoseconds the model spent generating completion tokens
    prompt_eval_duration_ns: int = 0   # nanoseconds the model spent evaluating the prompt

    @property
    def tokens_per_second(self) -> float:
        """Completion token generation rate (tok/s). Returns 0.0 when timing is unavailable."""
        if self.eval_duration_ns <= 0 or self.completion_tokens <= 0:
            return 0.0
        return self.completion_tokens / (self.eval_duration_ns / 1_000_000_000)


# ----------------------------------------------------------------------------------------------------
@dataclass
class ChatCallResult:
    """Structured return from call_llm_chat, covering token usage and optional tool calls."""
    message:           dict    # full assistant message: {"role", "content", "tool_calls"?}
    finish_reason:     str     # "stop" | "tool_calls"
    prompt_tokens:     int
    completion_tokens: int
    tokens_per_second: float

    @property
    def response(self) -> str:
        """Text content of the assistant message. Empty when the model issued tool_calls instead.

        Falls back to the 'thinking' field (Ollama 0.18+ reasoning models) when 'content' is
        absent, stripping the surrounding <think>...</think> wrapper so callers see plain text.
        """
        content = (self.message.get("content") or "").strip()
        if content:
            return content
        thinking = (self.message.get("thinking") or self.message.get("reasoning") or "").strip()
        if thinking:
            # Strip <think>...</think> wrapper if present, then return the raw reasoning as
            # a last-resort answer so the caller always gets something actionable.
            thinking = re.sub(r"^<think>\s*", "", thinking, flags=re.IGNORECASE)
            thinking = re.sub(r"\s*</think>$", "", thinking, flags=re.IGNORECASE)
            return thinking.strip()
        return ""

    @property
    def tool_calls(self) -> list[dict]:
        """Tool call objects requested by the model, or an empty list."""
        return self.message.get("tool_calls") or []


# ====================================================================================================
# MARK: HTTP
# ====================================================================================================
def _request_json(url: str, method: str = "GET", payload: dict | None = None, timeout: float = 10.0) -> dict:
    request_data = None
    headers      = {}

    if payload is not None:
        request_data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url=url,
        data=request_data,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, OSError) and "timed out" in str(reason).lower():
            raise TimeoutError(f"Request timed out after {timeout:.0f}s") from exc
        raise


# ====================================================================================================
# MARK: UTILITIES
# ====================================================================================================
def resolve_model_name(requested_model: str, available_models: list[str]) -> str | None:
    # Resolution order: (1) exact match, (2) base-name prefix (e.g. "llama3" -> "llama3:8b"),
    # (3) tag suffix (e.g. "8b" -> "llama3:8b"), (4) word-boundary token match (e.g. "20b").
    # Each step only returns a result when there is exactly one candidate, to avoid ambiguity.
    requested_lower = requested_model.lower().strip()
    if not requested_lower:
        return None

    # Exact full-name match (case-insensitive).
    for model_name in available_models:
        if model_name.lower() == requested_lower:
            return model_name

    # Match when the requested string is the base name part before a colon tag.
    exact_prefix_matches = [
        model_name
        for model_name in available_models
        if model_name.lower().startswith(f"{requested_lower}:")
    ]
    if len(exact_prefix_matches) == 1:
        return exact_prefix_matches[0]

    # Match when the requested string is the tag part after the colon.
    exact_suffix_matches = [
        model_name
        for model_name in available_models
        if model_name.lower().endswith(f":{requested_lower}")
    ]
    if len(exact_suffix_matches) == 1:
        return exact_suffix_matches[0]

    # Substring match as a last resort - only accepted when exactly one model matches.
    # Use word-boundary-aware matching so that "20b" doesn't match "120b" and vice-versa.
    # The negative look-behind/ahead blocks numeric adjacency (e.g. "3" inside "qwen3-coder")
    # but allows hyphen-separated words (e.g. "cascade" matches "nemotron-cascade-2").
    token_matches = [
        model_name
        for model_name in available_models
        if re.search(rf"(?<![0-9]){re.escape(requested_lower)}(?![0-9a-z])", model_name.lower())
    ]
    if len(token_matches) == 1:
        return token_matches[0]

    return None


# ----------------------------------------------------------------------------------------------------
def is_explicit_model_name(requested_model: str) -> bool:
    """Return True when *requested_model* looks like a fully qualified model tag.

    This is intentionally lightweight: hosts such as Ollama Cloud may allow models
    that do not appear in /api/tags, so slash-command model selection should accept
    an explicit tag override like ``gpt-oss:120b-cloud`` even when discovery is stale.
    """
    requested = requested_model.strip()
    return bool(requested) and ":" in requested and not any(ch.isspace() for ch in requested)


# ====================================================================================================
# MARK: LMSTUDIO
# ====================================================================================================
def ensure_lmstudio_reachable(host: str) -> None:
    if is_host_health_cached(host):
        return
    try:
        _request_json(f"{host.rstrip('/')}/v1/models", timeout=3.0)
        mark_host_healthy(host)
    except Exception:
        raise RuntimeError(f"LM Studio is not reachable at {host}. Ensure LM Studio is running.")


# ----------------------------------------------------------------------------------------------------
def list_lmstudio_models(host: str) -> list[str]:
    try:
        body = _request_json(f"{host.rstrip('/')}/v1/models", timeout=10.0)
        return [m.get("id", "") for m in body.get("data", []) if m.get("id")]
    except Exception as exc:
        raise RuntimeError(f"Unable to list LM Studio models at {host}: {exc}") from exc


# ----------------------------------------------------------------------------------------------------
def format_lmstudio_model_report(model_name: str) -> str:
    return f"Model runtime status: {model_name} via LM Studio (runtime details not available)"

