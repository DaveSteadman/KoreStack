from pathlib import Path

from config import get_suite_datauser_dir, load_config

_SECTION = "korerag"

_DEFAULTS = {
    "port": 8803,
    "host": "0.0.0.0",
    "log_level": "info",
    "data_dir": str(get_suite_datauser_dir() / "KoreData" / "RAG"),
}

cfg = load_config(_SECTION, _DEFAULTS)
cfg["data_dir"] = str(Path(cfg["data_dir"]).resolve())
