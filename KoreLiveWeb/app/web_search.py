from __future__ import annotations

from .config             import cfg
from .web_search_ddg     import reset_search_session
from .web_search_ddg     import search_web      as _search_web_ddg
from .web_search_ddg     import search_web_text as _search_web_text_ddg
from .web_search_ollama  import search_web      as _search_web_ollama
from .web_search_ollama  import search_web_text as _search_web_text_ollama


def get_preferred_search_provider() -> str:
    provider = str(cfg.get("search_provider") or "ddg").strip().lower()
    if provider in {"ollama", "ollama_web_search"}:
        return "ollama"
    return "ddg"


def get_enabled_search_providers() -> list[str]:
    enabled: list[str] = []
    if bool(cfg.get("ddg_enabled", True)):
        enabled.append("ddg")
    if bool(cfg.get("ollama_enabled", True)):
        enabled.append("ollama")
    return enabled


def get_search_provider() -> str:
    preferred = get_preferred_search_provider()
    enabled   = get_enabled_search_providers()

    if preferred in enabled:
        return preferred
    if enabled:
        return enabled[0]
    return preferred


def get_search_provider_label() -> str:
    provider = get_search_provider()
    if provider == "ollama":
        return "Ollama Web Search"
    return "DuckDuckGo Lite"


def get_search_provider_config() -> dict:
    return {
        "provider":              get_search_provider(),
        "preferred_provider":    get_preferred_search_provider(),
        "enabled_providers":     get_enabled_search_providers(),
        "ddg_enabled":           bool(cfg.get("ddg_enabled", True)),
        "ollama_enabled":        bool(cfg.get("ollama_enabled", True)),
        "ollama_web_search_url": str(cfg.get("ollama_web_search_url") or "").strip(),
        "ollama_api_key":        str(cfg.get("ollama_api_key")        or "").strip(),
    }


def search_web(*args, **kwargs):
    if not get_enabled_search_providers():
        return [{
            "rank":    0,
            "title":   "Search unavailable",
            "url":     "",
            "snippet": "No web search providers are currently enabled in KoreLiveWeb.",
        }]

    provider = get_search_provider()
    if provider == "ollama":
        provider_config = {
            "api_url": cfg.get("ollama_web_search_url"),
            "api_key": cfg.get("ollama_api_key"),
        }
        return _search_web_ollama(*args, provider_config=provider_config, **kwargs)
    return _search_web_ddg(*args, **kwargs)


def search_web_text(*args, **kwargs):
    if not get_enabled_search_providers():
        return "Web search results unavailable: no web search providers are currently enabled in KoreLiveWeb."

    provider = get_search_provider()
    if provider == "ollama":
        provider_config = {
            "api_url": cfg.get("ollama_web_search_url"),
            "api_key": cfg.get("ollama_api_key"),
        }
        return _search_web_text_ollama(*args, provider_config=provider_config, **kwargs)
    return _search_web_text_ddg(*args, **kwargs)
