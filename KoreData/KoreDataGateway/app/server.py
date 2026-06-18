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
#   - Serve web UI pages for feed management (GET|POST /ui/feeds/*)
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
import math
import os
import re
import signal
import sqlite3
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
import shutil
from typing import Any, Optional
from urllib.parse import quote, urlencode, unquote, urlsplit

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from app.config import cfg
from config import get_koredata_dir

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
_rag_processing_jobs: dict[str, subprocess.Popen] = {}
_RAG_SCRIPT_SCHEDULE_VALUES: set[str]             = {"manual", "daily", "weekly", "monthly"}


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
    print(f"  ◼ Clearing stale {label} listener  (pid {pid})")
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
        extra_env = {"KG_UI_PREFIX": "/graph"} if label == "KoreGraph" else {}
        proc = subprocess.Popen(
            [sys.executable, "main.py"],
            cwd=service_dir,
            stdout=log_file,
            stderr=log_file,
            env={**os.environ, **extra_env},
        )
        _children.append((proc, label, log_file))
        print(f"  > {label} starting  (pid {proc.pid})  log -> {_display_path(log_path)}")


def _stop_children() -> None:
    for proc, label, log_file in reversed(_children):
        if proc.poll() is not None:
            continue  # already exited
        print(f"  ◼ Stopping {label}  (pid {proc.pid})")
        proc.terminate()
    for proc, label, log_file in reversed(_children):
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            print(f"  ✗ Force-killing {label}")
            proc.kill()
        try:
            log_file.close()
        except Exception:
            pass


async def _wait_for(client: httpx.AsyncClient, label: str, timeout: float = 20.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        try:
            r = await client.get("/status", timeout=2.0)
            if r.status_code == 200:
                print(f"  ✓ {label} ready")
                return
        except Exception:
            pass
        await asyncio.sleep(0.5)
    print(f"  [!] {label} did not respond within {timeout:.0f}s - continuing anyway")


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
    global _feed_client, _lib_client, _ref_client, _rag_client, _scrape_client, _graph_client
    print("\n  KoreDataGateway — starting child services")
    _start_children()
    _feed_client  = httpx.AsyncClient(base_url=cfg["korefeed_url"],      timeout=15.0)
    _lib_client   = httpx.AsyncClient(base_url=cfg["korelibrary_url"],   timeout=15.0)
    _ref_client   = httpx.AsyncClient(base_url=cfg["korereference_url"], timeout=15.0)
    _rag_client   = httpx.AsyncClient(base_url=cfg["korerag_url"],       timeout=15.0)
    _scrape_client = httpx.AsyncClient(base_url=cfg["korescrape_url"],   timeout=30.0)
    _graph_client = httpx.AsyncClient(base_url=cfg["koregraph_url"],     timeout=15.0)
    await asyncio.gather(
        _wait_for(_feed_client,  "KoreFeed",      timeout=60.0),
        _wait_for(_lib_client,   "KoreLibrary"),
        _wait_for(_ref_client,   "KoreReference"),
        _wait_for(_rag_client,   "KoreRAG"),
        _wait_for(_scrape_client, "KoreScrape"),
        _wait_for(_graph_client, "KoreGraph"),
    )
    print("  All services ready\n")
    async with _mcp.session_manager.run():
        yield
    print("\n  KoreDataGateway — shutting down child services")
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
    "Fetch full entries with koredata_get_full_text(refid) or koredata_get_feed_entry(domain, entry_id)."
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
    "To extract KoreGraph connections from an entire book automatically, call "
    "koredata_build_graph_from_book(book_id) — it reads every chunk and submits all "
    "connections to KoreGraph in one tool call."
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

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
_UI_ELEMENTS_ASSETS = Path(
    os.environ.get(
        "KORE_UIELEMENTS_ASSETS_DIR",
        str(Path(__file__).resolve().parents[3] / "UIElements" / "assets"),
    )
).resolve()
_SCRAPE_DB_PATH = _scrape_data_root() / "scrape_index.db"

_TABLE_MARKER_RE = re.compile(r'<<<TABLE>>>(.*?)<<<ENDTABLE>>>', re.DOTALL)
_WIKILINK_RE     = re.compile(r'\[\[([^\]]+)\]\]')


def _resolve_wikilinks_in_html(html: str, dead_links: set | None = None) -> str:
    """Replace [[Display|Target]] / [[Target]] patterns inside already-safe HTML."""
    def _repl(m: re.Match) -> str:
        inner = m.group(1)
        if "|" in inner:
            display, target = inner.split("|", 1)
        else:
            display = target = inner
        target  = target.strip()
        display = display.strip()
        cls = ' class="ref-link-dead"' if dead_links and target.lower() in dead_links else ''
        return f'<a href="/ui/reference/{quote(target)}"{cls}>{escape(display)}</a>'
    return _WIKILINK_RE.sub(_repl, html)


def _process_inline(text: str, dead_links: set | None = None) -> str:
    """HTML-escape text and convert [[wikilinks]] to <a> anchors."""
    parts: list[str] = []
    last_end = 0
    for m in _WIKILINK_RE.finditer(text):
        parts.append(str(escape(text[last_end:m.start()])))
        inner = m.group(1)
        if "|" in inner:
            display, target = inner.split("|", 1)
        else:
            display = target = inner
        target = target.strip()
        display = display.strip()
        cls = ' class="ref-link-dead"' if dead_links and target.lower() in dead_links else ''
        parts.append(f'<a href="/ui/reference/{quote(target)}"{cls}>{escape(display)}</a>')
        last_end = m.end()
    parts.append(str(escape(text[last_end:])))
    return "".join(parts)


@app.get("/ui-elements/assets/{asset_path:path}", include_in_schema=False)
def serve_ui_elements_asset(asset_path: str):
    candidate = (_UI_ELEMENTS_ASSETS / asset_path).resolve()
    if candidate != _UI_ELEMENTS_ASSETS and _UI_ELEMENTS_ASSETS not in candidate.parents:
        raise HTTPException(status_code=404, detail="Asset not found")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(str(candidate), headers={"Cache-Control": "no-store"})


def _render_list_lines(lines: list[str], dead_links: set | None = None) -> str:
    """Recursively render indented '* '/'# '-prefixed lines into nested ul/ol HTML."""
    if not lines:
        return ''
    base_indent = len(lines[0]) - len(lines[0].lstrip())
    tag = 'ol' if lines[0].lstrip().startswith('# ') else 'ul'
    html = [f'<{tag}>']
    i = 0
    while i < len(lines):
        indent = len(lines[i]) - len(lines[i].lstrip())
        if indent < base_indent:
            break
        if indent == base_indent:
            item_text = _process_inline(lines[i].lstrip()[2:].strip(), dead_links)
            j = i + 1
            children: list[str] = []
            while j < len(lines) and (len(lines[j]) - len(lines[j].lstrip())) > base_indent:
                children.append(lines[j])
                j += 1
            child_html = _render_list_lines(children, dead_links) if children else ''
            html.append(f'<li>{item_text}{child_html}</li>')
            i = j
        else:
            i += 1
    html.append(f'</{tag}>')
    return ''.join(html)


def _process_wikitext(text: str, dead_links: set | None = None) -> str:
    """Escape text, convert [[wikilinks]], paragraphs, lists, and line breaks to HTML."""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    html_parts: list[str] = []
    for block in re.split(r'\n{2,}', text):
        block = block.strip()
        if not block:
            continue
        lines = [l for l in block.split('\n') if l.strip()]
        if lines and all(l.lstrip().startswith(('* ', '# ')) for l in lines):
            html_parts.append(_render_list_lines(lines, dead_links))
        else:
            inner = _process_inline('\n'.join(lines), dead_links)
            inner = inner.replace('\n', '<br>')
            html_parts.append(f'<p>{inner}</p>')
    return ''.join(html_parts)


def _wikilinks_filter(text: str, dead_links: set | None = None) -> Markup:
    """Convert [[Title]] wikilinks to anchors; pass <<<TABLE>>>...<<<ENDTABLE>>> through as raw HTML.
    HTML-escapes all user text. Double newlines → paragraph breaks; single → <br>.
    If dead_links is provided, links whose target is in that set get class="ref-link-dead"."""
    if not text:
        return Markup("")
    result: list[str] = []
    last_end = 0
    for m in _TABLE_MARKER_RE.finditer(text):
        segment = text[last_end:m.start()]
        if segment.strip():
            result.append(_process_wikitext(segment, dead_links))
        result.append(_resolve_wikilinks_in_html(m.group(1), dead_links))  # table HTML with wikilinks resolved
        last_end = m.end()
    remaining = text[last_end:]
    if remaining.strip():
        result.append(_process_wikitext(remaining, dead_links))
    return Markup("".join(result))


templates.env.filters["wikilinks"] = _wikilinks_filter


def _scrape_delete_chunk_local(chunk_id: int) -> bool:
    if not _SCRAPE_DB_PATH.exists():
        return False
    conn = sqlite3.connect(str(_SCRAPE_DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, page_title, page_url, content FROM scrape_chunks WHERE id = ?",
            (chunk_id,),
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            "INSERT INTO scrape_chunks_fts(scrape_chunks_fts, rowid, page_title, page_url, content) VALUES ('delete', ?, ?, ?, ?)",
            (row["id"], row["page_title"] or "", row["page_url"] or "", row["content"] or ""),
        )
        conn.execute("DELETE FROM scrape_chunks WHERE id = ?", (chunk_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def _scrape_capture_manifest_path(capture_id: str) -> Path | None:
    root = _scrape_data_root()
    for manifest_path in root.rglob("manifest.json"):
        try:
            data = _json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(data.get("id") or "") == capture_id:
            return manifest_path
    return None


def _scrape_read_capture_manifest(capture_id: str) -> dict | None:
    manifest_path = _scrape_capture_manifest_path(capture_id)
    if manifest_path is None or not manifest_path.exists():
        return None
    try:
        return _json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _scrape_write_capture_manifest(capture_id: str, manifest: dict) -> bool:
    manifest_path = _scrape_capture_manifest_path(capture_id)
    if manifest_path is None:
        return False
    manifest_path.write_text(_json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return True


def _scrape_delete_capture_content_local(capture_id: str) -> bool:
    manifest = _scrape_read_capture_manifest(capture_id)
    if not manifest:
        return False
    site_dir = Path(str(manifest.get("site_dir") or "")).resolve()
    if site_dir.exists() and site_dir.is_dir():
        shutil.rmtree(site_dir, ignore_errors=True)
    manifest["content_deleted"]    = True
    manifest["content_deleted_at"] = datetime.utcnow().isoformat() + "Z"
    return _scrape_write_capture_manifest(capture_id, manifest)


def _scrape_delete_capture_chunks_local(capture_id: str) -> bool:
    manifest = _scrape_read_capture_manifest(capture_id)
    if not manifest:
        return False
    if _SCRAPE_DB_PATH.exists():
        conn = sqlite3.connect(str(_SCRAPE_DB_PATH))
        try:
            conn.execute("DELETE FROM scrape_chunks WHERE capture_id = ?", (capture_id,))
            try:
                conn.execute("INSERT INTO scrape_chunks_fts(scrape_chunks_fts) VALUES ('rebuild')")
            except Exception:
                pass
            conn.commit()
        finally:
            conn.close()
    manifest["indexed_chunks"]    = 0
    manifest["chunks_deleted"]    = True
    manifest["chunks_deleted_at"] = datetime.utcnow().isoformat() + "Z"
    return _scrape_write_capture_manifest(capture_id, manifest)


def _scrape_delete_capture_record_local(capture_id: str) -> bool:
    manifest = _scrape_read_capture_manifest(capture_id)
    if not manifest:
        return False
    if _SCRAPE_DB_PATH.exists():
        conn = sqlite3.connect(str(_SCRAPE_DB_PATH))
        try:
            conn.execute("DELETE FROM scrape_chunks WHERE capture_id = ?", (capture_id,))
            try:
                conn.execute("INSERT INTO scrape_chunks_fts(scrape_chunks_fts) VALUES ('rebuild')")
            except Exception:
                pass
            conn.commit()
        finally:
            conn.close()
    capture_dir = Path(str(manifest.get("capture_dir") or "")).resolve()
    if capture_dir.exists() and capture_dir.is_dir():
        shutil.rmtree(capture_dir, ignore_errors=True)
    return True


def _scrape_capture_fully_deleted(manifest: dict | None) -> bool:
    if not manifest:
        return False
    content_deleted = bool(manifest.get("content_deleted"))
    chunks_deleted  = bool(manifest.get("chunks_deleted")) or int(manifest.get("indexed_chunks", 0) or 0) == 0
    return content_deleted and chunks_deleted


def _annotate_scrape_capture_chunks(rows: list[dict]) -> list[dict]:
    for row in rows:
        title   = str(row.get("page_title") or row.get("page_url") or "").strip()
        preview = str(row.get("preview") or "").strip()
        row["display_title"] = title or "(untitled page)"
        row["display_preview"] = preview
    return rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_wiki_links(body: str) -> list[str]:
    """Extract unique [[Title]] link targets from body text."""
    seen: set[str] = set()
    result: list[str] = []
    for m in re.finditer(r'\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]', body or ""):
        t = m.group(1).strip()
        if t and t not in seen:
            seen.add(t)
            result.append(t)
    return result


def _parse_wiki_sections(body: str) -> list[dict] | None:
    """Extract sections from == Heading == markers in wikitext body."""
    sections: list[dict] = []
    current_heading: str | None = None
    current_parts: list[str] = []
    for line in (body or "").split("\n"):
        hm = re.match(r'^==+\s*(.+?)\s*==+\s*$', line)
        if hm:
            if current_heading is not None:
                sections.append({"title": current_heading, "content": "\n".join(current_parts).strip()})
            current_heading = hm.group(1)
            current_parts = []
        else:
            current_parts.append(line)
    if current_heading is not None:
        sections.append({"title": current_heading, "content": "\n".join(current_parts).strip()})
    return sections or None


def _extract_summary(body: str) -> str | None:
    """Return first non-heading, non-empty line from body as summary."""
    for line in (body or "").split("\n"):
        line = line.strip()
        if line and not re.match(r'^==', line):
            return re.sub(r'\[\[(?:[^\]|]+\|)?([^\]]+)\]\]', r'\1', line)
    return None


def _sections_to_edit_body(article: dict) -> str:
    """Reconstruct wiki-formatted body (== Heading == markers) from stored sections.
    Used so editing a Kiwix-imported article round-trips correctly through save."""
    body = (article.get("body") or "").strip()
    sections = article.get("sections") or []
    if not sections:
        return body
    # If body already contains == markers, it's already wiki-formatted
    if re.search(r'^==', body, re.MULTILINE):
        return body
    # Reconstruct from sections
    parts: list[str] = []
    for s in sections:
        parts.append(f"== {s['title']} ==")
        content = (s.get("content") or "").strip()
        if content:
            parts.append(content)
    return "\n\n".join(parts)


def _parse_year(value: Optional[str]) -> Optional[int]:
    """Parse a year string from a browser form field; returns None if blank or non-numeric."""
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_article_form(
    body: Optional[str],
    summary: Optional[str],
    redirect_to: Optional[str],
    facts_raw: Optional[str] = None,
) -> dict:
    """Parse and normalise the shared form fields for new-article and edit-article POST handlers."""
    body = body.replace("\r\n", "\n").replace("\r", "\n").strip() if body else None
    summary = summary.strip() if summary else None
    links = _parse_wiki_links(body or "")
    sections = _parse_wiki_sections(body or "")
    facts: list[list[str]] = []
    for line in (facts_raw or "").splitlines():
        line = line.strip()
        if ":" in line:
            label, _, value = line.partition(":")
            label = label.strip()
            value = value.strip()
            if label and value:
                facts.append([label, value])
    return {
        "body":        body,
        "summary":     summary or _extract_summary(body or ""),
        "links":       links,
        "sections":    sections,
        "facts":       facts,
        "redirect_to": redirect_to.strip() if redirect_to and redirect_to.strip() else None,
    }


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
    query: str
    domains: list[str] = Field(default_factory=list)
    since: Optional[str] = None
    until: Optional[str] = None
    limit: int = Field(default=20, ge=1, le=200)


class _FullTextRequest(BaseModel):
    refid: str


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


def _map_feed_entry(e: dict) -> dict:
    domain = e.get("domain", "")
    eid    = e.get("id", "")
    body   = e.get("page_text") or e.get("content") or e.get("body") or e.get("summary") or ""
    return {
        "type":         "feed_entry",
        "artifact_ref": _build_artifact_ref("feed_entry", domain=domain, id=eid),
        "id":           eid,
        "title":        e.get("headline") or e.get("title", ""),
        "source":       e.get("feed_name") or e.get("source_name") or domain,
        "published_at": e.get("published") or e.get("published_at") or e.get("ingested_at"),
        "snippet":      body[:300].strip(),
        "url":          f"/ui/feeds/{domain}/{eid}",
    }


def _map_ref_article(a: dict) -> dict:
    title = a.get("title", "")
    return {
        "type":       "reference_article",
        "artifact_ref": _build_artifact_ref("reference_article", title=title),
        "title":      title,
        "summary":    a.get("summary", ""),
        "snippet":    a.get("snippet") or (a.get("summary") or "")[:300],
        "word_count": a.get("word_count"),
        "url":        f"/ui/reference/{quote(title, safe='')}",
    }


def _map_lib_book(b: dict) -> dict:
    route_id = b.get("route_id") or b.get("id")
    return {
        "type":     "library_book",
        "artifact_ref": _build_artifact_ref("library_book", book_id=route_id),
        "id":       route_id,
        "local_id": b.get("id"),
        "catalog":  b.get("catalog"),
        "route_id": route_id,
        "title":    b.get("title", ""),
        "author":   b.get("author", ""),
        "snippet":  b.get("snippet") or (b.get("notes") or "")[:300],
        "url":      f"/ui/library/{route_id}",
    }


def _map_rag_chunk(c: dict) -> dict:
    db_id = c.get("db", "default")
    return {
        "type":    "rag_chunk",
        "artifact_ref": _build_artifact_ref("rag_chunk", id=c.get("id")),
        "id":      c.get("id"),
        "title":   c.get("title", ""),
        "source":  c.get("source", ""),
        "tags":    c.get("tags", ""),
        "snippet": c.get("snippet") or "",
        "url":     f"/ui/rag/{c.get('id', '')}?db={db_id}",
    }


def _map_scrape_chunk(c: dict) -> dict:
    capture_id = c.get("capture_id", "")
    page_path  = c.get("page_path", "")
    return {
        "type":         "scrape_chunk",
        "artifact_ref": _build_artifact_ref("scrape_chunk", id=c.get("id")),
        "id":           c.get("id"),
        "capture_id":   capture_id,
        "title":        c.get("page_title", "") or c.get("page_url", ""),
        "source":       c.get("page_url", ""),
        "captured_at":  c.get("captured_at"),
        "snippet":      c.get("snippet") or "",
        "url":          f"/ui/scrape/files/{capture_id}/{page_path}" if capture_id and page_path else "",
    }


def _flatten_search_results(results_by_domain: dict) -> list[dict]:
    merged: list[dict] = []
    row_index = 0
    while True:
        added = False
        for domain in ("feeds", "reference", "library", "rag", "scrape", "graph"):
            items = results_by_domain.get(domain)
            if isinstance(items, list) and row_index < len(items):
                merged.append(items[row_index])
                added = True
        if not added:
            break
        row_index += 1
    return merged


@app.post("/api/search")
async def api_search(req: _SearchRequest):
    search_domains = [d.lower() for d in req.domains] if req.domains else ["feeds", "reference", "library", "rag", "scrape"]
    limit = req.limit

    async def _feeds():
        params: dict = {"q": req.query, "limit": limit, "full": "true"}
        if req.since: params["since"] = req.since
        if req.until: params["until"] = req.until
        r = await _feed_client.get("/api/search", params=params, timeout=10.0)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}"}
        return [_map_feed_entry(e) for e in (r.json() or [])[:limit]]

    async def _reference():
        params: dict = {"q": req.query, "limit": limit}
        r = await _ref_client.get("/search", params=params, timeout=10.0)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}"}
        return [_map_ref_article(a) for a in (r.json() or [])[:limit]]

    async def _library():
        params: dict = {"q": req.query, "limit": limit}
        r = await _lib_client.get("/search", params=params, timeout=10.0)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}"}
        return [_map_lib_book(b) for b in (r.json() or [])[:limit]]

    async def _rag():
        params: dict = {"q": req.query, "limit": limit}
        r = await _rag_client.get("/search/all", params=params, timeout=10.0)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}"}
        return [_map_rag_chunk(c) for c in (r.json() or [])[:limit]]

    async def _scrape():
        params: dict = {"q": req.query, "limit": limit}
        r = await _scrape_client.get("/search", params=params, timeout=10.0)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}"}
        return [_map_scrape_chunk(c) for c in (r.json() or [])[:limit]]

    async def _graph():
        params: dict = {"q": req.query, "depth": 1, "min_score": 0}
        r = await _graph_client.get("/api/expand-by-term", params=params, timeout=10.0)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}"}
        data = r.json()
        if not data.get("matched"):
            return []
        # Include unreviewed (0) and accepted (1); exclude rejected (2) and flagged (3)
        active = [e for e in (data.get("edges") or []) if e.get("state", 0) in (0, 1)]
        edges = sorted(active, key=lambda e: e.get("score", 0), reverse=True)[:50]
        return [
            {
                "start":      e.get("start_name", ""),
                "connection": e.get("connection_name", ""),
                "end":        e.get("end_name", ""),
                "score":      e.get("score", 0),
            }
            for e in edges
        ]

    tasks: list[tuple[str, Any]] = []
    if "feeds"     in search_domains: tasks.append(("feeds",     _feeds()))
    if "reference" in search_domains: tasks.append(("reference", _reference()))
    if "library"   in search_domains: tasks.append(("library",   _library()))
    if "rag"       in search_domains: tasks.append(("rag",       _rag()))
    if "scrape"    in search_domains: tasks.append(("scrape",    _scrape()))
    if "graph"     in search_domains: tasks.append(("graph",     _graph()))

    gathered = await asyncio.gather(*(coro for _, coro in tasks), return_exceptions=True)
    results_by_domain = {
        key: ({"error": str(val)} if isinstance(val, Exception) else val)
        for (key, _), val in zip(tasks, gathered)
    }
    return {
        "query":             req.query,
        "domains_searched":  [key for key, _ in tasks],
        "results":           _flatten_search_results(results_by_domain),
        "results_by_domain": results_by_domain,
    }


@app.post("/api/full-text")
async def api_full_text(req: _FullTextRequest):
    return await koredata_get_full_text(req.refid)


def _add_next_mins(feeds: list) -> None:
    """Compute _next_mins and _next_secs for each feed dict in-place."""
    now = datetime.utcnow()
    for f in feeds:
        last = f.get("last_fetched_at")
        if last:
            try:
                nxt = datetime.fromisoformat(last) + timedelta(minutes=int(f.get("update_rate", 60)))
                secs = int((nxt - now).total_seconds())
                f["_next_secs"] = max(0, secs)
                f["_next_mins"] = secs // 60
            except Exception:
                f["_next_mins"] = None
                f["_next_secs"] = None
        else:
            f["_next_mins"] = None
            f["_next_secs"] = None


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
        domains: Which services to search — any of "feeds", "reference", "library", "rag", "scrape".
                 Omit or pass null to search all five.
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


@_mcp.tool(description=(
    "Read every chunk of a library book, extract factual entity-relationship connections "
    "using linguistic patterns, and submit them all to KoreGraph automatically. "
    "Returns a summary: chunks_processed, connections_extracted, connections_submitted, errors. "
    "Use this instead of manually looping through chunks when the goal is to populate KoreGraph "
    "from a book. Call koredata_find_library_book first to obtain the book_id."
))
async def koredata_build_graph_from_book(book_id: str) -> dict:
    """Extract and submit KoreGraph connections from every chunk of a library book."""
    import re

    if _lib_client is None or _graph_client is None:
        return {"error": "KoreDataGateway is still starting up — retry in a moment"}

    _STOP = {
        "man", "men", "time", "times", "way", "ways", "fact", "thing", "things",
        "world", "work", "works", "first", "last", "great", "part", "parts",
        "same", "such", "this", "that", "these", "those", "which", "what",
        "one", "two", "three", "four", "five", "many", "more", "most",
        "place", "places", "name", "names", "view", "views", "form", "forms",
        "new", "old", "long", "large", "small", "early", "late", "good",
        "life", "hand", "head", "body", "line", "point", "case", "kind",
    }

    def _extract(sent: str) -> list[dict]:
        s = sent.strip()
        if len(s) < 20 or s.startswith("#"):
            return []
        out: list[dict] = []
        # Pattern 1: "Name verb [the/a/…] object"
        for m in re.finditer(
            r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})"
            r"\s+(discovered|invented|proposed|developed|founded|established|"
            r"proved|disproved|wrote|described|calculated|measured|introduced|"
            r"studied|applied|created|derived|formulated|demonstrated|showed)"
            r"\s+(?:(?:the|a|an|his|her|its|that|how)\s+)?"
            r"([A-Za-z][a-z]{2,}(?:\s+(?:of\s+)?[a-z]{2,}){0,3})",
            s,
        ):
            subj, verb, obj = m.group(1), m.group(2), m.group(3).strip()
            if obj.split()[0].lower() not in _STOP and len(obj) >= 4:
                out.append({"start": subj, "connection": verb, "end": obj})
        # Pattern 2: "Name was [a/an] profession/nationality"
        for m in re.finditer(
            r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})"
            r"\s+was\s+(?:a|an)\s+"
            r"(Greek|Roman|Egyptian|Arab|Persian|Babylonian|Chinese|Indian|"
            r"mathematician|philosopher|astronomer|physicist|chemist|biologist|"
            r"physician|geographer|geometer|naturalist|historian|engineer|"
            r"theologian|logician|scholar|scientist)",
            s,
        ):
            out.append({"start": m.group(1), "connection": "is_a", "end": m.group(2)})
        # Pattern 3: "Name lived/worked/taught in/at Place"
        for m in re.finditer(
            r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})"
            r"\s+(?:lived|worked|resided|taught|studied)\s+(?:in|at)\s+"
            r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,1})",
            s,
        ):
            out.append({"start": m.group(1), "connection": "lived_in", "end": m.group(2)})
        # Pattern 4: "Name influenced/inspired/succeeded/preceded Name"
        for m in re.finditer(
            r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})"
            r"\s+(influenced|inspired|succeeded|preceded)\s+"
            r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
            s,
        ):
            out.append({"start": m.group(1), "connection": m.group(2), "end": m.group(3)})
        return out

    # ── fetch all chunks ──────────────────────────────────────────────────
    all_conns: list[dict] = []
    offset = 0
    chunks_processed = 0

    while True:
        r = await _lib_client.get(
            f"/books/{book_id}/chunk",
            params={"offset": offset, "length": 16000},
            timeout=60.0,
        )
        if r.status_code == 404:
            return {"error": f"Book not found: {book_id}"}
        if r.status_code != 200:
            return {"error": f"KoreLibrary returned HTTP {r.status_code}"}
        data = r.json()
        text = data.get("chunk", "")
        for sent in re.split(r"(?<=[.!?])\s+", text):
            all_conns.extend(_extract(sent))
        chunks_processed += 1
        if not data.get("has_more"):
            break
        offset = data["next_offset"]

    # ── deduplicate ───────────────────────────────────────────────────────
    seen: set[tuple] = set()
    unique: list[dict] = []
    for c in all_conns:
        key = (c["start"].lower(), c["connection"], c["end"].lower())
        if key not in seen:
            seen.add(key)
            unique.append(c)

    # ── submit to KoreGraph ───────────────────────────────────────────────
    submitted = 0
    errors = 0
    batch_size = 100

    for i in range(0, len(unique), batch_size):
        batch = unique[i : i + batch_size]
        gr = await _graph_client.post(
            "/api/connections/by-name/batch",
            json=batch,
            timeout=60.0,
        )
        if gr.is_success:
            result = gr.json()
            submitted += result.get("accepted", len(batch))
            errors    += len(result.get("errors", []))
        else:
            # Fallback: individual calls
            for c in batch:
                gr2 = await _graph_client.post(
                    "/api/connections/by-name",
                    json=c,
                    timeout=10.0,
                )
                if gr2.is_success:
                    submitted += 1
                else:
                    errors += 1

    return {
        "book_id":                book_id,
        "chunks_processed":       chunks_processed,
        "connections_extracted":  len(all_conns),
        "connections_unique":     len(unique),
        "connections_submitted":  submitted,
        "errors":                 errors,
    }


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
    urls = os.environ.get("KORE_SUITE_URLS", "{}")
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


@app.get("/ui/scrape", response_class=HTMLResponse)
async def scrape_index(request: Request, q: Optional[str] = None, limit: int = 200):
    captures_r, status_r, chunks_r = await asyncio.gather(
        _scrape_client.get("/captures"),
        _scrape_client.get("/status"),
        _scrape_client.get("/chunks", params={"limit": 12}),
    )
    captures = captures_r.json().get("captures", []) if captures_r.status_code == 200 else []
    status   = status_r.json()                     if status_r.status_code == 200 else {}
    chunks   = chunks_r.json()                     if chunks_r.status_code == 200 else []
    searched = bool((q or "").strip())
    results: list[dict] = []
    next_url_param = ""
    if searched:
        next_url_param = quote(f"/ui/scrape?q={q or ''}&limit={limit}", safe="")
    if searched:
        search_r = await _scrape_client.get("/search", params={"q": q, "limit": limit})
        results  = search_r.json() if search_r.status_code == 200 else []
    return templates.TemplateResponse(
        request,
        "scrape_index.html",
        {
            "captures": captures,
            "limit":    limit,
            "next_url_param": next_url_param,
            "q":        q or "",
            "results":  results,
            "searched": searched,
            "status":   status,
            "chunks":   chunks,
        },
    )


@app.post("/ui/scrape/start")
async def scrape_start(url: str = Form(...), depth: int = Form(0), download_non_html: bool = Form(False)):
    r = await _scrape_client.post("/captures", json={"url": url, "depth": depth, "download_non_html": download_non_html})
    if r.status_code not in (200, 201, 202):
        try:
            detail = r.json().get("detail", f"Capture failed ({r.status_code})")
        except Exception:
            detail = f"Capture failed ({r.status_code})"
        raise HTTPException(status_code=r.status_code, detail=detail)
    capture_id = r.json()["id"]
    return RedirectResponse(url=f"/ui/scrape/{capture_id}", status_code=303)


@app.get("/ui/scrape/{capture_id}", response_class=HTMLResponse)
async def scrape_capture(request: Request, capture_id: str):
    capture_r, chunks_r = await asyncio.gather(
        _scrape_client.get(f"/captures/{capture_id}"),
        _scrape_client.get("/chunks", params={"capture_id": capture_id, "limit": 100}),
    )
    if capture_r.status_code == 404:
        raise HTTPException(status_code=404, detail="Capture not found")
    if capture_r.status_code != 200:
        raise HTTPException(status_code=capture_r.status_code, detail="Capture lookup failed")
    capture = capture_r.json()
    chunks  = _annotate_scrape_capture_chunks(chunks_r.json()) if chunks_r.status_code == 200 else []
    return templates.TemplateResponse(
        request,
        "scrape_capture.html",
        {
            "capture": capture,
            "chunks":  chunks,
        },
    )


@app.get("/ui/scrape/{capture_id}/json")
async def scrape_capture_json(capture_id: str):
    r = await _scrape_client.get(f"/captures/{capture_id}")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Capture not found")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail="Capture lookup failed")
    return JSONResponse(r.json())


@app.get("/ui/scrape/search", response_class=HTMLResponse)
async def scrape_search(
    request: Request,
    q: Optional[str] = None,
    limit: int = 200,
):
    params: dict[str, Any] = {}
    if q:
        params["q"] = q
    if limit:
        params["limit"] = limit
    target = "/ui/scrape"
    if params:
        target = f"{target}?{urlencode(params)}"
    return RedirectResponse(url=target, status_code=302)


@app.get("/ui/scrape/chunks/{chunk_id}", response_class=HTMLResponse)
async def scrape_chunk(request: Request, chunk_id: int, next: Optional[str] = None):
    r = await _scrape_client.get(f"/chunks/{chunk_id}")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Chunk not found")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail="Chunk lookup failed")
    chunk = r.json()
    next_url = next or f"/ui/scrape/{chunk.get('capture_id', '')}"
    return templates.TemplateResponse(
        request,
        "scrape_chunk.html",
        {
            "chunk":    chunk,
            "next_url": next_url,
        },
    )


@app.post("/ui/scrape/chunks/{chunk_id}/delete")
async def scrape_chunk_delete(chunk_id: int, next: str = Form("/ui/scrape")):
    r = await _scrape_client.post(f"/chunks/{chunk_id}/delete")
    if r.status_code == 405:
        r = await _scrape_client.delete(f"/chunks/{chunk_id}")
    if r.status_code not in (200, 204):
        deleted = _scrape_delete_chunk_local(chunk_id)
        if not deleted:
            if r.status_code == 404:
                raise HTTPException(status_code=404, detail="Chunk not found")
            raise HTTPException(status_code=r.status_code, detail="Chunk delete failed")
    target = next.strip() or "/ui/scrape"
    return RedirectResponse(url=target, status_code=303)


@app.get("/ui/scrape/chunks/{chunk_id}/delete")
async def scrape_chunk_delete_get(chunk_id: int, next: str = "/ui/scrape"):
    return await scrape_chunk_delete(chunk_id=chunk_id, next=next)


@app.get("/ui/scrape/{capture_id}/delete-content")
async def scrape_capture_delete_content(capture_id: str, next: Optional[str] = None):
    if not _scrape_delete_capture_content_local(capture_id):
        raise HTTPException(status_code=404, detail="Capture not found")
    manifest = _scrape_read_capture_manifest(capture_id)
    if _scrape_capture_fully_deleted(manifest):
        _scrape_delete_capture_record_local(capture_id)
        return RedirectResponse(url="/ui/scrape", status_code=303)
    return RedirectResponse(url=(next or f"/ui/scrape/{capture_id}"), status_code=303)


@app.get("/ui/scrape/{capture_id}/delete-chunks")
async def scrape_capture_delete_chunks(capture_id: str, next: Optional[str] = None):
    if not _scrape_delete_capture_chunks_local(capture_id):
        raise HTTPException(status_code=404, detail="Capture not found")
    manifest = _scrape_read_capture_manifest(capture_id)
    if _scrape_capture_fully_deleted(manifest):
        _scrape_delete_capture_record_local(capture_id)
        return RedirectResponse(url="/ui/scrape", status_code=303)
    return RedirectResponse(url=(next or f"/ui/scrape/{capture_id}"), status_code=303)


@app.get("/ui/scrape/files/{capture_id}/{file_path:path}", include_in_schema=False)
async def scrape_capture_file(capture_id: str, file_path: str):
    r = await _scrape_client.get(f"/captures/{capture_id}/files/{file_path}")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="File not found")
    content_type = r.headers.get("content-type")
    return Response(content=r.content, status_code=r.status_code, media_type=content_type)


@app.api_route("/graph/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"], include_in_schema=False)
async def proxy_graph(request: Request, path: str):
    """Transparent reverse proxy forwarding /graph/{path} to KoreGraph."""
    if _graph_client is None:
        raise HTTPException(status_code=503, detail="KoreGraph not available")
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
    headers["x-forwarded-prefix"] = "/graph"
    try:
        resp = await _graph_client.request(
            method=request.method,
            url=f"/{path}",
            headers=headers,
            content=body,
            params=dict(request.query_params),
            follow_redirects=False,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"KoreGraph unreachable: {exc}") from exc
    resp_headers = {k: v for k, v in resp.headers.items() if k.lower() != "transfer-encoding"}
    # Rewrite Location headers so redirects stay within the proxy path
    if "location" in resp_headers:
        loc = resp_headers["location"]
        if loc.startswith("/") and not loc.startswith("/graph"):
            resp_headers["location"] = f"/graph{loc}"
    return Response(content=resp.content, status_code=resp.status_code, headers=resp_headers)


@app.get("/ui/feeds", response_class=HTMLResponse)
async def web_index(request: Request):
    domains_r, feeds_r = await asyncio.gather(
        _feed_client.get("/api/domains"),
        _feed_client.get("/api/feeds"),
        return_exceptions=True,
    )
    domains   = domains_r.json() if not isinstance(domains_r, Exception) and domains_r.status_code == 200 else []
    all_feeds = feeds_r.json()   if not isinstance(feeds_r,   Exception) and feeds_r.status_code == 200   else []
    _add_next_mins(all_feeds)
    all_feeds.sort(key=lambda f: (
        0 if f["_next_mins"] is None else (1 if f["_next_mins"] <= 0 else 2),
        f["_next_mins"] if f["_next_mins"] is not None else 0,
    ))
    return templates.TemplateResponse(
        request, "feed_index.html",
        {"domains": domains, "all_feeds": all_feeds},
    )


@app.get("/ui/feeds/search", response_class=HTMLResponse)
async def web_search(
    request: Request,
    q: str = "",
    domain: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 50,
):
    results = []
    if q:
        params: dict = {"q": q, "limit": limit}
        if domain: params["domain"] = domain
        if since:  params["since"]  = since
        if until:  params["until"]  = until
        try:
            r = await _feed_client.get("/api/search", params=params)
            results = r.json() if r.status_code == 200 else []
        except Exception:
            results = []
    return templates.TemplateResponse(
        request, "feed_search.html",
        {"q": q, "domain": domain, "since": since or "", "until": until or "",
         "limit": limit, "results": results},
    )


@app.get("/ui/feeds/{domain}", response_class=HTMLResponse)
async def web_domain(request: Request, domain: str, limit: int = 50, offset: int = 0):
    entries_r, all_domains_r, feeds_all_r, age_r, counts_r = await asyncio.gather(
        _feed_client.get(f"/api/domains/{domain}/entries", params={"limit": limit, "offset": offset}),
        _feed_client.get("/api/domains"),
        _feed_client.get("/api/feeds"),
        _feed_client.get(f"/api/domains/{domain}/age-settings"),
        _feed_client.get(f"/api/domains/{domain}/feed-counts"),
        return_exceptions=True,
    )
    entries      = entries_r.json()     if not isinstance(entries_r,    Exception) and entries_r.status_code == 200     else []
    all_domains  = all_domains_r.json() if not isinstance(all_domains_r, Exception) and all_domains_r.status_code == 200 else []
    all_feeds    = feeds_all_r.json()   if not isinstance(feeds_all_r,  Exception) and feeds_all_r.status_code == 200   else []
    age_settings = age_r.json()         if not isinstance(age_r,         Exception) and age_r.status_code == 200         else {"mode": "none"}
    feed_counts  = counts_r.json()      if not isinstance(counts_r,      Exception) and counts_r.status_code == 200      else {}

    domain_info = next((d for d in all_domains if d["domain"] == domain), {})
    total       = domain_info.get("entry_count", len(entries))
    feeds       = [f for f in all_feeds if f.get("domain") == domain]
    _add_next_mins(feeds)
    feed_refresh_mins = {f["id"]: f.get("_next_mins") for f in feeds}
    feed_refresh_secs = {f["id"]: f.get("_next_secs") for f in feeds}

    return templates.TemplateResponse(
        request, "feed_domain.html",
        {
            "domain":            domain,
            "entries":           entries,
            "total":             total,
            "limit":             limit,
            "offset":            offset,
            "feeds":             feeds,
            "age_settings":      age_settings,
            "feed_counts":       feed_counts,
            "feed_refresh_mins": feed_refresh_mins,
            "feed_refresh_secs": feed_refresh_secs,
        },
    )


@app.get("/ui/feeds/{domain}/{entry_id}", response_class=HTMLResponse)
async def web_entry(request: Request, domain: str, entry_id: int):
    try:
        r = await _feed_client.get(f"/api/domains/{domain}/entries/{entry_id}")
    except Exception:
        raise HTTPException(status_code=503, detail="Feed service unavailable")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Entry not found")
    entry = r.json()
    if entry.get("metadata") and isinstance(entry["metadata"], str):
        try:
            entry["metadata"] = _json.loads(entry["metadata"])
        except Exception:
            pass
    return templates.TemplateResponse(
        request, "feed_entry.html",
        {"domain": domain, "entry": entry},
    )


# ===========================================================================
# KoreFeed — Web UI (POST / mutations)
# ===========================================================================

@app.post("/ui/feeds/domains/create")
async def web_create_domain(domain: str = Form(...)):
    await _feed_client.post("/api/domains", params={"domain": domain})
    return RedirectResponse("/ui/feeds", status_code=303)


@app.post("/ui/feeds/domains/{domain}/delete")
async def web_delete_domain(domain: str):
    await _feed_client.delete(f"/api/domains/{domain}")
    return RedirectResponse("/ui/feeds", status_code=303)


@app.post("/ui/feeds/domains/{domain}/rename")
async def web_rename_domain(domain: str, new_name: str = Form(...)):
    await _feed_client.post(f"/api/domains/{domain}/rename", params={"new_name": new_name})
    return RedirectResponse("/ui/feeds", status_code=303)


@app.post("/ui/feeds/{domain}/feeds/add")
async def web_add_feed(
    domain: str,
    name: str = Form(...),
    url: str = Form(...),
    update_rate: int = Form(60),
    feed_type: str = Form("rss"),
):
    await _feed_client.post("/api/feeds", json={
        "domain": domain, "name": name, "url": url,
        "update_rate": update_rate, "feed_type": feed_type,
    })
    return RedirectResponse(f"/ui/feeds/{domain}", status_code=303)


@app.post("/ui/feeds/{domain}/feeds/{feed_id}/delete")
async def web_delete_feed(domain: str, feed_id: str):
    await _feed_client.delete(f"/api/feeds/{feed_id}")
    return RedirectResponse(f"/ui/feeds/{domain}", status_code=303)


@app.post("/ui/feeds/{domain}/feeds/{feed_id}/update")
async def web_update_feed(
    domain: str,
    feed_id: str,
    name: str = Form(...),
    url: str = Form(...),
    update_rate: int = Form(60),
    feed_type: str = Form("rss"),
):
    await _feed_client.put(f"/api/feeds/{feed_id}", json={
        "name": name, "url": url,
        "update_rate": update_rate, "feed_type": feed_type,
    })
    return RedirectResponse(f"/ui/feeds/{domain}", status_code=303)


@app.post("/ui/feeds/{domain}/feeds/{feed_id}/refresh")
async def web_refresh_feed(domain: str, feed_id: str):
    await _feed_client.post(f"/api/feeds/{feed_id}/trigger")
    return JSONResponse({"triggered": feed_id})


@app.post("/ui/feeds/{domain}/entries/{entry_id}/delete")
async def web_delete_entry(request: Request, domain: str, entry_id: int):
    await _feed_client.delete(f"/api/domains/{domain}/entries/{entry_id}")
    return JSONResponse({"deleted": entry_id})


@app.post("/ui/feeds/{domain}/entries/delete-older-than")
async def web_delete_older_than(domain: str, days: float = Form(...)):
    await _feed_client.delete(
        f"/api/domains/{domain}/entries",
        params={"older_than_days": days},
    )
    return RedirectResponse(f"/ui/feeds/{domain}", status_code=303)


@app.post("/ui/feeds/{domain}/entries/delete-by-feed")
async def web_delete_by_feed(domain: str, feed_name: str = Form(...)):
    await _feed_client.delete(
        f"/api/domains/{domain}/entries",
        params={"feed_name": feed_name},
    )
    return RedirectResponse(f"/ui/feeds/{domain}", status_code=303)


@app.post("/ui/feeds/entries/bulk-delete")
async def web_bulk_delete_entries(request: Request, sel: list[str] = Form(default=[])):
    by_domain: dict[str, list[int]] = {}
    for item in sel:
        parts = item.split(":", 1)
        if len(parts) == 2:
            d, eid = parts
            try:
                by_domain.setdefault(d, []).append(int(eid))
            except ValueError:
                pass
    for d, ids in by_domain.items():
        await _feed_client.post(f"/api/domains/{d}/entries/bulk-delete", json=ids)
    ref = request.headers.get("referer", "/ui/feeds/search")
    return RedirectResponse(ref, status_code=303)


@app.post("/ui/feeds/{domain}/settings/age-mode")
async def web_set_age_mode(
    request: Request,
    domain: str,
    mode: str = Form(...),
    days: Optional[int] = Form(None),
    start_date: Optional[str] = Form(None),
    end_date: Optional[str] = Form(None),
):
    await _feed_client.post(
        f"/api/domains/{domain}/age-settings",
        json={"mode": mode, "days": days, "start_date": start_date, "end_date": end_date},
    )
    if "application/json" in request.headers.get("accept", ""):
        return {"ok": True}
    return RedirectResponse(f"/ui/feeds/{domain}", status_code=303)


@app.post("/ui/feeds/{domain}/entries/delete-outside-calendar")
async def web_delete_outside_calendar(
    domain: str,
    start_date: str = Form(...),
    end_date: str = Form(...),
):
    await _feed_client.post(
        f"/api/domains/{domain}/entries/purge-outside-calendar",
        params={"start_date": start_date, "end_date": end_date},
    )
    return RedirectResponse(f"/ui/feeds/{domain}", status_code=303)


# ---------------------------------------------------------------------------
# KoreFeed API proxy (called directly by browser JS)
# ---------------------------------------------------------------------------

@app.get("/api/domains")
async def api_proxy_feed_domains():
    r = await _feed_client.get("/api/domains")
    return JSONResponse(content=r.json(), status_code=r.status_code)


@app.get("/api/feeds")
async def api_proxy_feeds():
    r = await _feed_client.get("/api/feeds")
    return JSONResponse(content=r.json(), status_code=r.status_code)


@app.patch("/api/feeds/{feed_id}/rate")
async def api_proxy_feed_rate(feed_id: str, minutes: int):
    r = await _feed_client.patch(f"/api/feeds/{feed_id}/rate", params={"minutes": minutes})
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Feed not found")
    return r.json()


# ===========================================================================
# KoreLibrary — Web UI
# ===========================================================================

@app.get("/ui/library", response_class=HTMLResponse)
async def lib_index(request: Request, limit: int = 200, offset: int = 0, catalog: Optional[str] = None):
    books_r, status_r, catalogs_r = await asyncio.gather(
        _lib_client.get("/books", params={"limit": limit, "offset": offset, "catalog": catalog} if catalog else {"limit": limit, "offset": offset}),
        _lib_client.get("/status", params={"catalog": catalog} if catalog else None),
        _lib_client.get("/catalogs"),
    )
    books     = books_r.json()    if books_r.status_code == 200    else []
    status    = status_r.json()   if status_r.status_code == 200   else {}
    catalogs  = (catalogs_r.json().get("catalogs", []) if catalogs_r.status_code == 200 else [])
    return templates.TemplateResponse(
        request, "library_index.html",
        {
            "books":    books,
            "total":    status.get("total_books", len(books)),
            "limit":    limit,
            "offset":   offset,
            "mode":     "all",
            "catalog":  catalog or "",
            "catalogs": catalogs,
        },
    )


@app.get("/ui/library/incomplete", response_class=HTMLResponse)
async def lib_incomplete(request: Request, fields: Optional[str] = None, catalog: Optional[str] = None):
    params: dict = {}
    if fields:
        params["fields"] = fields
    if catalog:
        params["catalog"] = catalog
    r, catalogs_r = await asyncio.gather(
        _lib_client.get("/incomplete", params=params),
        _lib_client.get("/catalogs"),
    )
    books    = r.json()           if r.status_code == 200           else []
    catalogs = (catalogs_r.json().get("catalogs", []) if catalogs_r.status_code == 200 else [])
    return templates.TemplateResponse(
        request, "library_index.html",
        {
            "books":         books,
            "total":         len(books),
            "limit":         9999,
            "offset":        0,
            "mode":          "incomplete",
            "filter_fields": fields,
            "catalog":       catalog or "",
            "catalogs":      catalogs,
        },
    )


@app.get("/ui/library/search", response_class=HTMLResponse)
async def lib_search(
    request: Request,
    q: Optional[str] = None,
    author: Optional[str] = None,
    title: Optional[str] = None,
    year: Optional[str] = None,   # str to tolerate empty string from browser form
    language: Optional[str] = None,
    genre: Optional[str] = None,
    catalog: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    year_int = _parse_year(year)
    results = []
    searched = any([q, author, title, year_int, language, genre])
    if searched:
        params: dict = {"limit": limit, "offset": offset}
        if q:        params["q"]        = q
        if author:   params["author"]   = author
        if title:    params["title"]    = title
        if year_int: params["year"]     = year_int
        if language: params["language"] = language
        if genre:    params["genre"]    = genre
        if catalog:  params["catalog"]  = catalog
        r = await _lib_client.get("/search", params=params)
        results = r.json() if r.status_code == 200 else []
    catalogs_r = await _lib_client.get("/catalogs")
    catalogs = (catalogs_r.json().get("catalogs", []) if catalogs_r.status_code == 200 else [])
    return templates.TemplateResponse(
        request, "library_search.html",
        {
            "results":  results,
            "searched": searched,
            "q":        q or "",
            "author":   author or "",
            "title":    title or "",
            "year":     year_int or "",
            "language": language or "",
            "genre":    genre or "",
            "catalog":  catalog or "",
            "limit":    limit,
            "catalogs": catalogs,
        },
    )


@app.get("/ui/library/import", response_class=HTMLResponse)
async def lib_import(request: Request, error: Optional[str] = None):
    catalogs_r = await _lib_client.get("/catalogs")
    catalogs = (catalogs_r.json().get("catalogs", []) if catalogs_r.status_code == 200 else [])
    return templates.TemplateResponse(
        request, "library_import.html",
        {"error": error, "catalogs": catalogs},
    )


@app.post("/ui/library/import/manual", response_class=HTMLResponse)
async def lib_import_manual(
    request: Request,
    title:    str           = Form(...),
    body:     Optional[str] = Form(None),
    author:   Optional[str] = Form(None),
    year:     Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    genre:    Optional[str] = Form(None),
    notes:    Optional[str] = Form(None),
    catalog:  Optional[str] = Form(None),
):
    payload: dict = {"title": title}
    if body:      payload["body"]      = body
    if author:    payload["author"]    = author
    year_int = _parse_year(year)
    if year_int is not None: payload["year"] = year_int
    if language:  payload["language"]  = language
    if genre:     payload["genre"]     = genre
    if notes:     payload["notes"]     = notes
    if catalog:   payload["catalog"]   = catalog

    r = await _lib_client.post("/books", json=payload)
    if r.status_code in (200, 201):
        book_id = r.json().get("route_id") or r.json().get("id")
        return RedirectResponse(url=f"/ui/library/{book_id}", status_code=303)
    return templates.TemplateResponse(
        request, "library_import.html",
        {"error": r.json().get("detail", f"Error {r.status_code}")},
        status_code=400,
    )


@app.get("/ui/library/kiwix/inventory")
async def lib_kiwix_inventory(kiwix_url: Optional[str] = None):
    params = {}
    if kiwix_url:
        params["kiwix_url"] = kiwix_url
    r = await _lib_client.get("/kiwix/inventory", params=params)
    return JSONResponse(content=r.json(), status_code=r.status_code)


@app.get("/ui/library/kiwix/suggest")
async def lib_kiwix_suggest(zim: str, pattern: str = "", count: int = 100, kiwix_url: Optional[str] = None):
    params: dict = {"zim": zim, "pattern": pattern, "count": count}
    if kiwix_url:
        params["kiwix_url"] = kiwix_url
    r = await _lib_client.get("/kiwix/suggest", params=params)
    return JSONResponse(content=r.json(), status_code=r.status_code)


@app.get("/ui/library/kiwix/search")
async def lib_kiwix_search(zim: str, q: str, count: int = 100, kiwix_url: Optional[str] = None):
    params: dict = {"zim": zim, "q": q, "count": count}
    if kiwix_url:
        params["kiwix_url"] = kiwix_url
    r = await _lib_client.get("/kiwix/search", params=params)
    return JSONResponse(content=r.json(), status_code=r.status_code)


@app.get("/ui/library/kiwix/catalog")
async def lib_kiwix_catalog(zim: str, author: Optional[str] = None, kiwix_url: Optional[str] = None):
    params: dict = {"zim": zim}
    if author:
        params["author"] = author
    if kiwix_url:
        params["kiwix_url"] = kiwix_url
    r = await _lib_client.get("/kiwix/catalog", params=params)
    return JSONResponse(content=r.json(), status_code=r.status_code)


@app.post("/ui/library/import/kiwix")
async def lib_import_kiwix(request: Request):
    payload = await request.json()
    r = await _lib_client.post("/import/kiwix", json=payload)
    return JSONResponse(content=r.json(), status_code=r.status_code)


@app.post("/ui/library/import/kiwix/viewer")
async def lib_import_kiwix_viewer(request: Request):
    payload = await request.json()
    r = await _lib_client.post("/import/kiwix/viewer", json=payload)
    return JSONResponse(content=r.json(), status_code=r.status_code)


@app.post("/ui/library/import/kiwix/viewer/batch")
async def lib_import_kiwix_viewer_batch(request: Request):
    payload = await request.json()
    r = await _lib_client.post("/import/kiwix/viewer/batch", json=payload)
    return JSONResponse(content=r.json(), status_code=r.status_code)


@app.get("/ui/library/{book_id:path}/edit", response_class=HTMLResponse)
async def lib_book_edit(request: Request, book_id: str):
    r, catalogs_r = await asyncio.gather(
        _lib_client.get(f"/books/{book_id}"),
        _lib_client.get("/catalogs"),
    )
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Book not found")
    catalogs = catalogs_r.json() if catalogs_r.status_code == 200 else []
    return templates.TemplateResponse(
        request, "library_edit.html",
        {"book": r.json(), "error": None, "catalogs": catalogs},
    )


@app.post("/ui/library/{book_id:path}/edit", response_class=HTMLResponse)
async def lib_book_edit_post(
    request:   Request,
    book_id:   str,
    title:     str           = Form(...),
    body:      Optional[str] = Form(None),
    author:    Optional[str] = Form(None),
    year:      Optional[str] = Form(None),
    language:  Optional[str] = Form(None),
    genre:     Optional[str] = Form(None),
    notes:     Optional[str] = Form(None),
    source:    Optional[str] = Form(None),
):
    payload: dict = {"title": title}
    if body is not None: payload["body"] = body
    if author:    payload["author"]    = author
    year_int = _parse_year(year)
    if year_int is not None: payload["year"] = year_int
    if language:  payload["language"]  = language
    if genre:     payload["genre"]     = genre
    if notes:     payload["notes"]     = notes
    if source:    payload["source"]    = source

    r = await _lib_client.patch(f"/books/{book_id}", json=payload)
    if r.status_code == 200:
        return RedirectResponse(url=f"/ui/library/{book_id}", status_code=303)
    book_r = await _lib_client.get(f"/books/{book_id}")
    return templates.TemplateResponse(
        request, "library_edit.html",
        {"book": book_r.json(), "error": r.json().get("detail", f"Error {r.status_code}")},
        status_code=400,
    )


@app.post("/ui/library/{book_id:path}/delete")
async def lib_book_delete(book_id: str):
    r = await _lib_client.delete(f"/books/{book_id}")
    if r.status_code not in (200, 204):
        raise HTTPException(status_code=r.status_code, detail="Delete failed")
    return RedirectResponse(url="/ui/library", status_code=303)


@app.post("/ui/library/{book_id:path}/move")
async def lib_book_move(book_id: str, catalog: str = Form(...)):
    r = await _lib_client.post(f"/books/{book_id}/move", json={"catalog": catalog})
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Book not found")
    if r.status_code != 200:
        try:
            detail = r.json().get("detail", f"Move failed ({r.status_code})")
        except Exception:
            detail = f"Move failed ({r.status_code})"
        raise HTTPException(status_code=r.status_code, detail=detail)
    new_book = r.json()
    new_id = new_book.get("route_id") or new_book.get("id")
    return RedirectResponse(url=f"/ui/library/{new_id}", status_code=303)


@app.post("/ui/library/{book_id:path}/repair-anchors")
async def lib_repair_anchors(book_id: str):
    r = await _lib_client.post(f"/books/{book_id}/repair-anchors")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Book not found")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail="Repair failed")
    return RedirectResponse(url=f"/ui/library/{book_id}", status_code=303)


@app.get("/ui/library/{book_id:path}", response_class=HTMLResponse)
async def lib_book(request: Request, book_id: str):
    r, catalogs_r = await asyncio.gather(
        _lib_client.get(f"/books/{book_id}"),
        _lib_client.get("/catalogs"),
    )
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Book not found")
    catalogs = catalogs_r.json() if catalogs_r.status_code == 200 else []
    return templates.TemplateResponse(
        request, "library_book.html",
        {"book": r.json(), "catalogs": catalogs},
    )


# ===========================================================================
# KoreReference — Web UI
# ===========================================================================

@app.get("/ui/reference/import", response_class=HTMLResponse)
async def ref_import(request: Request):
    status_r = await _ref_client.get("/import/status")
    status = status_r.json() if status_r.status_code == 200 else {}
    return templates.TemplateResponse(
        request, "reference_import.html",
        {"status": status},
    )


@app.post("/ui/reference/import/crawl")
async def ref_import_crawl(request: Request):
    payload = await request.json()
    r = await _ref_client.post("/import/kiwix/crawl", json=payload)
    return JSONResponse(content=r.json(), status_code=r.status_code)


@app.get("/ui/reference/import/status")
async def ref_import_status():
    r = await _ref_client.get("/import/status")
    return JSONResponse(content=r.json(), status_code=r.status_code)


@app.post("/ui/reference/import/stop")
async def ref_import_stop():
    r = await _ref_client.post("/import/stop")
    return JSONResponse(content=r.json(), status_code=r.status_code)


@app.post("/ui/reference/import/throttle")
async def ref_import_throttle(request: Request):
    payload = await request.json()
    r = await _ref_client.post("/import/throttle", json=payload)
    return JSONResponse(content=r.json(), status_code=r.status_code)


@app.get("/ui/reference", response_class=HTMLResponse)
async def ref_index(request: Request, limit: int = 100, offset: int = 0):
    articles_r, status_r = await asyncio.gather(
        _ref_client.get("/articles", params={"limit": limit, "offset": offset}),
        _ref_client.get("/status"),
    )
    articles = articles_r.json() if articles_r.status_code == 200 else []
    status   = status_r.json()   if status_r.status_code == 200   else {}
    return templates.TemplateResponse(
        request, "reference_index.html",
        {
            "articles": articles,
            "total":    status.get("total_articles", len(articles)),
            "limit":    limit,
            "offset":   offset,
        },
    )


@app.get("/ui/reference/search", response_class=HTMLResponse)
async def ref_search(
    request: Request,
    q: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
):
    results = []
    searched = bool(q)
    if searched:
        params: dict = {"q": q, "limit": limit, "offset": offset}
        r = await _ref_client.get("/search", params=params)
        results = r.json() if r.status_code == 200 else []
    return templates.TemplateResponse(
        request, "reference_search.html",
        {
            "results":  results,
            "searched": searched,
            "q":        q or "",
            "limit":    limit,
        },
    )


@app.get("/ui/reference/new", response_class=HTMLResponse)
async def ref_article_new(request: Request):
    return templates.TemplateResponse(
        request, "reference_edit.html",
        {"article": None, "error": None},
    )


@app.post("/ui/reference/new", response_class=HTMLResponse)
async def ref_article_new_post(
    request:     Request,
    title:       str            = Form(...),
    summary:     Optional[str]  = Form(None),
    body:        Optional[str]  = Form(None),
    facts:       Optional[str]  = Form(None),
    redirect_to: Optional[str]  = Form(None),
):
    title = title.strip()
    f = _parse_article_form(body, summary, redirect_to, facts)
    payload: dict = {"title": title}
    if f["body"]:        payload["body"]        = f["body"]
    if f["summary"]:     payload["summary"]     = f["summary"]
    if f["sections"]:    payload["sections"]    = f["sections"]
    if f["facts"]:       payload["facts"]       = f["facts"]
    if f["redirect_to"]: payload["redirect_to"] = f["redirect_to"]
    if f["links"]:       payload["link_titles"] = f["links"]
    r = await _ref_client.post("/articles", json=payload)
    if r.status_code in (200, 201):
        stored_title = ((r.json() or {}).get("title") or title)
        return RedirectResponse(url=f"/ui/reference/{quote(stored_title, safe='')}", status_code=303)
    return templates.TemplateResponse(
        request, "reference_edit.html",
        {"article": None, "error": r.json().get("detail", f"Error {r.status_code}"),
         "form": {"title": title, "summary": summary or "", "body": f["body"] or "",
                  "redirect_to": redirect_to or ""}},
        status_code=400,
    )


@app.get("/ui/reference/{title}/edit", response_class=HTMLResponse)
async def ref_article_edit(request: Request, title: str):
    r = await _ref_client.get(f"/articles/{quote(title, safe='')}")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail=f"Article not found: {title!r}")
    article = r.json()
    return templates.TemplateResponse(
        request, "reference_edit.html",
        {"article": article, "edit_body": _sections_to_edit_body(article), "error": None},
    )


@app.post("/ui/reference/{title}/edit", response_class=HTMLResponse)
async def ref_article_edit_post(
    request:     Request,
    title:       str,
    summary:     Optional[str]  = Form(None),
    body:        Optional[str]  = Form(None),
    facts:       Optional[str]  = Form(None),
    redirect_to: Optional[str]  = Form(None),
):
    f = _parse_article_form(body, summary, redirect_to, facts)
    payload: dict = {"title": title}
    if f["body"] is not None: payload["body"]       = f["body"]
    if f["summary"]:          payload["summary"]    = f["summary"]
    payload["sections"]    = f["sections"] or []
    payload["facts"]       = f["facts"]
    payload["link_titles"] = f["links"]
    if f["redirect_to"]:   payload["redirect_to"] = f["redirect_to"]
    r = await _ref_client.post("/articles", json=payload)
    if r.status_code in (200, 201):
        stored_title = ((r.json() or {}).get("title") or title)
        return RedirectResponse(url=f"/ui/reference/{quote(stored_title, safe='')}", status_code=303)
    art_r = await _ref_client.get(f"/articles/{quote(title, safe='')}")
    article = art_r.json() if art_r.status_code == 200 else None
    return templates.TemplateResponse(
        request, "reference_edit.html",
        {"article": article, "edit_body": _sections_to_edit_body(article or {}),
         "error": r.json().get("detail", f"Error {r.status_code}")},
        status_code=400,
    )


@app.post("/ui/reference/delete-all")
async def ref_delete_all():
    r = await _ref_client.delete("/articles")
    if r.status_code not in (200, 204):
        raise HTTPException(status_code=r.status_code, detail="Delete failed")
    return RedirectResponse(url="/ui/reference", status_code=303)


@app.post("/ui/reference/{title}/delete")
async def ref_article_delete(title: str):
    r = await _ref_client.delete(f"/articles/{quote(title, safe='')}")
    if r.status_code not in (200, 204):
        raise HTTPException(status_code=r.status_code, detail="Delete failed")
    return RedirectResponse(url="/ui/reference", status_code=303)


@app.get("/ui/reference/{title}/links-json")
async def ref_article_links_json(title: str):
    r = await _ref_client.get(f"/articles/{quote(title, safe='')}/links")
    if r.status_code == 404:
        return JSONResponse([])
    return JSONResponse(r.json())


@app.get("/ui/reference/{title}", response_class=HTMLResponse)
async def ref_article(request: Request, title: str):
    r = await _ref_client.get(f"/articles/{quote(title, safe='')}")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail=f"Article not found: {title!r}")
    article = r.json()
    # Fetch backlinks and outbound links in parallel.
    bl_r, lk_r = await asyncio.gather(
        _ref_client.get(f"/articles/{quote(title, safe='')}/backlinks", params={"limit": 10}),
        _ref_client.get(f"/articles/{quote(title, safe='')}/links"),
    )
    backlinks = bl_r.json() if bl_r.status_code == 200 else []
    # Build the set of unresolved (dead) link titles: to_id is null in the links table.
    # Normalise to lower-case so the comparison is case-insensitive (the links table
    # sometimes stores titles with different capitalisation than the body wikitext).
    links_data = lk_r.json() if lk_r.status_code == 200 else []
    dead_links: set[str] = {l["to_title"].lower() for l in links_data if l.get("to_id") is None}
    # Extract the full lead (all paragraphs before the first == section heading ==).
    # body_to_sections() drops this preamble; we surface it separately as article["lead"].
    _body = article.get("body") or ""
    _heading = re.search(r'(?m)^== .+? ==$', _body)
    article["lead"] = _body[:_heading.start()].strip() if _heading else (article.get("summary") or "")
    return templates.TemplateResponse(
        request, "reference_article.html",
        {"article": article, "backlinks": backlinks, "dead_links": dead_links},
    )


# ===========================================================================
# Gateway status
# ===========================================================================

@app.get("/status")
async def gateway_status():
    if _feed_client is None:
        return {"service": "KoreDataGateway", "status": "starting"}
    kf_r, kl_r, kr_r, krag_r, ks_r, kg_r = await asyncio.gather(
        _feed_client.get("/status", timeout=3.0),
        _lib_client.get("/status", timeout=3.0),
        _ref_client.get("/status", timeout=3.0),
        _rag_client.get("/status", timeout=3.0),
        _scrape_client.get("/status", timeout=3.0),
        _graph_client.get("/status", timeout=3.0),
        return_exceptions=True,
    )
    return {
        "service": "KoreDataGateway",
        "children": {
            "korefeed":      _svc_status(kf_r,   cfg["korefeed_url"]),
            "korelibrary":   _svc_status(kl_r,   cfg["korelibrary_url"]),
            "korereference": _svc_status(kr_r,   cfg["korereference_url"]),
            "korerag":       _svc_status(krag_r, cfg["korerag_url"]),
            "korescrape":    _svc_status(ks_r,   cfg["korescrape_url"]),
            "koregraph":     _svc_status(kg_r,   cfg["koregraph_url"]),
        },
    }


# ===========================================================================
# KoreRAG — Web UI
# ===========================================================================

@app.get("/ui/rag", response_class=HTMLResponse)
async def rag_index(request: Request, limit: int = 100, offset: int = 0, db: str = "default"):
    # If no db param was explicitly given, redirect to the most-populated database
    if "db" not in request.query_params:
        dbs_r = await _rag_client.get("/databases")
        databases = dbs_r.json() if dbs_r.status_code == 200 else []
        if databases:
            # Pick the db with the most chunks; fall back to first in list
            best = databases[0]["id"]
            best_count = 0
            for d in databases:
                st = await _rag_client.get("/status", params={"db": d["id"]})
                if st.status_code == 200:
                    count = st.json().get("total_chunks", 0)
                    if count > best_count:
                        best_count = count
                        best = d["id"]
            # If the best DB has navigation, go straight to the explore view
            best_db = next((d for d in databases if d["id"] == best), None)
            if best_db and best_db.get("navigation"):
                return RedirectResponse(url=f"/ui/rag/explore/{best}", status_code=302)
            params = dict(request.query_params)
            params["db"] = best
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            return RedirectResponse(url=f"/ui/rag?{qs}", status_code=302)

    chunks_r, status_r, dbs_r = await asyncio.gather(
        _rag_client.get("/chunks", params={"limit": limit, "offset": offset, "db": db}),
        _rag_client.get("/status", params={"db": db}),
        _rag_client.get("/databases"),
    )
    chunks    = chunks_r.json() if chunks_r.status_code == 200 else []
    status    = status_r.json() if status_r.status_code == 200 else {}
    databases = dbs_r.json()    if dbs_r.status_code == 200    else []
    return templates.TemplateResponse(
        request, "rag_index.html",
        {
            "chunks":    chunks,
            "total":     status.get("total_chunks", len(chunks)),
            "limit":     limit,
            "offset":    offset,
            "db":        db,
            "databases": databases,
        },
    )


async def _rag_databases_enriched() -> list[dict]:
    """Fetch and enrich all RAG database descriptors from KoreRAG."""
    dbs_r = await _rag_client.get("/databases")
    databases = dbs_r.json() if dbs_r.status_code == 200 else []
    async def _enrich(db_id: str) -> dict:
        r = await _rag_client.get(f"/databases/{db_id}/info")
        return r.json() if r.status_code == 200 else {"id": db_id}
    enriched = await asyncio.gather(*[_enrich(d["id"]) for d in databases], return_exceptions=True)
    return [e if isinstance(e, dict) else {"id": "?", "error": str(e)} for e in enriched]


def _rag_processing_descriptor_path(script_id: str) -> Path:
    return Path(get_koredata_dir()) / "RAG" / "databases" / script_id / f"{script_id}.json"


def _read_rag_processing_descriptor(script_id: str) -> dict:
    descriptor_path = _rag_processing_descriptor_path(script_id)
    if not descriptor_path.exists():
        return {}
    try:
        return _json.loads(descriptor_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_rag_processing_descriptor(script_id: str, descriptor: dict) -> None:
    descriptor_path = _rag_processing_descriptor_path(script_id)
    descriptor_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor_path.write_text(_json.dumps(descriptor, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _normalize_rag_processing_schedule(value: Any) -> str:
    schedule = str(value or "").strip().lower()
    return schedule if schedule in _RAG_SCRIPT_SCHEDULE_VALUES else "manual"


def _rag_processing_scripts(database_ids: set[str]) -> list[dict]:
    """Discover RAG database builder scripts from the configured runtime databases folder."""
    runtime_root = Path(get_koredata_dir()) / "RAG" / "databases"

    results: list[dict] = []
    seen: set[str] = set()
    if not runtime_root.exists():
        return results

    for subdir in sorted(p for p in runtime_root.iterdir() if p.is_dir()):
        script_path = subdir / "ingest.py"
        descriptor_path = subdir / f"{subdir.name}.json"
        if not script_path.exists() or not descriptor_path.exists():
            continue
        script_id = subdir.name
        if script_id in seen:
            continue
        seen.add(script_id)
        descriptor = _read_rag_processing_descriptor(script_id)
        sync       = descriptor.get("sync") or {}
        results.append({
            "id":            script_id,
            "display_name":  descriptor.get("display_name") or script_id.replace("_", " ").title(),
            "description":   descriptor.get("description") or "Database builder script.",
            "managed_by":    descriptor.get("managed_by") or "ingestor",
            "ingestor":      descriptor.get("ingestor") or script_id,
            "schedule":      _normalize_rag_processing_schedule(descriptor.get("schedule")),
            "has_database":  script_id in database_ids,
            "running":       script_id in _rag_processing_jobs and _rag_processing_jobs[script_id].poll() is None,
            "source_path":   str(subdir),
            "log_exists":    (subdir / "processing.log").exists(),
            "last_run":      sync.get("last_run"),
            "last_ingested": sync.get("last_date_ingested"),
            "sync_status":   sync.get("status"),
        })
    return results


def _find_rag_processing_script(script_id: str) -> dict | None:
    for script in _rag_processing_scripts(set()):
        if script.get("id") == script_id:
            return script
    return None

@app.get("/ui/rag/databases/json")
async def rag_databases_json():
    """JSON snapshot of all RAG database descriptors — used by the page's live-update polling."""
    databases = await _rag_databases_enriched()
    return {
        "databases":          databases,
        "processing_scripts": _rag_processing_scripts({d.get("id") for d in databases if d.get("id")}),
    }


@app.get("/ui/rag/databases", response_class=HTMLResponse)
async def rag_databases(request: Request):
    enriched = await _rag_databases_enriched()
    processing = _rag_processing_scripts({d.get("id") for d in enriched if d.get("id")})
    return templates.TemplateResponse(
        request,
        "rag_databases.html",
        {"databases": enriched, "processing_scripts": processing},
    )


@app.post("/ui/rag/databases/{name}/sync")
async def rag_database_sync(name: str):
    """Fire-and-forget: ask KoreRAG to launch the database's ingest.py."""
    r = await _rag_client.post(f"/databases/{name}/sync", timeout=10.0)
    return RedirectResponse("/ui/rag/databases", status_code=303)


@app.post("/ui/rag/processing/{script_id}/run")
async def rag_processing_run(script_id: str, reset: int = Form(0)):
    script = _find_rag_processing_script(script_id)
    if script is None:
        raise HTTPException(status_code=404, detail=f"Unknown processing script: {script_id!r}")

    existing = _rag_processing_jobs.get(script_id)
    if existing is not None and existing.poll() is None:
        return RedirectResponse("/ui/rag/databases", status_code=303)

    script_dir = Path(script["source_path"])
    script_path = script_dir / "ingest.py"
    if not script_path.exists():
        raise HTTPException(status_code=404, detail=f"Missing ingest.py for {script_id!r}")

    argv = [sys.executable, str(script_path)]
    if reset:
        argv.append("--reset")

    log_path = script_dir / "processing.log"
    log_handle = open(log_path, "ab")
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(script_dir),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"},
        )
    finally:
        log_handle.close()

    _rag_processing_jobs[script_id] = proc
    return RedirectResponse("/ui/rag/databases", status_code=303)


@app.post("/ui/rag/processing/{script_id}/schedule")
async def rag_processing_schedule(script_id: str, schedule: str = Form("")):
    script = _find_rag_processing_script(script_id)
    if script is None:
        raise HTTPException(status_code=404, detail=f"Unknown processing script: {script_id!r}")

    descriptor = _read_rag_processing_descriptor(script_id)
    normalized = _normalize_rag_processing_schedule(schedule)
    descriptor["schedule"] = normalized
    _write_rag_processing_descriptor(script_id, descriptor)
    return RedirectResponse("/ui/rag/databases", status_code=303)


@app.get("/ui/rag/processing/{script_id}/log", response_class=HTMLResponse)
async def rag_processing_log(request: Request, script_id: str):
    script = _find_rag_processing_script(script_id)
    if script is None:
        raise HTTPException(status_code=404, detail=f"Unknown processing script: {script_id!r}")

    script_dir = Path(script["source_path"])
    log_path = script_dir / "processing.log"
    if not log_path.exists():
        raise HTTPException(status_code=404, detail=f"No processing log found for {script_id!r}")

    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read processing log: {exc}") from exc

    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(script_id)} processing log</title>
  <link rel="stylesheet" href="/ui-elements/assets/css/chrome.css">
</head>
<body class="kcui-shell-bg">
  <main class="kcui-page kcui-page--narrow kcui-stack">
    <section class="kcui-panel">
      <div class="kcui-panel-header">
        <span>{escape(script.get("display_name") or script_id)} processing log</span>
        <a class="kcui-tag kcui-tag--muted" href="/ui/rag/databases" style="margin-left:auto;">BACK</a>
      </div>
      <pre class="kcui-panel-body kcui-panel-body--mono kcui-panel-body--scroll" style="max-height:75vh; white-space:pre-wrap;">{escape(text)}</pre>
    </section>
  </main>
</body>
</html>"""
    )


@app.post("/ui/rag/databases/{name}/stop")
async def rag_database_stop(name: str):
    """Ask KoreRAG to terminate the running ingest process for this database."""
    await _rag_client.post(f"/databases/{name}/stop", timeout=10.0)
    return RedirectResponse("/ui/rag/databases", status_code=303)


@app.post("/ui/rag/databases/{name}/delete")
async def rag_database_delete(name: str):
    """Delete a database and all its data files."""
    await _rag_client.delete(f"/databases/{name}", timeout=10.0)
    return RedirectResponse("/ui/rag/databases", status_code=303)


@app.post("/ui/rag/databases", response_class=HTMLResponse)
async def rag_database_create(
    request: Request,
    name:         str           = Form(...),
    display_name: Optional[str] = Form(None),
    description:  Optional[str] = Form(None),
):
    """Create a new user-managed database and redirect back to the list."""
    payload = {"name": name}
    if display_name: payload["display_name"] = display_name
    if description:  payload["description"]  = description
    r = await _rag_client.post("/databases", json=payload, timeout=10.0)
    if r.status_code not in (200, 201):
        # Re-render the databases page with the error inline
        enriched = await _rag_databases_enriched()
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        return templates.TemplateResponse(
            request, "rag_databases.html",
            {
                "databases": enriched,
                "processing_scripts": _rag_processing_scripts({d.get("id") for d in enriched if d.get("id")}),
                "create_error": detail,
            },
        )
    return RedirectResponse("/ui/rag/databases", status_code=303)


@app.get("/ui/rag/search", response_class=HTMLResponse)
async def rag_search(
    request: Request,
    q: Optional[str] = None,
    source: Optional[str] = None,
    tags: Optional[str] = None,
    limit: int = 20,
    db: str = "default",
):
    results = []
    searched = bool(q)
    if searched:
        params: dict = {"q": q, "limit": limit, "db": db}
        if source: params["source"] = source
        if tags:   params["tags"]   = tags
        r = await _rag_client.get("/search", params=params)
        results = r.json() if r.status_code == 200 else []
    dbs_r = await _rag_client.get("/databases")
    databases = dbs_r.json() if dbs_r.status_code == 200 else []
    return templates.TemplateResponse(
        request, "rag_search.html",
        {
            "results":   results,
            "searched":  searched,
            "q":         q or "",
            "source":    source or "",
            "tags":      tags or "",
            "limit":     limit,
            "db":        db,
            "databases": databases,
        },
    )


@app.get("/ui/rag/insert", response_class=HTMLResponse)
async def rag_insert(request: Request, db: str = "default"):
    dbs_r = await _rag_client.get("/databases")
    databases = dbs_r.json() if dbs_r.status_code == 200 else []
    return templates.TemplateResponse(
        request, "rag_insert.html",
        {"error": None, "success": None, "db": db, "databases": databases},
    )


@app.post("/ui/rag/insert", response_class=HTMLResponse)
async def rag_insert_post(
    request: Request,
    content: str           = Form(...),
    title:   Optional[str] = Form(None),
    source:  Optional[str] = Form(None),
    tags:    Optional[str] = Form(None),
    db:      str           = Form("default"),
):
    payload: dict = {"content": content}
    if title:  payload["title"]  = title
    if source: payload["source"] = source
    if tags:   payload["tags"]   = tags
    r = await _rag_client.post("/chunks", params={"db": db}, json=payload)
    if r.status_code in (200, 201):
        chunk_id = r.json().get("id")
        return RedirectResponse(url=f"/ui/rag/{chunk_id}?db={db}", status_code=303)
    dbs_r = await _rag_client.get("/databases")
    databases = dbs_r.json() if dbs_r.status_code == 200 else []
    return templates.TemplateResponse(
        request, "rag_insert.html",
        {"error": r.json().get("detail", f"Error {r.status_code}"), "success": None, "db": db, "databases": databases},
        status_code=400,
    )


@app.get("/ui/rag/{chunk_id}", response_class=HTMLResponse)
async def rag_chunk(request: Request, chunk_id: int, db: str = "default"):
    r = await _rag_client.get(f"/chunks/{chunk_id}", params={"db": db})
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return templates.TemplateResponse(request, "rag_chunk.html", {"chunk": r.json(), "db": db})


@app.post("/ui/rag/{chunk_id}/edit")
async def rag_chunk_edit(
    request: Request,
    chunk_id: int,
    db: str = "default",
    title: str = Form(""),
    source: str = Form(""),
    tags: str = Form(""),
    content: str = Form(""),
):
    payload = {"title": title, "source": source, "tags": tags, "content": content}
    r = await _rag_client.patch(f"/chunks/{chunk_id}", params={"db": db}, json=payload)
    if r.status_code not in (200, 204):
        raise HTTPException(status_code=r.status_code, detail="Update failed")
    return RedirectResponse(url=f"/ui/rag/{chunk_id}?db={db}", status_code=303)


@app.post("/ui/rag/{chunk_id}/delete")
async def rag_chunk_delete(chunk_id: int, db: str = "default"):
    r = await _rag_client.delete(f"/chunks/{chunk_id}", params={"db": db})
    if r.status_code not in (200, 204):
        raise HTTPException(status_code=r.status_code, detail="Delete failed")
    return RedirectResponse(url=f"/ui/rag?db={db}", status_code=303)


# ---------------------------------------------------------------------------
# KoreRAG explore (navigation tables for structured databases)
# ---------------------------------------------------------------------------

@app.get("/ui/rag/explore/{db_id}", response_class=HTMLResponse)
async def rag_explore(request: Request, db_id: str):
    sittings_r, members_r, dbs_r = await asyncio.gather(
        _rag_client.get(f"/databases/{db_id}/sittings"),
        _rag_client.get(f"/databases/{db_id}/members"),
        _rag_client.get("/databases"),
    )
    sittings  = sittings_r.json()  if sittings_r.status_code == 200  else []
    members   = members_r.json()   if members_r.status_code == 200   else []
    databases = dbs_r.json()       if dbs_r.status_code == 200       else []
    return templates.TemplateResponse(
        request, "rag_explore.html",
        {"db_id": db_id, "sittings": sittings, "members": members, "databases": databases},
    )


@app.get("/ui/rag/explore/{db_id}/sitting/{date}", response_class=HTMLResponse)
async def rag_explore_sitting(request: Request, db_id: str, date: str):
    debates_r, dbs_r = await asyncio.gather(
        _rag_client.get(f"/databases/{db_id}/sittings/{date}/debates"),
        _rag_client.get("/databases"),
    )
    debates   = debates_r.json()  if debates_r.status_code == 200  else []
    databases = dbs_r.json()      if dbs_r.status_code == 200      else []
    return templates.TemplateResponse(
        request, "rag_explore_sitting.html",
        {"db_id": db_id, "date": date, "debates": debates, "databases": databases},
    )


@app.get("/ui/rag/explore/{db_id}/debate/{uuid}", response_class=HTMLResponse)
async def rag_explore_debate(request: Request, db_id: str, uuid: str):
    debate_r, speeches_r, dbs_r = await asyncio.gather(
        _rag_client.get(f"/databases/{db_id}/debates/{uuid}"),
        _rag_client.get(f"/databases/{db_id}/debates/{uuid}/speeches"),
        _rag_client.get("/databases"),
    )
    debate    = debate_r.json()   if debate_r.status_code == 200   else {}
    speeches  = speeches_r.json() if speeches_r.status_code == 200 else []
    databases = dbs_r.json()      if dbs_r.status_code == 200      else []
    return templates.TemplateResponse(
        request, "rag_explore_debate.html",
        {"db_id": db_id, "debate": debate, "speeches": speeches, "databases": databases},
    )


@app.get("/ui/rag/explore/{db_id}/member/{member_id}", response_class=HTMLResponse)
async def rag_explore_member(request: Request, db_id: str, member_id: int):
    member_r, speeches_r, dbs_r = await asyncio.gather(
        _rag_client.get(f"/databases/{db_id}/members/{member_id}"),
        _rag_client.get(f"/databases/{db_id}/members/{member_id}/speeches"),
        _rag_client.get("/databases"),
    )
    member    = member_r.json()   if member_r.status_code == 200   else {}
    speeches  = speeches_r.json() if speeches_r.status_code == 200 else []
    databases = dbs_r.json()      if dbs_r.status_code == 200      else []
    return templates.TemplateResponse(
        request, "rag_explore_member.html",
        {"db_id": db_id, "member": member, "speeches": speeches, "databases": databases},
    )


# ---------------------------------------------------------------------------
# KoreRAG JSON API proxy
# ---------------------------------------------------------------------------

@app.get("/api/rag/chunks")
async def api_rag_list(limit: int = 100, offset: int = 0):
    r = await _rag_client.get("/chunks", params={"limit": limit, "offset": offset})
    return JSONResponse(content=r.json(), status_code=r.status_code)


@app.get("/api/rag/chunks/{chunk_id}")
async def api_rag_get(chunk_id: int):
    r = await _rag_client.get(f"/chunks/{chunk_id}")
    return JSONResponse(content=r.json(), status_code=r.status_code)


@app.post("/api/rag/chunks", status_code=201)
async def api_rag_add(request: Request):
    payload = await request.json()
    r = await _rag_client.post("/chunks", json=payload)
    return JSONResponse(content=r.json(), status_code=r.status_code)


@app.patch("/api/rag/chunks/{chunk_id}")
async def api_rag_update(chunk_id: int, request: Request, db: str = "default"):
    payload = await request.json()
    r = await _rag_client.patch(f"/chunks/{chunk_id}", params={"db": db}, json=payload)
    return JSONResponse(content=r.json(), status_code=r.status_code)


@app.delete("/api/rag/chunks/{chunk_id}")
async def api_rag_delete(chunk_id: int, db: str = "default"):
    r = await _rag_client.delete(f"/chunks/{chunk_id}", params={"db": db})
    return JSONResponse(content=r.json(), status_code=r.status_code)


@app.get("/api/rag/search")
async def api_rag_search(
    q: str,
    limit: int = 20,
    source: Optional[str] = None,
    tags: Optional[str] = None,
    db: str = "default",
):
    params: dict = {"q": q, "limit": limit, "db": db}
    if source: params["source"] = source
    if tags:   params["tags"]   = tags
    r = await _rag_client.get("/search", params=params)
    return JSONResponse(content=r.json(), status_code=r.status_code)


# ===========================================================================
# MCP server mount
# ===========================================================================

app.mount("/mcp", _mcp.streamable_http_app())
