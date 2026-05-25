# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreDocs configuration loader.
#
# Reads host and port from the suite-level config/default.json + config/local.json.
# Exposes a module-level cfg dict so server.py can import one name.
#
# Defaults:
#   port: 8615   (env: KOREDOCS_PORT)
#   host: 0.0.0.0
#
# Related modules:
#   - app/server.py  -- imports cfg, load()
# ====================================================================================================
from __future__ import annotations

import os
import sys
from pathlib import Path

_SUITE_ROOT = Path(__file__).resolve().parents[2]   # KoreStack/
if str(_SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SUITE_ROOT))

from KoreCommon.suite_config import load_service_config

_DEFAULTS = {
    "port": int(os.environ.get("KOREDOCS_PORT", "5500")),
    "host": "127.0.0.1",
    "log_level": "info",
}


def load() -> dict:
    return load_service_config(
        service_key="koredocs",
        defaults=_DEFAULTS,
        suite_root=_SUITE_ROOT,
        env_overrides={
            "host": "KOREDOCS_HOST",
            "port": "KOREDOCS_PORT",
            "log_level": "KOREDOCS_LOG_LEVEL",
        },
    )


cfg = load()
