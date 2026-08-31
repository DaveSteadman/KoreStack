# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# server module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Function inventory:
# - _asset_version: Implements the  asset version operation for this module.
# - _read_koreliveweb_config_json: Implements the  read koreliveweb config json operation for this module.
# - _write_koreliveweb_config_json: Implements the  write koreliveweb config json operation for this module.
# - _coerce_checkbox_bool: Implements the  coerce checkbox bool operation for this module.
# - _search_settings_payload: Implements the  search settings payload operation for this module.
# - _apply_runtime_search_settings: Implements the  apply runtime search settings operation for this module.
# - _persist_search_settings: Implements the  persist search settings operation for this module.
# - _build_tool_rows: Implements the  build tool rows operation for this module.
# - search_web_skill: Implements the search web skill operation for this module.
# - search_web_text_skill: Implements the search web text skill operation for this module.
# - fetch_page_text_skill: Implements the fetch page text skill operation for this module.
# - get_page_links_skill: Returns page links for this module.
# - get_page_links_text_skill: Returns page links text for this module.
# - lookup_wikipedia_skill: Implements the Wikipedia lookup skill operation for this module.
# - _lifespan: Implements the  lifespan operation for this module.
# - _home_context: Implements the  home context operation for this module.
# - status: Implements the status operation for this module.
# - ui_home: Implements the ui home operation for this module.
# - activity: Implements the activity operation for this module.
# - get_search_provider_settings: Returns search provider settings for this module.
# - save_search_provider_settings: Saves search provider settings for this module.
# - save_search_provider_settings_form: Saves search provider settings form for this module.
# - root: Implements the root operation for this module.
# - main: Starts this module's primary operation.
# ====================================================================================================

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
from fastapi.responses import HTMLResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

_KORECOMMON_PARENT = next((parent for parent in Path(__file__).resolve().parents if (parent / "KoreCommon").is_dir()), None)
if _KORECOMMON_PARENT is not None:
    import sys
    if str(_KORECOMMON_PARENT) not in sys.path:
        sys.path.insert(0, str(_KORECOMMON_PARENT))

from KoreCommon.service_app import register_suite_shell_routes
from KoreCommon.service_logging import configure_service_logging
from KoreCommon.skill_registration import start_manifest_registration
from KoreCommon.skill_service import register_skill_invocation_routes
from KoreCommon.suite_paths import _load_paths_config
from KoreCommon.suite_paths import get_suite_urls_map
from .activity_log    import append_activity
from .activity_log    import list_activity
from .config          import cfg
from .web_fetch       import fetch_page_text
from .web_navigate    import get_page_links
from .web_navigate    import get_page_links_text
from .web_search      import get_enabled_search_providers
from .web_search      import get_search_provider
from .web_search      import get_search_provider_config
from .web_search      import get_search_provider_label
from .web_search      import search_web
from .web_search      import search_web_text
from .wikipedia       import lookup_wikipedia

_SERVICE_ROOT       = Path(__file__).resolve().parents[2]
_SERVICE_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
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
        str(_SERVICE_ROOT / "KoreUI" / "UIElements" / "assets"),
    )
).resolve()
_templates          = Jinja2Templates(directory=str(_TEMPLATES_DIR))
_KORELIVEWEB_CONFIG_PATH = (_SERVICE_ROOT / "config" / "koreliveweb_config.json").resolve()


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


def _read_koreliveweb_config_json() -> tuple[Path, dict]:
    path = _KORELIVEWEB_CONFIG_PATH
    if not path.exists():
        return path, {}

    try:
        with path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"KoreLiveWeb config is invalid JSON: {exc}") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read KoreLiveWeb config: {exc}") from exc

    if not isinstance(raw, dict):
        raise HTTPException(status_code=500, detail="KoreLiveWeb config root must be a JSON object")
    return path, raw


def _write_koreliveweb_config_json(path: Path, payload: dict) -> None:
    try:
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write KoreLiveWeb config: {exc}") from exc


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
    ollama_web_search_url: str,
    ollama_api_key: str | None,
) -> None:
    cfg["search_provider"]        = preferred_provider
    cfg["ddg_enabled"]            = ddg_enabled
    cfg["ollama_enabled"]         = ollama_enabled
    cfg["ollama_web_search_url"]  = ollama_web_search_url
    if ollama_api_key is not None:
        cfg["ollama_api_key"] = ollama_api_key


def _persist_search_settings(
    *,
    preferred_provider: str,
    ddg_enabled: bool,
    ollama_enabled: bool,
    ollama_web_search_url: str,
    ollama_api_key: str | None,
    clear_ollama_api_key: bool,
) -> dict:
    path, service_cfg = _read_koreliveweb_config_json()

    service_cfg["search_provider"]        = preferred_provider
    service_cfg["ddg_enabled"]            = bool(ddg_enabled)
    service_cfg["ollama_enabled"]         = bool(ollama_enabled)
    service_cfg["ollama_web_search_url"]  = ollama_web_search_url

    if clear_ollama_api_key:
        service_cfg["ollama_api_key"] = ""
    elif ollama_api_key is not None:
        service_cfg["ollama_api_key"] = ollama_api_key

    _write_koreliveweb_config_json(path, service_cfg)

    _apply_runtime_search_settings(
        preferred_provider       = preferred_provider,
        ddg_enabled              = ddg_enabled,
        ollama_enabled           = ollama_enabled,
        ollama_web_search_url    = ollama_web_search_url,
        ollama_api_key           = "" if clear_ollama_api_key else ollama_api_key,
    )

    _load_paths_config.cache_clear()

    append_activity(
        kind    = "config",
        target  = str(path),
        status  = "saved",
        message = (
            f"preferred={preferred_provider} ddg={'on' if ddg_enabled else 'off'} "
            f"ollama={'on' if ollama_enabled else 'off'} endpoint={ollama_web_search_url} api_key="
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
            "name":        "lookup_wikipedia",
            "summary":     "Resolve a topic and fetch a Wikipedia summary.",
            "requestType": "topic",
        },
    ]

def search_web_skill(
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


def search_web_text_skill(
    query               : str,
    max_results         : int = 5,
    timeout_seconds     : int = 15,
    max_chars_per_result: int = 500,
    offset              : int = 0,
    prefer_article_urls : bool = False,
) -> str:
    """Search the configured web provider and return a plain-text formatted result block.

    Result snippets are discovery-oriented summaries, not authoritative evidence. Prefer
    fetch_page_text before making factual claims from web material.
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


def fetch_page_text_skill(
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


def get_page_links_skill(
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


def get_page_links_text_skill(
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


def lookup_wikipedia_skill(topic: str, timeout: int = 15) -> str:
    """Resolve and fetch a Wikipedia summary for a topic."""
    append_activity(
        kind      = "tool",
        tool_name = "lookup_wikipedia",
        target    = topic,
        status    = "requested",
        message   = f"timeout={timeout}",
    )
    return lookup_wikipedia(topic=topic, timeout=timeout)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    start_manifest_registration(
        _SERVICE_PACKAGE_ROOT / "skill_registration.json",
        service_base_url=f"http://{cfg['host']}:{cfg['port']}",
        logger_name=__name__,
    )
    yield


app = FastAPI(title="KoreLiveWeb", lifespan=_lifespan)
app.mount("/static/liveweb", StaticFiles(directory=str(_STATIC_LIVEWEB_DIR)), name="koreliveweb-static")
register_suite_shell_routes(
    app,
    service_key            = "koreliveweb",
    service_label          = "KoreLiveWeb",
    ui_elements_assets_dir = _UI_ELEMENTS_ASSETS,
)
register_skill_invocation_routes(
    app,
    {
        "search_web": search_web_skill,
        "search_web_text": search_web_text_skill,
        "fetch_page_text": fetch_page_text_skill,
        "get_page_links": get_page_links_skill,
        "get_page_links_text": get_page_links_text_skill,
        "lookup_wikipedia": lookup_wikipedia_skill,
    },
)


def _home_context(request: Request) -> dict:
    suite_urls      = get_suite_urls_map()
    stack_root      = str(suite_urls.get("korestack")   or "").rstrip("/")
    service_root    = str(suite_urls.get("koreliveweb") or "/").rstrip("/") or ""
    provider        = get_search_provider()
    provider_label  = get_search_provider_label()
    search_settings = _search_settings_payload()
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
    ]
    bootstrap_json = json.dumps(
        {
            "serviceKey":       "koreliveweb",
            "serviceLabel":     "KoreLiveWeb",
            "serviceRoot":      service_root,
            "endpointExplorer": f"{stack_root}/endpoints" if stack_root else "/endpoints",
            "toolNames":        [row["name"] for row in tool_rows],
            "searchProvider":   provider,
            "searchSettings":   search_settings,
            "pollMs":           2000,
            "initialEntries":   initial_entries,
        }
    )
    return {
        "request":         request,
        "tool_rows":       tool_rows,
        "endpoint_rows":   endpoint_rows,
        "initial_entries": initial_entries,
        "search_settings": search_settings,
        "bootstrap_json":  bootstrap_json,
        "provider":        provider,
        "provider_label":  provider_label,
        "asset_version":   _asset_version(),
    }


@app.get("/status", include_in_schema=False)
def status() -> dict:
    return {"status": "ok", "service": "koreliveweb"}


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

    ddg_enabled            = _coerce_checkbox_bool(payload.get("ddg_enabled"))
    ollama_enabled         = _coerce_checkbox_bool(payload.get("ollama_enabled"))
    ollama_web_search_url = str(
        payload.get("ollama_web_search_url") or cfg.get("ollama_web_search_url") or ""
    ).strip()
    clear_ollama_api_key  = _coerce_checkbox_bool(payload.get("clear_ollama_api_key"))
    api_key_raw           = payload.get("ollama_api_key")
    ollama_api_key        = None if api_key_raw is None else str(api_key_raw).strip()

    if not ddg_enabled and not ollama_enabled:
        raise HTTPException(status_code=400, detail="At least one search provider must remain enabled")
    if not ollama_web_search_url.startswith(("https://", "http://")):
        raise HTTPException(status_code=400, detail="ollama_web_search_url must be an HTTP(S) URL")

    append_activity(
        kind    = "config",
        target  = "/api/settings/search-providers",
        status  = "requested",
        message = (
            f"preferred={preferred_provider} ddg={'on' if ddg_enabled else 'off'} "
            f"ollama={'on' if ollama_enabled else 'off'} endpoint={ollama_web_search_url} api_key="
            f"{'clear' if clear_ollama_api_key else ('provided' if ollama_api_key else 'empty')}"
        ),
    )

    return _persist_search_settings(
        preferred_provider       = preferred_provider,
        ddg_enabled              = ddg_enabled,
        ollama_enabled           = ollama_enabled,
        ollama_web_search_url    = ollama_web_search_url,
        ollama_api_key           = ollama_api_key,
        clear_ollama_api_key     = clear_ollama_api_key,
    )


@app.post("/ui/settings/search-providers", include_in_schema=False)
async def save_search_provider_settings_form(request: Request):
    form    = await request.form()
    payload = dict(form)
    save_search_provider_settings(payload)
    return RedirectResponse("/ui", status_code=303)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/ui")




def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="KoreLiveWeb server")
    parser.add_argument("--host", default=cfg["host"])
    parser.add_argument("--port", type=int, default=cfg["port"])
    args = parser.parse_args(argv)

    configure_service_logging("koreliveweb", cfg["log_level"])
    logger = logging.getLogger("koreliveweb.service")
    try:
        logger.info("starting host=%s port=%s", args.host, args.port)
        uvicorn.run(
            app,
            host       = args.host,
            port       = args.port,
            access_log = False,
            log_config = None,
        )
    except Exception:
        logger.exception("startup failed")
        raise
    finally:
        logger.info("shutdown complete")
    return 0
