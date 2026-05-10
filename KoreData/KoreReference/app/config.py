# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreReference configuration loader.
#
# Reads host, port, and data_dir from the suite-level config under the [korereference]
# section.  Default port: 8804  (gateway 8620 + offset 4).
#
# Related modules:
#   - KoreReference/main.py    -- imports cfg at startup
#   - KoreReference/app/server.py -- imports cfg for uvicorn bind
# ====================================================================================================
from pathlib import Path

from config import get_suite_datacontrol_dir, load_config

_SECTION = "korereference"

_DEFAULTS = {
    "port": 8804,
    "host": "0.0.0.0",
    "data_dir": str(get_suite_datacontrol_dir() / "koredata" / "Reference"),
    "log_level": "info",
}

cfg = load_config(_SECTION, _DEFAULTS)
cfg["data_dir"] = str(Path(cfg["data_dir"]).resolve())
