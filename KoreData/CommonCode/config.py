# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Suite configuration helpers shared by all KoreData sub-services.
#
# Provides get_suite_root(), get_suite_datacontrol_dir(), and get_suite_datauser_dir()
# which locate the KoreStack suite directories by traversing from __file__.
# load_config(section) reads config/default.json + config/local.json and returns the
# merged section dict.  _DATA_SUBSERVICE_OFFSETS maps service names to port offsets
# from the gateway base port.
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
_CONFIG_FILE  = _SUITE_ROOT / "config" / "default.json"
_LOCAL_CONFIG = _SUITE_ROOT / "config" / "local.json"


@lru_cache(maxsize=1)
def _load_paths_config() -> dict:
    paths: dict = {}
    for cfg_path in (_CONFIG_FILE, _LOCAL_CONFIG):
        if not cfg_path.exists():
            continue
        with open(cfg_path, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw.get("paths"), dict):
            paths.update(raw["paths"])
    return paths


@lru_cache(maxsize=1)
def _load_local_paths_config() -> dict:
    if not _LOCAL_CONFIG.exists():
        return {}

    with open(_LOCAL_CONFIG, encoding="utf-8") as f:
        raw = json.load(f)
    return raw["paths"] if isinstance(raw.get("paths"), dict) else {}


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
    raw_value = _load_local_paths_config().get(key)
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


def get_suite_datacontrol_dir() -> Path:
    env_path = os.environ.get("KORE_SUITE_DATACONTROL", "").strip()
    if env_path:
        return Path(env_path).resolve()
    configured = _resolve_configured_root("datacontrolroot")
    if configured is not None:
        return configured
    return get_suite_root() / "datacontrol"


def get_suite_datauser_dir() -> Path:
    env_path = os.environ.get("KORE_SUITE_DATAUSER", "").strip()
    if env_path:
        return Path(env_path).resolve()
    configured = _resolve_configured_root("datauserroot")
    if configured is not None:
        return configured
    return get_suite_root() / "datauser"


def get_koredata_dir() -> Path:
    env_path = os.environ.get("KOREDATA_DATA_DIR", "").strip()
    if env_path:
        return Path(env_path).resolve()
    return get_suite_datacontrol_dir() / "koredata"


def get_required_local_datacontrol_dir() -> Path:
    configured = _resolve_local_configured_root("datacontrolroot")
    if configured is None:
        raise RuntimeError(
            "KoreGraph requires paths.datacontrolroot to be set in config/local.json."
        )
    return configured


# KoreData sub-services are assigned ports as offsets from the gateway ("data") port.
# This means changing the gateway port in local.json automatically shifts all sub-services.
_DATA_SUBSERVICE_OFFSETS: dict[str, int] = {
    "korefeed":      1,
    "korelibrary":   2,
    "korerag":       3,
    "korereference": 4,
    "koregraph":     6,
}


def load_config(section: str, defaults: dict) -> dict:
    """Load config from central default.json + local.json.

    Resolution order (later wins):
      1. *defaults*
      2. ``network.host`` + ``services.data.port`` offset (for sub-services) from default.json
      3. ``services.<section>.port`` from default.json (explicit override)
      4. ``<section>`` dict from default.json
      5. Same three steps repeated for local.json
    """
    result = dict(defaults)
    offset = _DATA_SUBSERVICE_OFFSETS.get(section)
    for cfg_path in (_CONFIG_FILE, _LOCAL_CONFIG):
        if not cfg_path.exists():
            continue
        with open(cfg_path, encoding="utf-8") as f:
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
    return result
