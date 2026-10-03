# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Shared state and HTTP utilities for the Ollama client core.
#
# Contains shared Ollama-client state and utilities:
#   - Module-level connection state and all accessor/mutator functions.
#   - Ollama-host configuration.
#   - Health-check cache helpers.
#   - The _request_json HTTP helper (thread-safe, hard timeout enforcement).
#   - The chat-result data structure.
#   - Model name resolution utilities (resolve_model_name, is_explicit_model_name).
#
# Related modules:
#   - llm_client_ollama.py   -- Ollama-specific: model management, process lifecycle, /api/generate
#   - llm_client.py        -- public facade and native chat entry point
# MARK: FUNCTIONS
# Primary types: ChatCallResult.
# Function inventory:
# - _default_llm_timeout_from_env: Implements the  default llm timeout from env operation for this module.
# - get_llm_timeout: Returns llm timeout for this module.
# - set_llm_timeout: Sets llm timeout for this module.
# - register_llm_call_logger: Registers llm call logger for this module.
# - log_to_session: Implements the log to session operation for this module.
# - register_session_config: Registers session config for this module.
# - get_active_model: Returns active model for this module.
# - get_active_num_ctx: Returns active num ctx for this module.
# - get_active_max_predict: Returns active max predict for this module.
# - mark_host_healthy: Marks host healthy for this module.
# - invalidate_host_health: Invalidates host health for this module.
# - is_host_health_cached: Checks whether host health cached is true.
# - configure_host: Implements the configure host operation for this module.
# - get_active_host: Returns active host for this module.
# - _is_local_host: Implements the  is local host operation for this module.
# - tokens_per_second: Implements the tokens per second operation for this module.
# - response: Implements the response operation for this module.
# - tool_calls: Implements the tool calls operation for this module.
# - _request_json: Implements the  request json operation for this module.
# - resolve_model_name: Resolves model name for this module.
# - is_explicit_model_name: Checks whether explicit model name is true.
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from utils.workspace_utils import trunc


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
DEFAULT_OLLAMAHOST = "http://localhost:11434"


def _default_llm_timeout_from_env() -> int:
    raw = str(os.environ.get("KORE_LLM_TIMEOUT", "")).strip()
    if not raw:
        return 600
    try:
        value = int(raw)
    except ValueError:
        return 600
    return max(value, 1)


_DEFAULT_LLM_TIMEOUT: int = _default_llm_timeout_from_env()   # seconds; updated at runtime by /timeout slash command

# Active Ollama host. Set once at startup via configure_host() and overridden by
# --llmhost / LLMHOST.
_active_host: str = DEFAULT_OLLAMAHOST

# Active chat and System One model state. The models are independently configurable
# because Ollama can keep both resident at the same time.
_active_model:            str = ""
_active_system_one_model: str = "clef:27b"
_active_num_ctx:          int = 131072
_active_max_predict:      int = 1024
_active_state_lock: threading.RLock = threading.RLock()

# Cache of last successful server health-check time per host.
# Avoids an HTTP round-trip on every LLM call (many calls/prompt = unnecessary health hits).
_host_health_cache: dict[str, float] = {}  # host -> monotonic time of last healthy check
_host_health_lock:  threading.Lock   = threading.Lock()
_HOST_HEALTH_TTL_S: float = 30.0           # re-check if not confirmed healthy within this window


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
def register_session_config(model: str, num_ctx: int, max_predict: int | None = None) -> None:
    """Register the active session model and context window.

    Called once at startup (and again whenever /llmserverconfig model or ctx changes them) so that
    thick skills can read the ambient values without needing them passed as parameters.
    """
    global _active_model, _active_num_ctx, _active_max_predict
    with _active_state_lock:
        _active_model   = model
        _active_num_ctx = num_ctx
        if max_predict is not None:
            _active_max_predict = max(1, int(max_predict))


def get_active_model() -> str:
    """Return the currently active session model name."""
    with _active_state_lock:
        return _active_model


def register_system_one_model(model: str) -> None:
    """Register the System One decision model for future decision calls."""
    normalized = str(model or "").strip()
    if not normalized:
        raise ValueError("System One model name cannot be blank")
    global _active_system_one_model
    with _active_state_lock:
        _active_system_one_model = normalized


def get_active_system_one_model() -> str:
    """Return the configured Ollama System One decision model name."""
    with _active_state_lock:
        return _active_system_one_model


def get_active_num_ctx() -> int:
    """Return the currently active session context window in tokens."""
    with _active_state_lock:
        return _active_num_ctx


def get_active_max_predict() -> int:
    """Return the configured maximum number of generated tokens."""
    with _active_state_lock:
        return _active_max_predict


# ====================================================================================================
# MARK: HEALTH CACHE
# ====================================================================================================
def mark_host_healthy(host: str) -> None:
    """Record that host was reachable and responding at the current monotonic time."""
    with _host_health_lock:
        _host_health_cache[host] = time.monotonic()


def invalidate_host_health(host: str) -> None:
    """Require a fresh health check before the next request to *host*."""
    with _host_health_lock:
        _host_health_cache.pop(host, None)


def is_host_health_cached(host: str) -> bool:
    """Return True when host was confirmed healthy within the cache TTL window."""
    with _host_health_lock:
        return time.monotonic() - _host_health_cache.get(host, 0.0) < _HOST_HEALTH_TTL_S


# ====================================================================================================
# MARK: CONFIGURATION
# ====================================================================================================

def configure_host(host: str) -> None:
    """Set the Ollama host used for all subsequent model calls.

    Bare hostnames are expanded to ``http://<host>:11434``. A URL keeps its
    supplied scheme and port, allowing a remote Ollama server to be used.
    """
    global _active_host
    resolved = host.strip()
    if resolved.lower() == "local":
        resolved = DEFAULT_OLLAMAHOST
    if "://" not in resolved:
        resolved = f"http://{resolved}" if ":" in resolved else f"http://{resolved}:11434"
    with _active_state_lock:
        _active_host = resolved.rstrip("/")


# ----------------------------------------------------------------------------------------------------
def get_active_host() -> str:
    """Return the currently configured Ollama host URL."""
    with _active_state_lock:
        return _active_host


def _is_local_host(host: str) -> bool:
    return "localhost" in host or "127.0.0.1" in host or "0.0.0.0" in host


# ====================================================================================================
# MARK: DATA TYPES
# ====================================================================================================
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
        """Return answer content; reasoning is diagnostic output, not a completed answer."""
        return (self.message.get("content") or "").strip()

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
    # Human-friendly abbreviations such as "light" should match "nemotron-3.5-lightning".
    # Retain numeric boundaries for shortcuts such as "20b", so they cannot select "120b".
    if requested_lower[0].isdigit() or requested_lower[-1].isdigit():
        substring_matches = [
            model_name
            for model_name in available_models
            if re.search(rf"(?<![0-9]){re.escape(requested_lower)}(?![0-9a-z])", model_name.lower())
        ]
    else:
        substring_matches = [
            model_name
            for model_name in available_models
            if requested_lower in model_name.lower()
        ]
    if len(substring_matches) == 1:
        return substring_matches[0]

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

