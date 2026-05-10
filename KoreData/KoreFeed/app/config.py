# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreFeed configuration loader.
#
# Reads host, port, and data_dir from the suite-level config under the [korefeed]
# section.  Default port: 8801  (gateway 8620 + offset 1).
#
# Related modules:
#   - KoreFeed/main.py      -- imports cfg at startup
#   - KoreFeed/app/server.py -- imports cfg for uvicorn bind and data_dir
# ====================================================================================================
from pathlib import Path

from config import get_suite_datauser_dir, load_config

_SECTION = "korefeed"

_DEFAULTS = {
    "port": 8801,
    "host": "0.0.0.0",
    "data_dir": str(get_suite_datauser_dir() / "KoreData" / "Feeds"),
    "log_level": "info",
}

cfg = load_config(_SECTION, _DEFAULTS)
cfg["data_dir"] = str(Path(cfg["data_dir"]).resolve())
