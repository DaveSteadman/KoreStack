# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreComms configuration loader.
#
# Reads host, port, KoreChat URL, poll intervals, and missing-conversation policy from
# the suite-level config/korestack_config.json.  Exposes a module-level cfg
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
import os
import sys
from pathlib import Path

_SUITE_ROOT = Path(os.environ.get("KORE_SUITE_ROOT", str(Path(__file__).resolve().parents[2]))).resolve()
if str(_SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SUITE_ROOT))

from KoreCommon.suite_config import load_service_config
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
    def _merge_connections(result: dict, raw: dict) -> None:
        korechat = raw.get("connections", {}).get("korechat")
        if korechat is not None:
            result["korechat_url"] = korechat

    result = load_service_config(
        service_key="korecomms",
        defaults=_DEFAULTS,
        suite_root=_SUITE_ROOT,
        env_overrides={
            "host": "KORECOMMS_HOST",
            "port": "KORECOMMS_PORT",
            "log_level": "KORECOMMS_LOG_LEVEL",
            "data_dir": "KORECOMMS_DATA_DIR",
            "korechat_url": "KORECOMMS_KORECHAT_URL",
        },
        raw_merger=_merge_connections,
    )
    result["data_dir"] = str(Path(result["data_dir"]).resolve())
    return result


cfg = _load()
