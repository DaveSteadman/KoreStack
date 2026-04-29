import json
import os
from pathlib import Path

_CONFIG_FILE = Path("config/default.json")
_SUITE_ROOT = Path(os.environ.get("KORE_SUITE_ROOT", str(Path(__file__).resolve().parents[2].parent))).resolve()
_DEFAULT_DATA_DIR = _SUITE_ROOT / "datacontrol" / "korecomms"

_DEFAULTS: dict = {
    "host": os.environ.get("KORECOMMS_HOST", "0.0.0.0"),
    "port": int(os.environ.get("KORECOMMS_PORT", "8900")),
    "log_level": os.environ.get("KORECOMMS_LOG_LEVEL", "info"),
    "poll_interval": 60,
    "event_poll_interval": 1.0,
    "missing_kc_conversation_policy": "recreate",
    "data_dir": os.environ.get("KORECOMMS_DATA_DIR", str(_DEFAULT_DATA_DIR)),
    "koreconversation_url": os.environ.get("KORECOMMS_KORECONVERSATION_URL", "http://localhost:8700"),
}


def _load() -> dict:
    result = dict(_DEFAULTS)
    if not _CONFIG_FILE.exists():
        result["data_dir"] = str(Path(result["data_dir"]).resolve())
        return result
    with open(_CONFIG_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    result.update(raw)
    result["data_dir"] = str(Path(result["data_dir"]).resolve())
    return result


cfg = _load()
