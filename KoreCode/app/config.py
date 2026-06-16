# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreCode configuration loader.
#
# Reads host and port from the suite-level config/korestack_config.json.
# Exposes a module-level cfg dict so server.py can import one name.
#
# Defaults:
#   port: 5600   (env: KORECODE_PORT)
#   host: 127.0.0.1
#
# Related modules:
#   - app/server.py        -- imports cfg, load()
#   - KoreCommon/suite_config.py -- shared service config loader
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
    "port": int(os.environ.get("KORECODE_PORT", "5600")),
    "host": "127.0.0.1",
    "log_level": "info",
}


def load() -> dict:
    return load_service_config(
        service_key="korecode",
        defaults=_DEFAULTS,
        suite_root=_SUITE_ROOT,
        env_overrides={
            "host": "KORECODE_HOST",
            "port": "KORECODE_PORT",
            "log_level": "KORECODE_LOG_LEVEL",
        },
    )


cfg = load()
