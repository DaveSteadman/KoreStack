# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Public Ollama client facade.
#
# KoreAgent uses Ollama's native API for model discovery, runtime management, and
# chat with tools. This module provides the stable import surface used throughout
# the application; implementation remains in the focused core and Ollama modules.
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import llm_client_ollama as _ollama

from llm_client_core import ChatCallResult
from llm_client_core import configure_host
from llm_client_core import get_active_host
from llm_client_core import get_active_max_predict
from llm_client_core import get_active_model
from llm_client_core import get_active_num_ctx
from llm_client_core import get_llm_timeout
from llm_client_core import is_explicit_model_name
from llm_client_core import log_to_session
from llm_client_core import register_llm_call_logger
from llm_client_core import register_session_config
from llm_client_core import resolve_model_name
from llm_client_core import set_llm_timeout
from llm_client_ollama import DEFAULT_OLLAMAHOST
from llm_client_ollama import OLLAMA_CLOUD_HOST
from llm_client_ollama import OllamaCallResult
from llm_client_ollama import call_ollama
from llm_client_ollama import call_ollama_extended
from llm_client_ollama import configure_ollama_sampling_options
from llm_client_ollama import ensure_ollama_running
from llm_client_ollama import format_running_model_report
from llm_client_ollama import get_ollama_offload_mode
from llm_client_ollama import get_ollama_ps_rows
from llm_client_ollama import get_ollama_request_options
from llm_client_ollama import get_ollama_sampling_config
from llm_client_ollama import get_running_model_row
from llm_client_ollama import is_ollama_running
from llm_client_ollama import list_ollama_models
from llm_client_ollama import recover_ollama_runtime
from llm_client_ollama import set_ollama_offload_mode
from llm_client_ollama import stop_model


__all__ = [
    "DEFAULT_OLLAMAHOST",
    "OLLAMA_CLOUD_HOST",
    "OllamaCallResult",
    "ChatCallResult",
    "configure_host",
    "configure_ollama_sampling_options",
    "get_active_host",
    "get_active_model",
    "get_active_num_ctx",
    "get_active_max_predict",
    "get_ollama_offload_mode",
    "get_ollama_sampling_config",
    "get_ollama_request_options",
    "get_llm_timeout",
    "set_llm_timeout",
    "register_llm_call_logger",
    "log_to_session",
    "register_session_config",
    "set_ollama_offload_mode",
    "resolve_model_name",
    "is_explicit_model_name",
    "is_ollama_running",
    "recover_ollama_runtime",
    "stop_model",
    "call_ollama_extended",
    "call_ollama",
    "get_running_model_row",
    "get_ollama_ps_rows",
    "is_llm_running",
    "ensure_ollama_running",
    "list_ollama_models",
    "format_running_model_report",
    "call_llm_chat",
]


# ====================================================================================================
# MARK: PUBLIC OPERATIONS
# ====================================================================================================
def is_llm_running(host: str | None = None) -> bool:
    """Return whether the configured Ollama server is reachable."""
    try:
        return _ollama.is_ollama_running(host or get_active_host())
    except Exception:
        return False


def call_llm_chat(
    model_name: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    host: str | None = None,
    num_ctx: int | None = None,
    timeout: int | None = None,
    on_token = None,
) -> ChatCallResult:
    """Call Ollama's native ``/api/chat`` endpoint with optional tools."""
    return _ollama.call_ollama_chat(
        model_name = model_name,
        messages   = messages,
        tools      = tools,
        host       = host,
        num_ctx    = num_ctx,
        timeout    = timeout,
        on_token   = on_token,
    )
