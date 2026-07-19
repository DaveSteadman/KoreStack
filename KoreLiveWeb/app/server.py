from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
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
from KoreCommon.suite_paths import get_suite_urls_map
from .activity_log    import append_activity
from .activity_log    import list_activity
from .config          import cfg
from .web_fetch       import fetch_page_text
from .web_navigate    import get_page_links
from .web_navigate    import get_page_links_text
from .web_research    import research_traverse
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

_TOOL_ROWS = [
    {
        "name":        "search_web",
        "summary":     "Search DuckDuckGo and return structured ranked results.",
        "requestType": "query",
    },
    {
        "name":        "search_web_text",
        "summary":     "Search DuckDuckGo and return a plain-text formatted result block.",
        "requestType": "query",
    },
    {
        "name":        "fetch_page_text",
        "summary":     "Fetch a page and return cleaned readable text.",
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
        "summary":     "Run multi-page search, fetch, and evidence-led traversal.",
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
        "than relying on model memory."
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
    """Search DuckDuckGo and return structured ranked results."""
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
    """Search DuckDuckGo and return a plain-text formatted result block."""
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
    """Fetch a web page and return clean readable text or a query-focused extract."""
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
    """Search, fetch, and follow multiple pages to build an evidence-led research bundle."""
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
    suite_urls    = get_suite_urls_map()
    stack_root    = str(suite_urls.get("korestack")   or "").rstrip("/")
    service_root  = str(suite_urls.get("koreliveweb") or "/").rstrip("/") or ""
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
            "toolNames":        [row["name"] for row in _TOOL_ROWS],
            "pollMs":           2000,
            "initialEntries":   initial_entries,
        }
    )
    return {
        "request":        request,
        "tool_rows":      _TOOL_ROWS,
        "endpoint_rows":  endpoint_rows,
        "initial_entries": initial_entries,
        "bootstrap_json": bootstrap_json,
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
    return _templates.TemplateResponse(request, "home.html", _home_context(request))


@app.get("/api/activity")
def activity(limit: int = Query(default=200, ge=1, le=500)) -> dict:
    return {"entries": list_activity(limit=limit)}


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
