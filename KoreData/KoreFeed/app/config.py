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
