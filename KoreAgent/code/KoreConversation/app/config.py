# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Configuration loader for KoreConversation.
#
# Reads config/default.json relative to the working directory. Any key present in the file
# overrides the built-in default. Missing keys fall back to the defaults below so the service
# starts with no config file present.
#
# data_dir defaults to <repo_root>/datacontrol/conversations so that all persisted data
# (database, log) lands in the shared datacontrol folder alongside MiniAgentFramework data.
# The repo root is inferred from this file's location:
#   code/KoreConversation/app/config.py  ->  parents[3]  ->  repo root
# This is resilient to the working directory at launch time.
# ====================================================================================================

import json
import os
from pathlib import Path

_CONFIG_FILE = Path("config/default.json")

# Repo root is three levels above this file (code/KoreConversation/app/config.py)
_REPO_ROOT = Path(os.environ.get("KORE_SUITE_ROOT", str(Path(__file__).resolve().parents[3]))).resolve()

_DEFAULTS: dict = {
    "host":      os.environ.get("KORECONVERSATION_HOST", "0.0.0.0"),
    "port":      int(os.environ.get("KORECONVERSATION_PORT", "8700")),
    "log_level": os.environ.get("KORECONVERSATION_LOG_LEVEL", "info"),
    "data_dir":  os.environ.get("KORECONVERSATION_DATA_DIR", str(_REPO_ROOT / "datacontrol" / "conversations")),
}


# ----------------------------------------------------------------------------------------------------
def _load() -> dict:
    result = dict(_DEFAULTS)
    if not _CONFIG_FILE.exists():
        return result
    with open(_CONFIG_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    # Resolve a relative data_dir against repo root for consistency
    if "data_dir" in raw:
        p = Path(raw["data_dir"])
        if not p.is_absolute():
            raw["data_dir"] = str(_REPO_ROOT / p)
    result.update(raw)
    return result


cfg = _load()
