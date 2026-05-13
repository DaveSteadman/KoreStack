# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreChat client helpers for KoreAgent.
#
# KoreChat is a peer service managed by KoreStack (or started externally).
# This module provides the configured base URL and a reachability check.
#
# Configuration (default.json / connections.korechat):
#   "korechaturl": "http://127.0.0.1:8630"
#
# Related modules:
#   - input_layer/server.py                    -- uses get_base_url() for API endpoints
#   - input_layer/slash_command_handlers_sessions.py -- uses get_base_url() for session commands
#   - workspace_utils.py                       -- flattens connections.korechat -> korechaturl
# ====================================================================================================

import urllib.request

from utils.workspace_utils import load_runtime_config


# ====================================================================================================
# MARK: STATE
# ====================================================================================================

_base_url: str | None = None  # cached on first call to get_base_url()


# ====================================================================================================
# MARK: HELPERS
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def _reachable(url: str) -> bool:
    try:
        urllib.request.urlopen(f"{url}/status", timeout=3)
        return True
    except Exception:
        return False


# ====================================================================================================
# MARK: STATUS QUERY
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def is_reachable() -> bool:
    """Return True if the configured KoreChat service responds at /status."""
    url = get_base_url()
    if not url:
        return False
    return _reachable(url)


# ----------------------------------------------------------------------------------------------------
def get_base_url() -> str | None:
    """Return the configured KoreChat base URL, reading config on first call."""
    global _base_url
    if _base_url is None:
        try:
            raw = load_runtime_config()
            url = str(raw.get("korechaturl", "")).strip().rstrip("/")
            if url:
                _base_url = url
        except Exception:
            pass
    return _base_url
