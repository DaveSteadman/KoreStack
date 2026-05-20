# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreCode configuration loader.
#
# Reads host and port from the suite-level config/default.json + config/local.json.
# Exposes a module-level cfg dict so server.py can import one name.
#
# Defaults:
#   port: 8610   (env: KORECODE_PORT)
#   host: 0.0.0.0
#
# Related modules:
#   - app/server.py        -- imports cfg, load()
#   - KoreData/CommonCode/config.py  -- shared load_config() pattern
# ====================================================================================================
from __future__ import annotations

import json
import os
from pathlib import Path

_SUITE_ROOT   = Path(__file__).resolve().parents[2]   # KoreStack/
_CONFIG_FILE  = _SUITE_ROOT / "config" / "default.json"
_LOCAL_CONFIG = _SUITE_ROOT / "config" / "local.json"

_DEFAULTS = {
    "port": int(os.environ.get("KORECODE_PORT", "5600")),
    "host": "127.0.0.1",
    "log_level": "info",
}

def load() -> dict:
    """
    Loads the application configuration by merging defaults with settings from config files.

    The function checks for the existence of both the primary configuration file 
    (_CONFIG_FILE) and a local configuration override (_LOCAL_CONFIG). If these 
    files exist, it parses them as JSON and extracts specific networking and 
    service parameters (host, log_level, and port) to override the default values.

    Returns:
        dict: A dictionary containing the merged configuration settings.
    """
    result = dict(_DEFAULTS)
    for cfg_path in (_CONFIG_FILE, _LOCAL_CONFIG):
        if not cfg_path.exists():
            continue
        with open(cfg_path, encoding="utf-8") as f:
            raw = json.load(f)
        host = raw.get("network", {}).get("host")
        if host is not None:
            result["host"] = host
        if "log_level" in raw:
            result["log_level"] = raw["log_level"]
        port = raw.get("services", {}).get("korecode", {}).get("port")
        if port is not None:
            result["port"] = port
    return result

cfg = load()
