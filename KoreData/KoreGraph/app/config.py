# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreGraph configuration loader.
#
# Reads host, port, and data_dir from the suite-level config under the [koregraph]
# section.  Default port: 8826  (gateway 8620 + offset 6).
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

from config import get_koredata_dir, load_config

_SECTION = "koregraph"

_DEFAULTS = {
    "port": 8826,
    "host": "0.0.0.0",
    "log_level": "info",
    "data_dir": str(get_koredata_dir() / "Graph"),
    "ui_prefix": os.environ.get("KG_UI_PREFIX", ""),
}

cfg = load_config(_SECTION, _DEFAULTS)
cfg["data_dir"] = str(Path(cfg["data_dir"]).resolve())
