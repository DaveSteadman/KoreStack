# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Suite configuration helpers shared by all KoreData sub-services.
#
# Provides get_suite_root(), get_suite_dataroot_dir(), get_suite_datacontrol_dir(), and
# get_suite_datauser_dir() which locate the KoreStack suite directories by traversing from
# __file__.
# load_config(section) reads config/korestack_config.json and returns the section
# dict. _DATA_SUBSERVICE_OFFSETS maps service names to port offsets from the
# gateway base port.
#
# Respects the KORE_SUITE_ROOT environment variable for non-standard installations.
#
# Related modules:
#   - KoreDataGateway/app/config.py  -- uses _DATA_SUBSERVICE_OFFSETS to build child URLs
#   - KoreReference/app/config.py, KoreFeed/app/config.py, etc. -- call load_config()
# ====================================================================================================
import os
import json
from functools import lru_cache
from pathlib import Path

_SUITE_ROOT   = Path(__file__).resolve().parents[2]  # KoreStack/
_CONFIG_FILE  = _SUITE_ROOT / "config" / "korestack_config.json"


@lru_cache(maxsize=1)
def _load_paths_config() -> dict:
    paths: dict = {}
    if not _CONFIG_FILE.exists():
        return {}

    with open(_CONFIG_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw.get("paths"), dict):
        paths.update(raw["paths"])
    return paths


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


def _resolve_local_configured_root(key: str) -> Path | None:
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


def get_suite_datauser_dir() -> Path:
    env_path = os.environ.get("KORE_SUITE_DATAUSER", "").strip()
    if env_path:
        return Path(env_path).resolve()
    return get_suite_dataroot_dir() / "datauser"


def get_koredata_dir() -> Path:
    env_path = os.environ.get("KOREDATA_DATA_DIR", "").strip()
    if env_path:
        return Path(env_path).resolve()
    return get_suite_datacontrol_dir() / "koredata"


def get_required_local_datacontrol_dir() -> Path:
    configured = _resolve_local_configured_root("dataroot")
    if configured is None:
        raise RuntimeError("KoreGraph requires paths.dataroot to be set in config/korestack_config.json.")
    return (configured / "datacontrol").resolve()


# KoreData sub-services are assigned ports as offsets from the gateway ("data") port.
# This means changing the gateway port in korestack_config.json automatically shifts all sub-services.
_DATA_SUBSERVICE_OFFSETS: dict[str, int] = {
    "korefeed":      1,
    "korelibrary":   2,
    "korerag":       3,
    "korereference": 4,
    "korescrape":    5,
    "koregraph":     6,
}


def load_config(section: str, defaults: dict) -> dict:
    """Load config from central korestack_config.json.

    Resolution order:
      1. *defaults*
      2. ``network.host`` + ``services.data.port`` offset (for sub-services) from korestack_config.json
      3. ``services.<section>.port`` from korestack_config.json (explicit override)
      4. ``<section>`` dict from korestack_config.json
    """
    result = dict(defaults)
    offset = _DATA_SUBSERVICE_OFFSETS.get(section)
    if not _CONFIG_FILE.exists():
        return result

    with open(_CONFIG_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    host = raw.get("network", {}).get("host")
    if host is not None:
        result["host"] = host
    if "log_level" in raw:
        result["log_level"] = raw["log_level"]
    # Sub-services: derive port from data gateway port + fixed offset
    if offset is not None:
        data_port = raw.get("services", {}).get("koredatagateway", {}).get("port")
        if data_port is not None:
            result["port"] = data_port + offset
    # Explicit port entry still takes priority if present
    port = raw.get("services", {}).get(section, {}).get("port")
    if port is not None:
        result["port"] = port
    result.update(raw.get(section, {}))
    if result.get("port") is None:
        raise RuntimeError(f"Missing services.{section}.port in config/korestack_config.json.")
    return result
