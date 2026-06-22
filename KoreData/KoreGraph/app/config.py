# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreGraph configuration loader.
#
# Reads host, port, and data_dir from the suite-level config under the [koregraph]
# section.
#
# ui_prefix: set via KG_UI_PREFIX env var. Empty when running standalone,
# "/graph" when deployed behind the KoreDataGateway proxy.
#
# Related modules:
#   - KoreGraph/main.py       -- imports cfg at startup
#   - KoreGraph/app/server.py -- imports cfg for uvicorn bind and data_dir
# ====================================================================================================
import os
from pathlib import Path

from config import get_required_local_datacontrol_dir, load_config

_SECTION = "koregraph"
_LOCAL_DATACONTROL_DIR = get_required_local_datacontrol_dir()
_LOCAL_KOREDATA_DIR    = _LOCAL_DATACONTROL_DIR / "koredata"

_DEFAULTS = {
    "port": None,
    "host": "0.0.0.0",
    "log_level": "info",
    "data_dir":    str(_LOCAL_KOREDATA_DIR / "Graph"),
    "scripts_dir": str(_LOCAL_KOREDATA_DIR / "Graph" / "processing"),
    "ui_prefix": os.environ.get("KG_UI_PREFIX", ""),
}

cfg = load_config(_SECTION, _DEFAULTS)
cfg["data_dir"]    = str(Path(cfg["data_dir"]).resolve())
cfg["scripts_dir"] = str(Path(cfg["scripts_dir"]).resolve())
