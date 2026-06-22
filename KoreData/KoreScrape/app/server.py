import hashlib
import json
import mimetypes
import posixpath
import re
import sys
import threading
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urldefrag, urlsplit, urlunsplit, unquote

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field, HttpUrl

_KORECOMMON_PARENT = next((parent for parent in Path(__file__).resolve().parents if (parent / "KoreCommon").is_dir()), None)
if _KORECOMMON_PARENT is not None and str(_KORECOMMON_PARENT) not in sys.path:
    sys.path.insert(0, str(_KORECOMMON_PARENT))

from KoreCommon.endpoint_manifest import build_endpoint_manifest
from app.config import cfg
from app.database import (
    delete_chunk as _db_delete_chunk,
    get_chunk as _db_get_chunk,
    get_status as _db_get_status,
    init_db as _init_db,
    list_chunks as _db_list_chunks,
    make_chunk_row,
    replace_capture_chunks,
    search_chunks as _db_search_chunks,
)

_DATA_DIR            = Path(cfg["data_dir"])
_HTML_MIME_PREFIXES  = ("text/html", "application/xhtml+xml")
_ASSET_ATTRS         = (
    ("img",    "src"),
    ("script", "src"),
    ("iframe", "src"),
    ("source", "src"),
    ("audio",  "src"),
    ("video",  "src"),
)
_LINK_REL_ASSETS     = {"stylesheet", "icon", "shortcut icon", "apple-touch-icon", "preload"}
_INVALID_SEGMENT_RE  = re.compile(r'[<>:"/\\|?*]+')
_SPACE_RE            = re.compile(r"\s+")
_JOB_LOCK            = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_MIN_CHUNK_WORDS     = 100
_TARGET_CHUNK_WORDS  = 450
_MAX_CHUNK_WORDS     = 900
_SINGLE_PAGE_WORDS   = 1200


class CaptureRequest(BaseModel):
    url: HttpUrl
    depth: int = Field(default=0, ge=0, le=5)
    download_non_html: bool = False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _normalise_url(url: str) -> str:
    cleaned = str(url or "").strip()
    if not cleaned:
        raise ValueError("URL is required.")
    if "://" not in cleaned:
        cleaned = f"https://{cleaned}"
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http and https URLs are supported.")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def _sanitise_segment(text: str) -> str:
    value = unquote(text or "").strip()
    value = _INVALID_SEGMENT_RE.sub("_", value)
    value = value.replace("\0", "_").strip(" .")
    return value or "_"


def _host_dirname(netloc: str) -> str:
    return _sanitise_segment(netloc.replace(":", "_"))


def _capture_paths(url: str) -> tuple[str, Path]:
    parsed      = urlsplit(url)
    stamp_local = datetime.now().strftime("%Y%m%d_%H%M%S")
    day_dir     = datetime.now().strftime("%Y-%m-%d")
    host_dir    = _host_dirname(parsed.netloc)
    capture_id  = f"{day_dir}_{host_dir}_{stamp_local}"
    root_dir    = _DATA_DIR / day_dir / host_dir / stamp_local
    return capture_id, root_dir


def _rel_with_query(rel_path: Path, query: str) -> Path:
    if not query:
        return rel_path
    suffix = rel_path.suffix
    stem   = rel_path.stem if suffix else rel_path.name
    extra  = hashlib.sha1(query.encode("utf-8")).hexdigest()[:10]
    if suffix:
        return rel_path.with_name(f"{stem}__q_{extra}{suffix}")
    return rel_path.with_name(f"{stem}__q_{extra}")


def _guess_suffix(content_type: str, fallback: str) -> str:
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    guessed = mimetypes.guess_extension(mime or "")
    if guessed:
        return guessed
    return fallback


def _page_rel_path(url: str) -> Path:
    parsed   = urlsplit(url)
    raw_path = parsed.path or "/"
    parts    = [_sanitise_segment(part) for part in raw_path.split("/") if part]
    if not parts:
        rel_path = Path("index.html")
    elif raw_path.endswith("/"):
        rel_path = Path(*parts) / "index.html"
    else:
        rel_path = Path(*parts)
        if rel_path.suffix.lower() not in {".html", ".htm", ".xhtml"}:
            rel_path = rel_path / "index.html" if "." not in rel_path.name else rel_path.with_suffix(".html")
    return _rel_with_query(rel_path, parsed.query)


def _asset_rel_path(url: str, root_host: str, content_type: str = "") -> Path:
    parsed    = urlsplit(url)
    raw_path  = parsed.path or "/"
    host_bits = [] if parsed.netloc == root_host else ["_external", _host_dirname(parsed.netloc)]
    parts     = [_sanitise_segment(part) for part in raw_path.split("/") if part]
    rel_path  = Path(*(host_bits + parts)) if host_bits or parts else Path("_root")

    if raw_path.endswith("/") or rel_path.suffix == "":
        suffix   = _guess_suffix(content_type, ".bin")
        rel_path = rel_path / f"index{suffix}" if raw_path.endswith("/") else rel_path.with_suffix(suffix)

    return _rel_with_query(rel_path, parsed.query)


def _manifest_path(capture_dir: Path) -> Path:
    return capture_dir / "manifest.json"


def _site_dir(capture_dir: Path) -> Path:
    return capture_dir / "site"


def _write_manifest(capture_dir: Path, payload: dict[str, Any]) -> None:
    manifest_path = _manifest_path(capture_dir)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_manifest(manifest_path: Path) -> dict[str, Any]:
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _list_capture_manifests() -> list[Path]:
    if not _DATA_DIR.exists():
        return []
    manifests = list(_DATA_DIR.rglob("manifest.json"))
    manifests.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return manifests


def list_captures() -> list[dict[str, Any]]:
    captures: list[dict[str, Any]] = []
    for manifest_path in _list_capture_manifests():
        try:
            capture = _read_manifest(manifest_path)
        except Exception:
            continue
        captures.append(capture)
    return captures


def get_capture(capture_id: str) -> dict[str, Any]:
    for capture in list_captures():
        if capture.get("id") == capture_id:
            return capture
    raise KeyError(capture_id)


def get_status() -> dict[str, Any]:
    captures      = list_captures()
    running_jobs  = 0
    queued_jobs   = 0
    total_pages   = 0
    total_assets  = 0

    with _JOB_LOCK:
        for job in _JOBS.values():
            if job.get("status") == "running":
                running_jobs += 1
            elif job.get("status") == "queued":
                queued_jobs += 1

    for capture in captures:
        total_pages  += int(capture.get("pages_saved", 0)  or 0)
        total_assets += int(capture.get("assets_saved", 0) or 0)

    index_stats = _db_get_status()

    return {
        "service":      "KoreScrape",
        "captures":     len(captures),
        "running_jobs": running_jobs,
        "queued_jobs":  queued_jobs,
        "pages":        total_pages,
        "assets":       total_assets,
        "indexed_pages": index_stats.get("indexed_pages", 0),
        "indexed_chunks": index_stats.get("indexed_chunks", 0),
        "db_size_bytes": index_stats.get("db_size_bytes", 0),
        "latest_run":   captures[0].get("created_at") if captures else None,
    }


def _same_origin(a: str, b: str) -> bool:
    pa = urlsplit(a)
    pb = urlsplit(b)
    return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)


def _should_follow_link(target_url: str, root_url: str) -> bool:
    parsed = urlsplit(target_url)
    if parsed.scheme not in {"http", "https"}:
        return False
    return _same_origin(target_url, root_url)


def _is_html_response(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "").lower()
    return any(content_type.startswith(prefix) for prefix in _HTML_MIME_PREFIXES)


def _save_bytes(site_dir: Path, relative_path: Path, content: bytes) -> str:
    target = (site_dir / relative_path).resolve()
    site    = site_dir.resolve()
    if target != site and site not in target.parents:
        raise ValueError("Refusing to write outside capture site directory.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return relative_path.as_posix()


def _relative_href(from_page_rel: Path, target_rel: str) -> str:
    from_dir = Path(from_page_rel).parent.as_posix()
    from_dir = from_dir if from_dir not in {"", "."} else "."
    return posixpath.relpath(target_rel, from_dir)


def _page_link_replacement(
    page_url: str,
    page_rel: Path,
    candidate: str,
    page_lookup: dict[str, str],
) -> Optional[str]:
    if not candidate:
        return None
    abs_url, _ = urldefrag(urljoin(page_url, candidate))
    if not abs_url:
        return None
    rel = page_lookup.get(abs_url)
    if rel is None:
        rel = _page_rel_path(abs_url).as_posix()
        page_lookup[abs_url] = rel
    return _relative_href(page_rel, rel)


def _asset_link_replacement(
    page_url: str,
    page_rel: Path,
    candidate: str,
    root_host: str,
    asset_lookup: dict[str, str],
    asset_targets: set[str],
) -> Optional[str]:
    if not candidate:
        return None
    abs_url, _ = urldefrag(urljoin(page_url, candidate))
    parsed = urlsplit(abs_url)
    if parsed.scheme not in {"http", "https"}:
        return None
    rel = asset_lookup.get(abs_url)
    if rel is None:
        rel = _asset_rel_path(abs_url, root_host).as_posix()
        asset_lookup[abs_url] = rel
    asset_targets.add(abs_url)
    return _relative_href(page_rel, rel)


def _clean_block_text(text: str) -> str:
    cleaned = _SPACE_RE.sub(" ", (text or "").strip())
    return cleaned.strip()


def _extract_text_blocks(soup: BeautifulSoup) -> list[str]:
    for tag in soup.select("script, style, noscript, nav, header, footer, svg, canvas"):
        tag.decompose()

    blocks: list[str] = []
    selectors = ("main", "article", "[role=main]", "body")
    root = None
    for selector in selectors:
        root = soup.select_one(selector)
        if root is not None:
            break
    root = root or soup

    for tag in root.find_all(["p", "li", "blockquote", "pre", "td", "th"]):
        text = _clean_block_text(tag.get_text(" ", strip=True))
        if len(text.split()) >= 12:
            blocks.append(text)
    return blocks


def _build_text_chunks(
    blocks: list[str],
    min_words: int = _MIN_CHUNK_WORDS,
    target_words: int = _TARGET_CHUNK_WORDS,
    max_words: int = _MAX_CHUNK_WORDS,
    single_page_words: int = _SINGLE_PAGE_WORDS,
) -> list[str]:
    total_words = sum(len(block.split()) for block in blocks)
    if total_words <= 0:
        return []
    if total_words <= single_page_words:
        merged = " ".join(blocks).strip()
        return [merged] if len(merged.split()) >= min_words else []

    chunks: list[str] = []
    current: list[str] = []
    words = 0

    for block in blocks:
        block_words = len(block.split())
        if block_words >= max_words:
            if current and words >= min_words:
                chunks.append(" ".join(current).strip())
                current = []
                words = 0
            chunks.append(block.strip())
            continue

        if current and words >= target_words and (words + block_words) > max_words:
            chunks.append(" ".join(current).strip())
            current = []
            words = 0

        current.append(block)
        words += block_words
        if words >= max_words:
            chunks.append(" ".join(current).strip())
            current = []
            words = 0

    if current and words >= min_words:
        chunks.append(" ".join(current).strip())
    return chunks


def _index_capture_pages(capture_id: str, pages: list[dict[str, Any]], capture_dir: Path, captured_at: str) -> int:
    site_dir = _site_dir(capture_dir)
    rows: list[dict] = []

    for page in pages:
        page_path = str(page.get("path") or "").strip()
        page_url  = str(page.get("url") or "").strip()
        if not page_path or not page_url:
            continue

        candidate = (site_dir / page_path).resolve()
        if candidate != site_dir.resolve() and site_dir.resolve() not in candidate.parents:
            continue
        if not candidate.exists() or not candidate.is_file():
            continue
        if candidate.suffix.lower() not in {".html", ".htm", ".xhtml"}:
            continue

        try:
            html = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        soup = BeautifulSoup(html, "html.parser")
        title = _clean_block_text(soup.title.get_text(" ", strip=True) if soup.title else "")
        blocks = _extract_text_blocks(soup)
        chunks = _build_text_chunks(blocks)

        for idx, chunk in enumerate(chunks):
            rows.append(make_chunk_row(
                capture_id  = capture_id,
                page_url    = page_url,
                page_path   = page_path,
                page_title  = title,
                captured_at = captured_at,
                chunk_index = idx,
                content     = chunk,
            ))

    return replace_capture_chunks(capture_id, rows)


def _crawl_capture(job: dict[str, Any]) -> None:
    capture_dir   = Path(job["capture_dir"])
    site_dir      = _site_dir(capture_dir)
    root_url      = job["url"]
    root_host     = urlsplit(root_url).netloc
    max_pages     = int(cfg.get("max_pages", 200) or 200)
    pages_saved   = 0
    assets_saved  = 0
    page_lookup:  dict[str, str] = {}
    asset_lookup: dict[str, str] = {}
    errors:       list[dict[str, Any]] = []
    pages_meta:   list[dict[str, Any]] = []
    assets_meta:  list[dict[str, Any]] = []
    queue                    = deque([(root_url, 0)])
    enqueued: set[str]       = {root_url}
    visited_pages: set[str]  = set()
    downloaded_assets: set[str] = set()

    job["status"]     = "running"
    job["started_at"] = _iso_now()

    manifest = {
        "id":            job["id"],
        "url":           root_url,
        "depth":         job["depth"],
        "download_non_html": bool(job.get("download_non_html")),
        "created_at":    job["created_at"],
        "started_at":    job["started_at"],
        "completed_at":  None,
        "status":        "running",
        "capture_dir":   capture_dir.as_posix(),
        "site_dir":      site_dir.as_posix(),
        "local_root":    _page_rel_path(root_url).as_posix(),
        "pages_saved":   0,
        "assets_saved":  0,
        "page_count":    0,
        "asset_count":   0,
        "errors":        errors,
        "pages":         pages_meta,
        "assets":        assets_meta,
    }
    _write_manifest(capture_dir, manifest)

    client = httpx.Client(
        follow_redirects = True,
        timeout          = 30.0,
        headers          = {"User-Agent": cfg.get("user_agent", "KoreScrape/1.0")},
    )
    download_non_html = bool(job.get("download_non_html"))

    try:
        while queue and len(visited_pages) < max_pages:
            page_url, depth = queue.popleft()
            if page_url in visited_pages:
                continue
            visited_pages.add(page_url)

            try:
                response = client.get(page_url)
                response.raise_for_status()
            except Exception as exc:
                errors.append({"kind": "page", "url": page_url, "error": str(exc)})
                continue

            final_url = str(response.url)
            if not _is_html_response(response):
                if not download_non_html:
                    continue
                rel_asset = asset_lookup.get(final_url) or _asset_rel_path(final_url, root_host).as_posix()
                saved_rel = _save_bytes(site_dir, Path(rel_asset), response.content)
                asset_lookup[final_url] = saved_rel
                if assets_saved == 0 and pages_saved == 0:
                    manifest["local_root"] = saved_rel
                assets_saved += 1
                assets_meta.append({"url": final_url, "path": saved_rel, "content_type": response.headers.get("content-type", "")})
                continue

            page_rel      = _page_rel_path(final_url)
            page_lookup[final_url] = page_rel.as_posix()
            soup          = BeautifulSoup(response.text, "html.parser")
            asset_targets: set[str] = set()

            if download_non_html:
                for tag_name, attr_name in _ASSET_ATTRS:
                    for tag in soup.find_all(tag_name):
                        candidate = tag.get(attr_name)
                        rewritten = _asset_link_replacement(final_url, page_rel, candidate, root_host, asset_lookup, asset_targets)
                        if rewritten:
                            tag[attr_name] = rewritten

                for link in soup.find_all("link"):
                    candidate = link.get("href")
                    rel_text  = " ".join(link.get("rel", []))
                    if rel_text.lower() in _LINK_REL_ASSETS:
                        rewritten = _asset_link_replacement(final_url, page_rel, candidate, root_host, asset_lookup, asset_targets)
                        if rewritten:
                            link["href"] = rewritten

            for anchor in soup.find_all("a"):
                candidate = anchor.get("href")
                if not candidate:
                    continue
                abs_url, _ = urldefrag(urljoin(final_url, candidate))
                if not _should_follow_link(abs_url, root_url):
                    continue
                rewritten = _page_link_replacement(final_url, page_rel, candidate, page_lookup)
                if rewritten:
                    anchor["href"] = rewritten
                if depth < job["depth"] and abs_url not in enqueued:
                    enqueued.add(abs_url)
                    queue.append((abs_url, depth + 1))

            html_bytes = soup.encode(formatter="html")
            saved_rel  = _save_bytes(site_dir, page_rel, html_bytes)
            if pages_saved == 0 and assets_saved == 0:
                manifest["local_root"] = saved_rel
            pages_saved += 1
            pages_meta.append({"url": final_url, "path": saved_rel, "depth": depth})

            if download_non_html:
                for asset_url in sorted(asset_targets):
                    if asset_url in downloaded_assets:
                        continue
                    downloaded_assets.add(asset_url)
                    try:
                        asset_resp = client.get(asset_url)
                        asset_resp.raise_for_status()
                        asset_rel  = asset_lookup.get(asset_url) or _asset_rel_path(asset_url, root_host).as_posix()
                        saved_rel  = _save_bytes(site_dir, Path(asset_rel), asset_resp.content)
                        asset_lookup[asset_url] = saved_rel
                        assets_saved += 1
                        assets_meta.append({
                            "url":          asset_url,
                            "path":         saved_rel,
                            "content_type": asset_resp.headers.get("content-type", ""),
                        })
                    except Exception as exc:
                        errors.append({"kind": "asset", "url": asset_url, "error": str(exc)})

            manifest["pages_saved"]  = pages_saved
            manifest["assets_saved"] = assets_saved
            manifest["page_count"]   = len(pages_meta)
            manifest["asset_count"]  = len(assets_meta)
            manifest["errors"]       = errors
            _write_manifest(capture_dir, manifest)

        manifest["completed_at"] = _iso_now()
        manifest["status"]       = "completed" if not errors else "completed_with_errors"
        manifest["indexed_chunks"] = _index_capture_pages(
            capture_id = manifest["id"],
            pages      = pages_meta,
            capture_dir = capture_dir,
            captured_at = manifest["completed_at"],
        )
        job["status"]            = manifest["status"]
    except Exception as exc:
        errors.append({"kind": "job", "url": root_url, "error": str(exc)})
        manifest["completed_at"] = _iso_now()
        manifest["status"]       = "failed"
        manifest["indexed_chunks"] = 0
        job["status"]            = "failed"
    finally:
        client.close()
        manifest["pages_saved"]  = pages_saved
        manifest["assets_saved"] = assets_saved
        manifest["page_count"]   = len(pages_meta)
        manifest["asset_count"]  = len(assets_meta)
        manifest["errors"]       = errors
        _write_manifest(capture_dir, manifest)
        job["completed_at"] = manifest["completed_at"]


def _start_capture(url: str, depth: int, download_non_html: bool) -> dict[str, Any]:
    normalised           = _normalise_url(url)
    capture_id, out_dir  = _capture_paths(normalised)
    job = {
        "id":                 capture_id,
        "url":                normalised,
        "depth":              int(depth),
        "download_non_html":  bool(download_non_html),
        "created_at":         _iso_now(),
        "capture_dir":        out_dir.as_posix(),
        "status":             "queued",
    }
    worker = threading.Thread(target=_crawl_capture, args=(job,), daemon=True, name=f"korescrape-{capture_id}")
    with _JOB_LOCK:
        _JOBS[capture_id] = job
    worker.start()
    return job


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _init_db()
    for capture in list_captures():
        capture_id   = str(capture.get("id") or "").strip()
        capture_dir  = Path(str(capture.get("capture_dir") or ""))
        pages        = capture.get("pages") if isinstance(capture.get("pages"), list) else []
        completed_at = str(capture.get("completed_at") or capture.get("created_at") or "")
        if capture_id and capture_dir.exists() and pages:
            try:
                _index_capture_pages(capture_id, pages, capture_dir, completed_at)
            except Exception:
                continue
    yield


app = FastAPI(
    title       = "KoreScrape",
    description = "Website snapshot capture service",
    lifespan    = _lifespan,
)


@app.get("/__endpoint_manifest", include_in_schema=False)
def endpoint_manifest() -> dict:
    return build_endpoint_manifest(app, service_key="korescrape", service_label="KoreScrape")


@app.get("/", include_in_schema=False)
def route_root():
    return RedirectResponse("/captures", status_code=302)


@app.get("/status")
def route_status():
    return get_status()


@app.get("/api/captures")
@app.get("/captures", include_in_schema=False)
def route_list_captures():
    return {"captures": list_captures()}


@app.get("/api/captures/{capture_id}")
@app.get("/captures/{capture_id}", include_in_schema=False)
def route_get_capture(capture_id: str):
    try:
        return get_capture(capture_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Capture not found") from exc


@app.post("/api/captures", status_code=202)
@app.post("/captures", status_code=202, include_in_schema=False)
def route_create_capture(payload: CaptureRequest):
    job = _start_capture(str(payload.url), payload.depth, payload.download_non_html)
    return {
        "id":                job["id"],
        "url":               job["url"],
        "depth":             job["depth"],
        "download_non_html": job["download_non_html"],
        "created_at":        job["created_at"],
        "status":            job["status"],
    }


@app.get("/api/chunks")
@app.get("/chunks", include_in_schema=False)
def route_list_chunks(limit: int = 100, offset: int = 0, capture_id: Optional[str] = None):
    return _db_list_chunks(limit=limit, offset=offset, capture_id=capture_id)


@app.get("/api/chunks/{chunk_id}")
@app.get("/chunks/{chunk_id}", include_in_schema=False)
def route_get_chunk(chunk_id: int):
    chunk = _db_get_chunk(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return chunk


@app.delete("/api/chunks/{chunk_id}")
@app.delete("/chunks/{chunk_id}", include_in_schema=False)
def route_delete_chunk(chunk_id: int):
    if not _db_delete_chunk(chunk_id):
        raise HTTPException(status_code=404, detail="Chunk not found")
    return {"ok": True, "id": chunk_id}


@app.post("/api/chunks/{chunk_id}/delete")
@app.post("/chunks/{chunk_id}/delete", include_in_schema=False)
def route_delete_chunk_post(chunk_id: int):
    if not _db_delete_chunk(chunk_id):
        raise HTTPException(status_code=404, detail="Chunk not found")
    return {"ok": True, "id": chunk_id}


@app.get("/api/search")
@app.get("/search", include_in_schema=False)
def route_search(q: str, limit: int = 20, capture_id: Optional[str] = None):
    return _db_search_chunks(q=q, limit=limit, capture_id=capture_id)


@app.get("/captures/{capture_id}/files/{file_path:path}", include_in_schema=False)
def route_capture_file(capture_id: str, file_path: str):
    try:
        capture = get_capture(capture_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Capture not found") from exc

    site_dir   = Path(capture["site_dir"]).resolve()
    candidate  = (site_dir / file_path).resolve()
    if candidate != site_dir and site_dir not in candidate.parents:
        raise HTTPException(status_code=404, detail="File not found")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(candidate)
