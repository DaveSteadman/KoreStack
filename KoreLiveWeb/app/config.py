from __future__ import annotations

import sys
from pathlib import Path

_SUITE_ROOT = Path(__file__).resolve().parents[2]
if str(_SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SUITE_ROOT))

from KoreCommon.suite_config import load_service_config

_DEFAULTS = {
    "port":      None,
    "host":      "127.0.0.1",
    "log_level": "info",
}


def load() -> dict:
    return load_service_config(
        service_key   = "koreliveweb",
        defaults      = _DEFAULTS,
        suite_root    = _SUITE_ROOT,
        env_overrides = {
            "host":      "KORELIVEWEB_HOST",
            "log_level": "KORELIVEWEB_LOG_LEVEL",
        },
        require_port  = True,
    )


cfg = load()
