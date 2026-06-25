# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Configuration helpers for KoreDevice/KoreDeviceDriver/app.
# Centralises environment-derived settings and default values used by this component.
# ====================================================================================================

from pathlib import Path

from config import get_koredevice_dir, load_config

_SECTION = "koredevicedriver"

_DEFAULTS = {
    "port":                  None,
    "host":                  "0.0.0.0",
    "data_dir":              str(get_koredevice_dir() / "Drivers"),
    "log_level":             "info",
    "default_protocol":      "modbus-tcp",
    "default_vendor":        "Example Vendor",
    "default_poll_interval": 5,
}

cfg = load_config(_SECTION, _DEFAULTS)
cfg["data_dir"] = str(Path(cfg["data_dir"]).resolve())
