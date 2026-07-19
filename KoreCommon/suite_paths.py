from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Shared suite path and KoreData service-configuration helpers.
#
# Centralises suite-root discovery, well-known suite data paths, and the
# KoreData-specific service config/url resolution that was previously housed
# under KoreData/CommonCode/config.py.
# ====================================================================================================

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def get_workspace_root() -> Path:
    return Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def get_suite_root() -> Path:
    env_root = os.environ.get("KORE_SUITE_ROOT", "").strip()
    if env_root:
        return Path(env_root).resolve()

    workspace_root = get_workspace_root().resolve()
    parent         = workspace_root.parent
    if (parent / "config" / "korestack_config.json").exists():
        return parent.resolve()
    return workspace_root


@lru_cache(maxsize=1)
def get_suite_config_file() -> Path:
    configured = os.environ.get("KORE_SUITE_CONFIG", "").strip()
    if configured:
        return Path(configured).resolve()
    return (get_suite_root() / "config" / "korestack_config.json").resolve()


def _read_json_file(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=1)
def load_suite_config() -> dict[str, Any]:
    cfg_path = get_suite_config_file()
    if not cfg_path.exists():
        return {}
    return _read_json_file(cfg_path)


@lru_cache(maxsize=1)
def _load_paths_config() -> dict[str, Any]:
    raw   = load_suite_config()
    paths = raw.get("paths")
    return dict(paths) if isinstance(paths, dict) else {}


def _resolve_configured_root(key: str) -> Path | None:
    raw_value = _load_paths_config().get(key)
    if not isinstance(raw_value, str):
        return None

    cleaned = raw_value.strip()
    if not cleaned:
        return None

    candidate = Path(cleaned)
    if any(part.lower() == "absolutepath" for part in candidate.parts):
        return None
    if not candidate.is_absolute():
        candidate = get_suite_root() / candidate
    return candidate.resolve()


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
    return (get_suite_dataroot_dir() / "datacontrol").resolve()


def get_suite_datauser_dir() -> Path:
    env_path = os.environ.get("KORE_SUITE_DATAUSER", "").strip()
    if env_path:
        return Path(env_path).resolve()
    return (get_suite_dataroot_dir() / "datauser").resolve()


def get_koredata_dir() -> Path:
    env_path = os.environ.get("KOREDATA_DATA_DIR", "").strip()
    if env_path:
        return Path(env_path).resolve()
    return (get_suite_datacontrol_dir() / "koredata").resolve()


def get_required_local_datacontrol_dir() -> Path:
    configured = _resolve_configured_root("dataroot")
    if configured is None:
        raise RuntimeError("KoreGraph requires paths.dataroot to be set in config/korestack_config.json.")
    return (configured / "datacontrol").resolve()


_DATA_SUBSERVICE_OFFSETS: dict[str, int] = {
    "korefeed":      1,
    "korelibrary":   2,
    "korerag":       3,
    "korereference": 4,
    "korescrape":    5,
    "koregraph":     6,
}


def load_config(section: str, defaults: dict[str, Any]) -> dict[str, Any]:
    """Load a KoreData service config from the suite config file."""
    result = dict(defaults)
    raw    = load_suite_config()
    offset = _DATA_SUBSERVICE_OFFSETS.get(section)
    if not raw:
        return result

    host = raw.get("network", {}).get("host")
    if host is not None:
        result["host"] = host

    if "log_level" in raw:
        result["log_level"] = raw["log_level"]

    if offset is not None:
        data_port = raw.get("services", {}).get("koredatagateway", {}).get("port")
        if data_port is not None:
            result["port"] = data_port + offset

    port = raw.get("services", {}).get(section, {}).get("port")
    if port is not None:
        result["port"] = port

    service_cfg = raw.get(section, {})
    if isinstance(service_cfg, dict):
        result.update(service_cfg)

    if result.get("port") is None:
        raise RuntimeError(f"Missing services.{section}.port in config/korestack_config.json.")

    return result


def get_suite_urls_map() -> dict[str, str]:
    env_urls = os.environ.get("KORE_SUITE_URLS", "").strip()
    if env_urls:
        try:
            parsed = json.loads(env_urls)
            if isinstance(parsed, dict) and parsed:
                normalized: dict[str, str] = {}
                for key, value in parsed.items():
                    name = str(key).strip().lower()
                    url = str(value).strip()
                    if name and url:
                        normalized[name] = url
                if normalized:
                    return normalized
        except Exception:
            pass

    raw      = load_suite_config()
    host     = str(raw.get("network", {}).get("host") or "127.0.0.1").strip() or "127.0.0.1"
    services = raw.get("services", {}) if isinstance(raw.get("services"), dict) else {}

    def _port(name: str) -> int | None:
        value = services.get(name, {}).get("port")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    urls: dict[str, str] = {}
    port_map = {
        "korestack":       _port("korestack"),
        "koreagent":       _port("koreagent"),
        "korechat":        _port("korechat"),
        "koredata":        _port("koredatagateway"),
        "koredatagateway": _port("koredatagateway"),
        "koredocs":        _port("koredocs"),
        "korecode":        _port("korecode"),
        "korecomms":       _port("korecomms"),
        "koreliveweb":     _port("koreliveweb"),
        "korefeed":        _port("korefeed"),
        "korelibrary":     _port("korelibrary"),
        "korereference":   _port("korereference"),
        "korerag":         _port("korerag"),
        "korescrape":      _port("korescrape"),
        "koregraph":       _port("koregraph"),
    }

    for name, port in port_map.items():
        if port is None:
            continue
        base_url = f"http://{host}:{port}"
        urls[name] = f"{base_url}/ui" if name in {"korechat", "koredocs", "korecode"} else f"{base_url}/"

    return urls
