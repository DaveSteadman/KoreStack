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
import json as _json
import logging
import math
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import threading
from typing import Any, Optional
from urllib.parse import quote, urlencode, unquote, urlsplit

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

_KORECOMMON_PARENT = next((parent for parent in Path(__file__).resolve().parents if (parent / "KoreCommon").is_dir()), None)
if _KORECOMMON_PARENT is not None and str(_KORECOMMON_PARENT) not in sys.path:
    sys.path.insert(0, str(_KORECOMMON_PARENT))

from KoreCommon.endpoint_manifest import build_endpoint_manifest
from app.config import cfg
from config import get_koredata_dir, get_suite_urls_map


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


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _child_readiness_task
    global _feed_client, _lib_client, _ref_client, _rag_client, _scrape_client, _graph_client
    print("\n  KoreDataGateway — starting child services")
    _set_gateway_status("starting", "Starting child services")
    _start_children()
    _feed_client   = httpx.AsyncClient(base_url=cfg["korefeed_url"],      timeout=15.0)
    _lib_client    = httpx.AsyncClient(base_url=cfg["korelibrary_url"],   timeout=15.0)
    _ref_client    = httpx.AsyncClient(base_url=cfg["korereference_url"], timeout=15.0)
    _rag_client    = httpx.AsyncClient(base_url=cfg["korerag_url"],       timeout=15.0)
    _scrape_client = httpx.AsyncClient(base_url=cfg["korescrape_url"],   timeout=30.0)
    _graph_client  = httpx.AsyncClient(base_url=cfg["koregraph_url"],     timeout=15.0)
    _child_readiness_task = asyncio.create_task(_wait_for_children_ready())
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
    "Never attempt to read a whole book in one call — always use chunks."
)

_INSTR_RAG = (
    "KoreRAG — internal documents and user notes. "
    "Search with domains=[\"rag\"]. "
    "Fetch full chunks with koredata_get_full_text(refid) or koredata_get_rag_chunk(chunk_id)."
)


@app.get("/__endpoint_manifest", include_in_schema=False)
def endpoint_manifest() -> dict:
    return build_endpoint_manifest(app, service_key="koredatagateway", service_label="KoreDataGateway")

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
        str(Path(__file__).resolve().parents[3] / "UIElements" / "assets"),
    )
).resolve()

@app.get("/ui-elements/assets/{asset_path:path}", include_in_schema=False)
def serve_ui_elements_asset(asset_path: str):
    candidate = (_UI_ELEMENTS_ASSETS / asset_path).resolve()
    if candidate != _UI_ELEMENTS_ASSETS and _UI_ELEMENTS_ASSETS not in candidate.parents:
        raise HTTPException(status_code=404, detail="Asset not found")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(str(candidate), headers={"Cache-Control": "no-store"})


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

class _SearchRequest(BaseModel):
    query:     str
    domains:   list[str]      = Field(default_factory=list)
    since:     Optional[str]  = None
    until:     Optional[str]  = None
    mode:      str            = "keyword"
    min_match: float          = Field(default=0.4, ge=0.0, le=1.0)
    limit:     int            = Field(default=20, ge=1, le=200)


class _FullTextRequest(BaseModel):
    refid: str


class _SentenceRequest(BaseModel):
    locator: str


def _normalise_graph_query_literal(query: str) -> str:
    """Treat a fully quoted graph query as one literal term before gateway dispatch."""
    text = str(query or "").strip()
    while len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    return text


def _build_artifact_ref(kind: str, **parts: Any) -> str:
    ref_parts = [kind]
    for key, value in parts.items():
        encoded = quote("" if value is None else str(value), safe="")
        ref_parts.append(f"{key}={encoded}")
    return "|".join(ref_parts)


def _parse_artifact_ref(refid: str) -> tuple[str, dict[str, str]]:
    text = str(refid or "").strip()
    if not text:
        raise ValueError("Artifact ref is empty.")
    segments = text.split("|")
    kind = segments[0].strip()
    if not kind:
        raise ValueError("Artifact ref is missing its kind.")
    values: dict[str, str] = {}
    for segment in segments[1:]:
        if "=" not in segment:
            raise ValueError(f"Malformed artifact ref component: {segment!r}")
        key, encoded = segment.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError("Artifact ref contains an empty key.")
        values[key] = unquote(encoded)
    return kind, values


def _parse_sentence_locator(locator: str) -> tuple[str, str, int]:
    text = str(locator or "").strip().strip("/")
    parts = [part.strip() for part in text.split("/") if part.strip()]
    if len(parts) != 3:
        raise ValueError(
            "Sentence locator must look like <service>/<database>/<sentence_id>."
        )
    service, database, raw_id = parts
    try:
        sentence_id = int(raw_id)
    except ValueError as exc:
        raise ValueError(f"Sentence locator has non-numeric sentence_id: {raw_id!r}") from exc
    return service.lower(), database, sentence_id


def _map_feed_entry(e: dict) -> dict:
    domain = e.get("domain", "")
    eid    = e.get("id", "")
    body   = e.get("page_text") or e.get("content") or e.get("body") or e.get("summary") or ""
    return {
        "domain":       "feeds",
        "type":         "feed_entry",
        "artifact_ref": _build_artifact_ref("feed_entry", domain=domain, id=eid),
        "id":           eid,
        "title":        e.get("headline") or e.get("title", ""),
        "source":       e.get("feed_name") or e.get("source_name") or domain,
        "published_at": e.get("published") or e.get("published_at") or e.get("ingested_at"),
        "snippet":      body[:300].strip(),
        "url":          f"{cfg['korefeed_url']}/ui/feeds/{domain}/{eid}",
        "score":        e.get("score"),
    }


def _map_ref_article(a: dict) -> dict:
    title = a.get("title", "")
    return {
        "domain":       "reference",
        "type":         "reference_article",
        "artifact_ref": _build_artifact_ref("reference_article", title=title),
        "title":      title,
        "summary":    a.get("summary", ""),
        "snippet":    a.get("snippet") or (a.get("summary") or "")[:300],
        "word_count": a.get("word_count"),
        "url":        f"{cfg['korereference_url']}/ui/reference/{quote(title, safe='')}",
        "score":      a.get("score"),
    }


def _map_lib_book(b: dict) -> dict:
    route_id = b.get("route_id") or b.get("id")
    return {
        "domain":       "library",
        "type":         "library_book",
        "artifact_ref": _build_artifact_ref("library_book", book_id=route_id),
        "id":       route_id,
        "local_id": b.get("id"),
        "catalog":  b.get("catalog"),
        "route_id": route_id,
        "title":    b.get("title", ""),
        "author":   b.get("author", ""),
        "snippet":  b.get("snippet") or (b.get("notes") or "")[:300],
        "url":      f"{cfg['korelibrary_url']}/ui/library/{route_id}",
        "score":    b.get("score"),
    }


def _map_rag_chunk(c: dict) -> dict:
    db_id = c.get("db", "default")
    return {
        "domain":       "rag",
        "type":         "rag_chunk",
        "artifact_ref": _build_artifact_ref("rag_chunk", id=c.get("id")),
        "id":      c.get("id"),
        "title":   c.get("title", ""),
        "source":  c.get("source", ""),
        "tags":    c.get("tags", ""),
        "snippet": c.get("snippet") or "",
        "url":     f"{cfg['korerag_url']}/ui/rag/{c.get('id', '')}?db={db_id}",
        "score":   c.get("score"),
    }


def _map_scrape_chunk(c: dict) -> dict:
    capture_id = c.get("capture_id", "")
    page_path  = c.get("page_path", "")
    return {
        "domain":       "scrape",
        "type":         "scrape_chunk",
        "artifact_ref": _build_artifact_ref("scrape_chunk", id=c.get("id")),
        "id":           c.get("id"),
        "capture_id":   capture_id,
        "title":        c.get("page_title", "") or c.get("page_url", ""),
        "source":       c.get("page_url", ""),
        "captured_at":  c.get("captured_at"),
        "snippet":      c.get("snippet") or "",
        "url":          f"{cfg['korescrape_url']}/ui/scrape/files/{capture_id}/{page_path}" if capture_id and page_path else "",
        "score":        c.get("score"),
    }


def _search_query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for term in re.findall(r'"([^"]+)"|([A-Za-z0-9_:+.-]+)', str(query or "")):
        value = (term[0] or term[1] or "").strip().lower()
        if not value or value in {"and", "or", "not"}:
            continue
        terms.append(value)
    return terms


def _parse_search_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _result_match_score(item: dict, query: str, query_terms: list[str]) -> float:
    text_fields = [
        str(item.get("title", "")),
        str(item.get("snippet", "")),
        str(item.get("summary", "")),
        str(item.get("source", "")),
        str(item.get("start", "")),
        str(item.get("connection", "")),
        str(item.get("end", "")),
    ]
    haystack = "\n".join(text_fields).lower()
    title_l  = str(item.get("title", "")).lower()
    query_l  = str(query or "").strip().lower()
    score    = 0.0

    raw_score = item.get("score")
    if isinstance(raw_score, (int, float)):
        score += -float(raw_score)

    if query_l and query_l in title_l:
        score += 18.0
    elif query_l and query_l in haystack:
        score += 9.0

    for term in query_terms:
        if term in title_l:
            score += 6.0
        elif term in haystack:
            score += 2.0

    timestamp = (
        _parse_search_timestamp(item.get("published_at"))
        or _parse_search_timestamp(item.get("captured_at"))
    )
    if timestamp is not None:
        age_days = max(0.0, (datetime.now(timezone.utc) - timestamp).total_seconds() / 86400.0)
        score += max(0.0, 5.0 - min(age_days, 30.0) / 6.0)

    domain = str(item.get("domain", "")).lower()
    if domain == "reference":
        score += 1.0
    if domain == "feeds":
        score += 0.5
    return score


def _merge_search_results(results_by_domain: dict[str, list[dict]], query: str, limit: int) -> list[dict]:
    query_terms = _search_query_terms(query)
    merged: list[tuple[float, float, int, dict]] = []
    ordinal = 0

    for domain in ("feeds", "reference", "library", "rag", "scrape", "graph"):
        items = results_by_domain.get(domain) or []
        for item in items:
            if not isinstance(item, dict):
                continue
            annotated = dict(item)
            annotated.setdefault("domain", domain)
            timestamp = (
                _parse_search_timestamp(annotated.get("published_at"))
                or _parse_search_timestamp(annotated.get("captured_at"))
            )
            recency = timestamp.timestamp() if timestamp is not None else 0.0
            merged.append((_result_match_score(annotated, query, query_terms), recency, ordinal, annotated))
            ordinal += 1

    merged.sort(
        key=lambda row: (
            -row[0],
            -row[1],
            row[2],
        ),
    )
    return [item for _score, _recency, _ordinal, item in merged[:limit]]


_SEMANTIC_SEARCH_DOMAINS = {"feeds", "library", "reference"}


@app.post("/api/search")
async def api_search(req: _SearchRequest):
    requested_domains = [d.lower() for d in req.domains] if req.domains else ["feeds", "reference", "library", "rag", "scrape"]
    search_mode       = "semantic" if str(req.mode or "").strip().lower() == "semantic" else "keyword"
    min_match         = max(0.0, min(1.0, float(req.min_match or 0.0)))
    limit             = req.limit

    if search_mode == "semantic":
        unsupported_domains = [domain for domain in requested_domains if domain not in _SEMANTIC_SEARCH_DOMAINS]
        search_domains      = [domain for domain in requested_domains if domain in _SEMANTIC_SEARCH_DOMAINS]
        if not search_domains:
            search_domains = sorted(_SEMANTIC_SEARCH_DOMAINS)
    else:
        unsupported_domains = []
        search_domains      = requested_domains

    async def _feeds():
        if search_mode == "semantic":
            params: dict = {"q": req.query, "limit": limit, "min_match": min_match}
            r = await _feed_client.get("/api/semantic-search", params=params, timeout=10.0)
        else:
            params = {"q": req.query, "limit": limit, "full": "true"}
            if req.since: params["since"] = req.since
            if req.until: params["until"] = req.until
            r = await _feed_client.get("/api/search", params=params, timeout=10.0)
        if search_mode == "semantic" and r.status_code == 503:
            detail = ""
            try:
                detail = str((r.json() or {}).get("detail") or "")
            except Exception:
                detail = ""
            warning = detail or "Semantic search unavailable."
            return {
                "status":   "partial",
                "results":  [],
                "error":    "",
                "warnings": [f"Feed semantic search unavailable: {warning}"],
            }
        if r.status_code != 200:
            return {
                "status":   "error",
                "results":  [],
                "error":    f"HTTP {r.status_code}",
                "warnings": [],
            }
        payload = r.json() or []
        if not isinstance(payload, list):
            return {
                "status":   "error",
                "results":  [],
                "error":    "Feed search returned a non-list payload.",
                "warnings": [],
            }
        failed_domains = [part.strip() for part in str(r.headers.get("X-Kore-Failed-Domains", "")).split(",") if part.strip()]
        warnings: list[str] = []
        if failed_domains:
            warnings.append(f"Feed search skipped failing domains: {', '.join(failed_domains)}")
        if search_mode == "semantic":
            return {
                "status":   "partial" if failed_domains else "ok",
                "results":  [
                    {
                        "domain":          "feeds",
                        "feed_domain":     e.get("domain") or "",
                        "type":            "feed_entry",
                        "artifact_ref":    _build_artifact_ref("feed_entry", domain=e.get("domain"), id=e.get("id")),
                        "id":              e.get("id"),
                        "title":           e.get("headline") or "",
                        "headline":        e.get("headline") or "",
                        "source":          e.get("feed_name") or "",
                        "published_at":    e.get("published") or "",
                        "snippet":         e.get("snippet") or "",
                        "url":             e.get("url") or "",
                        "sentence_id":     e.get("sentence_id"),
                        "sentence_locator": e.get("sentence_locator") or "",
                        "match_score":     e.get("match_score"),
                    }
                    for e in payload[:limit]
                ],
                "error":    "",
                "warnings": warnings,
            }
        return {
            "status":   "partial" if failed_domains else "ok",
            "results":  [_map_feed_entry(e) for e in payload[:limit]],
            "error":    "",
            "warnings": warnings,
        }

    async def _reference():
        params: dict = {"q": req.query, "limit": limit}
        if search_mode == "semantic":
            params["min_match"] = min_match
            r = await _ref_client.get("/api/semantic-search", params=params, timeout=10.0)
        else:
            r = await _ref_client.get("/api/search", params=params, timeout=10.0)
        if search_mode == "semantic" and r.status_code == 503:
            detail = ""
            try:
                detail = str((r.json() or {}).get("detail") or "")
            except Exception:
                detail = ""
            warning = detail or "Semantic search unavailable."
            return {"status": "partial", "results": [], "error": "", "warnings": [f"Reference semantic search unavailable: {warning}"]}
        if r.status_code != 200:
            return {"status": "error", "results": [], "error": f"HTTP {r.status_code}", "warnings": []}
        payload = r.json() or []
        if not isinstance(payload, list):
            return {"status": "error", "results": [], "error": "Reference search returned a non-list payload.", "warnings": []}
        if search_mode == "semantic":
            return {
                "status": "ok",
                "results": [
                    {
                        "domain":           "reference",
                        "type":             "reference_article",
                        "artifact_ref":     _build_artifact_ref("reference_article", title=a.get("title") or ""),
                        "id":               a.get("id"),
                        "title":            a.get("title", ""),
                        "snippet":          a.get("snippet") or "",
                        "word_count":       a.get("word_count"),
                        "url":              f"{cfg['korereference_url']}/ui/reference/{quote(a.get('title') or '', safe='')}",
                        "sentence_id":      a.get("sentence_id"),
                        "sentence_locator": a.get("sentence_locator") or "",
                        "match_score":      a.get("match_score"),
                    }
                    for a in payload[:limit]
                ],
                "error":    "",
                "warnings": [],
            }
        return {"status": "ok", "results": [_map_ref_article(a) for a in payload[:limit]], "error": "", "warnings": []}

    async def _library():
        if search_mode == "semantic":
            params: dict = {"q": req.query, "limit": limit, "min_match": min_match}
            r = await _lib_client.get("/api/semantic-search", params=params, timeout=10.0)
        else:
            params = {"q": req.query, "limit": limit}
            r = await _lib_client.get("/api/search", params=params, timeout=10.0)
        if search_mode == "semantic" and r.status_code == 503:
            detail = ""
            try:
                detail = str((r.json() or {}).get("detail") or "")
            except Exception:
                detail = ""
            warning = detail or "Semantic search unavailable."
            return {"status": "partial", "results": [], "error": "", "warnings": [f"Library semantic search unavailable: {warning}"]}
        if r.status_code != 200:
            return {"status": "error", "results": [], "error": f"HTTP {r.status_code}", "warnings": []}
        payload = r.json() or []
        if not isinstance(payload, list):
            return {"status": "error", "results": [], "error": "Library search returned a non-list payload.", "warnings": []}
        if search_mode == "semantic":
            return {
                "status": "ok",
                "results": [
                    {
                        "domain":          "library",
                        "type":            "library_book",
                        "artifact_ref":    _build_artifact_ref("library_book", book_id=b.get("route_id") or b.get("id")),
                        "id":              b.get("route_id") or b.get("id"),
                        "local_id":        b.get("id"),
                        "title":           b.get("title", ""),
                        "author":          b.get("author", ""),
                        "language":        b.get("language", ""),
                        "genre":           b.get("genre", ""),
                        "year":            b.get("year"),
                        "snippet":         b.get("snippet") or "",
                        "url":             f"{cfg['korelibrary_url']}/ui/library/{b.get('route_id') or b.get('id')}",
                        "sentence_id":     b.get("sentence_id"),
                        "sentence_locator": b.get("sentence_locator") or "",
                        "match_score":     b.get("match_score"),
                    }
                    for b in payload[:limit]
                ],
                "error":    "",
                "warnings": [],
            }
        return {"status": "ok", "results": [_map_lib_book(b) for b in payload[:limit]], "error": "", "warnings": []}

    async def _rag():
        params: dict = {"q": req.query, "limit": limit}
        r = await _rag_client.get("/api/search/all", params=params, timeout=10.0)
        if r.status_code != 200:
            return {"status": "error", "results": [], "error": f"HTTP {r.status_code}", "warnings": []}
        payload = r.json() or []
        if not isinstance(payload, list):
            return {"status": "error", "results": [], "error": "RAG search returned a non-list payload.", "warnings": []}
        return {"status": "ok", "results": [_map_rag_chunk(c) for c in payload[:limit]], "error": "", "warnings": []}

    async def _scrape():
        params: dict = {"q": req.query, "limit": limit}
        r = await _scrape_client.get("/api/search", params=params, timeout=10.0)
        if r.status_code != 200:
            return {"status": "error", "results": [], "error": f"HTTP {r.status_code}", "warnings": []}
        payload = r.json() or []
        if not isinstance(payload, list):
            return {"status": "error", "results": [], "error": "Scrape search returned a non-list payload.", "warnings": []}
        return {"status": "ok", "results": [_map_scrape_chunk(c) for c in payload[:limit]], "error": "", "warnings": []}

    async def _graph():
        query = _normalise_graph_query_literal(req.query)
        query_l = query.lower()
        r = await _graph_client.get("/api/search", params={"q": query, "limit": min(limit, 50)}, timeout=10.0)
        if r.status_code != 200:
            return {"status": "error", "results": [], "error": f"HTTP {r.status_code}", "warnings": []}
        matches = r.json() or []
        if not matches:
            return {"status": "ok", "results": [], "error": "", "warnings": []}

        concept_rows = matches[: min(len(matches), max(1, min(limit, 8)))]
        expand_calls = [
            _graph_client.get(
                "/api/expand",
                params={"concept_id": row.get("concept_id"), "depth": 1, "min_score": 0},
                timeout=10.0,
            )
            for row in concept_rows
            if row.get("concept_id") is not None
        ]
        expand_results = await asyncio.gather(*expand_calls, return_exceptions=True)

        seen: set[tuple[str, str, str]] = set()
        edges: list[dict] = []
        for response in expand_results:
            if isinstance(response, Exception) or response.status_code != 200:
                continue
            data = response.json() or {}
            for edge in data.get("edges") or []:
                if edge.get("state", 0) not in (0, 1, 4):
                    continue
                key = (
                    str(edge.get("start_name", "")),
                    str(edge.get("connection_name", "")),
                    str(edge.get("end_name", "")),
                )
                if key in seen:
                    continue
                seen.add(key)
                edges.append(edge)

        def _edge_match_rank(edge: dict) -> tuple[int, int]:
            start_l      = str(edge.get("start_name", "")).lower()
            end_l        = str(edge.get("end_name", "")).lower()
            connection_l = str(edge.get("connection_name", "")).lower()
            if query_l and query_l in start_l:
                return (0, start_l.index(query_l))
            if query_l and query_l in end_l:
                return (1, end_l.index(query_l))
            if query_l and query_l in connection_l:
                return (2, connection_l.index(query_l))
            return (3, 10_000)

        edges = sorted(
            edges,
            key=lambda e: (
                _edge_match_rank(e)[0],
                _edge_match_rank(e)[1],
                -int(e.get("score", 0)),
                str(e.get("start_name", "")).lower(),
                str(e.get("connection_name", "")).lower(),
                str(e.get("end_name", "")).lower(),
            ),
        )[:50]
        return {
            "status": "ok",
            "results": [
                {
                    "domain":      "graph",
                    "start":       e.get("start_name", ""),
                    "connection":  e.get("connection_name", ""),
                    "end":         e.get("end_name", ""),
                    "score":       e.get("score", 0),
                }
                for e in edges
            ],
            "error": "",
            "warnings": [],
        }

    tasks: list[tuple[str, Any]] = []
    if "feeds"     in search_domains: tasks.append(("feeds",     _feeds()))
    if "reference" in search_domains: tasks.append(("reference", _reference()))
    if "library"   in search_domains: tasks.append(("library",   _library()))
    if "rag"       in search_domains: tasks.append(("rag",       _rag()))
    if "scrape"    in search_domains: tasks.append(("scrape",    _scrape()))
    if "graph"     in search_domains and search_mode != "semantic": tasks.append(("graph",     _graph()))

    gathered         = await asyncio.gather(*(coro for _, coro in tasks), return_exceptions=True)
    results_by_domain: dict[str, list[dict]] = {}
    domain_statuses:  dict[str, dict[str, Any]] = {}
    warnings:         list[str] = []

    for (key, _task), value in zip(tasks, gathered):
        if isinstance(value, Exception):
            results_by_domain[key] = []
            domain_statuses[key]   = {
                "status":   "error",
                "count":    0,
                "error":    str(value),
                "warnings": [],
            }
            warnings.append(f"{key} search failed: {value}")
            continue

        payload = value if isinstance(value, dict) else {}
        results = payload.get("results") if isinstance(payload.get("results"), list) else []
        status  = str(payload.get("status") or "ok")
        error   = str(payload.get("error") or "")
        domain_warnings = [str(item) for item in (payload.get("warnings") or []) if str(item).strip()]

        results_by_domain[key] = results
        domain_statuses[key]   = {
            "status":   status,
            "count":    len(results),
            "error":    error,
            "warnings": domain_warnings,
        }
        warnings.extend(domain_warnings)
        if error:
            warnings.append(f"{key} search failed: {error}")

    non_ok_statuses = [item.get("status") for item in domain_statuses.values() if item.get("status") != "ok"]
    total_results   = sum(len(items) for items in results_by_domain.values())
    if unsupported_domains:
        warnings.append(
            f"Semantic search is currently available only for: {', '.join(sorted(_SEMANTIC_SEARCH_DOMAINS))}. "
            f"Ignored: {', '.join(unsupported_domains)}"
        )
    if any(status == "error" for status in non_ok_statuses) and total_results == 0:
        overall_status = "error"
    elif non_ok_statuses:
        overall_status = "partial"
    else:
        overall_status = "ok"

    return {
        "query":              req.query,
        "mode":               search_mode,
        "min_match":          min_match if search_mode == "semantic" else None,
        "semantic_capable_domains": sorted(_SEMANTIC_SEARCH_DOMAINS),
        "domains_searched":   [key for key, _ in tasks],
        "status":             overall_status,
        "partial_failure":    overall_status != "ok",
        "result_counts_by_domain": {key: len(value) for key, value in results_by_domain.items()},
        "domain_statuses":    domain_statuses,
        "warnings":           warnings,
        "results":            _merge_search_results(results_by_domain, req.query, limit),
        "results_by_domain":  results_by_domain,
    }


@app.post("/api/full-text")
async def api_full_text(req: _FullTextRequest):
    return await koredata_get_full_text(req.refid)


@app.post("/api/sentence")
async def api_sentence(req: _SentenceRequest):
    return await koredata_get_sentence(req.locator)


@app.get("/api/sentence/{locator:path}")
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
        if _feed_client is None:
            return {"error": "KoreDataGateway is still starting up — retry in a moment"}
        r = await _feed_client.get(
            f"/api/domains/{quote(database, safe='')}/sentences/{sentence_id}",
            timeout=10.0,
        )
        if r.status_code == 404:
            return {
                "error": (
                    f"Sentence not found: locator={locator!r}"
                )
            }
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}"}
        data = r.json()
        if isinstance(data, dict) and "locator" not in data:
            data["locator"] = f"feeds/{database}/{sentence_id}"
        return data

    if service == "reference":
        if _ref_client is None:
            return {"error": "KoreDataGateway is still starting up — retry in a moment"}
        r = await _ref_client.get(
            f"/api/sentences/{sentence_id}",
            timeout=10.0,
        )
        if r.status_code == 404:
            return {
                "error": (
                    f"Sentence not found: locator={locator!r}"
                )
            }
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}"}
        data = r.json()
        if isinstance(data, dict) and "locator" not in data:
            data["locator"] = f"reference/{database}/{sentence_id}"
        return data

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
    if _feed_client is None:
        return {"error": "KoreDataGateway is still starting up — retry in a moment"}
    r = await _feed_client.get(f"/api/domains/{domain}/entries/{entry_id}", timeout=10.0)
    if r.status_code == 404:
        return {"error": f"Feed entry not found: domain={domain!r} id={entry_id}"}
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    return r.json()


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
    if _ref_client is None:
        return {"error": "KoreDataGateway is still starting up — retry in a moment"}
    r = await _ref_client.get(f"/articles/{quote(title, safe='')}", timeout=10.0)
    if r.status_code == 404:
        return {"error": f"Reference article not found: {title!r}"}
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    return r.json()


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
    if _lib_client is None:
        return {"error": "KoreDataGateway is still starting up — retry in a moment"}
    r = await _lib_client.get("/search", params={"title": title, "limit": 20}, timeout=10.0)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    books = r.json()
    if not isinstance(books, list):
        books = books.get("value", [])

    # Rank: exact match > starts-with > contains (all case-insensitive).
    q_lower = title.lower()
    def _rank(b):
        t = (b.get("title") or "").lower()
        if t == q_lower:           return 0
        if t.startswith(q_lower):  return 1
        return 2
    books.sort(key=_rank)

    return {
        "count": len(books),
        "matches": [
            {
                "book_id":    b.get("route_id") or f"{b.get('catalog')}:{b.get('id')}",
                "title":      b.get("title"),
                "author":     b.get("author"),
                "year":       b.get("year"),
                "genre":      b.get("genre"),
                "word_count": b.get("word_count"),
                "chunks":     math.ceil((b.get("word_count") or 0) * 5 / _CHUNK_SIZE) or None,
            }
            for b in books
        ],
    }


@_mcp.tool()
async def koredata_get_library_index() -> dict:
    """Return a full index of all library books — title, author, catalog, genre, word_count,
    and chunk count (how many _CHUNK_SIZE-char chunks it takes to read the full text).

    Call this once to choose a book, then call koredata_get_library_book_chunk to read it.
    Chunk count is calculated from word_count (≈5 chars/word ÷ _CHUNK_SIZE chars/chunk).
    """
    if _lib_client is None:
        return {"error": "KoreDataGateway is still starting up — retry in a moment"}
    r = await _lib_client.get("/books", params={"limit": 200, "offset": 0}, timeout=15.0)
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    data  = r.json()
    books = data if isinstance(data, list) else data.get("value", [])
    return {
        "count": len(books),
        "books": [
            {
                "book_id":      b.get("route_id") or f"local:{b.get('id')}",
                "title":        b.get("title"),
                "author":       b.get("author"),
                "year":         b.get("year"),
                "catalog":      b.get("catalog"),
                "genre":        b.get("genre"),
                "word_count":   b.get("word_count"),
                "chunks":       math.ceil((b.get("word_count") or 0) * 5 / _CHUNK_SIZE) or None,
            }
            for b in books
        ],
    }


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
    if _lib_client is None:
        return {"error": "KoreDataGateway is still starting up — retry in a moment"}
    length_chars = max(100, min(length_chars, 16000))
    offset_chars = max(0, offset_chars)
    r = await _lib_client.get(
        f"/books/{book_id}/chunk",
        params={"offset": offset_chars, "length": length_chars},
        timeout=15.0,
    )
    if r.status_code == 404:
        return {"error": f"Library book not found: id={book_id}"}
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    data = r.json()
    # Strip repeating book metadata from non-first chunks to reduce context noise.
    if offset_chars > 0:
        return {
            "chunk":        data.get("chunk"),
            "offset_chars": data.get("offset_chars"),
            "next_offset":  data.get("next_offset"),
            "total_chars":  data.get("total_chars"),
            "has_more":     data.get("has_more"),
        }
    return data


# MARK: KoreRAG Routines
@_mcp.tool()
async def koredata_get_rag_chunk(chunk_id: int) -> dict:
    """Fetch the full content of a RAG (retrieval-augmented generation) chunk.

    Args:
        chunk_id: Numeric chunk ID returned by search.

    Returns the full chunk including decompressed content, title, source, and tags.
    """
    if _rag_client is None:
        return {"error": "KoreDataGateway is still starting up — retry in a moment"}
    r = await _rag_client.get(f"/chunks/{chunk_id}", timeout=10.0)
    if r.status_code == 404:
        return {"error": f"RAG chunk not found: id={chunk_id}"}
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    return r.json()


# MARK: KoreScrape Routines
@_mcp.tool()
async def koredata_get_scrape_chunk(chunk_id: int) -> dict:
    """Fetch the full content of a KoreScrape extracted text chunk."""
    if _scrape_client is None:
        return {"error": "KoreDataGateway is still starting up — retry in a moment"}
    r = await _scrape_client.get(f"/chunks/{chunk_id}", timeout=10.0)
    if r.status_code == 404:
        return {"error": f"Scrape chunk not found: id={chunk_id}"}
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    return r.json()


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


# ===========================================================================
# Web UI — Core routes
# ===========================================================================

@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse("/ui", status_code=302)


@app.get("/suite-config.js", include_in_schema=False)
def suite_config_js():
    urls = _json.dumps(get_suite_urls_map())
    return Response(content=f"window.__koreSuiteUrls = {urls};", media_type="application/javascript", headers={"Cache-Control": "no-store"})


@app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def web_root(request: Request):
    if _feed_client is None:
        raise HTTPException(status_code=503, detail="Gateway is still starting up")
    kf_r, kl_r, kr_r, krag_r, ks_r, kg_r = await asyncio.gather(
        _feed_client.get("/status", timeout=3.0),
        _lib_client.get("/status", timeout=3.0),
        _ref_client.get("/status", timeout=3.0),
        _rag_client.get("/status", timeout=3.0),
        _scrape_client.get("/status", timeout=3.0),
        _graph_client.get("/status", timeout=3.0),
        return_exceptions=True,
    )
    services = [
        _svc_ui(kf_r,   "KoreFeed",      "feeds",     cfg["korefeed_url"],      "korefeed"),
        _svc_ui(kl_r,   "KoreLibrary",   "library",   cfg["korelibrary_url"],   "korelibrary"),
        _svc_ui(kr_r,   "KoreReference", "reference", cfg["korereference_url"], "korereference"),
        _svc_ui(krag_r, "KoreRAG",       "rag",       cfg["korerag_url"],       "korerag"),
        _svc_ui(ks_r,   "KoreScrape",    "scrape",    cfg["korescrape_url"],    "korescrape"),
        _svc_ui(kg_r,   "KoreGraph",     "graph",     cfg["koregraph_url"],     "koregraph"),
    ]
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
