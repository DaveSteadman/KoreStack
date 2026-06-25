# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Database helpers for KoreDevice/KoreDeviceDriver/app.
# Owns persistence access patterns, schema-facing helpers, and storage utilities for this component.
# ====================================================================================================

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from app.config import cfg

_LOCK          = RLock()
_DATA_DIR      = Path(cfg["data_dir"])
_REGISTRY_PATH = _DATA_DIR / "drivers.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_registry() -> list[dict]:
    if not _REGISTRY_PATH.exists():
        return []
    try:
        raw = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    return raw if isinstance(raw, list) else []


def _write_registry(entries: list[dict]) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _REGISTRY_PATH.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _default_python_snippet(name: str) -> str:
    return (
        "def read_driver(context):\n"
        f"    # context contains metadata for '{name}' plus any runtime adapter state.\n"
        "    # Replace this boilerplate with the real protocol call.\n"
        "    return {\n"
        "        'ok': True,\n"
        "        'samples': [],\n"
        "        'message': 'Driver boilerplate not implemented yet.',\n"
        "    }\n"
    )


def _normalize_entry(entry: dict) -> dict:
    normalized = dict(entry)
    name       = str(normalized.get("name") or "").strip()
    normalized["name"]              = name
    normalized["display_name"]      = str(normalized.get("display_name") or name).strip()
    normalized["vendor"]            = str(normalized.get("vendor") or cfg["default_vendor"]).strip()
    normalized["protocol"]          = str(normalized.get("protocol") or cfg["default_protocol"]).strip()
    normalized["transport_address"] = str(normalized.get("transport_address") or "").strip()
    normalized["poll_interval_sec"] = int(normalized.get("poll_interval_sec") or int(cfg["default_poll_interval"]))
    normalized["enabled"]           = bool(normalized.get("enabled"))
    normalized["description"]       = str(normalized.get("description") or "").strip()
    normalized["python_snippet"]    = str(normalized.get("python_snippet") or _default_python_snippet(name)).rstrip() + "\n"
    if not normalized.get("created_at"):
        normalized["created_at"] = _utc_now()
    if not normalized.get("updated_at"):
        normalized["updated_at"] = normalized["created_at"]
    return normalized


def init_db() -> None:
    with _LOCK:
        entries = _read_registry()
        if entries:
            return
        now = _utc_now()
        _write_registry(
            [
                _normalize_entry(
                    {
                    "name":              "ExamplePLC",
                    "display_name":      "Example PLC Driver",
                    "vendor":            str(cfg["default_vendor"]),
                    "protocol":          str(cfg["default_protocol"]),
                    "transport_address": "host:port",
                    "poll_interval_sec": int(cfg["default_poll_interval"]),
                    "enabled":           False,
                    "description":       "Boilerplate driver entry created on first start.",
                    "python_snippet":    _default_python_snippet("ExamplePLC"),
                    "created_at":        now,
                    "updated_at":        now,
                    }
                )
            ]
        )


def get_status() -> dict:
    with _LOCK:
        entries = _read_registry()
    latest = max((entry.get("updated_at") or entry.get("created_at") or "" for entry in entries), default=None)
    return {
        "ok":                    True,
        "service":               "KoreDeviceDriver",
        "registry_path":         str(_REGISTRY_PATH),
        "total_drivers":         len(entries),
        "enabled_drivers":       sum(1 for entry in entries if entry.get("enabled")),
        "default_protocol":      str(cfg["default_protocol"]),
        "default_vendor":        str(cfg["default_vendor"]),
        "default_poll_interval": int(cfg["default_poll_interval"]),
        "last_updated_at":       latest,
    }


def list_drivers() -> list[dict]:
    with _LOCK:
        entries = [_normalize_entry(entry) for entry in _read_registry()]
    return sorted(entries, key=lambda entry: str(entry.get("name") or "").lower())


def get_driver(name: str) -> dict | None:
    clean_name = name.strip().lower()
    if not clean_name:
        return None

    with _LOCK:
        entries = _read_registry()
    for entry in entries:
        normalized = _normalize_entry(entry)
        if normalized["name"].lower() == clean_name:
            return normalized
    return None


def add_driver(
    *,
    name:              str,
    display_name:      str | None = None,
    vendor:            str | None = None,
    protocol:          str | None = None,
    transport_address: str | None = None,
    poll_interval_sec: int | None = None,
    enabled:           bool       = False,
    description:       str | None = None,
    python_snippet:    str | None = None,
) -> dict:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Driver name is required")

    with _LOCK:
        entries = _read_registry()
        if any(str(entry.get("name") or "").lower() == clean_name.lower() for entry in entries):
            raise ValueError(f"Driver '{clean_name}' already exists")

        now   = _utc_now()
        entry = _normalize_entry(
            {
            "name":              clean_name,
            "display_name":      (display_name or clean_name).strip(),
            "vendor":            (vendor or str(cfg["default_vendor"])).strip(),
            "protocol":          (protocol or str(cfg["default_protocol"])).strip(),
            "transport_address": (transport_address or "").strip(),
            "poll_interval_sec": int(poll_interval_sec or int(cfg["default_poll_interval"])),
            "enabled":           bool(enabled),
            "description":       (description or "").strip(),
            "python_snippet":    python_snippet or _default_python_snippet(clean_name),
            "created_at":        now,
            "updated_at":        now,
            }
        )
        entries.append(entry)
        _write_registry(entries)
        return entry


def update_driver(
    *,
    name:              str,
    display_name:      str | None = None,
    vendor:            str | None = None,
    protocol:          str | None = None,
    transport_address: str | None = None,
    poll_interval_sec: int | None = None,
    enabled:           bool       = False,
    description:       str | None = None,
    python_snippet:    str | None = None,
) -> dict:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Driver name is required")

    with _LOCK:
        entries = _read_registry()
        for index, entry in enumerate(entries):
            normalized = _normalize_entry(entry)
            if normalized["name"].lower() != clean_name.lower():
                continue

            updated = _normalize_entry(
                {
                    **normalized,
                    "display_name":      display_name,
                    "vendor":            vendor,
                    "protocol":          protocol,
                    "transport_address": transport_address,
                    "poll_interval_sec": poll_interval_sec,
                    "enabled":           enabled,
                    "description":       description,
                    "python_snippet":    python_snippet,
                    "updated_at":        _utc_now(),
                }
            )
            entries[index] = updated
            _write_registry(entries)
            return updated

    raise ValueError(f"Driver '{clean_name}' not found")


def delete_driver(name: str) -> dict:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Driver name is required")

    with _LOCK:
        entries = _read_registry()
        for index, entry in enumerate(entries):
            normalized = _normalize_entry(entry)
            if normalized["name"].lower() != clean_name.lower():
                continue

            removed = entries.pop(index)
            _write_registry(entries)
            return {
                "ok":          True,
                "deleted":     _normalize_entry(removed),
                "remaining":   len(entries),
                "deleted_at":  _utc_now(),
            }

    raise ValueError(f"Driver '{clean_name}' not found")
