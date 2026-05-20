# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreComms configuration loader.
#
# Reads host, port, KoreChat URL, poll intervals, and missing-conversation policy from
# the suite-level config/default.json + config/local.json.  Exposes a module-level cfg
# dict so server.py and poller.py can import one name.
#
# Defaults:
#   port:                          8900  (env: KORECOMMS_PORT)
#   korechat_url:                  http://localhost:8700
#   poll_interval:                 60  (seconds)
#   event_poll_interval:           5   (seconds)
#   missing_kc_conversation_policy: "create"
#
# Related modules:
#   - app/server.py   -- imports cfg
#   - app/poller.py   -- reads poll_interval from cfg
# ====================================================================================================
import json
import os
from pathlib import Path

_SUITE_ROOT   = Path(os.environ.get("KORE_SUITE_ROOT", str(Path(__file__).resolve().parents[2]))).resolve()
_CONFIG_FILE  = _SUITE_ROOT / "config" / "default.json"
_LOCAL_CONFIG = _SUITE_ROOT / "config" / "local.json"
_DEFAULT_DATA_DIR = _SUITE_ROOT / "datacontrol" / "korecomms"

_DEFAULTS: dict = {
    "host": os.environ.get("KORECOMMS_HOST", "0.0.0.0"),
    "port": int(os.environ.get("KORECOMMS_PORT", "8900")),
    "log_level": os.environ.get("KORECOMMS_LOG_LEVEL", "info"),
    "poll_interval": 60,
    "event_poll_interval": 1.0,
    "missing_kc_conversation_policy": "recreate",
    "data_dir": os.environ.get("KORECOMMS_DATA_DIR", str(_DEFAULT_DATA_DIR)),
    "korechat_url": os.environ.get("KORECOMMS_KORECHAT_URL", "http://localhost:8630"),
}


def _load() -> dict:
    result = dict(_DEFAULTS)
    for cfg_path in (_CONFIG_FILE, _LOCAL_CONFIG):
        if not cfg_path.exists():
            continue
        with open(cfg_path, encoding="utf-8") as f:
            raw = json.load(f)
        host = raw.get("network", {}).get("host")
        if host is not None:
            result["host"] = host
        port = raw.get("services", {}).get("korecomms", {}).get("port")
        if port is not None:
            result["port"] = port
        korechat = raw.get("connections", {}).get("korechat")
        if korechat is not None:
            result["korechat_url"] = korechat
    result["data_dir"] = str(Path(result["data_dir"]).resolve())
    # Env vars always take final precedence over config file values
    # (KoreStack injects the correct port at spawn time via env)
    for key, env_var in [
        ("host",               "KORECOMMS_HOST"),
        ("port",               "KORECOMMS_PORT"),
        ("log_level",          "KORECOMMS_LOG_LEVEL"),
        ("data_dir",           "KORECOMMS_DATA_DIR"),
        ("korechat_url", "KORECOMMS_KORECHAT_URL"),
    ]:
        val = os.environ.get(env_var)
        if val is not None:
            result[key] = int(val) if key == "port" else val
    return result


cfg = _load()
