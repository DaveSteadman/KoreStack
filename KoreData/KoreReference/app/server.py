# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FastAPI application for KoreReference — a Wikipedia-snapshot article store.
#
# Provides REST API for article CRUD, backlink resolution, full-text search,
# and Kiwix import automation (bulk crawl from a local Kiwix server).
#
# Key endpoints:
#   GET  /api/articles           -- article listing (limit/offset)
#   GET  /api/articles/{id}      -- single article with body and tables
#   POST /api/articles           -- create/update an article
#   DELETE /api/articles/{id}    -- delete an article
#   GET  /api/search?q=          -- full-text search
#   POST /api/import/kiwix       -- start a background Kiwix crawl
#   GET  /api/import/status      -- current import progress
#
# Related modules:
#   - app/database.py               -- all DB operations
#   - app/importers/kiwix.py        -- background Kiwix import
#   - app/importers/state.py        -- thread-safe import progress state
#   - app/config.py                 -- cfg (host, port, data_dir)
# ====================================================================================================
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import threading
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

_KORECOMMON_PARENT = next((parent for parent in Path(__file__).resolve().parents if (parent / "KoreCommon").is_dir()), None)
if _KORECOMMON_PARENT is not None and str(_KORECOMMON_PARENT) not in sys.path:
    sys.path.insert(0, str(_KORECOMMON_PARENT))

from KoreCommon.service_app import register_endpoint_manifest
from app.config import cfg
from app.database import (
    backfill_sentence_index,
    delete_all_articles,
    delete_article,
    get_article_by_title,
    get_article_sentences,
    get_backlinks,
    get_links,
    get_sentence,
    get_random_article,
    get_status,
    get_unresolved_link_titles,
    init_db,
    list_articles,
    rebuild_sentence_index,
    resolve_article,
    resolve_links,
    search_articles,
    upsert_article,
)
from app.chroma_index import chroma_available, close_client, semantic_search, sync_pending_sentences
from app.importers.kiwix import (
    _http_client,
    import_one,
    parse_seed_url,
    run_kiwix_backfill,
    run_kiwix_crawl,
    run_kiwix_import,
)
from app.importers.state import import_lock, import_state, import_stop_event
from app.endpoint_ui import register_reference_ui


def _warm_reference_semantic_index() -> None:
    try:
        init_db()
    except Exception:
        return
    try:
        sync_pending_sentences(batch_size=250)
    except Exception:
        pass


@asynccontextmanager
async def _lifespan(app: FastAPI):
    threading.Thread(
        target = _warm_reference_semantic_index,
        daemon = True,
        name   = "korereference-startup-warm",
    ).start()
    try:
        yield
    finally:
        close_client()


app = FastAPI(
    title="KoreReference",
    description="Wikipedia-scale encyclopedia service for LLM agents",
    lifespan=_lifespan,
)

register_reference_ui(app)
register_endpoint_manifest(app, service_key="korereference", service_label="KoreReference")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ArticleCreate(BaseModel):
    title: str
    body: Optional[str] = None
    summary: Optional[str] = None
    facts: Optional[list] = None
    redirect_to: Optional[str] = None
    link_titles: Optional[list[str]] = None


class KiwixImportRequest(BaseModel):
    zim_name: str
    kiwix_url: str
    titles: Optional[list[str]] = None
    prefix: str = ""
    limit: Optional[int] = None
    resume: bool = True


class KiwixCrawlRequest(BaseModel):
    seed_url: str
    max_depth: int = 1
    limit: int = 200
    delay_seconds: float = Field(default=1.0, ge=0.1, le=10.0)
    resume: bool = True


class KiwixThrottleRequest(BaseModel):
    delay_seconds: float = Field(default=1.0, ge=0.1, le=10.0)



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_article(title: str) -> dict:
    article = resolve_article(title)
    if article is None:
        raise HTTPException(status_code=404, detail=f"Article not found: {title!r}")
    return article


# Articles
# ---------------------------------------------------------------------------

@app.get("/api/articles", summary="List articles (metadata only)")
@app.get("/articles", include_in_schema=False)
def route_list_articles(limit: int = 100, offset: int = 0):
    return list_articles(limit=limit, offset=offset)


@app.get("/api/articles/random", summary="Random non-redirect article")
@app.get("/articles/random", include_in_schema=False)
def route_random_article():
    article = get_random_article()
    if article is None:
        raise HTTPException(status_code=404, detail="No articles in database")
    return article


@app.get("/api/articles/{title}", summary="Get article by title, following redirects")
@app.get("/articles/{title}", include_in_schema=False)
def route_get_article(title: str):
    article = resolve_article(title)
    if article is None:
        raise HTTPException(status_code=404, detail=f"Article not found: {title!r}")
    return article


@app.get("/api/articles/{title}/summary", summary="Summary paragraph only")
@app.get("/articles/{title}/summary", include_in_schema=False)
def route_get_summary(title: str):
    article = resolve_article(title)
    if article is None:
        raise HTTPException(status_code=404, detail=f"Article not found: {title!r}")
    return {
        "title":      article["title"],
        "summary":    article.get("summary"),
        "word_count": article.get("word_count"),
    }


@app.get("/api/articles/{title}/section/{section_name}", summary="Single named section")
@app.get("/articles/{title}/section/{section_name}", include_in_schema=False)
def route_get_section(title: str, section_name: str):
    article = resolve_article(title)
    if article is None:
        raise HTTPException(status_code=404, detail=f"Article not found: {title!r}")
    sections: list = article.get("sections") or []
    match = next(
        (s for s in sections if s["title"].lower() == section_name.lower()),
        None,
    )
    if match is None:
        raise HTTPException(status_code=404, detail=f"Section not found: {section_name!r}")
    return {"title": article["title"], "section": match["title"], "content": match["content"]}


@app.get("/api/articles/{title}/links", summary="Outbound links from an article")
@app.get("/articles/{title}/links", include_in_schema=False)
def route_get_links(title: str):
    if get_article_by_title(title, full=False) is None:
        raise HTTPException(status_code=404, detail=f"Article not found: {title!r}")
    return get_links(title)


@app.get("/api/articles/{title}/backlinks", summary="Articles that link to this article")
@app.get("/articles/{title}/backlinks", include_in_schema=False)
def route_get_backlinks(title: str, limit: int = 50, offset: int = 0):
    if get_article_by_title(title, full=False) is None:
        raise HTTPException(status_code=404, detail=f"Article not found: {title!r}")
    return get_backlinks(title, limit=limit, offset=offset)


@app.get("/api/articles/{title}/sentences", summary="List indexed sentences for a single article")
def route_article_sentences(title: str, include_deleted: bool = False):
    article = get_article_by_title(title, full=False)
    if article is None:
        raise HTTPException(status_code=404, detail=f"Article not found: {title!r}")
    return get_article_sentences(int(article["id"]), include_deleted=include_deleted)


@app.get("/api/sentences/{sentence_id}", summary="Fetch a single indexed sentence")
def route_sentence(sentence_id: int):
    row = get_sentence(sentence_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Sentence not found: {sentence_id}")
    return row


@app.post("/api/articles", status_code=201, summary="Add or upsert an article")
@app.post("/articles", status_code=201, include_in_schema=False)
def route_upsert_article(data: ArticleCreate):
    return upsert_article(
        title=data.title,
        body=data.body,
        summary=data.summary,
        facts=data.facts,
        redirect_to=data.redirect_to,
        link_titles=data.link_titles,
    )


@app.delete("/api/articles", summary="Delete all articles")
@app.delete("/articles", include_in_schema=False)
def route_delete_all_articles():
    count = delete_all_articles()
    return {"deleted": count}


@app.delete("/api/articles/{title}", status_code=204, summary="Remove an article and its links")
@app.delete("/articles/{title}", status_code=204, include_in_schema=False)
def route_delete_article(title: str):
    if not delete_article(title):
        raise HTTPException(status_code=404, detail=f"Article not found: {title!r}")
    return JSONResponse(status_code=204, content=None)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@app.get("/api/search", summary="Full-text and prefix search across articles")
@app.get("/search", include_in_schema=False)
def route_search(
    q: Optional[str] = None,
    title: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
):
    if not any([q, title]):
        raise HTTPException(
            status_code=400,
            detail="Provide at least one of: q, title",
        )
    return search_articles(q=q, title=title, limit=limit, offset=offset)


@app.get("/api/semantic-search", summary="Semantic search across indexed reference sentences")
def route_semantic_search(q: str, limit: int = 50, min_match: float = 0.4):
    if not chroma_available():
        raise HTTPException(status_code=503, detail="Semantic search unavailable: chromadb is not installed")
    return semantic_search(q, limit=limit, min_match=min_match)


@app.post("/api/sentences/backfill", summary="Create sentence rows for articles that do not yet have them")
def route_backfill_sentence_index():
    return backfill_sentence_index()


@app.post("/api/sentences/rebuild", summary="Rebuild sentence rows for all articles or one article")
def route_rebuild_sentence_index(article_id: Optional[int] = None):
    return rebuild_sentence_index(article_id=article_id)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

@app.post("/api/import/kiwix", summary="Trigger import from configured Kiwix server")
@app.post("/import/kiwix", include_in_schema=False)
def route_import_kiwix(req: KiwixImportRequest, background_tasks: BackgroundTasks):
    if not import_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Import already running")
    import_stop_event.clear()
    import_state.update({
        "running": True, "done": 0, "total": 0,
        "errors": 0, "last_error": None, "mode": "prefix", "seed": None,
        "redirects_stored": 0, "last_redirect": None,
    })
    import_lock.release()
    background_tasks.add_task(
        run_kiwix_import, req.zim_name, req.kiwix_url, req.titles, req.prefix, req.limit, req.resume
    )
    return {"started": True, "zim_name": req.zim_name}


@app.post("/api/import/kiwix/crawl", status_code=202, summary="BFS crawl from a Kiwix or Wikipedia article URL")
@app.post("/import/kiwix/crawl", status_code=202, include_in_schema=False)
def route_import_kiwix_crawl(req: KiwixCrawlRequest, background_tasks: BackgroundTasks):
    if not import_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Import already running")
    import_stop_event.clear()
    try:
        _, _, _, start_title = parse_seed_url(req.seed_url)
    except ValueError as exc:
        import_lock.release()
        raise HTTPException(status_code=422, detail=str(exc))
    import_state.update({
        "running": True, "done": 0, "total": 1,
        "errors": 0, "last_error": None, "mode": "crawl", "seed": start_title,
        "delay_seconds": req.delay_seconds,
        "redirects_stored": 0, "last_redirect": None,
    })
    import_lock.release()
    background_tasks.add_task(
        run_kiwix_crawl, req.seed_url, req.max_depth, req.limit, req.delay_seconds, req.resume
    )
    return {
        "started": True,
        "seed": start_title,
        "max_depth": req.max_depth,
        "limit": req.limit,
        "delay_seconds": req.delay_seconds,
    }



@app.post("/api/import/stop", summary="Abort in-progress import or crawl")
@app.post("/import/stop", include_in_schema=False)
def route_import_stop():
    if import_state.get("running"):
        import_state["running"] = False
        import_stop_event.set()
        return {"stopped": True}
    return {"stopped": False, "detail": "No import was running"}


@app.post("/api/import/throttle", summary="Adjust crawl delay while import is running")
@app.post("/import/throttle", include_in_schema=False)
def route_import_throttle(req: KiwixThrottleRequest):
    import_state["delay_seconds"] = req.delay_seconds
    return {
        "running": bool(import_state.get("running")),
        "delay_seconds": float(import_state.get("delay_seconds") or 0.0),
    }



@app.post("/api/import/article", status_code=201, summary="Import a single article by title from Kiwix")
@app.post("/import/article", status_code=201, include_in_schema=False)
def route_import_article(zim_name: str, title: str, kiwix_url: str):
    """Synchronous single-article import — useful for testing and on-demand fetch."""
    kiwix_base = kiwix_url.rstrip("/")
    try:
        with _http_client() as client:
            import_one(client, "kiwix", kiwix_base, zim_name, title, resume=True)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Kiwix returned {exc.response.status_code} for {title!r}",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return get_article_by_title(title, full=False)


class KiwixBackfillRequest(BaseModel):
    zim_name: str
    kiwix_url: str
    limit: int = 10_000


@app.post("/api/import/kiwix/backfill", status_code=202, summary="Fetch unresolved link targets from Kiwix")
@app.post("/import/kiwix/backfill", status_code=202, include_in_schema=False)
def route_import_kiwix_backfill(req: KiwixBackfillRequest, background_tasks: BackgroundTasks):
    """Fetch every link target that exists in the links table but has no article row.

    This repairs the historical gap where redirect pages were silently dropped during
    import.  Titles like 'colour', 'NYC', 'World War 2' are stored as links.to_title
    but were never imported; this endpoint fetches each one and stores it — either as
    a redirect row or as a full article if it turns out to be a real page.
    """
    if not import_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Import already running")
    pending = get_unresolved_link_titles(limit=req.limit)
    if not pending:
        import_lock.release()
        return {"started": False, "detail": "No unresolved link targets found"}
    import_state.update({
        "running": True, "done": 0, "total": len(pending),
        "errors": 0, "last_error": None, "mode": "backfill", "seed": None,
        "redirects_stored": 0, "last_redirect": None,
    })
    import_lock.release()
    background_tasks.add_task(run_kiwix_backfill, req.zim_name, req.kiwix_url, req.limit)
    return {"started": True, "pending": len(pending), "zim_name": req.zim_name}


@app.get("/api/import/status", summary="Progress of in-progress import")
@app.get("/import/status", include_in_schema=False)
def route_import_status():
    return dict(import_state)


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@app.get("/status", summary="Server status and database statistics")
def route_status():
    return {
        "service": "KoreReference",
        **get_status(),
    }
