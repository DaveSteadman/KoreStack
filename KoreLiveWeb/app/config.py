from __future__ import annotations

import json
import sys
from pathlib import Path

_SUITE_ROOT = Path(__file__).resolve().parents[2]
if str(_SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SUITE_ROOT))

from KoreCommon.suite_config import load_service_config

_SERVICE_CONFIG_PATH = (_SUITE_ROOT / "config" / "koreliveweb_config.json").resolve()

_DEFAULTS = {
    "port":                  None,
    "host":                  "127.0.0.1",
    "log_level":             "info",
    "search_provider":       "ddg",
    "ddg_enabled":           True,
    "ollama_enabled":        True,
    "ollama_web_search_url": "https://ollama.com/api/web_search",
    "ollama_api_key":        "",
}


def _merge_liveweb_settings(result: dict, source: dict) -> None:
    if not isinstance(source, dict):
        return

    if "search_provider" in source:
        result["search_provider"] = str(source.get("search_provider") or "").strip() or result["search_provider"]

    if "ddg_enabled" in source:
        result["ddg_enabled"] = bool(source.get("ddg_enabled"))

    if "ollama_enabled" in source:
        result["ollama_enabled"] = bool(source.get("ollama_enabled"))

    if "ollama_web_search_url" in source:
        result["ollama_web_search_url"] = str(source.get("ollama_web_search_url") or "").strip() or result["ollama_web_search_url"]

    if "ollama_api_key" in source:
        result["ollama_api_key"] = str(source.get("ollama_api_key") or "").strip()


def _read_liveweb_config_file() -> dict:
    if not _SERVICE_CONFIG_PATH.exists():
        return {}

    with _SERVICE_CONFIG_PATH.open(encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def _merge_raw_service_config(result: dict, raw: dict) -> None:
    service_cfg = raw.get("services", {}).get("koreliveweb", {})
    _merge_liveweb_settings(result, service_cfg)
    _merge_liveweb_settings(result, _read_liveweb_config_file())


def load() -> dict:
    return load_service_config(
        service_key    = "koreliveweb",
        defaults       = _DEFAULTS,
        suite_root     = _SUITE_ROOT,
        env_overrides  = {
            "host":                  "KORELIVEWEB_HOST",
            "log_level":             "KORELIVEWEB_LOG_LEVEL",
            "search_provider":       "KORELIVEWEB_SEARCH_PROVIDER",
            "ddg_enabled":           "KORELIVEWEB_DDG_ENABLED",
            "ollama_enabled":        "KORELIVEWEB_OLLAMA_ENABLED",
            "ollama_web_search_url": "KORELIVEWEB_OLLAMA_WEB_SEARCH_URL",
            "ollama_api_key":        "OLLAMA_API_KEY",
        },
        raw_merger     = _merge_raw_service_config,
        require_port   = True,
    )


cfg = load()
