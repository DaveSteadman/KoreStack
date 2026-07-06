import json
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader
from config import get_suite_urls_map

from app.config import cfg
from app.database import delete_chunk, get_chunk, list_chunks, search_chunks


_SCRAPE_UI_ROOT = Path(
    os.environ.get(
        "KORE_KORESCRAPE_UI_DIR",
        str(Path(__file__).resolve().parents[3] / "KoreUI" / "KoreData" / "KoreScrape"),
    )
).resolve()
TEMPLATES_DIR = Path(
    os.environ.get(
        "KORE_KORESCRAPE_TEMPLATES_DIR",
        str(_SCRAPE_UI_ROOT / "templates"),
    )
).resolve()
_SHARED_TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "KoreUI" / "KoreData" / "KoreDataGateway" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.loader = ChoiceLoader([
    FileSystemLoader(str(TEMPLATES_DIR)),
    FileSystemLoader(str(_SHARED_TEMPLATES_DIR)),
])

_UI_ELEMENTS_ASSETS = Path(
    os.environ.get(
        "KORE_UIELEMENTS_ASSETS_DIR",
        str(Path(__file__).resolve().parents[3] / "UIElements" / "assets"),
    )
).resolve()
_SCRAPE_DB_PATH = Path(cfg["data_dir"]) / "scrape_index.db"


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
    root = Path(cfg["data_dir"])
    for manifest_path in root.rglob("manifest.json"):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
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
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _scrape_write_capture_manifest(capture_id: str, manifest: dict) -> bool:
    manifest_path = _scrape_capture_manifest_path(capture_id)
    if manifest_path is None:
        return False
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
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
        row["display_title"]   = title or "(untitled page)"
        row["display_preview"] = preview
    return rows


def register_scrape_ui(app: FastAPI) -> None:
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

    @app.get("/ui", include_in_schema=False)
    def route_ui():
        return RedirectResponse("/ui/scrape", status_code=302)

    @app.get("/ui/scrape", response_class=HTMLResponse, include_in_schema=False)
    def scrape_index(request: Request, q: Optional[str] = None, limit: int = 200):
        from app.server import get_status, list_captures
        captures = list_captures()
        status   = get_status()
        chunks   = list_chunks(limit=12, offset=0, capture_id=None)
        results: list[dict] = []
        searched           = bool(q)
        next_url_param     = ""
        if searched:
            next_url_param = request.url.path + (f"?q={q}&limit={limit}" if q else f"?limit={limit}")
            results        = search_chunks(q=q or "", limit=limit, capture_id=None)
        return templates.TemplateResponse(
            request,
            "scrape_index.html",
            {
                "captures":        captures,
                "status":          status,
                "chunks":          chunks,
                "q":               q or "",
                "limit":           limit,
                "searched":        searched,
                "results":         results,
                "next_url_param":  next_url_param.replace("&", "%26").replace("?", "%3F").replace("=", "%3D"),
            },
        )

    @app.post("/ui/scrape/start", include_in_schema=False)
    def scrape_start(url: str = Form(...), depth: int = Form(0), download_non_html: bool = Form(False)):
        from app.server import CaptureRequest, route_create_capture
        payload = CaptureRequest(url=url, depth=depth, download_non_html=download_non_html)
        created = route_create_capture(payload)
        capture_id = str(created.get("id") or "")
        return RedirectResponse(url=f"/ui/scrape/{capture_id}", status_code=303)

    @app.get("/ui/scrape/{capture_id}", response_class=HTMLResponse, include_in_schema=False)
    def scrape_capture(request: Request, capture_id: str):
        from app.server import get_capture
        try:
            capture = get_capture(capture_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Capture not found") from exc
        chunks = _annotate_scrape_capture_chunks(list_chunks(limit=100, offset=0, capture_id=capture_id))
        return templates.TemplateResponse(request, "scrape_capture.html", {"capture": capture, "chunks": chunks})

    @app.get("/ui/scrape/{capture_id}/json", include_in_schema=False)
    def scrape_capture_json(capture_id: str):
        from app.server import get_capture
        try:
            return JSONResponse(get_capture(capture_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Capture not found") from exc

    @app.get("/ui/scrape/search", response_class=HTMLResponse, include_in_schema=False)
    def scrape_search(
        request:    Request,
        q:          Optional[str] = None,
        capture_id: Optional[str] = None,
        limit:      int           = 20,
    ):
        from app.server import list_captures
        captures = list_captures()
        results: list[dict] = []
        searched           = bool(q)
        if searched:
            results = search_chunks(q=q or "", limit=limit, capture_id=capture_id)
            for chunk in results:
                target = "/ui/scrape"
                if capture_id:
                    target = f"/ui/scrape/{capture_id}"
                chunk["next_url_param"] = target.replace("/", "%2F").replace("?", "%3F").replace("=", "%3D").replace("&", "%26")
        return templates.TemplateResponse(
            request,
            "scrape_search.html",
            {
                "captures":   captures,
                "results":    results,
                "q":          q or "",
                "capture_id": capture_id or "",
                "limit":      limit,
                "searched":   searched,
            },
        )

    @app.get("/ui/scrape/chunks/{chunk_id}", response_class=HTMLResponse, include_in_schema=False)
    def scrape_chunk(request: Request, chunk_id: int, next: Optional[str] = None):
        chunk = get_chunk(chunk_id)
        if chunk is None:
            raise HTTPException(status_code=404, detail="Chunk not found")
        next_url = next or f"/ui/scrape/{chunk.get('capture_id', '')}"
        return templates.TemplateResponse(request, "scrape_chunk.html", {"chunk": chunk, "next_url": next_url})

    @app.post("/ui/scrape/chunks/{chunk_id}/delete", include_in_schema=False)
    def scrape_chunk_delete(chunk_id: int, next: str = Form("/ui/scrape")):
        deleted = delete_chunk(chunk_id)
        if not deleted:
            deleted = _scrape_delete_chunk_local(chunk_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Chunk not found")
        target = next.strip() or "/ui/scrape"
        return RedirectResponse(url=target, status_code=303)

    @app.get("/ui/scrape/chunks/{chunk_id}/delete", include_in_schema=False)
    def scrape_chunk_delete_get(chunk_id: int, next: str = "/ui/scrape"):
        deleted = delete_chunk(chunk_id)
        if not deleted:
            deleted = _scrape_delete_chunk_local(chunk_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Chunk not found")
        target = next.strip() or "/ui/scrape"
        return RedirectResponse(url=target, status_code=303)

    @app.get("/ui/scrape/{capture_id}/delete-content", include_in_schema=False)
    def scrape_capture_delete_content(capture_id: str, next: Optional[str] = None):
        if not _scrape_delete_capture_content_local(capture_id):
            raise HTTPException(status_code=404, detail="Capture not found")
        manifest = _scrape_read_capture_manifest(capture_id)
        if _scrape_capture_fully_deleted(manifest):
            _scrape_delete_capture_record_local(capture_id)
            return RedirectResponse(url="/ui/scrape", status_code=303)
        return RedirectResponse(url=(next or f"/ui/scrape/{capture_id}"), status_code=303)

    @app.get("/ui/scrape/{capture_id}/delete-chunks", include_in_schema=False)
    def scrape_capture_delete_chunks(capture_id: str, next: Optional[str] = None):
        if not _scrape_delete_capture_chunks_local(capture_id):
            raise HTTPException(status_code=404, detail="Capture not found")
        manifest = _scrape_read_capture_manifest(capture_id)
        if _scrape_capture_fully_deleted(manifest):
            _scrape_delete_capture_record_local(capture_id)
            return RedirectResponse(url="/ui/scrape", status_code=303)
        return RedirectResponse(url=(next or f"/ui/scrape/{capture_id}"), status_code=303)

    @app.get("/ui/scrape/files/{capture_id}/{file_path:path}", include_in_schema=False)
    def scrape_capture_file(capture_id: str, file_path: str):
        from app.server import route_capture_file
        return route_capture_file(capture_id, file_path)
