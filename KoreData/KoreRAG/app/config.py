# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreRAG configuration loader.
#
# Reads host, port, and data_dir from the suite-level config under the [korerag]
# section.  Default port: 8803  (gateway 8620 + offset 3).
#
# Related modules:
#   - KoreRAG/main.py      -- imports cfg at startup
#   - KoreRAG/app/server.py -- imports cfg for uvicorn bind and data_dir
# ====================================================================================================
from pathlib import Path

from config import get_koredata_dir, load_config

_SECTION = "korerag"

_DEFAULTS = {
    "port": 8803,
    "host": "0.0.0.0",
    "log_level": "info",
    "data_dir": str(get_koredata_dir() / "RAG"),
}

cfg = load_config(_SECTION, _DEFAULTS)
cfg["data_dir"] = str(Path(cfg["data_dir"]).resolve())
