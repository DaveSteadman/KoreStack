"""Read KoreCode port/host from the central suite config."""
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
        port = raw.get("services", {}).get("code", {}).get("port")
        if port is not None:
            result["port"] = port
    return result


cfg = load()
