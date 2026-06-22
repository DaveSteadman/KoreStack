# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreFeed configuration loader.
#
# Reads host, port, and data_dir from the suite-level config under the [korefeed]
# section.
#
# Related modules:
#   - KoreFeed/main.py      -- imports cfg at startup
#   - KoreFeed/app/server.py -- imports cfg for uvicorn bind and data_dir
# ====================================================================================================
from pathlib import Path

from config import get_koredata_dir, load_config

_SECTION = "korefeed"

_DEFAULTS = {
    "port": None,
    "host": "0.0.0.0",
    "data_dir": str(get_koredata_dir() / "Feeds"),
    "log_level": "info",
}

cfg = load_config(_SECTION, _DEFAULTS)
cfg["data_dir"] = str(Path(cfg["data_dir"]).resolve())
