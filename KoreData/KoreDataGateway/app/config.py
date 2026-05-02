import os

from config import load_config, _DATA_SUBSERVICE_OFFSETS

_SECTION = "data"

_DEFAULTS = {
    "port":     int(os.environ.get("KOREDATA_PORT", "8800")),
    "host":     "0.0.0.0",
    "log_level": "info",
    # Sub-service URL defaults match default.json data port + offsets
    "korefeed_url":      "http://127.0.0.1:8801",
    "korelibrary_url":   "http://127.0.0.1:8802",
    "korerag_url":       "http://127.0.0.1:8803",
    "korereference_url": "http://127.0.0.1:8804",
}

# Sub-service name -> url key in cfg
_SVC_URL_KEYS = {
    "korefeed":      "korefeed_url",
    "korelibrary":   "korelibrary_url",
    "korerag":       "korerag_url",
    "korereference": "korereference_url",
}


def load() -> dict:
    result = load_config(_SECTION, _DEFAULTS)
    # Sub-service URLs are derived from gateway port + offsets (same logic as load_config).
    gateway_port = result["port"]
    for svc, url_key in _SVC_URL_KEYS.items():
        offset = _DATA_SUBSERVICE_OFFSETS[svc]
        result[url_key] = f"http://127.0.0.1:{gateway_port + offset}"
    return result


cfg = load()
