import os
import re
from pathlib import Path
import sys
import json
from typing import Optional
from urllib.parse import quote

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader
from markupsafe import Markup, escape

_KORECOMMON_PARENT = next((parent for parent in Path(__file__).resolve().parents if (parent / "KoreCommon").is_dir()), None)
if _KORECOMMON_PARENT is not None and str(_KORECOMMON_PARENT) not in sys.path:
    sys.path.insert(0, str(_KORECOMMON_PARENT))

from KoreCommon.service_app import register_suite_config_js
from KoreCommon.service_app import register_ui_elements_assets
from app.database import (
    delete_all_articles,
    delete_article,
    get_backlinks,
    get_links,
    get_status,
    list_articles,
    resolve_article,
    search_articles,
    upsert_article,
)
from app.chroma_index import chroma_available, semantic_search
from app.importers.kiwix import parse_seed_url, run_kiwix_crawl
from app.importers.state import import_lock, import_state, import_stop_event


_REFERENCE_UI_ROOT = Path(
    os.environ.get(
        "KORE_KOREREFERENCE_UI_DIR",
        str(Path(__file__).resolve().parents[3] / "KoreUI" / "KoreData" / "KoreReference"),
    )
).resolve()
TEMPLATES_DIR = Path(
    os.environ.get(
        "KORE_KOREREFERENCE_TEMPLATES_DIR",
        str(_REFERENCE_UI_ROOT / "templates"),
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

_TABLE_MARKER_RE = re.compile(r'<<<TABLE>>>(.*?)<<<ENDTABLE>>>', re.DOTALL)
_WIKILINK_RE     = re.compile(r'\[\[([^\]]+)\]\]')


def _resolve_wikilinks_in_html(html: str, dead_links: set | None = None) -> str:
    def _repl(match: re.Match) -> str:
        inner = match.group(1)
        if "|" in inner:
            display, target = inner.split("|", 1)
        else:
            display = target = inner
        target  = target.strip()
        display = display.strip()
        cls     = ' class="ref-link-dead"' if dead_links and target.lower() in dead_links else ''
        return f'<a href="/ui/reference/{quote(target)}"{cls}>{escape(display)}</a>'

    return _WIKILINK_RE.sub(_repl, html)


def _process_inline(text: str, dead_links: set | None = None) -> str:
    parts: list[str] = []
    last_end         = 0
    for match in _WIKILINK_RE.finditer(text):
        parts.append(str(escape(text[last_end:match.start()])))
        inner = match.group(1)
        if "|" in inner:
            display, target = inner.split("|", 1)
        else:
            display = target = inner
        target  = target.strip()
        display = display.strip()
        cls     = ' class="ref-link-dead"' if dead_links and target.lower() in dead_links else ''
        parts.append(f'<a href="/ui/reference/{quote(target)}"{cls}>{escape(display)}</a>')
        last_end = match.end()
    parts.append(str(escape(text[last_end:])))
    return "".join(parts)


def _render_list_lines(lines: list[str], dead_links: set | None = None) -> str:
    if not lines:
        return ""
    base_indent = len(lines[0]) - len(lines[0].lstrip())
    tag         = "ol" if lines[0].lstrip().startswith("# ") else "ul"
    html        = [f"<{tag}>"]
    index       = 0
    while index < len(lines):
        indent = len(lines[index]) - len(lines[index].lstrip())
        if indent < base_indent:
            break
        if indent == base_indent:
            item_text = _process_inline(lines[index].lstrip()[2:].strip(), dead_links)
            child_idx = index + 1
            children: list[str] = []
            while child_idx < len(lines) and (len(lines[child_idx]) - len(lines[child_idx].lstrip())) > base_indent:
                children.append(lines[child_idx])
                child_idx += 1
            child_html = _render_list_lines(children, dead_links) if children else ""
            html.append(f"<li>{item_text}{child_html}</li>")
            index = child_idx
        else:
            index += 1
    html.append(f"</{tag}>")
    return "".join(html)


def _process_wikitext(text: str, dead_links: set | None = None) -> str:
    text       = text.replace("\r\n", "\n").replace("\r", "\n")
    html_parts: list[str] = []
    for block in re.split(r"\n{2,}", text):
        block = block.strip()
        if not block:
            continue
        lines = [line for line in block.split("\n") if line.strip()]
        if lines and all(line.lstrip().startswith(("* ", "# ")) for line in lines):
            html_parts.append(_render_list_lines(lines, dead_links))
        else:
            inner = _process_inline("\n".join(lines), dead_links)
            inner = inner.replace("\n", "<br>")
            html_parts.append(f"<p>{inner}</p>")
    return "".join(html_parts)


def _wikilinks_filter(text: str, dead_links: set | None = None) -> Markup:
    if not text:
        return Markup("")
    result: list[str] = []
    last_end          = 0
    for match in _TABLE_MARKER_RE.finditer(text):
        segment = text[last_end:match.start()]
        if segment.strip():
            result.append(_process_wikitext(segment, dead_links))
        result.append(_resolve_wikilinks_in_html(match.group(1), dead_links))
        last_end = match.end()
    remaining = text[last_end:]
    if remaining.strip():
        result.append(_process_wikitext(remaining, dead_links))
    return Markup("".join(result))


templates.env.filters["wikilinks"] = _wikilinks_filter


def _parse_wiki_links(body: str) -> list[str]:
    seen:   set[str]  = set()
    result: list[str] = []
    for match in re.finditer(r'\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]', body or ""):
        title = match.group(1).strip()
        if title and title not in seen:
            seen.add(title)
            result.append(title)
    return result


def _parse_wiki_sections(body: str) -> list[dict] | None:
    sections:         list[dict] = []
    current_heading:  str | None = None
    current_parts:    list[str]  = []
    for line in (body or "").split("\n"):
        heading_match = re.match(r'^==+\s*(.+?)\s*==+\s*$', line)
        if heading_match:
            if current_heading is not None:
                sections.append({"title": current_heading, "content": "\n".join(current_parts).strip()})
            current_heading = heading_match.group(1)
            current_parts   = []
        else:
            current_parts.append(line)
    if current_heading is not None:
        sections.append({"title": current_heading, "content": "\n".join(current_parts).strip()})
    return sections or None


def _extract_summary(body: str) -> str | None:
    for line in (body or "").split("\n"):
        line = line.strip()
        if line and not re.match(r"^==", line):
            return re.sub(r'\[\[(?:[^\]|]+\|)?([^\]]+)\]\]', r"\1", line)
    return None


def _sections_to_edit_body(article: dict) -> str:
    body     = (article.get("body") or "").strip()
    sections = article.get("sections") or []
    if not sections:
        return body
    if re.search(r"^==", body, re.MULTILINE):
        return body
    parts: list[str] = []
    for section in sections:
        parts.append(f"== {section['title']} ==")
        content = (section.get("content") or "").strip()
        if content:
            parts.append(content)
    return "\n\n".join(parts)


def _parse_article_form(
    body:        Optional[str],
    summary:     Optional[str],
    redirect_to: Optional[str],
    facts_raw:   Optional[str] = None,
) -> dict:
    body     = body.replace("\r\n", "\n").replace("\r", "\n").strip() if body else None
    summary  = summary.strip() if summary else None
    links    = _parse_wiki_links(body or "")
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


def register_reference_ui(app: FastAPI) -> None:
    register_suite_config_js(app)
    register_ui_elements_assets(app, _UI_ELEMENTS_ASSETS)

    @app.get("/", include_in_schema=False)
    def route_root():
        return RedirectResponse("/ui/reference")

    @app.get("/ui", include_in_schema=False)
    def route_ui():
        return RedirectResponse("/ui/reference")

    @app.get("/ui/reference/import", response_class=HTMLResponse, include_in_schema=False)
    def ref_import(request: Request):
        return templates.TemplateResponse(request, "reference_import.html", {"status": dict(import_state)})

    @app.post("/ui/reference/import/crawl", include_in_schema=False)
    async def ref_import_crawl(request: Request, background_tasks: BackgroundTasks):
        payload        = await request.json()
        seed_url       = str(payload.get("seed_url") or "").strip()
        max_depth      = int(payload.get("max_depth") or 1)
        limit          = int(payload.get("limit") or 200)
        delay_seconds  = float(payload.get("delay_seconds") or 1.0)
        resume         = bool(payload.get("resume", True))
        if not import_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="Import already running")
        import_stop_event.clear()
        try:
            _, _, _, start_title = parse_seed_url(seed_url)
        except ValueError as exc:
            import_lock.release()
            raise HTTPException(status_code=422, detail=str(exc))
        import_state.update({
            "running":           True,
            "done":              0,
            "total":             1,
            "errors":            0,
            "last_error":        None,
            "mode":              "crawl",
            "seed":              start_title,
            "delay_seconds":     delay_seconds,
            "redirects_stored":  0,
            "last_redirect":     None,
            "limit":             limit,
        })
        import_lock.release()
        background_tasks.add_task(run_kiwix_crawl, seed_url, max_depth, limit, delay_seconds, resume)
        return JSONResponse(
            {
                "started":       True,
                "seed":          start_title,
                "max_depth":     max_depth,
                "limit":         limit,
                "delay_seconds": delay_seconds,
            },
            status_code = 202,
        )

    @app.get("/ui/reference/import/status", include_in_schema=False)
    def ref_import_status():
        return JSONResponse(dict(import_state))

    @app.post("/ui/reference/import/stop", include_in_schema=False)
    def ref_import_stop():
        if import_state.get("running"):
            import_state["running"] = False
            import_stop_event.set()
            return JSONResponse({"stopped": True})
        return JSONResponse({"stopped": False, "detail": "No import was running"})

    @app.post("/ui/reference/import/throttle", include_in_schema=False)
    async def ref_import_throttle(request: Request):
        payload = await request.json()
        delay   = float(payload.get("delay_seconds") or 0.0)
        import_state["delay_seconds"] = delay
        return JSONResponse(
            {
                "running":       bool(import_state.get("running")),
                "delay_seconds": float(import_state.get("delay_seconds") or 0.0),
            }
        )

    @app.get("/ui/reference", response_class=HTMLResponse, include_in_schema=False)
    def ref_index(request: Request, limit: int = 100, offset: int = 0):
        articles = list_articles(limit=limit, offset=offset)
        status   = get_status()
        return templates.TemplateResponse(
            request,
            "reference_index.html",
            {
                "articles": articles,
                "total":    status.get("total_articles", len(articles)),
                "limit":    limit,
                "offset":   offset,
            },
        )

    @app.get("/ui/reference/search", response_class=HTMLResponse, include_in_schema=False)
    def ref_search(
        request: Request,
        q:       Optional[str] = None,
        limit:   int           = 20,
        offset:  int           = 0,
        mode:    str           = "keyword",
        min_match: float       = 0.4,
    ):
        results      = []
        searched     = bool(q)
        search_error = ""
        search_mode  = "semantic" if str(mode).strip().lower() == "semantic" else "keyword"
        if searched:
            if search_mode == "semantic":
                if not chroma_available():
                    search_error = "Semantic search is unavailable because chromadb is not installed in this service environment."
                else:
                    results = semantic_search(q or "", limit=limit + offset, min_match=min_match)[offset: offset + limit]
            else:
                results = search_articles(q=q, title=None, limit=limit, offset=offset)
        return templates.TemplateResponse(
            request,
            "reference_search.html",
            {
                "results":    results,
                "searched":   searched,
                "q":          q or "",
                "limit":      limit,
                "mode":       search_mode,
                "min_match":  min_match,
                "search_error": search_error,
            },
        )

    @app.get("/ui/reference/new", response_class=HTMLResponse, include_in_schema=False)
    def ref_article_new(request: Request):
        return templates.TemplateResponse(request, "reference_edit.html", {"article": None, "error": None})

    @app.post("/ui/reference/new", response_class=HTMLResponse, include_in_schema=False)
    def ref_article_new_post(
        request:     Request,
        title:       str           = Form(...),
        summary:     Optional[str] = Form(None),
        body:        Optional[str] = Form(None),
        facts:       Optional[str] = Form(None),
        redirect_to: Optional[str] = Form(None),
    ):
        title   = title.strip()
        parsed  = _parse_article_form(body, summary, redirect_to, facts)
        try:
            stored = upsert_article(
                title       = title,
                body        = parsed["body"],
                summary     = parsed["summary"],
                facts       = parsed["facts"],
                redirect_to = parsed["redirect_to"],
                link_titles = parsed["links"],
            )
        except Exception as exc:
            return templates.TemplateResponse(
                request,
                "reference_edit.html",
                {
                    "article": None,
                    "error":   str(exc),
                    "form":    {
                        "title":       title,
                        "summary":     summary or "",
                        "body":        parsed["body"] or "",
                        "redirect_to": redirect_to or "",
                    },
                },
                status_code = 400,
            )
        stored_title = (stored or {}).get("title") or title
        return RedirectResponse(url=f"/ui/reference/{quote(stored_title, safe='')}", status_code=303)

    @app.get("/ui/reference/{title}/edit", response_class=HTMLResponse, include_in_schema=False)
    def ref_article_edit(request: Request, title: str):
        article = resolve_article(title)
        if article is None:
            raise HTTPException(status_code=404, detail=f"Article not found: {title!r}")
        return templates.TemplateResponse(
            request,
            "reference_edit.html",
            {"article": article, "edit_body": _sections_to_edit_body(article), "error": None},
        )

    @app.post("/ui/reference/{title}/edit", response_class=HTMLResponse, include_in_schema=False)
    def ref_article_edit_post(
        request:     Request,
        title:       str,
        summary:     Optional[str] = Form(None),
        body:        Optional[str] = Form(None),
        facts:       Optional[str] = Form(None),
        redirect_to: Optional[str] = Form(None),
    ):
        parsed = _parse_article_form(body, summary, redirect_to, facts)
        try:
            stored = upsert_article(
                title       = title,
                body        = parsed["body"],
                summary     = parsed["summary"],
                facts       = parsed["facts"],
                redirect_to = parsed["redirect_to"],
                link_titles = parsed["links"],
            )
        except Exception as exc:
            article = resolve_article(title) or {}
            return templates.TemplateResponse(
                request,
                "reference_edit.html",
                {
                    "article":   article,
                    "edit_body": _sections_to_edit_body(article),
                    "error":     str(exc),
                },
                status_code = 400,
            )
        stored_title = (stored or {}).get("title") or title
        return RedirectResponse(url=f"/ui/reference/{quote(stored_title, safe='')}", status_code=303)

    @app.post("/ui/reference/delete-all", include_in_schema=False)
    def ref_delete_all():
        delete_all_articles()
        return RedirectResponse(url="/ui/reference", status_code=303)

    @app.post("/ui/reference/{title}/delete", include_in_schema=False)
    def ref_article_delete(title: str):
        if not delete_article(title):
            raise HTTPException(status_code=404, detail=f"Article not found: {title!r}")
        return RedirectResponse(url="/ui/reference", status_code=303)

    @app.get("/ui/reference/{title}/links-json", include_in_schema=False)
    def ref_article_links_json(title: str):
        try:
            return JSONResponse(get_links(title))
        except Exception:
            return JSONResponse([])

    @app.get("/ui/reference/{title}", response_class=HTMLResponse, include_in_schema=False)
    def ref_article(request: Request, title: str):
        article = resolve_article(title)
        if article is None:
            raise HTTPException(status_code=404, detail=f"Article not found: {title!r}")
        backlinks  = get_backlinks(title, limit=10)
        links_data = get_links(title)
        dead_links = {item["to_title"].lower() for item in links_data if item.get("to_id") is None}
        body       = article.get("body") or ""
        heading    = re.search(r'(?m)^== .+? ==$', body)
        article["lead"] = body[:heading.start()].strip() if heading else (article.get("summary") or "")
        return templates.TemplateResponse(
            request,
            "reference_article.html",
            {"article": article, "backlinks": backlinks, "dead_links": dead_links},
        )
