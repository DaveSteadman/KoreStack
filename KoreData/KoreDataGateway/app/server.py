# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FastAPI gateway for KoreData — proxy, web UI, child process lifecycle, and MCP federation.
#
# Manages the KoreData sub-service processes (KoreFeed, KoreLibrary, KoreRAG,
# KoreReference, KoreScrape, KoreGraph),
# federates their MCP endpoints, and proxies API requests.  Also serves the KoreData
# web UI via Jinja2 templates.
#
# Key responsibilities:
#   - Spawn and supervise child sub-service processes
#   - Proxy /api/search across all sub-services and merge results
#   - Mount MCP tools from each sub-service via federation
#   - Serve gateway-owned UI pages only
#
# Related modules:
#   - app/config.py       -- cfg (host, port, sub-service URLs)
#   - KoreFeed/           -- feed management sub-service
#   - KoreLibrary/        -- book catalog sub-service
#   - KoreRAG/            -- RAG chunk store sub-service
#   - KoreReference/      -- Wikipedia reference article sub-service
#   - KoreScrape/         -- website snapshot sub-service
# ====================================================================================================
import asyncio
import logging
import os
import signal
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
import threading
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from mcp.server.fastmcp import FastMCP

_KORECOMMON_PARENT = next((parent for parent in Path(__file__).resolve().parents if (parent / "KoreCommon").is_dir()), None)
if _KORECOMMON_PARENT is not None and str(_KORECOMMON_PARENT) not in sys.path:
    sys.path.insert(0, str(_KORECOMMON_PARENT))

from KoreCommon.service_app import register_suite_shell_routes
from app.config import cfg
from app.gateway_feed import get_feed_entry as _gateway_get_feed_entry
from app.gateway_feed import get_feed_sentence as _gateway_get_feed_sentence
from app.gateway_library import find_library_book as _gateway_find_library_book
from app.gateway_library import get_library_book_chunk as _gateway_get_library_book_chunk
from app.gateway_library import get_library_index as _gateway_get_library_index
from app.gateway_library import repair_library_book_anchors as _gateway_repair_library_book_anchors
from app.gateway_library import update_library_book as _gateway_update_library_book
from app.gateway_rag import enrich_databases as _gateway_enrich_rag_databases
from app.gateway_rag import get_rag_chunk as _gateway_get_rag_chunk
from app.gateway_rag import list_processing_scripts as _gateway_list_rag_processing_scripts
from app.gateway_rag import normalise_processing_schedule as _gateway_normalise_rag_processing_schedule
from app.gateway_reference import get_reference_article as _gateway_get_reference_article
from app.gateway_reference import get_reference_sentence as _gateway_get_reference_sentence
from app.gateway_scrape import get_scrape_chunk as _gateway_get_scrape_chunk
from app.gateway_search import _map_feed_entry as _canonical_map_feed_entry
from app.gateway_search import _map_reference_article as _canonical_map_reference_article
from app.gateway_search import parse_artifact_ref as _parse_artifact_ref
from app.gateway_search import parse_sentence_locator as _parse_sentence_locator
from app.gateway_search import run_search as _run_search
from app.gateway_api import FullTextRequest as _FullTextRequest
from app.gateway_api import SearchRequest as _SearchRequest
from app.gateway_api import SentenceRequest as _SentenceRequest
from app.gateway_api import register_gateway_api_routes
from config import get_koredata_dir


LOG = logging.getLogger("koredata.gateway")

# ---------------------------------------------------------------------------
# Child process management
# ---------------------------------------------------------------------------

_BASE = Path(__file__).parent.parent.parent  # KoreData/ root
_DATA = get_koredata_dir()


def _scrape_data_root() -> Path:
    new_root = _DATA / "Scrape"
    old_root = _DATA / "KoreScrape"
    return new_root if new_root.exists() or not old_root.exists() else old_root


_SERVICES = [
    (_BASE / "KoreFeed",      "KoreFeed",      _DATA / "Feeds"),
    (_BASE / "KoreLibrary",   "KoreLibrary",   _DATA / "Library"),
    (_BASE / "KoreReference", "KoreReference", _DATA / "Reference"),
    (_BASE / "KoreRAG",       "KoreRAG",       _DATA / "RAG"),
    (_BASE / "KoreScrape",    "KoreScrape",    _scrape_data_root()),
    (_BASE / "KoreGraph",     "KoreGraph",     _DATA / "Graph"),
]

_children: list[tuple[subprocess.Popen, str, object]] = []
_children_lock = threading.Lock()
_startup_state_lock = threading.Lock()
_gateway_startup_state: dict[str, Any] = {
    "status":       "starting",
    "message":      "Gateway booting",
    "children":     {},
    "started_at":   datetime.now().isoformat(timespec="seconds"),
    "completed_at": None,
}
_child_readiness_task: asyncio.Task | None = None


def _display_path(path: Path) -> Path:
    try:
        return path.relative_to(_BASE.parent)
    except ValueError:
        return path


def _port_from_url(url: str) -> int:
    return int(urlsplit(url).port or 0)


def _listening_pids_on_port(port: int) -> list[int]:
    try:
        output = subprocess.check_output(["netstat", "-ano"], text=True, encoding="utf-8", errors="ignore")
    except Exception:
        return []
    pids: list[int] = []
    needle = f":{port}"
    for line in output.splitlines():
        text = line.strip()
        if "LISTENING" not in text or needle not in text:
            continue
        parts = text.split()
        if len(parts) < 5:
            continue
        local_addr = parts[1]
        state      = parts[3]
        pid_text   = parts[4]
        if not local_addr.endswith(needle) or state != "LISTENING":
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid not in pids:
            pids.append(pid)
    return pids


def _terminate_pid(pid: int, label: str) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    print(f"  [stale] Clearing {label} listener  (pid {pid})")
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception:
        return


def _clear_stale_child_listeners() -> None:
    service_ports = {
        "KoreFeed":      _port_from_url(cfg["korefeed_url"]),
        "KoreLibrary":   _port_from_url(cfg["korelibrary_url"]),
        "KoreReference": _port_from_url(cfg["korereference_url"]),
        "KoreRAG":       _port_from_url(cfg["korerag_url"]),
        "KoreScrape":    _port_from_url(cfg["korescrape_url"]),
        "KoreGraph":     _port_from_url(cfg["koregraph_url"]),
    }
    for label, port in service_ports.items():
        if port <= 0:
            continue
        for pid in _listening_pids_on_port(port):
            _terminate_pid(pid, label)


def _start_children() -> None:
    _clear_stale_child_listeners()
    for service_dir, label, data_dir in _SERVICES:
        log_path = data_dir / "service.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
        proc = subprocess.Popen(
            [sys.executable, "main.py"],
            cwd=service_dir,
            stdout=log_file,
            stderr=log_file,
            env=os.environ.copy(),
        )
        with _children_lock:
            _children.append((proc, label, log_file))
        print(f"  > {label} starting  (pid {proc.pid})  log -> {_display_path(log_path)}")


def _stop_children() -> None:
    with _children_lock:
        children = list(_children)
        _children.clear()
    for proc, label, log_file in reversed(children):
        if proc.poll() is not None:
            continue  # already exited
        print(f"  [stop] Stopping {label}  (pid {proc.pid})")
        proc.terminate()
    for proc, label, log_file in reversed(children):
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            print(f"  [kill] Force-killing {label}")
            proc.kill()
        try:
            log_file.close()
        except Exception:
            # Shutdown should keep draining the child list even if one log handle has
            # already been torn down elsewhere.
            pass


async def _wait_for(client: httpx.AsyncClient, label: str, timeout: float = 20.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        try:
            r = await client.get("/status", timeout=2.0)
            if r.status_code == 200:
                print(f"  [ok] {label} ready")
                return
        except Exception:
            # Startup probes are retried for the full timeout window because each
            # child process may bind and warm independently.
            pass
        await asyncio.sleep(0.5)
    print(f"  [!] {label} did not respond within {timeout:.0f}s - continuing anyway")


def _set_child_status(label: str, status: str, detail: str) -> None:
    with _startup_state_lock:
        children = _gateway_startup_state.setdefault("children", {})
        children[label] = {"status": status, "detail": detail}


def _set_gateway_status(status: str, message: str) -> None:
    with _startup_state_lock:
        _gateway_startup_state["status"]  = status
        _gateway_startup_state["message"] = message
        if status == "ready":
            _gateway_startup_state["completed_at"] = datetime.now().isoformat(timespec="seconds")


def _get_gateway_startup_snapshot() -> dict[str, Any]:
    with _startup_state_lock:
        children = _gateway_startup_state.get("children")
        return {
            **_gateway_startup_state,
            "children": dict(children) if isinstance(children, dict) else {},
        }


async def _wait_for_children_ready() -> None:
    checks = [
        ("KoreFeed",      _feed_client,   60.0),
        ("KoreLibrary",   _lib_client,    20.0),
        ("KoreReference", _ref_client,    20.0),
        ("KoreRAG",       _rag_client,    20.0),
        ("KoreScrape",    _scrape_client, 20.0),
        ("KoreGraph",     _graph_client,  20.0),
    ]
    for label, _client, timeout in checks:
        _set_child_status(label, "starting", "Waiting for /status")

    async def _probe(label: str, client: httpx.AsyncClient | None, timeout: float) -> tuple[str, bool]:
        if client is None:
            _set_child_status(label, "degraded", "Client not initialised")
            return label, False
        loop = asyncio.get_running_loop()
        end  = loop.time() + timeout
        while loop.time() < end:
            try:
                r = await client.get("/status", timeout=2.0)
                if r.status_code == 200:
                    _set_child_status(label, "ready", "Responding to /status")
                    print(f"  [ok] {label} ready")
                    return label, True
            except Exception:
                pass
            await asyncio.sleep(0.5)
        _set_child_status(label, "degraded", f"Did not respond within {timeout:.0f}s")
        print(f"  [!] {label} did not respond within {timeout:.0f}s - continuing anyway")
        return label, False

    results = await asyncio.gather(
        *(_probe(label, client, timeout) for label, client, timeout in checks),
        return_exceptions=False,
    )
    ready_count = sum(1 for _label, ok in results if ok)
    if ready_count == len(checks):
        _set_gateway_status("ready", "All child services ready")
        print("  All services ready\n")
    else:
        _set_gateway_status("degraded", f"{ready_count}/{len(checks)} child services ready")
        print("  Gateway started with degraded child readiness\n")


# ---------------------------------------------------------------------------
# App + lifespan
# ---------------------------------------------------------------------------

_feed_client:  httpx.AsyncClient | None = None
_lib_client:   httpx.AsyncClient | None = None
_ref_client:   httpx.AsyncClient | None = None
_rag_client:   httpx.AsyncClient | None = None
_scrape_client: httpx.AsyncClient | None = None
_graph_client: httpx.AsyncClient | None = None

_ui_service_cards: list[dict] = []
_ui_status_task:   asyncio.Task | None = None

_UI_SERVICE_SPECS = (
    ("KoreFeed",      "feeds",     "korefeed",      "_feed_client",   "korefeed_url"),
    ("KoreLibrary",   "library",   "korelibrary",   "_lib_client",    "korelibrary_url"),
    ("KoreReference", "reference", "korereference", "_ref_client",    "korereference_url"),
    ("KoreRAG",       "rag",       "korerag",       "_rag_client",    "korerag_url"),
    ("KoreScrape",    "scrape",    "korescrape",    "_scrape_client", "korescrape_url"),
    ("KoreGraph",     "graph",     "koregraph",     "_graph_client",  "koregraph_url"),
)


def _unavailable_ui_service_cards() -> list[dict]:
    unavailable = RuntimeError("Status check pending")
    return [
        _svc_ui(unavailable, label, slug, cfg[url_key], icon_key)
        for label, slug, icon_key, _client_name, url_key in _UI_SERVICE_SPECS
    ]


async def _refresh_ui_service_cards() -> None:
    global _ui_service_cards
    clients = [globals()[client_name] for _label, _slug, _icon_key, client_name, _url_key in _UI_SERVICE_SPECS]
    responses = await asyncio.gather(
        *(client.get("/status", timeout=3.0) for client in clients if client is not None),
        return_exceptions = True,
    )
    _ui_service_cards = [
        _svc_ui(response, label, slug, cfg[url_key], icon_key)
        for (label, slug, icon_key, _client_name, url_key), response in zip(_UI_SERVICE_SPECS, responses)
    ]


async def _refresh_ui_service_cards_loop() -> None:
    while True:
        try:
            await _refresh_ui_service_cards()
        except Exception:
            pass
        await asyncio.sleep(2.0)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _child_readiness_task
    global _feed_client, _lib_client, _ref_client, _rag_client, _scrape_client, _graph_client
    global _ui_status_task
    print("\n  KoreDataGateway — starting child services")
    _set_gateway_status("starting", "Starting child services")
    loop = asyncio.get_running_loop()

    def _exception_handler(loop_obj: asyncio.AbstractEventLoop, context: dict) -> None:
        exc      = context.get("exception")
        handle   = context.get("handle")
        callback = getattr(handle, "_callback", None)
        cb_name  = getattr(callback, "__qualname__", repr(callback))
        if (
            isinstance(exc, ConnectionResetError)
            and getattr(exc, "winerror", None) == 10054
            and "_call_connection_lost" in str(cb_name)
        ):
            return
        loop_obj.default_exception_handler(context)

    loop.set_exception_handler(_exception_handler)
    _start_children()
    _feed_client   = httpx.AsyncClient(base_url=cfg["korefeed_url"],      timeout=15.0)
    _lib_client    = httpx.AsyncClient(base_url=cfg["korelibrary_url"],   timeout=15.0)
    _ref_client    = httpx.AsyncClient(base_url=cfg["korereference_url"], timeout=15.0)
    _rag_client    = httpx.AsyncClient(base_url=cfg["korerag_url"],       timeout=15.0)
    _scrape_client = httpx.AsyncClient(base_url=cfg["korescrape_url"],   timeout=30.0)
    _graph_client  = httpx.AsyncClient(base_url=cfg["koregraph_url"],     timeout=15.0)
    _child_readiness_task = asyncio.create_task(_wait_for_children_ready())
    _ui_status_task       = asyncio.create_task(_refresh_ui_service_cards_loop())
    async with _mcp.session_manager.run():
        yield
    print("\n  KoreDataGateway — shutting down child services")
    if _child_readiness_task is not None:
        _child_readiness_task.cancel()
        try:
            await _child_readiness_task
        except asyncio.CancelledError:
            pass
        _child_readiness_task = None
    if _ui_status_task is not None:
        _ui_status_task.cancel()
        try:
            await _ui_status_task
        except asyncio.CancelledError:
            pass
        _ui_status_task = None
    await _feed_client.aclose()
    await _lib_client.aclose()
    await _ref_client.aclose()
    await _rag_client.aclose()
    await _scrape_client.aclose()
    await _graph_client.aclose()
    _stop_children()


app = FastAPI(
    title="KoreDataGateway",
    description="Central web UI for KoreData services",
    lifespan=_lifespan,
)

# ---------------------------------------------------------------------------
# MCP server (mounted at /mcp — Streamable HTTP transport)
# ---------------------------------------------------------------------------

_CHUNK_SIZE = 8000  # default characters per library book chunk

_INSTR_SEARCH = (
    "Use koredata_search(query, domains) to search across services. "
    "Omit domains to search all at once. "
    "Results include a snippet field (first ~300 chars) and an artifact_ref for follow-up fetches. "
    "Base answers ONLY on content retrieved from the get_* tools — do not supplement with training knowledge."
)

_INSTR_FEEDS = (
    "KoreFeeds — current news and articles. "
    "Search with domains=[\"feeds\"]; optionally filter by since/until (YYYY-MM-DD). "
    "Fetch full entries with koredata_get_full_text(refid) or koredata_get_feed_entry(domain, entry_id). "
    "Fetch a specific indexed sentence with koredata_get_sentence(locator), where locator looks like feeds/<domain>/<sentence_id>."
)

_INSTR_REFERENCE = (
    "KoreReference — encyclopedia-style wiki articles. "
    "Search with domains=[\"reference\"]. "
    "Fetch full articles with koredata_get_full_text(refid) or koredata_get_reference_article(title)."
)

_INSTR_LIBRARY = (
    "KoreLibrary — full-text books. "
    "Find a book by title with koredata_find_library_book(title) — returns book_id, author, "
    f"genre, word_count, and chunks (number of {_CHUNK_SIZE}-char chunks to read the full text). "
    "Browse all books with koredata_get_library_index(). "
    f"Read a book chunk-by-chunk with koredata_get_library_book_chunk(book_id, offset_chars, length_chars={_CHUNK_SIZE}). "
    "Each call returns: chunk (the text slice), next_offset, has_more. "
    "Pass next_offset as offset_chars for the next call. Stop when has_more is false. "
    "Never attempt to read a whole book in one call — always use chunks. "
    "When a book needs editing, use koredata_update_library_book(book_id, title=?, body=?, author=?, year=?, language=?, genre=?, notes=?, source=?, source_id=?) to patch metadata and/or body directly. "
    "When the body only needs anchor cleanup after TOC or hyperlink repairs, use koredata_repair_library_book_anchors(book_id)."
)

_INSTR_RAG = (
    "KoreRAG — internal documents and user notes. "
    "Search with domains=[\"rag\"]. "
    "Fetch full chunks with koredata_get_full_text(refid) or koredata_get_rag_chunk(chunk_id)."
)


_INSTR_SCRAPE = (
    "KoreScrape — captured web pages indexed into extracted text chunks. "
    "Search with domains=[\"scrape\"]. "
    "Fetch full chunks with koredata_get_full_text(refid) or koredata_get_scrape_chunk(chunk_id)."
)

_INSTR_GRAPH = (
    "KoreGraph — concept knowledge graph. "
    "Search with domains=[\"graph\"] returns concept edges (start, connection, end, score). "
    "If KoreGraph MCP tools are available, add a single graph connection with graph_connection_create(start, connection, end). "
    "Add multiple graph connections at once with graph_connection_create_many([{start, connection, end}, ...]). "
    "Always use graph_connection_create_many when submitting more than one graph connection. "
    "Preferred relationship types: is_a (taxonomy only), part_of, contributed_to, discovered, "
    "developed, proposed, invented, studied, applied_to, influenced, precedes, lived_in, "
    "wrote, disproved, succeeded, is_type_of. "
    "Nodes must be named entities — people, theories, instruments, places — not chapter headings, "
    "historical eras, or abstract topic labels."
)

_mcp = FastMCP(
    "KoreDataGateway",
    instructions="\n\n".join([
        _INSTR_SEARCH,
        _INSTR_FEEDS,
        _INSTR_REFERENCE,
        _INSTR_LIBRARY,
        _INSTR_RAG,
        _INSTR_SCRAPE,
        _INSTR_GRAPH,
    ]),
    streamable_http_path="/",
    stateless_http=True,
)

_GATEWAY_UI_ROOT = Path(
    os.environ.get(
        "KORE_KOREDATAGATEWAY_UI_DIR",
        str(Path(__file__).resolve().parents[3] / "KoreUI" / "KoreData" / "KoreDataGateway"),
    )
).resolve()
TEMPLATES_DIR = Path(
    os.environ.get(
        "KORE_KOREDATAGATEWAY_TEMPLATES_DIR",
        str(_GATEWAY_UI_ROOT / "templates"),
    )
).resolve()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
_UI_ELEMENTS_ASSETS = Path(
    os.environ.get(
        "KORE_UIELEMENTS_ASSETS_DIR",
        str(Path(__file__).resolve().parents[3] / "KoreUI" / "UIElements" / "assets"),
    )
).resolve()
register_suite_shell_routes(
    app,
    service_key            = "koredatagateway",
    service_label          = "KoreDataGateway",
    ui_elements_assets_dir = _UI_ELEMENTS_ASSETS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _svc_ui(r: Any, label: str, slug: str, url: str, icon_key: str) -> dict:
    """Build a service summary dict for the landing page template."""
    healthy = not isinstance(r, Exception) and r.status_code == 200
    return {"label": label, "slug": slug, "url": url, "icon_key": icon_key, "healthy": healthy,
            "stats": r.json() if healthy else {}}


def _svc_status(r: Any, url: str) -> dict:
    """Build a child status dict for the /status endpoint (flattens child /status fields)."""
    healthy = not isinstance(r, Exception) and r.status_code == 200
    return {"url": url, "healthy": healthy, **(r.json() if healthy else {})}


# ---------------------------------------------------------------------------
# Unified search — agent API
# ---------------------------------------------------------------------------

def _normalise_graph_query_literal(query: str) -> str:
    """Treat a fully quoted graph query as one literal term before gateway dispatch."""
    text = str(query or "").strip()
    while len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    return text


async def _rag_databases_enriched() -> list[dict[str, Any]]:
    return await _gateway_enrich_rag_databases(_rag_client, data_root=get_koredata_dir())


def _rag_processing_scripts(database_ids: set[str]) -> list[dict[str, Any]]:
    return _gateway_list_rag_processing_scripts(get_koredata_dir(), database_ids)


def _normalize_rag_processing_schedule(value: object) -> str:
    return _gateway_normalise_rag_processing_schedule(value)


def _map_feed_entry(entry: dict) -> dict:
    """Compatibility adapter for callers that previously imported this server helper."""
    return _canonical_map_feed_entry(entry, cfg)


def _map_ref_article(article: dict) -> dict:
    """Compatibility adapter for callers that previously imported this server helper."""
    return _canonical_map_reference_article(article, cfg)


async def api_search(req: _SearchRequest):
    return await _run_search(
        req,
        cfg           = cfg,
        feed_client   = _feed_client,
        lib_client    = _lib_client,
        ref_client    = _ref_client,
        rag_client    = _rag_client,
        scrape_client = _scrape_client,
        graph_client  = _graph_client,
    )
async def api_full_text(req: _FullTextRequest):
    return await koredata_get_full_text(req.refid)


async def api_sentence(req: _SentenceRequest):
    return await koredata_get_sentence(req.locator)


async def api_sentence_get(locator: str):
    return await koredata_get_sentence(locator)


# ===========================================================================
# MCP tools
# ===========================================================================

@_mcp.tool()
async def koredata_search(
    query: str,
    domains: Optional[list[str]] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """Search across KoreData services and return structured results.

    Args:
        query: Search string. Bare terms use AND by default. Use quoted phrases,
               OR or | for alternatives, NOT to exclude, and parentheses to group.
        domains: Which services to search — any of "feeds", "reference", "library", "rag", "scrape", "graph".
                 Omit or pass null to search the default UI/API set.
        since: Earliest published-date filter (YYYY-MM-DD). Applied to feeds only.
        until: Latest published-date filter (YYYY-MM-DD). Applied to feeds only.
        limit: Maximum results per selected domain (1–200, default 20).

    Returns a dict with keys "query", "domains_searched", "results" (merged flat list),
    and "results_by_domain" (per-service lists). Text-bearing result items include a
    "snippet" for relevance assessment, a "url" field, and an "artifact_ref" string that
    can be passed to koredata_get_full_text(refid) to fetch the full content.
    """
    if _feed_client is None:
        return {"error": "KoreDataGateway is still starting up — retry in a moment"}
    # Coerce comma-separated string to list in case the model serialises incorrectly
    if isinstance(domains, str):
        domains = [d.strip() for d in domains.split(",") if d.strip()]
    req = _SearchRequest(query=query, domains=domains or [], since=since, until=until, limit=limit)
    return await api_search(req)


@_mcp.tool()
async def koredata_get_sentence(locator: str) -> dict:
    """Fetch a single indexed sentence by semantic locator.

    Args:
        locator: Sentence locator in the form "<service>/<database>/<sentence_id>".
                 Currently supported: feeds/<domain>/<sentence_id>,
                 reference/main/<sentence_id>.

    Returns the sentence text plus source metadata so the agent can recover the
    originating entry and surrounding provenance.
    """
    try:
        service, database, sentence_id = _parse_sentence_locator(locator)
    except ValueError as exc:
        return {"error": str(exc)}

    if service == "feeds":
        return await _gateway_get_feed_sentence(
            _feed_client,
            domain      = database,
            sentence_id = sentence_id,
        )

    if service == "reference":
        return await _gateway_get_reference_sentence(
            _ref_client,
            database    = database,
            sentence_id = sentence_id,
        )

    return {"error": f"Unsupported sentence locator service: {service!r}"}


# MARK: KoreFeed Routines
@_mcp.tool()
async def koredata_get_feed_entry(domain: str, entry_id: int) -> dict:
    """Fetch the full content of a news feed entry.

    Args:
        domain: Feed domain slug (e.g. "tech", "world"). Use the value from search results.
        entry_id: Numeric entry ID returned by search.

    Returns the full entry including page text, metadata, and publication details.
    """
    return await _gateway_get_feed_entry(
        _feed_client,
        domain   = domain,
        entry_id = entry_id,
    )


# MARK: KoreReference Routines
@_mcp.tool()
async def koredata_get_reference_article(title: str) -> dict:
    """Fetch the full content of a reference (wiki-style) article.

    Args:
        title: Article title exactly as returned by search (URL-decoding is handled automatically).

    Returns the full article including:
    - body: full wikitext body
    - sections: list of section dicts [{title, content}]
    - summary: short description
    - lead: introductory paragraphs before the first section heading
    - facts: structured infobox data as a list of {key, value} pairs (empty list when not available)
    - links: internal links from this article to other articles

    Use this tool when you have a specific article title. For keyword searches across the
    reference collection, use koredata_search(domains=["reference"]) instead.
    """
    return await _gateway_get_reference_article(
        _ref_client,
        title = title,
    )


# MARK: KoreLibrary Routines
@_mcp.tool()
async def koredata_find_library_book(title: str) -> dict:
    """Find library books by title. Returns closest matches ranked by title similarity.

    Use this to locate a book_id before reading with koredata_get_library_book_chunk.
    Searches across all catalogs. Prefer this over koredata_search for known titles.

    Args:
        title: Book title or partial title (e.g. "History of Science").

    Returns:
        count   — number of matches found
        matches — list ordered best-match first, each with book_id, title, author,
                  year, genre, word_count, chunks.
    """
    return await _gateway_find_library_book(
        _lib_client,
        title      = title,
        chunk_size = _CHUNK_SIZE,
    )


@_mcp.tool()
async def koredata_get_library_index() -> dict:
    """Return a full index of all library books — title, author, catalog, genre, word_count,
    and chunk count (how many _CHUNK_SIZE-char chunks it takes to read the full text).

    Call this once to choose a book, then call koredata_get_library_book_chunk to read it.
    Chunk count is calculated from word_count (≈5 chars/word ÷ _CHUNK_SIZE chars/chunk).
    """
    return await _gateway_get_library_index(
        _lib_client,
        chunk_size = _CHUNK_SIZE,
    )


@_mcp.tool()
async def koredata_get_library_book_chunk(
    book_id: str,
    offset_chars: int = 0,
    length_chars: int = _CHUNK_SIZE,
) -> dict:
    """Read a section of a library book body by character offset.

    Books are often 50,000–100,000 words. Use this instead of koredata_get_library_book
    to read long books in manageable chunks. Call repeatedly with increasing offset_chars
    to page through the full text.

    Args:
        book_id: Book ID from search or koredata_get_library_index (e.g. "sciencehistory:6").
        offset_chars: Character position to start reading from (default 0 = beginning).
        length_chars: Characters to return (default 8000, max 16000).

    Returns:
        title, author, genre — book metadata
        chunk              — the text slice
        offset_chars       — offset used
        next_offset        — pass this as offset_chars for the next chunk (null if at end)
        total_chars        — full body length in characters
        has_more           — true if there is more content after this chunk
    """
    return await _gateway_get_library_book_chunk(
        _lib_client,
        book_id      = book_id,
        offset_chars = offset_chars,
        length_chars = length_chars,
    )


@_mcp.tool()
async def koredata_update_library_book(
    book_id: str,
    title: Optional[str] = None,
    body: Optional[str] = None,
    author: Optional[str] = None,
    year: Optional[int] = None,
    language: Optional[str] = None,
    genre: Optional[str] = None,
    notes: Optional[str] = None,
    source: Optional[str] = None,
    source_id: Optional[str] = None,
) -> dict:
    """Patch a library book's metadata and/or body.

    Use this for TOC fixes, hyperlink repairs, metadata cleanup, and any other direct book edit.
    Pass only the fields that need changing; omitted fields are left untouched.
    """
    return await _gateway_update_library_book(
        _lib_client,
        book_id   = book_id,
        title     = title,
        body      = body,
        author    = author,
        year      = year,
        language  = language,
        genre     = genre,
        notes     = notes,
        source    = source,
        source_id = source_id,
    )


@_mcp.tool()
async def koredata_repair_library_book_anchors(book_id: str) -> dict:
    """Repair stored anchor spans in a book body after TOC or hyperlink navigation fixes."""
    return await _gateway_repair_library_book_anchors(
        _lib_client,
        book_id = book_id,
    )


# MARK: KoreRAG Routines
@_mcp.tool()
async def koredata_get_rag_chunk(chunk_id: int) -> dict:
    """Fetch the full content of a RAG (retrieval-augmented generation) chunk.

    Args:
        chunk_id: Numeric chunk ID returned by search.

    Returns the full chunk including decompressed content, title, source, and tags.
    """
    return await _gateway_get_rag_chunk(
        _rag_client,
        chunk_id = chunk_id,
    )


# MARK: KoreScrape Routines
@_mcp.tool()
async def koredata_get_scrape_chunk(chunk_id: int) -> dict:
    """Fetch the full content of a KoreScrape extracted text chunk."""
    return await _gateway_get_scrape_chunk(
        _scrape_client,
        chunk_id = chunk_id,
    )


@_mcp.tool()
async def koredata_get_full_text(refid: str) -> dict:
    """Fetch the full content for a text-bearing search result via its artifact_ref.

    Args:
        refid: The artifact_ref value returned by koredata_search(...). Supported kinds:
               feed_entry, reference_article, rag_chunk, scrape_chunk. Library books return a chunking
               guidance error because they should be read incrementally.

    Use this when you already have a search result row and want a single follow-up fetch path
    without switching on domain-specific ids or title fields.
    """
    try:
        kind, parts = _parse_artifact_ref(refid)
    except ValueError as exc:
        return {"error": str(exc)}

    if kind == "feed_entry":
        domain = (parts.get("domain") or "").strip()
        raw_id = (parts.get("id") or "").strip()
        if not domain or not raw_id:
            return {"error": f"Feed artifact ref is incomplete: {refid!r}"}
        try:
            entry_id = int(raw_id)
        except ValueError:
            return {"error": f"Feed artifact ref has non-numeric id: {raw_id!r}"}
        return await koredata_get_feed_entry(domain=domain, entry_id=entry_id)

    if kind == "reference_article":
        title = (parts.get("title") or "").strip()
        if not title:
            return {"error": f"Reference artifact ref is missing title: {refid!r}"}
        return await koredata_get_reference_article(title=title)

    if kind == "rag_chunk":
        raw_id = (parts.get("id") or "").strip()
        if not raw_id:
            return {"error": f"RAG artifact ref is missing id: {refid!r}"}
        try:
            chunk_id = int(raw_id)
        except ValueError:
            return {"error": f"RAG artifact ref has non-numeric id: {raw_id!r}"}
        return await koredata_get_rag_chunk(chunk_id=chunk_id)

    if kind == "scrape_chunk":
        raw_id = (parts.get("id") or "").strip()
        if not raw_id:
            return {"error": f"Scrape artifact ref is missing id: {refid!r}"}
        try:
            chunk_id = int(raw_id)
        except ValueError:
            return {"error": f"Scrape artifact ref has non-numeric id: {raw_id!r}"}
        return await koredata_get_scrape_chunk(chunk_id=chunk_id)

    if kind == "library_book":
        book_id = (parts.get("book_id") or "").strip()
        if not book_id:
            return {"error": f"Library artifact ref is missing book_id: {refid!r}"}
        return {
            "error": (
                "Library books are chunked by design. "
                f"Use koredata_get_library_book_chunk(book_id={book_id!r}, offset_chars=0)."
            )
        }

    return {"error": f"Unsupported artifact ref kind: {kind!r}"}


register_gateway_api_routes(
    app,
    search        = api_search,
    get_full_text = koredata_get_full_text,
    get_sentence  = koredata_get_sentence,
)


# ===========================================================================
# Web UI — Core routes
# ===========================================================================

@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse("/ui", status_code=302)


@app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def web_root(request: Request):
    if _feed_client is None:
        raise HTTPException(status_code=503, detail="Gateway is still starting up")
    services = _ui_service_cards or _unavailable_ui_service_cards()
    return templates.TemplateResponse(request, "home.html", {"services": services})


# ===========================================================================
# Gateway status
# ===========================================================================

@app.get("/status")
async def gateway_status():
    if _feed_client is None:
        return {"service": "KoreDataGateway", "status": "starting"}
    startup = _get_gateway_startup_snapshot()
    child_snapshot = startup.get("children") if isinstance(startup.get("children"), dict) else {}
    return {
        "service": "KoreDataGateway",
        "status":  startup.get("status", "starting"),
        "message": startup.get("message", ""),
        "startup": startup,
        "children": {
            "korefeed": {
                "url": cfg["korefeed_url"],
                **(child_snapshot.get("KoreFeed") or {}),
            },
            "korelibrary": {
                "url": cfg["korelibrary_url"],
                **(child_snapshot.get("KoreLibrary") or {}),
            },
            "korereference": {
                "url": cfg["korereference_url"],
                **(child_snapshot.get("KoreReference") or {}),
            },
            "korerag": {
                "url": cfg["korerag_url"],
                **(child_snapshot.get("KoreRAG") or {}),
            },
            "korescrape": {
                "url": cfg["korescrape_url"],
                **(child_snapshot.get("KoreScrape") or {}),
            },
            "koregraph": {
                "url": cfg["koregraph_url"],
                **(child_snapshot.get("KoreGraph") or {}),
            },
        },
    }


# ===========================================================================
# MCP server mount
# ===========================================================================

app.mount("/mcp", _mcp.streamable_http_app())
