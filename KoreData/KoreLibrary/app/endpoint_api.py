import html as _html
import json as _json
import re as _re
import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import Optional
from urllib.parse import quote as _urlquote, urlparse as _urlparse, unquote as _urlunquote

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import cfg
from app.database import (
    COMPLETENESS_FIELDS,
    add_book,
    backfill_sentence_index,
    delete_book,
    get_book,
    get_book_chunk,
    get_book_sentences,
    get_sentence,
    get_status,
    list_catalogs,
    list_books,
    list_incomplete,
    move_book,
    rebuild_sentence_index,
    search_books,
    set_sentence_deleted,
    title_exists,
    update_book,
    update_book_body,
)
from app.chroma_index import semantic_search
from app.endpoint_ui import repair_kore_anchors


class BookCreate(BaseModel):
    title: str
    body: Optional[str]      = None
    author: Optional[str]    = None
    year: Optional[int]      = None
    language: Optional[str]  = None
    genre: Optional[str]     = None
    notes: Optional[str]     = None
    source: Optional[str]    = None
    source_id: Optional[str] = None
    catalog: Optional[str]   = None


class BookUpdate(BaseModel):
    title: Optional[str]     = None
    body: Optional[str]      = None
    author: Optional[str]    = None
    year: Optional[int]      = None
    language: Optional[str]  = None
    genre: Optional[str]     = None
    notes: Optional[str]     = None
    source: Optional[str]    = None
    source_id: Optional[str] = None


class BookMoveRequest(BaseModel):
    catalog: str


class SentenceToggleRequest(BaseModel):
    deleted: bool


class KiwixImportRequest(BaseModel):
    zim_name: str
    title: str
    article_url: Optional[str] = None
    author: Optional[str]      = None
    year: Optional[int]        = None
    language: Optional[str]    = None
    kiwix_url: Optional[str]   = None
    catalog: Optional[str]     = None


class KiwixViewerImportRequest(BaseModel):
    viewer_url: str
    kiwix_url: Optional[str] = None
    language: str            = "en"
    catalog: Optional[str]   = None


class KiwixViewerBatchRequest(BaseModel):
    urls: list[str]
    language: str            = "en"
    kiwix_url: Optional[str] = None
    catalog: Optional[str]   = None


_OPDS_NS = "http://www.w3.org/2005/Atom"


def _parse_gutenberg_html(html: str) -> dict:
    from markdownify import markdownify as _md

    soup = BeautifulSoup(html, "html.parser")

    author: Optional[str] = None
    year: Optional[int]   = None
    title: Optional[str]  = None
    genre: Optional[str]  = None

    for meta in soup.find_all("meta"):
        name    = (meta.get("name") or meta.get("property") or "").lower()
        content = meta.get("content", "")
        if not content:
            continue
        if name in ("author", "dc.creator", "citation_author"):
            author = content
        elif name in ("dc.date", "date", "citation_date") and not year:
            try:
                year = int(content[:4])
            except ValueError:
                pass
        elif name in ("dc.title", "title", "citation_title"):
            title = content
        elif name == "dc.subject" and not genre:
            genre = content

    if not title:
        tag = soup.find("title")
        if tag and tag.string:
            t = tag.string.strip()
            t = _re.sub(r'\s*[\|\-â€”]\s*.{3,60}$', '', t).strip()
            if t:
                title = t

    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True) or None

    for tag in soup.select(
        "nav, header, footer, .noprint, #table_of_contents, "
        ".mw-editsection, script, style, noscript, img, figure, .thumbinner"
    ):
        tag.decompose()

    anchor_map = {}
    for a in soup.find_all("a"):
        anchor_id = a.get("id") or a.get("name")
        if anchor_id and not a.get("href") and not a.get_text(strip=True):
            key = f"KANCHORX{len(anchor_map)}X"
            anchor_map[key] = anchor_id
            a.replace_with(key)

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("http://", "https://", "#")):
            continue
        a.unwrap()

    content = soup.find(id="mw-content-text") or soup.find("article") or soup.find("body") or soup
    body_md = _md(str(content), heading_style="ATX", bullets="-", strip=["img"])

    for key, anchor_id in anchor_map.items():
        body_md = body_md.replace(key, f'<span id="{anchor_id}"></span>')

    body_md = _re.sub(r'\n{3,}', '\n\n', body_md).strip()
    return {"body": body_md, "author": author, "year": year, "title": title, "genre": genre}


def _extract_link(el) -> str:
    if el.text and el.text.strip():
        return el.text.strip()
    href = el.get("href", "").strip()
    if href:
        return href
    return ""


async def _kiwix_search_url(host: str, zim: str, title: str) -> tuple[Optional[str], Optional[str]]:
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            r = await client.get(
                f"{host}/search",
                params={"books.name": zim, "pattern": title, "format": "xml", "pageLength": 5},
            )
        if r.status_code != 200:
            return None, f"Kiwix search returned HTTP {r.status_code}"
        root = ET.fromstring(r.text)
        for item in list(root.iter("item")) + list(root.iter("entry")) + list(root.iter("result")):
            item_title = (item.findtext("title") or "").strip()
            link_el    = item.find("link")
            raw        = _extract_link(link_el) if link_el is not None else ""
            if not raw:
                raw = (item.findtext("guid") or "").strip()
            if raw and item_title.lower() == title.lower():
                path = _urlparse(raw).path
                if path:
                    return path, None
        return None, f"No exact title match in Kiwix search XML (response: {r.text[:2000]!r})"
    except ET.ParseError as exc:
        return None, f"Kiwix search XML parse error: {exc}"
    except Exception as exc:
        return None, f"Kiwix search error: {exc}"


def _fallback_title_from_url(url: str) -> str:
    up   = _urlparse(url)
    tail = (up.path.rstrip("/").split("/")[-1] if up.path else "").strip()
    if not tail:
        return "Imported Page"
    tail = tail.rsplit(".", 1)[0]
    tail = _urlunquote(tail).replace("_", " ").replace("-", " ").strip()
    return tail or "Imported Page"


async def _fetch_and_import_viewer_url(viewer_url: str, language: str, kiwix_url: Optional[str], catalog: Optional[str]) -> dict:
    result: dict = {"url": viewer_url, "status": "error", "title": None, "id": None, "detail": None}
    try:
        up       = _urlparse(viewer_url)
        fragment = up.fragment
        if fragment and "/" in fragment:
            host         = kiwix_url or f"{up.scheme}://{up.netloc}"
            slash        = fragment.index("/")
            zim          = fragment[:slash]
            article_path = fragment[slash + 1:]
            content_url  = f"{host}/content/{zim}/{article_path}"
            source       = "kiwix"
            title_hint   = _urlunquote(article_path.rsplit(".", 1)[0].replace("_", " "))
        elif up.scheme in ("http", "https") and up.netloc:
            content_url = viewer_url
            source      = "web"
            title_hint  = _fallback_title_from_url(viewer_url)
        else:
            result["detail"] = "URL must be either a Kiwix viewer URL or a direct http(s) page URL"
            return result

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                r = await client.get(content_url)
            if r.status_code == 404 and source == "kiwix":
                result["detail"] = f"Not found in Kiwix: {content_url}"
                return result
            r.raise_for_status()
        except httpx.HTTPError as exc:
            result["detail"] = f"Page fetch failed: {exc}"
            return result

        parsed          = _parse_gutenberg_html(r.text)
        title           = parsed["title"] or title_hint
        result["title"] = title

        if title_exists(title, catalog=catalog):
            result["status"] = "exists"
            result["detail"] = f"Already imported: {title!r}"
            return result

        book = add_book(
            title     = title,
            body      = parsed["body"],
            author    = parsed["author"],
            year      = parsed["year"],
            language  = language,
            genre     = parsed["genre"],
            source    = source,
            source_id = viewer_url,
            catalog   = catalog,
        )
        result["status"] = "ok"
        result["id"]     = book.get("route_id") or book["id"]
    except Exception as exc:
        result["detail"] = str(exc)
    return result


def register_library_api(app: FastAPI) -> None:
    @app.get("/api/catalogs", summary="List available catalogs")
    @app.get("/catalogs", include_in_schema=False)
    def route_list_catalogs():
        return {"catalogs": list_catalogs()}

    @app.get("/api/books", summary="List all books (metadata only)")
    @app.get("/books", include_in_schema=False)
    def route_list_books(limit: int = 100, offset: int = 0, catalog: Optional[str] = None):
        try:
            return list_books(limit=limit, offset=offset, catalog=catalog)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/books/{book_id:path}/chunk", summary="Get a character-slice of a book body")
    @app.get("/books/{book_id:path}/chunk", include_in_schema=False)
    def route_get_book_chunk(book_id: str, offset: int = 0, length: int = 4096):
        try:
            result = get_book_chunk(book_id, offset_chars=offset, length_chars=length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="Book not found")
        return result

    @app.get("/api/books/{book_id:path}", summary="Get a single book with full body")
    @app.get("/books/{book_id:path}", include_in_schema=False)
    def route_get_book(book_id: str):
        try:
            book = get_book(book_id, include_body=True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if book is None:
            raise HTTPException(status_code=404, detail="Book not found")
        return book

    @app.post("/api/books", status_code=201, summary="Add a new book")
    @app.post("/books", status_code=201, include_in_schema=False)
    def route_add_book(data: BookCreate):
        try:
            return add_book(
                title     = data.title,
                body      = data.body,
                author    = data.author,
                year      = data.year,
                language  = data.language,
                genre     = data.genre,
                notes     = data.notes,
                source    = data.source,
                source_id = data.source_id,
                catalog   = data.catalog,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/books/{book_id:path}", summary="Update book metadata or body")
    @app.patch("/books/{book_id:path}", include_in_schema=False)
    def route_update_book(book_id: str, data: BookUpdate):
        if get_book(book_id, include_body=False) is None:
            raise HTTPException(status_code=404, detail="Book not found")
        try:
            return update_book(book_id, data.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/books/{book_id:path}/repair-anchors", summary="Repair broken anchor spans in stored body")
    @app.post("/books/{book_id:path}/repair-anchors", include_in_schema=False)
    def route_repair_anchors(book_id: str):
        book = get_book(book_id, include_body=True)
        if book is None:
            raise HTTPException(status_code=404, detail="Book not found")
        body     = book.get("body") or ""
        repaired = repair_kore_anchors(body)
        if repaired == body:
            return {"repaired": False, "message": "Nothing to repair"}
        try:
            updated = update_book_body(book_id, repaired)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"repaired": True, "book": updated}

    @app.post("/api/books/{book_id:path}/move", summary="Move a book to a different catalog")
    @app.post("/books/{book_id:path}/move", include_in_schema=False)
    def route_move_book(book_id: str, data: BookMoveRequest):
        try:
            new_book = move_book(book_id, dest_catalog=data.catalog)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if new_book is None:
            raise HTTPException(status_code=404, detail="Book not found")
        return new_book

    @app.delete("/api/books/{book_id:path}", status_code=204, summary="Delete a book")
    @app.delete("/books/{book_id:path}", status_code=204, include_in_schema=False)
    def route_delete_book(book_id: str):
        try:
            deleted = delete_book(book_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="Book not found")
        return JSONResponse(status_code=204, content=None)

    @app.get("/api/search", summary="Search books by full-text query and/or metadata filters")
    @app.get("/search", include_in_schema=False)
    def route_search(
        q: Optional[str]         = None,
        author: Optional[str]    = None,
        title: Optional[str]     = None,
        year: Optional[int]      = None,
        language: Optional[str]  = None,
        genre: Optional[str]     = None,
        limit: int               = 50,
        offset: int              = 0,
        catalog: Optional[str]   = None,
        catalogs: Optional[str]  = None,
        scope: Optional[str]     = None,
        mode: str                = "keyword",
        min_match: float         = 0.4,
    ):
        if not any([q, author, title, year, language, genre]):
            raise HTTPException(
                status_code=400,
                detail="Provide at least one search parameter: q, author, title, year, language, or genre",
            )
        parsed_catalogs = [value.strip() for value in (catalogs or "").split(",") if value.strip()] or None
        try:
            search_mode = "semantic" if str(mode).strip().lower() == "semantic" else "keyword"
            if search_mode == "semantic":
                if not q:
                    raise HTTPException(status_code=400, detail="Semantic search requires q")
                results = semantic_search(catalog, q, limit=limit + offset, min_match=min_match)
                if author:
                    results = [item for item in results if author.lower() in str(item.get("author") or "").lower()]
                if title:
                    results = [item for item in results if title.lower() in str(item.get("title") or "").lower()]
                if year is not None:
                    results = [item for item in results if item.get("year") == year]
                if language:
                    results = [item for item in results if str(item.get("language") or "").lower() == language.lower()]
                if genre:
                    results = [item for item in results if genre.lower() in str(item.get("genre") or "").lower()]
                return results[offset: offset + limit]
            return search_books(
                q         = q,
                author    = author,
                title     = title,
                year      = year,
                language  = language,
                genre     = genre,
                limit     = limit,
                offset    = offset,
                catalog   = catalog,
                catalogs  = parsed_catalogs,
                fts_scope = scope if scope in ("all", "metadata") else "all",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/semantic-search", summary="Semantic search across indexed library sentences")
    def route_semantic_search(q: str, catalog: Optional[str] = None, limit: int = 50, min_match: float = 0.4):
        try:
            return semantic_search(catalog, q, limit=limit, min_match=min_match)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/books/{book_id:path}/sentences", summary="List indexed sentences for a single book")
    def route_book_sentences(book_id: str, include_deleted: bool = False):
        try:
            return get_book_sentences(book_id, include_deleted=include_deleted)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/catalogs/{catalog}/sentences/{sentence_id}", summary="Fetch a single indexed sentence")
    def route_sentence(catalog: str, sentence_id: int):
        try:
            row = get_sentence(catalog, sentence_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if row is None:
            raise HTTPException(status_code=404, detail="Sentence not found")
        return row

    @app.post("/api/catalogs/{catalog}/sentences/backfill", summary="Backfill sentence rows for a catalog")
    def route_backfill_catalog_sentences(catalog: str):
        try:
            return backfill_sentence_index(catalog)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/catalogs/{catalog}/sentences/rebuild", summary="Rebuild sentence rows for a catalog or one book")
    def route_rebuild_catalog_sentences(catalog: str, book_id: Optional[str] = None):
        try:
            return rebuild_sentence_index(catalog, book_id=book_id)
        except ValueError as exc:
            message = str(exc)
            status  = 404 if "not found" in message.lower() else 400
            raise HTTPException(status_code=status, detail=message) from exc

    @app.post("/api/catalogs/{catalog}/sentences/{sentence_id}/deleted", summary="Mark one sentence deleted or active")
    def route_set_sentence_deleted(catalog: str, sentence_id: int, data: SentenceToggleRequest):
        try:
            return set_sentence_deleted(catalog, sentence_id, data.deleted)
        except ValueError as exc:
            message = str(exc)
            status  = 404 if "not found" in message.lower() else 400
            raise HTTPException(status_code=status, detail=message) from exc

    @app.get("/api/incomplete", summary="List books with missing metadata fields")
    @app.get("/incomplete", include_in_schema=False)
    def route_incomplete(fields: Optional[str] = None, catalog: Optional[str] = None, catalogs: Optional[str] = None):
        parsed_fields = None
        if fields:
            parsed_fields = [f.strip() for f in fields.split(",") if f.strip() in COMPLETENESS_FIELDS]
            if not parsed_fields:
                raise HTTPException(status_code=400, detail=f"Valid fields are: {', '.join(COMPLETENESS_FIELDS)}")
        parsed_catalogs = [value.strip() for value in (catalogs or "").split(",") if value.strip()] or None
        try:
            return list_incomplete(fields=parsed_fields, catalog=catalog, catalogs=parsed_catalogs)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/kiwix/inventory", summary="List available ZIM files from Kiwix OPDS catalog")
    @app.get("/kiwix/inventory", include_in_schema=False)
    async def kiwix_inventory(kiwix_url: Optional[str] = None):
        url = kiwix_url or cfg.get("kiwix_url", "")
        if not url:
            raise HTTPException(status_code=503, detail="kiwix_url not configured")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{url}/catalog/v2/entries", params={"count": -1})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Kiwix unreachable: {exc}") from exc

        try:
            root = ET.fromstring(r.text)
        except ET.ParseError as exc:
            raise HTTPException(status_code=502, detail=f"Kiwix OPDS parse error: {exc}") from exc

        books = []
        for entry in root.findall(f"{{{_OPDS_NS}}}entry"):
            title     = entry.findtext(f"{{{_OPDS_NS}}}title", default="") or ""
            author_el = entry.find(f"{{{_OPDS_NS}}}author/{{{_OPDS_NS}}}name")
            author    = author_el.text if author_el is not None else ""
            zim_name  = ""
            for link in entry.findall(f"{{{_OPDS_NS}}}link"):
                if link.get("type") == "text/html":
                    parts    = link.get("href", "").strip("/").split("/")
                    zim_name = parts[1] if len(parts) > 1 and parts[0] == "content" else parts[0]
                    break
            if zim_name:
                books.append({"name": zim_name, "title": title, "author": author})
        return {"books": books}

    @app.get("/api/kiwix/search", summary="Full-text search within a Kiwix ZIM")
    @app.get("/kiwix/search", include_in_schema=False)
    async def kiwix_search(zim: str, q: str, count: int = 100, kiwix_url: Optional[str] = None):
        url = kiwix_url or cfg.get("kiwix_url", "")
        if not url:
            raise HTTPException(status_code=503, detail="kiwix_url not configured")
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(
                    f"{url}/search",
                    params={"books.name": zim, "pattern": q, "format": "xml", "pageLength": count},
                )
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Kiwix unreachable: {exc}") from exc

        try:
            root = ET.fromstring(r.text)
        except ET.ParseError as exc:
            raise HTTPException(status_code=502, detail=f"Kiwix search parse error: {exc}") from exc

        results = []
        for channel in root.iter("channel"):
            for item in channel.findall("item"):
                title       = (item.findtext("title") or "").strip()
                snippet     = (item.findtext("description") or item.findtext("snippet") or "").strip()
                snippet     = _re.sub(r"<[^>]+>", " ", _html.unescape(snippet)).strip()
                link_el     = item.find("link")
                url_raw     = _extract_link(link_el) if link_el is not None else ""
                article_url = _urlparse(url_raw).path if url_raw else None
                if title:
                    results.append({"label": title, "value": title, "snippet": snippet, "url": article_url})
        if not results:
            for item in root.iter("result"):
                title       = (item.findtext("title") or "").strip()
                snippet     = (item.findtext("snippet") or item.findtext("description") or "").strip()
                snippet     = _re.sub(r"<[^>]+>", " ", _html.unescape(snippet)).strip()
                link_el     = item.find("link")
                url_raw     = _extract_link(link_el) if link_el is not None else ""
                article_url = _urlparse(url_raw).path if url_raw else None
                if title:
                    results.append({"label": title, "value": title, "snippet": snippet, "url": article_url})
        return results

    @app.get("/api/kiwix/suggest", summary="Suggest article titles from a Kiwix ZIM")
    @app.get("/kiwix/suggest", include_in_schema=False)
    async def kiwix_suggest(zim: str, pattern: str = "", count: int = 50, kiwix_url: Optional[str] = None):
        url = kiwix_url or cfg.get("kiwix_url", "")
        if not url:
            raise HTTPException(status_code=503, detail="kiwix_url not configured")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{url}/suggest", params={"content": zim, "term": pattern, "count": count})
            r.raise_for_status()
            items   = r.json()
            cleaned = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                url_field = item.get("url", "")
                raw_label = item.get("label") or item.get("value") or ""
                if "/search?" in url_field or not item.get("value") or _re.search(r"containing\s+['\"]", raw_label, _re.IGNORECASE):
                    continue
                label        = _html.unescape(item.get("label") or item.get("value") or "")
                label        = _re.sub(r"<[^>]+>", "", label).strip()
                value        = _html.unescape(item.get("value") or label)
                value        = _re.sub(r"<[^>]+>", "", value).strip()
                item["label"] = label
                item["value"] = value
                if not item.get("url"):
                    item["url"] = f"/content/{zim}/A/{_urlquote(value.replace(' ', '_'), safe='')}"
                cleaned.append(item)
            return cleaned
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Kiwix unreachable: {exc}") from exc

    @app.get("/api/kiwix/catalog", summary="Return full Gutenberg ZIM catalog grouped by author")
    @app.get("/kiwix/catalog", include_in_schema=False)
    async def kiwix_catalog(zim: str, author: Optional[str] = None, kiwix_url: Optional[str] = None):
        url = kiwix_url or cfg.get("kiwix_url", "")
        if not url:
            raise HTTPException(status_code=503, detail="kiwix_url not configured")
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get(f"{url}/content/{zim}/full_by_popularity.js")
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Kiwix unreachable: {exc}") from exc

        m = _re.search(r"var\s+json_data\s*=\s*(\[.*?\])\s*;?\s*$", r.text, _re.DOTALL)
        if not m:
            raise HTTPException(status_code=502, detail="Could not locate json_data in catalog JS file")
        try:
            raw = _json.loads(m.group(1))
        except _json.JSONDecodeError as exc:
            raise HTTPException(status_code=502, detail=f"Catalog JSON parse error: {exc}") from exc

        author_lower = author.lower() if author else None
        by_author: dict[str, list[dict]] = defaultdict(list)
        total = 0
        for entry in raw:
            if not isinstance(entry, list) or len(entry) < 4:
                continue
            title       = str(entry[0]).strip()
            author_name = str(entry[1]).strip()
            gut_id      = entry[3]
            if author_lower and author_lower not in author_name.lower():
                continue
            slug         = title.replace("/", "-")[:230] + "." + str(gut_id)
            article_path = f"/content/{zim}/{_urlquote(slug)}"
            viewer_url   = f"{url}/viewer#{zim}/{_urlquote(slug)}"
            by_author[author_name].append(
                {
                    "title":        title,
                    "gutenberg_id": gut_id,
                    "article_path": article_path,
                    "viewer_url":   viewer_url,
                }
            )
            total += 1

        return {
            "total": total,
            "authors": [{"author": a, "books": sorted(bks, key=lambda x: x["title"])} for a, bks in sorted(by_author.items())],
        }

    @app.post("/api/kiwix/import", status_code=201, summary="Import a single book from Kiwix by title")
    @app.post("/api/import/kiwix", status_code=201, include_in_schema=False)
    @app.post("/import/kiwix", status_code=201, include_in_schema=False)
    async def import_kiwix(data: KiwixImportRequest):
        url = data.kiwix_url or cfg.get("kiwix_url", "")
        if not url:
            raise HTTPException(status_code=503, detail="kiwix_url not configured")
        if title_exists(data.title, catalog=data.catalog):
            raise HTTPException(status_code=409, detail=f"Already imported: {data.title!r}")

        kiwix_path_id = data.title.replace(" ", "_")
        if data.article_url:
            article_url = url.rstrip("/") + data.article_url
        else:
            encoded_path = _urlquote(kiwix_path_id, safe="")
            article_url  = f"{url}/content/{data.zim_name}/A/{encoded_path}"
        search_err: Optional[str] = None
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                r = await client.get(article_url)
                if r.status_code == 404 and data.article_url and "/A/" in data.article_url:
                    pfx, _, title_part = data.article_url.partition("/A/")
                    alt = url.rstrip("/") + pfx + "/A/" + _urlquote(title_part, safe='-_~')
                    if alt != article_url:
                        r = await client.get(alt)
                if r.status_code == 404 and data.article_url and "/A/" in data.article_url:
                    pfx, _, title_part = data.article_url.partition("/A/")
                    alt2 = url.rstrip("/") + pfx + "/A/" + _urlquote(title_part.replace("_", " "), safe='-~.')
                    if alt2 not in (article_url, alt):
                        r = await client.get(alt2)
                if r.status_code == 404:
                    resolved, search_err = await _kiwix_search_url(url, data.zim_name, data.title)
                    if resolved:
                        r = await client.get(url.rstrip("/") + resolved)
            if r.status_code == 404:
                detail = f"Not found in Kiwix: {data.title!r}"
                if search_err:
                    detail += f" (search fallback: {search_err})"
                raise HTTPException(status_code=404, detail=detail)
            r.raise_for_status()
        except HTTPException:
            raise
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Kiwix fetch failed: {exc}") from exc

        parsed = _parse_gutenberg_html(r.text)
        try:
            return add_book(
                title     = data.title,
                body      = parsed["body"],
                author    = data.author or parsed["author"],
                year      = data.year or parsed["year"],
                language  = data.language,
                genre     = parsed["genre"],
                source    = "kiwix",
                source_id = data.article_url,
                catalog   = data.catalog,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/kiwix/import/viewer", status_code=201, summary="Import a Kiwix article by its viewer URL")
    @app.post("/api/import/kiwix/viewer", status_code=201, include_in_schema=False)
    @app.post("/import/kiwix/viewer", status_code=201, include_in_schema=False)
    async def import_kiwix_viewer(data: KiwixViewerImportRequest):
        r = await _fetch_and_import_viewer_url(data.viewer_url, data.language, data.kiwix_url, data.catalog)
        if r["status"] == "error":
            raise HTTPException(status_code=502, detail=r["detail"])
        if r["status"] == "exists":
            raise HTTPException(status_code=409, detail=r["detail"])
        return get_book(r["id"], include_body=False)

    @app.post("/api/kiwix/import/viewer/batch", summary="Batch-import Kiwix articles from a list of viewer URLs")
    @app.post("/api/import/kiwix/viewer/batch", include_in_schema=False)
    @app.post("/import/kiwix/viewer/batch", include_in_schema=False)
    async def import_kiwix_viewer_batch(data: KiwixViewerBatchRequest):
        results = []
        for raw in data.urls:
            url = raw.strip()
            if not url or url.startswith('#'):
                continue
            results.append(await _fetch_and_import_viewer_url(url, data.language, data.kiwix_url, data.catalog))
        ok    = sum(1 for r in results if r["status"] == "ok")
        exist = sum(1 for r in results if r["status"] == "exists")
        err   = sum(1 for r in results if r["status"] == "error")
        return {"results": results, "summary": {"ok": ok, "exists": exist, "error": err}}

    @app.get("/status", summary="Server status and database statistics")
    def route_status(catalog: Optional[str] = None):
        try:
            stats = get_status(catalog=catalog)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"service": "KoreLibrary", **stats}
