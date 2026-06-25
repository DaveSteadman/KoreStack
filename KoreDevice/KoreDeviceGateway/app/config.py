# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Configuration helpers for KoreDevice/KoreDeviceGateway/app.
# Centralises environment-derived settings and default values used by this component.
# ====================================================================================================

import os

from config import _DEVICE_SUBSERVICE_OFFSETS, load_config

_SECTION = "koredevicegateway"

_DEFAULTS = {
    "port":                 None,
    "host":                 "0.0.0.0",
    "log_level":            "info",
    "koredevicenumber_url": None,
    "koredevicedriver_url": None,
}


def load() -> dict:
    result       = load_config(_SECTION, _DEFAULTS)
    gateway_port = result["port"]
    host         = str(result.get("host") or "127.0.0.1").strip() or "127.0.0.1"
    svc_cfg      = load_config("koredevicenumber", {"port": gateway_port + _DEVICE_SUBSERVICE_OFFSETS["koredevicenumber"]})
    drv_cfg      = load_config("koredevicedriver", {"port": gateway_port + _DEVICE_SUBSERVICE_OFFSETS["koredevicedriver"]})
    result["koredevicenumber_url"] = f"http://{host}:{svc_cfg['port']}"
    result["koredevicedriver_url"] = f"http://{host}:{drv_cfg['port']}"
    return result


cfg = load()
