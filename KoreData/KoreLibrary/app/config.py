# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreLibrary configuration loader.
#
# Reads host, port, and data_dir from the suite-level config under the [korelibrary]
# section.
#
# Related modules:
#   - KoreLibrary/main.py      -- imports cfg at startup
#   - KoreLibrary/app/server.py -- imports cfg for uvicorn bind and data_dir
# ====================================================================================================
from pathlib import Path

from config import get_koredata_dir, load_config

_SECTION = "korelibrary"

_DEFAULTS = {
    "port": None,
    "host": "0.0.0.0",
    "data_dir": str(get_koredata_dir() / "Library"),
    "log_level": "info",
}

cfg = load_config(_SECTION, _DEFAULTS)
cfg["data_dir"] = str(Path(cfg["data_dir"]).resolve())
