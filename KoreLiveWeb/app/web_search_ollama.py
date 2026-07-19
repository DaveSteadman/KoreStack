# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Ollama-hosted web search provider for KoreLiveWeb.
#
# This uses Ollama's hosted web search API while preserving the same KoreLiveWeb
# result shape used by the existing DuckDuckGo-backed tools.
# ====================================================================================================

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .activity_log import append_activity as _append_activity


DEFAULT_MAX_RESULTS      = 5
DEFAULT_TIMEOUT          = 15
DEFAULT_MAX_CHARS        = 500
MAX_RESULTS_CAP          = 10
TIMEOUT_SECONDS_CAP      = 30
MAX_CHARS_PER_RESULT_CAP = 2000
DEFAULT_API_URL          = "https://ollama.com/api/web_search"


def _coerce_provider_config(config: dict | None) -> dict:
    cfg = dict(config or {})
    return {
        "api_url": str(cfg.get("api_url") or DEFAULT_API_URL).strip() or DEFAULT_API_URL,
        "api_key": str(cfg.get("api_key") or "").strip(),
    }


def _append_search_activity(
    target: str,
    *,
    status: str,
    message: str = "",
    final_url: str = "",
) -> None:
    _append_activity(
        kind      = "http",
        target    = target,
        status    = status,
        message   = message,
        final_url = final_url,
    )


def _normalise_result(item: dict, rank: int) -> dict:
    title   = str(item.get("title")   or "").strip()
    url     = str(item.get("url")     or "").strip()
    snippet = str(item.get("content") or item.get("snippet") or "").strip()
    return {
        "rank":    rank,
        "title":   title or url or f"Result {rank}",
        "url":     url,
        "snippet": snippet,
    }


def _perform_ollama_search(
    *,
    query: str,
    max_results: int,
    timeout_seconds: int,
    provider_config: dict | None,
) -> list[dict]:
    cfg     = _coerce_provider_config(provider_config)
    api_url = cfg["api_url"]
    api_key = cfg["api_key"]

    if not api_key:
        return [{
            "rank":    0,
            "title":   "Search unavailable",
            "url":     "",
            "snippet": "Ollama web search requires OLLAMA_API_KEY or services.koreliveweb.ollama_api_key.",
        }]

    payload = json.dumps({"query": query}).encode("utf-8")
    request = urllib.request.Request(
        url     = api_url,
        data    = payload,
        method  = "POST",
        headers = {
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=float(timeout_seconds)) as response:
            final_url   = getattr(response, "url", api_url) or api_url
            status_code = int(getattr(response, "status", 200) or 200)
            raw_body    = response.read()

        decoded = raw_body.decode("utf-8", errors="replace")
        parsed  = json.loads(decoded) if decoded.strip() else {}
        results = parsed.get("results")
        if not isinstance(results, list):
            results = []

        _append_search_activity(
            api_url,
            status    = f"http-{status_code}",
            message   = f"ollama web_search ok q={query[:120]}",
            final_url = final_url,
        )

        if not results:
            return [{"rank": 0, "title": "No results", "url": "", "snippet": f"Ollama returned no results for: {query}"}]

        normalised = []
        seen_urls  = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            candidate = _normalise_result(item, len(normalised) + 1)
            url       = candidate["url"]
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            normalised.append(candidate)
            if len(normalised) >= max_results:
                break

        return normalised or [{"rank": 0, "title": "No results", "url": "", "snippet": f"Ollama returned no results for: {query}"}]

    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            body = ""
        _append_search_activity(
            api_url,
            status    = f"http-{exc.code}",
            message   = body[:240] or f"HTTP {exc.code}",
            final_url = getattr(exc, "url", "") or api_url,
        )
        return [{"rank": 0, "title": "Search failed", "url": "", "snippet": f"Ollama HTTP {exc.code}: {body[:240] or 'request failed'}"}]
    except urllib.error.URLError as exc:
        reason = str(getattr(exc, "reason", exc))
        _append_search_activity(
            api_url,
            status  = "url-error",
            message = reason,
        )
        return [{"rank": 0, "title": "Search failed", "url": "", "snippet": f"Ollama URL error: {reason}"}]
    except Exception as exc:
        _append_search_activity(
            api_url,
            status  = "error",
            message = str(exc),
        )
        return [{"rank": 0, "title": "Search failed", "url": "", "snippet": f"Ollama error: {exc}"}]


def search_web(
    query: str = "",
    max_results: int = DEFAULT_MAX_RESULTS,
    timeout_seconds: int = DEFAULT_TIMEOUT,
    offset: int = 0,
    prefer_article_urls: bool = False,
    num_results: int | None = None,
    limit: int | None = None,
    n: int | None = None,
    provider_config: dict | None = None,
    **kwargs,
) -> list[dict]:
    if not query:
        for alias in ("search_query", "q", "text", "keywords", "search", "term"):
            if alias in kwargs:
                query = str(kwargs[alias])
                break

    if not query or not query.strip():
        return [{"rank": 0, "title": "Error", "url": "", "snippet": "query cannot be empty"}]

    effective_max    = num_results if num_results is not None else (limit if limit is not None else (n if n is not None else max_results))
    max_results      = max(1, min(int(effective_max),   MAX_RESULTS_CAP))
    timeout_seconds  = max(5, min(int(timeout_seconds), TIMEOUT_SECONDS_CAP))
    _                = int(offset)
    _                = bool(prefer_article_urls)

    return _perform_ollama_search(
        query           = query.strip(),
        max_results     = max_results,
        timeout_seconds = timeout_seconds,
        provider_config = provider_config,
    )


def search_web_text(
    query: str = "",
    max_results: int = DEFAULT_MAX_RESULTS,
    timeout_seconds: int = DEFAULT_TIMEOUT,
    max_chars_per_result: int = DEFAULT_MAX_CHARS,
    offset: int = 0,
    prefer_article_urls: bool = False,
    num_results: int | None = None,
    limit: int | None = None,
    n: int | None = None,
    provider_config: dict | None = None,
    **kwargs,
) -> str:
    if not query:
        for alias in ("search_query", "q", "text", "keywords", "search", "term"):
            if alias in kwargs:
                query = str(kwargs[alias])
                break

    effective_max = num_results if num_results is not None else (limit if limit is not None else (n if n is not None else max_results))
    results       = search_web(
        query               = query,
        max_results         = int(effective_max),
        timeout_seconds     = int(timeout_seconds),
        offset              = int(offset),
        prefer_article_urls = prefer_article_urls,
        provider_config     = provider_config,
    )

    char_cap = max(0, min(int(max_chars_per_result), MAX_CHARS_PER_RESULT_CAP)) if int(max_chars_per_result) > 0 else 0

    lines = [f"Web search results for: {query}", ""]
    for r in results:
        rank    = r.get("rank", "?")
        title   = r.get("title", "")
        url     = r.get("url",   "")
        snippet = r.get("snippet", "")

        if char_cap and len(snippet) > char_cap:
            snippet = snippet[:char_cap] + "..."

        lines.append(f"[{rank}] {title}")
        if url:
            lines.append(f"    {url}")
        if snippet:
            lines.append(f"    {snippet}")
        lines.append("")

    return "\n".join(lines).strip()
