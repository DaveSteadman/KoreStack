# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# LM Studio-specific client functions.
#
# Provides health checking, model listing, and runtime status for LM Studio.
# LM Studio exposes an OpenAI-compatible /v1 API; no process lifecycle management
# is needed (the user starts/stops LM Studio manually).
#
# Related modules:
#   - llm_client_openai.py -- Shared state, config, HTTP helpers, data types
#   - llm_client_ollama.py -- Ollama-specific: process lifecycle, /api/tags, /api/generate
#   - llm_client.py        -- Routing facade: re-exports all public names + call_llm_chat
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import llm_client_openai as _core


# ====================================================================================================
# MARK: HEALTH
# ====================================================================================================
def ensure_lmstudio_reachable(host: str) -> None:
    """Check that LM Studio is reachable at *host* via GET /v1/models.

    Uses the shared health-check cache so the round-trip only happens once per TTL window.
    Raises RuntimeError when the server is not responding.
    """
    if _core.is_host_health_cached(host):
        return
    try:
        _core._request_json(f"{host.rstrip('/')}/v1/models", timeout=3.0)
        _core.mark_host_healthy(host)
    except Exception:
        raise RuntimeError(f"LM Studio is not reachable at {host}. Ensure LM Studio is running.")


# ====================================================================================================
# MARK: MODEL LISTING
# ====================================================================================================
def list_lmstudio_models(host: str) -> list[str]:
    """Return the list of model IDs currently available in LM Studio via GET /v1/models."""
    try:
        body = _core._request_json(f"{host.rstrip('/')}/v1/models", timeout=10.0)
        return [m.get("id", "") for m in body.get("data", []) if m.get("id")]
    except Exception as exc:
        raise RuntimeError(f"Unable to list LM Studio models at {host}: {exc}") from exc


# ====================================================================================================
# MARK: RUNTIME STATUS
# ====================================================================================================
def format_lmstudio_model_report(model_name: str) -> str:
    """Return a one-line runtime status string for *model_name* via LM Studio."""
    return f"Model runtime status: {model_name} via LM Studio (runtime details not available)"
