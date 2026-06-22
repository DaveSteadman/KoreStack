# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Suite configuration helpers shared by all KoreDevice sub-services.
# ====================================================================================================
import json
import os
from functools import lru_cache
from pathlib import Path

_SUITE_ROOT  = Path(__file__).resolve().parents[2]
_CONFIG_FILE = _SUITE_ROOT / "config" / "korestack_config.json"


@lru_cache(maxsize=1)
def _load_paths_config() -> dict:
    if not _CONFIG_FILE.exists():
        return {}

    with open(_CONFIG_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    return raw.get("paths", {}) if isinstance(raw.get("paths"), dict) else {}


def _resolve_configured_root(key: str) -> Path | None:
    raw_value = _load_paths_config().get(key)
    if not isinstance(raw_value, str):
        return None

    raw_value = raw_value.strip()
    if not raw_value:
        return None

    candidate = Path(raw_value)
    if any(part.lower() == "absolutepath" for part in candidate.parts):
        return None
    if not candidate.is_absolute():
        candidate = get_suite_root() / candidate
    return candidate.resolve()


def get_suite_root() -> Path:
    env_root = os.environ.get("KORE_SUITE_ROOT", "").strip()
    if env_root:
        return Path(env_root).resolve()
    return _SUITE_ROOT


def get_suite_dataroot_dir() -> Path:
    env_path = os.environ.get("KORE_SUITE_DATAROOT", "").strip()
    if env_path:
        return Path(env_path).resolve()
    configured = _resolve_configured_root("dataroot")
    if configured is not None:
        return configured
    return get_suite_root()


def get_suite_datacontrol_dir() -> Path:
    env_path = os.environ.get("KORE_SUITE_DATACONTROL", "").strip()
    if env_path:
        return Path(env_path).resolve()
    return get_suite_dataroot_dir() / "datacontrol"


def get_koredevice_dir() -> Path:
    env_path = os.environ.get("KOREDEVICE_DATA_DIR", "").strip()
    if env_path:
        return Path(env_path).resolve()
    return get_suite_datacontrol_dir() / "koredevice"


_DEVICE_SUBSERVICE_OFFSETS: dict[str, int] = {
    "koredevicenumber": 1,
    "koredevicedriver": 2,
}


def load_config(section: str, defaults: dict) -> dict:
    result = dict(defaults)
    offset = _DEVICE_SUBSERVICE_OFFSETS.get(section)
    if not _CONFIG_FILE.exists():
        return result

    with open(_CONFIG_FILE, encoding="utf-8") as f:
        raw = json.load(f)

    host = raw.get("network", {}).get("host")
    if host is not None:
        result["host"] = host
    if "log_level" in raw:
        result["log_level"] = raw["log_level"]

    if offset is not None:
        gateway_port = raw.get("services", {}).get("koredevicegateway", {}).get("port")
        if gateway_port is not None:
            result["port"] = gateway_port + offset

    port = raw.get("services", {}).get(section, {}).get("port")
    if port is not None:
        result["port"] = port

    result.update(raw.get(section, {}))
    if result.get("port") is None:
        raise RuntimeError(f"Missing services.{section}.port in config/korestack_config.json.")
    return result
