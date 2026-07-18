# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Configuration loader for KoreChat.
#
# Reads config/korestack_config.json via the shared suite loader. Any key present in the file
# overrides the built-in default. Missing keys fall back to the defaults below so the service
# starts with no config file present.
#
# data_dir defaults to the configured suite datacontrol/korechat directory so that all persisted data
# (database, log) lands in the shared datacontrol folder alongside MiniAgentFramework data.
# The suite path resolver keeps standalone and suite-managed launches consistent.
# ====================================================================================================

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(os.environ.get("KORE_SUITE_ROOT", str(Path(__file__).resolve().parents[2]))).resolve()
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from KoreCommon.suite_config import load_service_config
from KoreCommon.suite_paths import get_suite_datacontrol_dir

_DEFAULTS: dict = {
    "host":      os.environ.get("KORECHAT_HOST", "0.0.0.0"),
    "port":      None,
    "log_level": os.environ.get("KORECHAT_LOG_LEVEL", "info"),
    "data_dir":  os.environ.get("KORECHAT_DATA_DIR", str(get_suite_datacontrol_dir() / "korechat")),
}


# ----------------------------------------------------------------------------------------------------
def _load() -> dict:
    result = load_service_config(
        service_key="korechat",
        defaults=_DEFAULTS,
        suite_root=_REPO_ROOT,
        env_overrides={
            "host": "KORECHAT_HOST",
            "log_level": "KORECHAT_LOG_LEVEL",
            "data_dir": "KORECHAT_DATA_DIR",
        },
        require_port=True,
    )
    return result


cfg = _load()
