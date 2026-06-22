# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreDataGateway configuration loader.
#
# Reads host and port from the suite-level config under the [koredatagateway]
# section and resolves child sub-service base URLs from the suite config.
#
# Related modules:
#   - KoreDataGateway/main.py    -- imports cfg at startup
#   - KoreDataGateway/app/server.py -- imports cfg and sub-service URLs
# ====================================================================================================
import os

from config import load_config, _DATA_SUBSERVICE_OFFSETS

_SECTION = "koredatagateway"

_DEFAULTS = {
    "port":     None,
    "host":     "0.0.0.0",
    "log_level": "info",
    "korefeed_url":      None,
    "korelibrary_url":   None,
    "korerag_url":       None,
    "korereference_url": None,
    "korescrape_url":    None,
    "koregraph_url":     None,
}

# Sub-service name -> url key in cfg
_SVC_URL_KEYS = {
    "korefeed":      "korefeed_url",
    "korelibrary":   "korelibrary_url",
    "korerag":       "korerag_url",
    "korereference": "korereference_url",
    "korescrape":    "korescrape_url",
    "koregraph":     "koregraph_url",
}


def load() -> dict:
    result       = load_config(_SECTION, _DEFAULTS)
    gateway_port = result["port"]
    host         = str(result.get("host") or "127.0.0.1").strip() or "127.0.0.1"
    # For each sub-service, prefer an explicit port from config; fall back to gateway offset.
    for svc, url_key in _SVC_URL_KEYS.items():
        svc_cfg = load_config(svc, {"port": gateway_port + _DATA_SUBSERVICE_OFFSETS[svc]})
        result[url_key] = f"http://{host}:{svc_cfg['port']}"
    return result


cfg = load()
