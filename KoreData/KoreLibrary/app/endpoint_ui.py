import os
import re as _re
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader
from config import get_suite_urls_map

from app.database import (
    COMPLETENESS_FIELDS,
    add_book,
    delete_book,
    get_book,
    get_status,
    list_books,
    list_catalogs,
    list_incomplete,
    move_book,
    search_books,
    update_book,
    update_book_body,
)
from app.chroma_index import chroma_available, semantic_search

_LIBRARY_UI_ROOT = Path(
    os.environ.get(
        "KORE_KORELIBRARY_UI_DIR",
        str(Path(__file__).resolve().parents[3] / "KoreUI" / "KoreData" / "KoreLibrary"),
    )
).resolve()
TEMPLATES_DIR = Path(
    os.environ.get(
        "KORE_KORELIBRARY_TEMPLATES_DIR",
        str(_LIBRARY_UI_ROOT / "templates"),
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


def _parse_year(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid year: {value!r}")


def repair_kore_anchors(body: str) -> str:
    toc_ids: list[str] = []
    seen: set[str]     = set()
    for anchor_id in _re.findall(r'\[[^\]]*\]\(#([^)]+)\)', body):
        if anchor_id not in seen:
            seen.add(anchor_id)
            toc_ids.append(anchor_id)

    placeholders = _re.findall(r'KORE\\_ANCHOR\\_\d+\\_END', body)
    if not placeholders:
        return body
    if len(placeholders) != len(toc_ids):
        return body

    result = body
    for placeholder, anchor_id in zip(placeholders, toc_ids):
        result = result.replace(placeholder, f'<span id="{anchor_id}"></span>', 1)
    return result


def register_library_ui(app: FastAPI) -> None:
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

    @app.get("/", include_in_schema=False)
    def route_root():
        return RedirectResponse("/ui/library")

    @app.get("/ui", include_in_schema=False)
    def route_ui():
        return RedirectResponse("/ui/library")

    @app.get("/ui/library", response_class=HTMLResponse, include_in_schema=False)
    def route_ui_library_index(request: Request, limit: int = 200, offset: int = 0, catalog: Optional[str] = None):
        try:
            books    = list_books(limit=limit, offset=offset, catalog=catalog)
            status   = get_status(catalog=catalog)
            catalogs = list_catalogs()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return templates.TemplateResponse(
            request,
            "library_index.html",
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

    @app.get("/ui/library/incomplete", response_class=HTMLResponse, include_in_schema=False)
    def route_ui_library_incomplete(request: Request, fields: Optional[str] = None, catalog: Optional[str] = None):
        parsed_fields = None
        if fields:
            parsed_fields = [f.strip() for f in fields.split(",") if f.strip() in COMPLETENESS_FIELDS]
            if not parsed_fields:
                raise HTTPException(status_code=400, detail=f"Valid fields are: {', '.join(COMPLETENESS_FIELDS)}")
        try:
            books    = list_incomplete(fields=parsed_fields, catalog=catalog)
            catalogs = list_catalogs()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return templates.TemplateResponse(
            request,
            "library_index.html",
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

    @app.get("/ui/library/search", response_class=HTMLResponse, include_in_schema=False)
    def route_ui_library_search(
        request: Request,
        q: Optional[str]        = None,
        author: Optional[str]   = None,
        title: Optional[str]    = None,
        year: Optional[str]     = None,
        language: Optional[str] = None,
        genre: Optional[str]    = None,
        catalog: Optional[str]  = None,
        limit: int              = 50,
        offset: int             = 0,
        mode: str               = "keyword",
        min_match: float        = 0.4,
    ):
        year_int    = _parse_year(year)
        search_mode = "semantic" if str(mode).strip().lower() == "semantic" else "keyword"
        searched    = any([q, author, title, year_int, language, genre])
        results     = []
        search_error = ""
        if searched:
            try:
                if search_mode == "semantic" and q:
                    if not chroma_available():
                        search_error = "Semantic search is unavailable because chromadb is not installed in this service environment."
                    else:
                        results = semantic_search(catalog, q, limit=limit + offset, min_match=min_match)
                        if author:
                            results = [item for item in results if author.lower() in str(item.get("author") or "").lower()]
                        if title:
                            results = [item for item in results if title.lower() in str(item.get("title") or "").lower()]
                        if year_int is not None:
                            results = [item for item in results if item.get("year") == year_int]
                        if language:
                            results = [item for item in results if str(item.get("language") or "").lower() == language.lower()]
                        if genre:
                            results = [item for item in results if genre.lower() in str(item.get("genre") or "").lower()]
                        results = results[offset: offset + limit]
                else:
                    results = search_books(
                        q         = q,
                        author    = author,
                        title     = title,
                        year      = year_int,
                        language  = language,
                        genre     = genre,
                        limit     = limit,
                        offset    = offset,
                        catalog   = catalog,
                        catalogs  = None,
                        fts_scope = "all",
                    )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        catalogs = list_catalogs()
        return templates.TemplateResponse(
            request,
            "library_search.html",
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
                "mode":     search_mode,
                "min_match": max(0.0, min(1.0, float(min_match or 0.0))),
                "catalogs": catalogs,
                "search_error": search_error,
            },
        )

    @app.get("/ui/library/import", response_class=HTMLResponse, include_in_schema=False)
    def route_ui_library_import(request: Request, error: Optional[str] = None):
        return templates.TemplateResponse(
            request,
            "library_import.html",
            {"error": error, "catalogs": list_catalogs()},
        )

    @app.post("/ui/library/import/manual", response_class=HTMLResponse, include_in_schema=False)
    def route_ui_library_import_manual(
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
        year_int = _parse_year(year)
        try:
            book = add_book(
                title    = title,
                body     = body,
                author   = author,
                year     = year_int,
                language = language,
                genre    = genre,
                notes    = notes,
                catalog  = catalog,
            )
        except ValueError as exc:
            return templates.TemplateResponse(
                request,
                "library_import.html",
                {"error": str(exc), "catalogs": list_catalogs()},
                status_code=400,
            )
        book_id = book.get("route_id") or book.get("id")
        return RedirectResponse(url=f"/ui/library/{book_id}", status_code=303)

    @app.get("/ui/library/{book_id:path}/edit", response_class=HTMLResponse, include_in_schema=False)
    def route_ui_library_book_edit(request: Request, book_id: str):
        book = get_book(book_id, include_body=True)
        if book is None:
            raise HTTPException(status_code=404, detail="Book not found")
        return templates.TemplateResponse(
            request,
            "library_edit.html",
            {"book": book, "error": None, "catalogs": list_catalogs()},
        )

    @app.post("/ui/library/{book_id:path}/edit", response_class=HTMLResponse, include_in_schema=False)
    def route_ui_library_book_edit_post(
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
        if get_book(book_id, include_body=False) is None:
            raise HTTPException(status_code=404, detail="Book not found")
        payload: dict = {"title": title}
        if body is not None:
            payload["body"] = body
        if author:
            payload["author"] = author
        year_int = _parse_year(year)
        if year_int is not None:
            payload["year"] = year_int
        if language:
            payload["language"] = language
        if genre:
            payload["genre"] = genre
        if notes:
            payload["notes"] = notes
        if source:
            payload["source"] = source
        try:
            update_book(book_id, payload)
        except ValueError as exc:
            book = get_book(book_id, include_body=True)
            return templates.TemplateResponse(
                request,
                "library_edit.html",
                {"book": book, "error": str(exc), "catalogs": list_catalogs()},
                status_code=400,
            )
        return RedirectResponse(url=f"/ui/library/{book_id}", status_code=303)

    @app.post("/ui/library/{book_id:path}/delete", include_in_schema=False)
    def route_ui_library_book_delete(book_id: str):
        try:
            deleted = delete_book(book_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="Book not found")
        return RedirectResponse(url="/ui/library", status_code=303)

    @app.post("/ui/library/{book_id:path}/move", include_in_schema=False)
    def route_ui_library_book_move(book_id: str, catalog: str = Form(...)):
        try:
            new_book = move_book(book_id, dest_catalog=catalog)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if new_book is None:
            raise HTTPException(status_code=404, detail="Book not found")
        new_id = new_book.get("route_id") or new_book.get("id")
        return RedirectResponse(url=f"/ui/library/{new_id}", status_code=303)

    @app.post("/ui/library/{book_id:path}/repair-anchors", include_in_schema=False)
    def route_ui_library_repair_anchors(book_id: str):
        book = get_book(book_id, include_body=True)
        if book is None:
            raise HTTPException(status_code=404, detail="Book not found")
        body     = book.get("body") or ""
        repaired = repair_kore_anchors(body)
        if repaired != body:
            try:
                update_book_body(book_id, repaired)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(url=f"/ui/library/{book_id}", status_code=303)

    @app.get("/ui/library/{book_id:path}", response_class=HTMLResponse, include_in_schema=False)
    def route_ui_library_book(request: Request, book_id: str):
        book = get_book(book_id, include_body=True)
        if book is None:
            raise HTTPException(status_code=404, detail="Book not found")
        return templates.TemplateResponse(
            request,
            "library_book.html",
            {"book": book, "catalogs": list_catalogs()},
        )
