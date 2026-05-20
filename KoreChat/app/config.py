# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Configuration loader for KoreChat.
#
# Reads config/default.json relative to the working directory. Any key present in the file
# overrides the built-in default. Missing keys fall back to the defaults below so the service
# starts with no config file present.
#
# data_dir defaults to <repo_root>/datacontrol/korechat so that all persisted data
# (database, log) lands in the shared datacontrol folder alongside MiniAgentFramework data.
# The repo root is inferred from this file's location:
#   KoreChat/app/config.py  ->  parents[2]  ->  repo root
# This is resilient to the working directory at launch time.
# ====================================================================================================

import json
import os
from pathlib import Path

_REPO_ROOT    = Path(os.environ.get("KORE_SUITE_ROOT", str(Path(__file__).resolve().parents[2]))).resolve()
_CONFIG_FILE  = _REPO_ROOT / "config" / "default.json"
_LOCAL_CONFIG = _REPO_ROOT / "config" / "local.json"

_DEFAULTS: dict = {
    "host":      os.environ.get("KORECHAT_HOST", "0.0.0.0"),
    "port":      int(os.environ.get("KORECHAT_PORT", "8700")),
    "log_level": os.environ.get("KORECHAT_LOG_LEVEL", "info"),
    "data_dir":  os.environ.get("KORECHAT_DATA_DIR", str(_REPO_ROOT / "datacontrol" / "korechat")),
}


# ----------------------------------------------------------------------------------------------------
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
        port = raw.get("services", {}).get("korechat", {}).get("port")
        if port is not None:
            result["port"] = port
    # Env vars always take final precedence over config file values
    # (KoreStack injects the correct port at spawn time via env)
    for key, env_var in [
        ("host",      "KORECHAT_HOST"),
        ("port",      "KORECHAT_PORT"),
        ("log_level", "KORECHAT_LOG_LEVEL"),
        ("data_dir",  "KORECHAT_DATA_DIR"),
    ]:
        val = os.environ.get(env_var)
        if val is not None:
            result[key] = int(val) if key == "port" else val
    return result


cfg = _load()
