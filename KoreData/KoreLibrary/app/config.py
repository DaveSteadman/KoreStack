from pathlib import Path

from config import get_suite_datauser_dir, load_config

_SECTION = "korelibrary"

_DEFAULTS = {
    "port": 8802,
    "host": "0.0.0.0",
    "data_dir": str(get_suite_datauser_dir() / "KoreData" / "Library"),
    "log_level": "info",
}

cfg = load_config(_SECTION, _DEFAULTS)
cfg["data_dir"] = str(Path(cfg["data_dir"]).resolve())
