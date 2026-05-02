# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Routing facade for the llm_client_*.py sub-modules.
#
# Provides a single import point for all LLM client functionality. Callers that import from
# KoreAgent.llm_client do not need to know which sub-module owns a given function.
#
# Also contains call_llm_chat - the shared OpenAI-compatible /v1/chat/completions call - which
# requires backend-routing ensure_ollama_running and list_ollama_models (both defined here
# as routing wrappers that delegate to llm_client_ollama or llm_client_lmstudio).
#
# Sub-modules:
#   - llm_client_openai.py -- Shared state, config, HTTP, data types, LM Studio management
#   - llm_client_ollama.py -- Ollama: /api/tags, /api/generate, /api/ps, process lifecycle
#
# Related callers:
#   - main.py                   -- calls configure_host(), ensure_ollama_running(), model utilities
#   - orchestration.py          -- uses call_llm_chat for the tool-calling pipeline
#   - skills_catalog_builder.py -- uses call_ollama for optional LLM skill summarisation
#   - utils/system_check.py     -- uses model listing and call_ollama for diagnostics
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import json
import time
import urllib.error
import urllib.request

import KoreAgent.llm_client_openai as _openai
import KoreAgent.llm_client_ollama as _ollama

from KoreAgent.llm_client_openai import DEFAULT_OLLAMAHOST
from KoreAgent.llm_client_openai import DEFAULT_LMSTUDIO_HOST
from KoreAgent.llm_client_openai import OLLAMA_CLOUD_HOST
from KoreAgent.llm_client_openai import HOST_ALIASES
from KoreAgent.llm_client_openai import OllamaCallResult
from KoreAgent.llm_client_openai import ChatCallResult
from KoreAgent.llm_client_openai import configure_host
from KoreAgent.llm_client_openai import configure_server
from KoreAgent.llm_client_openai import get_active_host
from KoreAgent.llm_client_openai import get_active_backend
from KoreAgent.llm_client_openai import get_active_model
from KoreAgent.llm_client_openai import get_active_num_ctx
from KoreAgent.llm_client_openai import get_llm_timeout
from KoreAgent.llm_client_openai import set_llm_timeout
from KoreAgent.llm_client_openai import register_llm_call_logger
from KoreAgent.llm_client_openai import log_to_session
from KoreAgent.llm_client_openai import register_session_config
from KoreAgent.llm_client_openai import resolve_model_name
from KoreAgent.llm_client_openai import is_explicit_model_name
from KoreAgent.llm_client_ollama  import is_ollama_running
from KoreAgent.llm_client_ollama  import start_ollama_server
from KoreAgent.llm_client_ollama  import stop_model
from KoreAgent.llm_client_ollama  import call_ollama_extended
from KoreAgent.llm_client_ollama  import call_ollama
from KoreAgent.llm_client_ollama  import get_ollama_ps_rows
from KoreAgent.llm_client_ollama  import get_running_model_row
from KoreAgent.utils.workspace_utils         import trunc


# ====================================================================================================
# MARK: ROUTING
# ====================================================================================================
def ensure_ollama_running(
    host: str | None = None,
    start_if_needed: bool = True,
    wait_seconds: float = 20.0,
    verbose: bool = False,
) -> None:
    """Ensure the configured server is reachable; auto-start Ollama locally when needed.

    Routes to the backend-specific health check based on the active backend:
    - Ollama: checks /api/tags, starts local server if needed.
    - LM Studio: checks /v1/models; no auto-start (must be started manually).
    """
    host = host or _openai.get_active_host()
    if _openai._is_lmstudio_host(host):
        _openai.ensure_lmstudio_reachable(host)
        return
    _ollama.ensure_ollama_running(
        host=host,
        start_if_needed=start_if_needed,
        wait_seconds=wait_seconds,
        verbose=verbose,
    )


# ----------------------------------------------------------------------------------------------------
def list_ollama_models(host: str | None = None) -> list[str]:
    """Return the list of available model IDs from the active server.

    Routes to the backend-specific listing:
    - Ollama: calls /api/tags.
    - LM Studio: calls /v1/models.
    """
    host = host or _openai.get_active_host()
    if _openai._is_lmstudio_host(host):
        return _openai.list_lmstudio_models(host)
    return _ollama.list_ollama_models(host=host)


# ----------------------------------------------------------------------------------------------------
def format_running_model_report(model_name: str) -> str:
    """Return a one-line runtime status string for the given model name.

    Routes to the backend-specific implementation.
    """
    if _openai._is_lmstudio_host(_openai.get_active_host()):
        return _openai.format_lmstudio_model_report(model_name)
    return _ollama.format_running_model_report(model_name)


# ====================================================================================================
# MARK: CHAT WITH TOOLS
# ====================================================================================================
def call_llm_chat(
    model_name: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    host: str | None = None,
    num_ctx: int | None = None,
    timeout: int | None = None,
) -> ChatCallResult:
    """Call /v1/chat/completions (OpenAI-compatible) and return a ChatCallResult.

    Supports optional tool definitions for native tool calling. Compatible with Ollama,
    LM Studio, and any OpenAI-format server. The num_ctx value is passed in an Ollama
    extensions 'options' block and is silently ignored by non-Ollama servers.
    """
    host = host or _openai.get_active_host()
    # Check/start server before each call; routes to backend-specific health check.
    ensure_ollama_running(host=host, start_if_needed=True)

    last_user = next(
        (trunc(m.get("content", ""), 32) for m in reversed(messages) if m.get("role") == "user"),
        "",
    )
    ctx_str  = f"{num_ctx:,}" if num_ctx is not None else "default"
    tool_str = f" | {len(tools)} tools" if tools else ""
    log_to_session(f"[LLM chat] {model_name} | ctx={ctx_str}{tool_str} | {last_user!r}")

    payload: dict = {
        "model":    model_name,
        "messages": messages,
        "stream":   False,
    }
    if tools:
        payload["tools"] = tools
    if num_ctx is not None:
        payload["options"] = {"num_ctx": num_ctx}

    effective_timeout = timeout if timeout is not None else _openai.get_llm_timeout()
    start_time        = time.monotonic()

    try:
        body = _openai._request_json(
            url=f"{host.rstrip('/')}/v1/chat/completions",
            method="POST",
            payload=payload,
            timeout=effective_timeout,
        )
    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        if error.code == 404 and "not found" in error_body.lower():
            available_models = []
            try:
                available_models = list_ollama_models(host=host)
            except Exception:
                pass
            if available_models:
                raise RuntimeError(
                    f"Model '{model_name}' not found. Installed models: {', '.join(available_models)}"
                ) from error
        raise RuntimeError(f"LLM chat HTTP error {error.code}: {error_body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Unable to reach server at {host}: {error.reason}") from error
    except TimeoutError as error:
        raise RuntimeError(f"LLM chat timed out after {effective_timeout}s") from error
    except json.JSONDecodeError as error:
        raise RuntimeError("LLM chat returned a non-JSON response") from error

    elapsed = time.monotonic() - start_time

    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError(f"LLM chat response has no choices: {body}")

    choice        = choices[0]
    message       = choice.get("message") or {}
    finish_reason = choice.get("finish_reason") or "stop"

    # Debug: log unexpected empty-content responses so we can see the raw message structure.
    if not (message.get("content") or "").strip() and not (message.get("tool_calls") or []):
        log_to_session(f"[debug] empty content - message keys: {list(message.keys())!r}; "
                       f"finish_reason={finish_reason!r}; "
                       f"thinking_preview={trunc(str(message.get('thinking', '')), 120)!r}")

    usage             = body.get("usage") or {}
    prompt_tokens     = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    tps               = completion_tokens / elapsed if elapsed > 0 and completion_tokens > 0 else 0.0

    return ChatCallResult(
        message=message,
        finish_reason=finish_reason,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        tokens_per_second=tps,
    )
