# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreDataGateway configuration loader.
#
# Reads host, port from the suite-level config under the [data] section and computes
# child sub-service base URLs from _DATA_SUBSERVICE_OFFSETS:
#   KoreFeed      -- base + 1 (8621)
#   KoreLibrary   -- base + 2 (8622)
#   KoreRAG       -- base + 3 (8623)
#   KoreReference -- base + 4 (8624)
#   KoreScrape    -- base + 5 (8625)
#   KoreGraph     -- base + 6 (8626)
# Default gateway port: 8620.
#
# Related modules:
#   - KoreDataGateway/main.py    -- imports cfg at startup
#   - KoreDataGateway/app/server.py -- imports cfg and sub-service URLs
# ====================================================================================================
import os

from config import load_config, _DATA_SUBSERVICE_OFFSETS

_SECTION = "data"

_DEFAULTS = {
    "port":     int(os.environ.get("KOREDATA_PORT", "8620")),
    "host":     "0.0.0.0",
    "log_level": "info",
    # Sub-service URLs are overwritten at load() time using gateway port + offsets.
    # These values are only ever used if load_config fails to read any config file.
    "korefeed_url":      "http://127.0.0.1:8621",
    "korelibrary_url":   "http://127.0.0.1:8622",
    "korerag_url":       "http://127.0.0.1:8623",
    "korereference_url": "http://127.0.0.1:8624",
    "korescrape_url":    "http://127.0.0.1:8625",
    "koregraph_url":     "http://127.0.0.1:8626",
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
    result = load_config(_SECTION, _DEFAULTS)
    gateway_port = result["port"]
    # For each sub-service, prefer an explicit port from config; fall back to gateway offset.
    for svc, url_key in _SVC_URL_KEYS.items():
        svc_cfg = load_config(svc, {"port": gateway_port + _DATA_SUBSERVICE_OFFSETS[svc]})
        result[url_key] = f"http://127.0.0.1:{svc_cfg['port']}"
    return result


cfg = load()
