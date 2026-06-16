# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Configuration loader for KoreChat.
#
# Reads config/korestack_config.json via the shared suite loader. Any key present in the file
# overrides the built-in default. Missing keys fall back to the defaults below so the service
# starts with no config file present.
#
# data_dir defaults to <repo_root>/datacontrol/korechat so that all persisted data
# (database, log) lands in the shared datacontrol folder alongside MiniAgentFramework data.
# The repo root is inferred from this file's location:
#   KoreChat/app/config.py  ->  parents[2]  ->  repo root
# This is resilient to the working directory at launch time.
# ====================================================================================================

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(os.environ.get("KORE_SUITE_ROOT", str(Path(__file__).resolve().parents[2]))).resolve()
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from KoreCommon.suite_config import load_service_config

_DEFAULTS: dict = {
    "host":      os.environ.get("KORECHAT_HOST", "0.0.0.0"),
    "port":      int(os.environ.get("KORECHAT_PORT", "8700")),
    "log_level": os.environ.get("KORECHAT_LOG_LEVEL", "info"),
    "data_dir":  os.environ.get("KORECHAT_DATA_DIR", str(_REPO_ROOT / "datacontrol" / "korechat")),
}


# ----------------------------------------------------------------------------------------------------
def _load() -> dict:
    result = load_service_config(
        service_key="korechat",
        defaults=_DEFAULTS,
        suite_root=_REPO_ROOT,
        env_overrides={
            "host": "KORECHAT_HOST",
            "port": "KORECHAT_PORT",
            "log_level": "KORECHAT_LOG_LEVEL",
            "data_dir": "KORECHAT_DATA_DIR",
        },
    )
    return result


cfg = _load()
