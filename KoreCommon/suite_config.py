from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Suite configuration loading helpers.
# Loads and normalises shared suite configuration used across multiple services.
# ====================================================================================================

import json
import os
from typing import Any, Callable

from pathlib import Path

from KoreCommon.suite_paths import get_suite_config_file


RawMerger = Callable[[dict[str, Any], dict[str, Any]], None]


def _coerce_env_value(raw_value: str, current_value: Any) -> Any:
    if isinstance(current_value, bool):
        return raw_value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(current_value, int) and not isinstance(current_value, bool):
        return int(raw_value)
    if isinstance(current_value, float):
        return float(raw_value)
    return raw_value


def _read_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def load_service_config(
    *,
    service_key: str,
    defaults: dict[str, Any],
    suite_root: Path,
    env_overrides: dict[str, str] | None = None,
    raw_merger: RawMerger | None = None,
    require_port: bool = False,
) -> dict[str, Any]:
    """Load service config from config/korestack_config.json.

    Merge order is defaults -> korestack_config.json -> env overrides.
    Common keys resolved from the suite config are:
      - network.host      -> result['host']
      - log_level         -> result['log_level']
      - services.<key>.port -> result['port']
    """
    result = dict(defaults)
    default_cfg_path = get_suite_config_file()
    cfg_path         = Path(
        os.environ.get(
            "KORE_SUITE_CONFIG",
            str((suite_root / "config" / "korestack_config.json").resolve()),
        )
    ).resolve()
    if str(default_cfg_path) == str(cfg_path):
        cfg_path = default_cfg_path

    if cfg_path.exists():
        raw = _read_json(cfg_path)
        host = raw.get("network", {}).get("host")
        if host is not None:
            result["host"] = host

        if "log_level" in raw:
            result["log_level"] = raw["log_level"]

        port = raw.get("services", {}).get(service_key, {}).get("port")
        if port is not None:
            result["port"] = port

        if raw_merger is not None:
            raw_merger(result, raw)

    for key, env_name in (env_overrides or {}).items():
        env_value = os.environ.get(env_name)
        if env_value is None:
            continue
        current = result.get(key)
        if current is None:
            result[key] = env_value
            continue
        result[key] = _coerce_env_value(env_value, current)

    if require_port and result.get("port") is None:
        raise RuntimeError(
            f"Missing services.{service_key}.port in config/korestack_config.json"
        )

    return result
