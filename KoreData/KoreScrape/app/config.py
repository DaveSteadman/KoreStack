from pathlib import Path

from config import get_koredata_dir, load_config

_SECTION = "korescrape"


def _default_data_dir() -> str:
    base_dir = get_koredata_dir()
    new_dir  = (base_dir / "Scrape").resolve()
    old_dir  = (base_dir / "KoreScrape").resolve()

    if not new_dir.exists() and old_dir.exists():
        try:
            old_dir.replace(new_dir)
        except OSError:
            pass
    return str(new_dir if new_dir.exists() or not old_dir.exists() else old_dir)

_DEFAULTS = {
    "port":       8805,
    "host":       "0.0.0.0",
    "log_level":  "info",
    "data_dir":   _default_data_dir(),
    "max_pages":  200,
    "user_agent": "KoreScrape/1.0",
}

cfg = load_config(_SECTION, _DEFAULTS)
cfg["data_dir"] = str(Path(cfg["data_dir"]).resolve())
