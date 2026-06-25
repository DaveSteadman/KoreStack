# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Configuration helpers for KoreDevice/KoreDeviceNumber/app.
# Centralises environment-derived settings and default values used by this component.
# ====================================================================================================

from pathlib import Path

from config import get_koredevice_dir, load_config

_SECTION = "koredevicenumber"

_DEFAULTS = {
    "port":      None,
    "host":      "0.0.0.0",
    "data_dir":  str(get_koredevice_dir() / "Numbers"),
    "log_level": "info",
}

cfg = load_config(_SECTION, _DEFAULTS)
cfg["data_dir"] = str(Path(cfg["data_dir"]).resolve())
