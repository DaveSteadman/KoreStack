from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi import Body
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi import Response
from fastapi.responses import FileResponse
from fastapi.responses import HTMLResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from mcp.server.fastmcp import FastMCP

_KORECOMMON_PARENT = next((parent for parent in Path(__file__).resolve().parents if (parent / "KoreCommon").is_dir()), None)
if _KORECOMMON_PARENT is not None:
    import sys
    if str(_KORECOMMON_PARENT) not in sys.path:
        sys.path.insert(0, str(_KORECOMMON_PARENT))

from KoreCommon.endpoint_manifest import build_endpoint_manifest
from KoreCommon.service_logging import configure_service_logging
from KoreCommon.suite_paths import _load_paths_config
from KoreCommon.suite_paths import get_suite_config_file
from KoreCommon.suite_paths import get_suite_urls_map
from KoreCommon.suite_paths import load_suite_config
from .activity_log    import append_activity
from .activity_log    import list_activity
from .config          import cfg
from .web_fetch       import fetch_page_text
from .web_navigate    import get_page_links
from .web_navigate    import get_page_links_text
from .web_research    import research_traverse
from .web_search      import get_enabled_search_providers
from .web_search      import get_search_provider
from .web_search      import get_search_provider_config
from .web_search      import get_search_provider_label
from .web_search      import search_web
from .web_search      import search_web_text
from .wikipedia       import lookup_wikipedia

_SERVICE_ROOT       = Path(__file__).resolve().parents[2]
_TEMPLATES_DIR      = Path(
    os.environ.get(
        "KORE_KORELIVEWEB_TEMPLATES_DIR",
        str(_SERVICE_ROOT / "KoreUI" / "KoreLiveWeb" / "templates"),
    )
).resolve()
_STATIC_ROOT        = Path(
    os.environ.get(
        "KORE_KORELIVEWEB_STATIC_DIR",
        str(_SERVICE_ROOT / "KoreUI" / "KoreLiveWeb" / "static"),
    )
).resolve()
_STATIC_LIVEWEB_DIR = (_STATIC_ROOT / "liveweb").resolve()
_UI_ELEMENTS_ASSETS = Path(
    os.environ.get(
        "KORE_UIELEMENTS_ASSETS_DIR",
        str(_SERVICE_ROOT / "UIElements" / "assets"),
    )
).resolve()
_templates          = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _asset_version() -> str:
    candidates = [
        _STATIC_LIVEWEB_DIR / "liveweb.css",
        _STATIC_LIVEWEB_DIR / "liveweb.js",
        _TEMPLATES_DIR      / "base.html",
        _TEMPLATES_DIR      / "home.html",
    ]
    stamps = []
    for candidate in candidates:
        try:
            stamps.append(str(int(candidate.stat().st_mtime)))
        except OSError:
            continue
    return "-".join(stamps) if stamps else "1"


def _read_suite_config_json() -> tuple[Path, dict]:
    path = get_suite_config_file()
    if not path.exists():
        raise HTTPException(status_code=500, detail="Suite config file not found")

    try:
        with path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Suite config is invalid JSON: {exc}") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read suite config: {exc}") from exc

    if not isinstance(raw, dict):
        raise HTTPException(status_code=500, detail="Suite config root must be a JSON object")
    return path, raw


def _write_suite_config_json(path: Path, payload: dict) -> None:
    try:
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write suite config: {exc}") from exc


def _coerce_checkbox_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _search_settings_payload() -> dict:
    provider_cfg = get_search_provider_config()
    enabled      = set(get_enabled_search_providers())
    stored_key   = str(provider_cfg.get("ollama_api_key") or "").strip()
    return {
        "preferred_provider": get_search_provider_config().get("preferred_provider", "ddg"),
        "active_provider":    get_search_provider(),
        "active_label":       get_search_provider_label(),
        "ddg_enabled":        "ddg"    in enabled,
        "ollama_enabled":     "ollama" in enabled,
        "ollama_has_api_key": bool(stored_key),
        "ollama_web_search_url": str(provider_cfg.get("ollama_web_search_url") or "").strip(),
    }


def _apply_runtime_search_settings(
    *,
    preferred_provider: str,
    ddg_enabled: bool,
    ollama_enabled: bool,
    ollama_api_key: str | None,
) -> None:
    cfg["search_provider"] = preferred_provider
    cfg["ddg_enabled"]     = ddg_enabled
    cfg["ollama_enabled"]  = ollama_enabled
    if ollama_api_key is not None:
        cfg["ollama_api_key"] = ollama_api_key


def _persist_search_settings(
    *,
    preferred_provider: str,
    ddg_enabled: bool,
    ollama_enabled: bool,
    ollama_api_key: str | None,
    clear_ollama_api_key: bool,
) -> dict:
    path, raw      = _read_suite_config_json()
    services       = raw.setdefault("services", {})
    if not isinstance(services, dict):
        raise HTTPException(status_code=500, detail="Suite config services section must be a JSON object")
    service_cfg    = services.setdefault("koreliveweb", {})
    if not isinstance(service_cfg, dict):
        raise HTTPException(status_code=500, detail="services.koreliveweb must be a JSON object")

    service_cfg["search_provider"] = preferred_provider
    service_cfg["ddg_enabled"]     = bool(ddg_enabled)
    service_cfg["ollama_enabled"]  = bool(ollama_enabled)

    if clear_ollama_api_key:
        service_cfg["ollama_api_key"] = ""
    elif ollama_api_key is not None:
        service_cfg["ollama_api_key"] = ollama_api_key

    _write_suite_config_json(path, raw)

    _apply_runtime_search_settings(
        preferred_provider = preferred_provider,
        ddg_enabled        = ddg_enabled,
        ollama_enabled     = ollama_enabled,
        ollama_api_key     = "" if clear_ollama_api_key else ollama_api_key,
    )

    load_suite_config.cache_clear()
    _load_paths_config.cache_clear()

    append_activity(
        kind    = "config",
        target  = str(path),
        status  = "saved",
        message = (
            f"preferred={preferred_provider} ddg={'on' if ddg_enabled else 'off'} "
            f"ollama={'on' if ollama_enabled else 'off'} api_key="
            f"{'cleared' if clear_ollama_api_key else ('updated' if ollama_api_key is not None and ollama_api_key != '' else 'unchanged')}"
        ),
    )

    return _search_settings_payload()

def _build_tool_rows() -> list[dict]:
    provider_label = get_search_provider_label()
    search_summary = f"Search via {provider_label} and return structured ranked results for discovery; use fetched page content as evidence."
    if get_search_provider() == "ddg":
        search_summary += " Reliability can vary under rate limiting or upstream blocking."

    return [
        {
            "name":        "search_web",
            "summary":     search_summary,
            "requestType": "query",
        },
        {
            "name":        "search_web_text",
            "summary":     search_summary.replace("structured ranked results", "a plain-text formatted result block"),
            "requestType": "query",
        },
        {
            "name":        "fetch_page_text",
            "summary":     "Fetch a page and return cleaned readable text for evidence-bearing factual synthesis.",
            "requestType": "url",
        },
        {
            "name":        "get_page_links",
            "summary":     "Extract navigable links from a page as structured data.",
            "requestType": "url",
        },
        {
            "name":        "get_page_links_text",
            "summary":     "Extract navigable links from a page as formatted text.",
            "requestType": "url",
        },
        {
            "name":        "research_traverse",
            "summary":     "Run multi-page search, fetch, and evidence-led traversal across sources.",
            "requestType": "query",
        },
        {
            "name":        "lookup_wikipedia",
            "summary":     "Resolve a topic and fetch a Wikipedia summary.",
            "requestType": "topic",
        },
    ]

_mcp = FastMCP(
    "KoreLiveWeb",
    instructions=(
        "KoreLiveWeb provides live web discovery, page fetching, navigation, multi-page research, "
        "and Wikipedia lookup tools. Use these tools for current or web-specific information rather "
        "than relying on model memory. Search is provider-backed and may be routed through either "
        "DuckDuckGo Lite or Ollama web search depending on suite configuration. Search results and "
        "snippets are discovery aids; fetched page content and research traversal outputs are the "
        "preferred evidence sources for factual answers."
    ),
    streamable_http_path="/",
    stateless_http=True,
)


@_mcp.tool(name="search_web")
def search_web_mcp(
    query              : str,
    max_results        : int = 5,
    timeout_seconds    : int = 15,
    offset             : int = 0,
    prefer_article_urls: bool = False,
) -> list[dict]:
    """Search the configured web provider and return structured ranked results for discovery.

    Result snippets help identify promising sources, but fetched page content should be used
    as the primary evidence for factual synthesis.
    """
    append_activity(
        kind      = "tool",
        tool_name = "search_web",
        target    = query,
        status    = "requested",
        message   = f"max_results={max_results} offset={offset}",
    )
    return search_web(
        query               = query,
        max_results         = max_results,
        timeout_seconds     = timeout_seconds,
        offset              = offset,
        prefer_article_urls = prefer_article_urls,
    )


@_mcp.tool(name="search_web_text")
def search_web_text_mcp(
    query               : str,
    max_results         : int = 5,
    timeout_seconds     : int = 15,
    max_chars_per_result: int = 500,
    offset              : int = 0,
    prefer_article_urls : bool = False,
) -> str:
    """Search the configured web provider and return a plain-text formatted result block.

    Result snippets are discovery-oriented summaries, not authoritative evidence. Prefer
    fetch_page_text or research_traverse before making factual claims from web material.
    """
    append_activity(
        kind      = "tool",
        tool_name = "search_web_text",
        target    = query,
        status    = "requested",
        message   = f"max_results={max_results} offset={offset}",
    )
    return search_web_text(
        query                = query,
        max_results          = max_results,
        timeout_seconds      = timeout_seconds,
        max_chars_per_result = max_chars_per_result,
        offset               = offset,
        prefer_article_urls  = prefer_article_urls,
    )


@_mcp.tool(name="fetch_page_text")
def fetch_page_text_mcp(
    url            : str,
    max_words      : int = 2000,
    timeout_seconds: int = 15,
    query          : str | None = None,
) -> str:
    """Fetch a web page and return clean readable text or a query-focused extract.

    This is an evidence-bearing retrieval tool and should be preferred over search snippets
    when synthesizing factual answers from the web.
    """
    append_activity(
        kind      = "tool",
        tool_name = "fetch_page_text",
        target    = url,
        status    = "requested",
        message   = f"query={'yes' if query else 'no'} max_words={max_words}",
    )
    return fetch_page_text(
        url             = url,
        max_words       = max_words,
        timeout_seconds = timeout_seconds,
        query           = query,
    )


@_mcp.tool(name="get_page_links")
def get_page_links_mcp(
    url            : str,
    filter_text    : str = "",
    max_links      : int = 30,
    timeout_seconds: int = 15,
) -> list[dict]:
    """Fetch a page and return its navigable links as a structured list."""
    append_activity(
        kind      = "tool",
        tool_name = "get_page_links",
        target    = url,
        status    = "requested",
        message   = f"filter={filter_text or '-'} max_links={max_links}",
    )
    return get_page_links(
        url             = url,
        filter_text     = filter_text,
        max_links       = max_links,
        timeout_seconds = timeout_seconds,
    )


@_mcp.tool(name="get_page_links_text")
def get_page_links_text_mcp(
    url            : str,
    filter_text    : str = "",
    max_links      : int = 30,
    timeout_seconds: int = 15,
) -> str:
    """Fetch a page and return its navigable links as formatted plain text."""
    append_activity(
        kind      = "tool",
        tool_name = "get_page_links_text",
        target    = url,
        status    = "requested",
        message   = f"filter={filter_text or '-'} max_links={max_links}",
    )
    return get_page_links_text(
        url             = url,
        filter_text     = filter_text,
        max_links       = max_links,
        timeout_seconds = timeout_seconds,
    )


@_mcp.tool(name="research_traverse")
def research_traverse_mcp(
    query                    : str,
    max_search_results       : int = 5,
    max_pages                : int = 6,
    max_hops                 : int = 1,
    same_domain_only_for_hops: bool = True,
    timeout_seconds          : int = 15,
    max_words_per_page       : int = 450,
    max_evidence_quotes      : int = 3,
) -> dict:
    """Search, fetch, and follow multiple pages to build an evidence-led research bundle.

    Prefer this when the answer requires cross-source synthesis rather than a single search
    result or snippet-led summary.
    """
    append_activity(
        kind      = "tool",
        tool_name = "research_traverse",
        target    = query,
        status    = "requested",
        message   = f"search_results={max_search_results} max_pages={max_pages} max_hops={max_hops}",
    )
    return research_traverse(
        query                     = query,
        max_search_results        = max_search_results,
        max_pages                 = max_pages,
        max_hops                  = max_hops,
        same_domain_only_for_hops = same_domain_only_for_hops,
        timeout_seconds           = timeout_seconds,
        max_words_per_page        = max_words_per_page,
        max_evidence_quotes       = max_evidence_quotes,
    )


@_mcp.tool(name="lookup_wikipedia")
def lookup_wikipedia_mcp(topic: str, timeout: int = 15) -> str:
    """Resolve and fetch a Wikipedia summary for a topic."""
    append_activity(
        kind      = "tool",
        tool_name = "lookup_wikipedia",
        target    = topic,
        status    = "requested",
        message   = f"timeout={timeout}",
    )
    return lookup_wikipedia(topic=topic, timeout=timeout)


_mcp_http_app = _mcp.streamable_http_app()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    async with _mcp_http_app.router.lifespan_context(_mcp_http_app):
        yield


app = FastAPI(title="KoreLiveWeb", lifespan=_lifespan)
app.mount("/static/liveweb", StaticFiles(directory=str(_STATIC_LIVEWEB_DIR)), name="koreliveweb-static")


def _home_context(request: Request) -> dict:
    suite_urls      = get_suite_urls_map()
    stack_root      = str(suite_urls.get("korestack")   or "").rstrip("/")
    service_root    = str(suite_urls.get("koreliveweb") or "/").rstrip("/") or ""
    provider        = get_search_provider()
    provider_label  = get_search_provider_label()
    tool_rows       = _build_tool_rows()
    initial_entries = list_activity(limit=120)
    endpoint_rows = [
        {
            "label":   "Landing",
            "path":    "/ui",
            "summary": "Shared-shell monitor page for live web traffic and tool visibility.",
        },
        {
            "label":   "Status",
            "path":    "/status",
            "summary": "Health probe for launcher checks and service supervision.",
        },
        {
            "label":   "Activity API",
            "path":    "/api/activity",
            "summary": "Live request feed for MCP calls and outbound HTTP results.",
        },
        {
            "label":   "MCP",
            "path":    "/mcp",
            "summary": "Mounted Streamable HTTP MCP endpoint for KoreAgent integration.",
        },
    ]
    bootstrap_json = json.dumps(
        {
            "serviceKey":       "koreliveweb",
            "serviceLabel":     "KoreLiveWeb",
            "serviceRoot":      service_root,
            "endpointExplorer": f"{stack_root}/endpoints" if stack_root else "/endpoints",
            "toolNames":        [row["name"] for row in tool_rows],
            "searchProvider":   provider,
            "searchSettings":   _search_settings_payload(),
            "pollMs":           2000,
            "initialEntries":   initial_entries,
        }
    )
    return {
        "request":         request,
        "tool_rows":       tool_rows,
        "endpoint_rows":   endpoint_rows,
        "initial_entries": initial_entries,
        "bootstrap_json":  bootstrap_json,
        "provider":        provider,
        "provider_label":  provider_label,
        "asset_version":   _asset_version(),
    }


@app.get("/__endpoint_manifest", include_in_schema=False)
def endpoint_manifest() -> dict:
    return build_endpoint_manifest(app, service_key="koreliveweb", service_label="KoreLiveWeb")


@app.get("/status", include_in_schema=False)
def status() -> dict:
    return {"status": "ok", "service": "koreliveweb"}


@app.get("/suite-config.js", include_in_schema=False)
def suite_config_js():
    urls = json.dumps(get_suite_urls_map())
    return Response(
        content    = f"window.__koreSuiteUrls = {urls};",
        media_type = "application/javascript",
        headers    = {"Cache-Control": "no-store"},
    )


@app.get("/ui-elements/assets/{asset_path:path}", include_in_schema=False)
def serve_ui_elements_asset(asset_path: str):
    candidate = (_UI_ELEMENTS_ASSETS / asset_path).resolve()
    if candidate != _UI_ELEMENTS_ASSETS and _UI_ELEMENTS_ASSETS not in candidate.parents:
        raise HTTPException(status_code=404, detail="Asset not found")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(str(candidate), headers={"Cache-Control": "no-store"})


@app.get("/ui", include_in_schema=False, response_class=HTMLResponse)
def ui_home(request: Request):
    response = _templates.TemplateResponse(request, "home.html", _home_context(request))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/activity")
def activity(limit: int = Query(default=200, ge=1, le=500)) -> dict:
    return {"entries": list_activity(limit=limit)}


@app.get("/api/settings/search-providers")
def get_search_provider_settings() -> dict:
    return _search_settings_payload()


@app.post("/api/settings/search-providers")
def save_search_provider_settings(payload: dict = Body(default={})) -> dict:
    preferred_provider = str(payload.get("preferred_provider") or cfg.get("search_provider") or "ddg").strip().lower()
    if preferred_provider not in {"ddg", "ollama"}:
        raise HTTPException(status_code=400, detail="preferred_provider must be 'ddg' or 'ollama'")

    ddg_enabled         = _coerce_checkbox_bool(payload.get("ddg_enabled"))
    ollama_enabled      = _coerce_checkbox_bool(payload.get("ollama_enabled"))
    clear_ollama_api_key = _coerce_checkbox_bool(payload.get("clear_ollama_api_key"))
    api_key_raw         = payload.get("ollama_api_key")
    ollama_api_key      = None if api_key_raw is None else str(api_key_raw).strip()

    if not ddg_enabled and not ollama_enabled:
        raise HTTPException(status_code=400, detail="At least one search provider must remain enabled")

    append_activity(
        kind    = "config",
        target  = "/api/settings/search-providers",
        status  = "requested",
        message = (
            f"preferred={preferred_provider} ddg={'on' if ddg_enabled else 'off'} "
            f"ollama={'on' if ollama_enabled else 'off'} api_key="
            f"{'clear' if clear_ollama_api_key else ('provided' if ollama_api_key else 'empty')}"
        ),
    )

    return _persist_search_settings(
        preferred_provider  = preferred_provider,
        ddg_enabled         = ddg_enabled,
        ollama_enabled      = ollama_enabled,
        ollama_api_key      = ollama_api_key,
        clear_ollama_api_key = clear_ollama_api_key,
    )


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/ui")


app.mount("/mcp", _mcp_http_app)


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="KoreLiveWeb server")
    parser.add_argument("--host", default=cfg["host"])
    parser.add_argument("--port", type=int, default=cfg["port"])
    args = parser.parse_args(argv)

    configure_service_logging("koreliveweb", cfg["log_level"])
    logging.getLogger("koreliveweb.service").info("starting host=%s port=%s", args.host, args.port)
    uvicorn.run(
        app,
        host       = args.host,
        port       = args.port,
        access_log = False,
        log_config = None,
    )
    return 0
