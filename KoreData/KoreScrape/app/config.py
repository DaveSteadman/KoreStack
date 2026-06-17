from pathlib import Path

from config import get_koredata_dir, load_config

_SECTION = "korescrape"

_DEFAULTS = {
    "port":       8805,
    "host":       "0.0.0.0",
    "log_level":  "info",
    "data_dir":   str(get_koredata_dir() / "KoreScrape"),
    "max_pages":  200,
    "user_agent": "KoreScrape/1.0",
}

cfg = load_config(_SECTION, _DEFAULTS)
cfg["data_dir"] = str(Path(cfg["data_dir"]).resolve())
