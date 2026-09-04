# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Shared state and OpenAI-compatible core for the llm_client_*.py sub-modules.
#
# Contains everything that is not backend-proprietary:
#   - Module-level connection state and all accessor/mutator functions.
#   - Host configuration and backend detection utilities, including configure_server() for
#     explicit backend targeting.
#   - Health-check cache helpers used by both backends.
#   - The _request_json HTTP helper (thread-safe, hard timeout enforcement).
#   - OllamaCallResult and ChatCallResult data structures.
#   - Model name resolution utilities (resolve_model_name, is_explicit_model_name).
#
# Related modules:
#   - llm_client_ollama.py   -- Ollama-specific: model management, process lifecycle, /api/generate
#   - llm_client_lmstudio.py -- LM Studio-specific: health check, /v1/models listing, model report
#   - llm_client.py          -- Routing facade: re-exports all public names + call_llm_chat
# MARK: FUNCTIONS
# Primary types: OllamaCallResult, ChatCallResult.
# Function inventory:
# - _default_llm_timeout_from_env: Implements the  default llm timeout from env operation for this module.
# - get_local_ollama_autostart_enabled: Returns local ollama autostart enabled for this module.
# - get_llm_timeout: Returns llm timeout for this module.
# - set_llm_timeout: Sets llm timeout for this module.
# - register_llm_call_logger: Registers llm call logger for this module.
# - log_to_session: Implements the log to session operation for this module.
# - register_session_config: Registers session config for this module.
# - get_active_model: Returns active model for this module.
# - get_ollama_offload_mode: Returns ollama offload mode for this module.
# - set_ollama_offload_mode: Sets ollama offload mode for this module.
# - get_ollama_request_options: Returns ollama request options for this module.
# - get_active_num_ctx: Returns active num ctx for this module.
# - mark_host_healthy: Marks host healthy for this module.
# - invalidate_host_health: Invalidates host health for this module.
# - is_host_health_cached: Checks whether host health cached is true.
# - configure_host: Implements the configure host operation for this module.
# - configure_server: Implements the configure server operation for this module.
# - get_active_host: Returns active host for this module.
# - get_active_backend: Returns active backend for this module.
# - _is_local_host: Implements the  is local host operation for this module.
# - _is_lmstudio_host: Implements the  is lmstudio host operation for this module.
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
import math
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
DEFAULT_OLLAMAHOST    = "http://localhost:11434"
OLLAMA_CLOUD_HOST     = "https://api.ollama.com"
DEFAULT_LMSTUDIO_HOST = "http://localhost:1234"


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

# Active host and backend - set once at startup via configure_host() or configure_server().
# Default to local Ollama; overridden by --llmhost / LLMHOST env var.
# backend is "ollama" or "lmstudio".
_active_host:    str = DEFAULT_OLLAMAHOST
_active_backend: str = "ollama"

# Active session model and context window - set once at startup via register_session_config().
# Skills use get_active_model() / get_active_num_ctx() instead of accepting these as parameters.
_active_model:       str = ""
_active_num_ctx:     int = 131072
_active_max_predict: int = 1024
_ollama_temperature:         float = 0.8
_ollama_temperature_enabled: bool = False
_ollama_seed:                int = 0
_ollama_seed_enabled:        bool = False
_active_state_lock: threading.RLock = threading.RLock()
_ollama_offload_mode: str = "autogpu"
_OLLAMA_OFFLOAD_MODES: frozenset[str] = frozenset({"forcecpu", "forcegpu", "autogpu"})

# Cache of last successful server health-check time per host.
# Avoids an HTTP round-trip on every LLM call (many calls/prompt = unnecessary health hits).
_ollama_health_cache: dict[str, float] = {}  # host -> monotonic time of last healthy check
_ollama_health_lock:  threading.Lock   = threading.Lock()
_OLLAMA_HEALTH_TTL_S: float = 30.0           # re-check if not confirmed healthy within this window


def get_local_ollama_autostart_enabled() -> bool:
    """Return True when local Ollama may be auto-started by the agent.

    Default is disabled so Windows setups that already run Ollama as a service do
    not get a second process manager hidden inside prompt execution.
    """
    raw = str(os.environ.get("KORE_OLLAMA_AUTOSTART", "")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


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


def get_ollama_offload_mode() -> str:
    """Return the requested Ollama CPU/GPU offload policy for new model loads."""
    with _active_state_lock:
        return _ollama_offload_mode


def set_ollama_offload_mode(mode: str) -> None:
    """Set the requested Ollama CPU/GPU offload policy."""
    normalized = mode.strip().lower()
    if normalized not in _OLLAMA_OFFLOAD_MODES:
        raise ValueError(f"Unknown Ollama offload mode: {mode}")
    global _ollama_offload_mode
    with _active_state_lock:
        _ollama_offload_mode = normalized


def _coerce_config_bool(value: object) -> bool:
    """Return a predictable Boolean for JSON configuration values."""
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def configure_ollama_sampling_options(
    temperature: object = 0.8,
    temperature_enabled: object = False,
    seed: object = 0,
    seed_enabled: object = False,
) -> None:
    """Set config-file-controlled Ollama sampling options for future requests."""
    global _ollama_temperature, _ollama_temperature_enabled, _ollama_seed, _ollama_seed_enabled
    try:
        normalized_temperature = float(temperature)
    except (TypeError, ValueError):
        normalized_temperature = 0.8
    if not math.isfinite(normalized_temperature):
        normalized_temperature = 0.8
    try:
        normalized_seed = int(seed)
    except (TypeError, ValueError):
        normalized_seed = 0

    with _active_state_lock:
        _ollama_temperature         = normalized_temperature
        _ollama_temperature_enabled = _coerce_config_bool(temperature_enabled)
        _ollama_seed                = normalized_seed
        _ollama_seed_enabled        = _coerce_config_bool(seed_enabled)


def get_ollama_sampling_config() -> dict:
    """Return the Ollama sampling values persisted by ``/defaults set``."""
    with _active_state_lock:
        return {
            "temperature":         _ollama_temperature,
            "temperature_enabled": _ollama_temperature_enabled,
            "seed":                _ollama_seed,
            "seed_enabled":        _ollama_seed_enabled,
        }


def get_ollama_request_options(num_ctx: int | None = None) -> dict:
    """Build Ollama-only request options for context, sampling, and model offload."""
    options: dict = {}
    if num_ctx is not None:
        options["num_ctx"] = num_ctx
    with _active_state_lock:
        options["num_predict"] = _active_max_predict
    if get_active_backend() != "ollama":
        return options
    with _active_state_lock:
        if _ollama_temperature_enabled:
            options["temperature"] = _ollama_temperature
        if _ollama_seed_enabled:
            options["seed"] = _ollama_seed
    mode = get_ollama_offload_mode()
    if mode == "forcecpu":
        options["num_gpu"] = 0
    elif mode == "forcegpu":
        # Ollama interprets a layer count larger than the model as all layers.
        options["num_gpu"] = 999
    return options


def get_active_num_ctx() -> int:
    """Return the currently active session context window in tokens."""
    with _active_state_lock:
        return _active_num_ctx


# ====================================================================================================
# MARK: HEALTH CACHE
# ====================================================================================================
def mark_host_healthy(host: str) -> None:
    """Record that host was reachable and responding at the current monotonic time."""
    with _ollama_health_lock:
        _ollama_health_cache[host] = time.monotonic()


def invalidate_host_health(host: str) -> None:
    """Require a fresh health check before the next request to *host*."""
    with _ollama_health_lock:
        _ollama_health_cache.pop(host, None)


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
    with _active_state_lock:
        _active_host = resolved.rstrip("/")
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
    with _active_state_lock:
        _active_host = resolved.rstrip("/")
        _active_backend = backend


# ----------------------------------------------------------------------------------------------------
def get_active_host() -> str:
    """Return the currently configured server host URL."""
    with _active_state_lock:
        return _active_host


def get_active_backend() -> str:
    """Return the currently configured backend: 'ollama' or 'lmstudio'."""
    with _active_state_lock:
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

