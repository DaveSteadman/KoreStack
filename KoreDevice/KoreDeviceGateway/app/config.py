import os

from config import _DEVICE_SUBSERVICE_OFFSETS, load_config

_SECTION = "koredevicegateway"

_DEFAULTS = {
    "port":                 int(os.environ.get("KOREDEVICE_PORT", "9613")),
    "host":                 "0.0.0.0",
    "log_level":            "info",
    "koredevicenumber_url": "http://127.0.0.1:9614",
}


def load() -> dict:
    result       = load_config(_SECTION, _DEFAULTS)
    gateway_port = result["port"]
    svc_cfg      = load_config("koredevicenumber", {"port": gateway_port + _DEVICE_SUBSERVICE_OFFSETS["koredevicenumber"]})
    result["koredevicenumber_url"] = f"http://127.0.0.1:{svc_cfg['port']}"
    return result


cfg = load()
