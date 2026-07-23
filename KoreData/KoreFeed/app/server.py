# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FastAPI application for KoreFeed — RSS/Atom feed aggregator.
#
# Provides REST API and Jinja2 web UI for managing feed subscriptions and browsing
# article entries.  Feed polling is handled by the background ingest scheduler.
#
# Key endpoints:
#   GET  /api/feeds           -- list all feed subscriptions
#   POST /api/feeds           -- add a new feed
#   DELETE /api/feeds/{id}    -- remove a feed
#   GET  /api/entries?domain= -- paginated entry listing by domain
#   GET  /api/search?q=       -- full-text article search
#   GET  /                    -- web UI feed dashboard
#
# Related modules:
#   - app/database.py      -- all DB operations
#   - app/ingest.py        -- background RSS polling scheduler
#   - app/feed_manager.py  -- feed JSON configuration file I/O
#   - app/config.py        -- cfg (host, port, data_dir)
# ====================================================================================================
from contextlib import asynccontextmanager
import os
import sys
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl

_KORECOMMON_PARENT = next((parent for parent in Path(__file__).resolve().parents if (parent / "KoreCommon").is_dir()), None)
if _KORECOMMON_PARENT is not None and str(_KORECOMMON_PARENT) not in sys.path:
    sys.path.insert(0, str(_KORECOMMON_PARENT))

from KoreCommon.service_app import register_suite_shell_routes
from app.endpoint_ui import register_feed_ui
from app.database import (
    backfill_sentence_index,
    FeedDatabaseError,
    delete_domain_db,
    delete_entries_by_feed,
    delete_entries_by_ids,
    delete_entries_older_than,
    delete_entries_outside_calendar,
    delete_entry,
    get_entry_sentences,
    get_domain_age_settings,
    get_entries,
    get_entry,
    get_entry_count,
    get_feed_counts,
    get_recent_entries,
    get_sentence,
    init_db,
    list_domains,
    rebuild_sentence_index,
    rename_domain_db,
    search_entries,
    search_entries_detailed,
    set_sentence_deleted,
    set_domain_age_settings,
    update_entry_page_text,
)
from app.chroma_index import chroma_available, migrate_legacy_domain_stores, semantic_search
from app.feed_manager import (
    add_feed,
    create_domain,
    delete_domain_feeds,
    get_feed,
    get_domain_enabled,
    list_feed_domains,
    load_feeds,
    remove_feed,
    rename_domain_feeds,
    set_domain_enabled,
    sync_domain_spec,
    update_feed,
    update_feed_rate,
    update_domain_age_settings_spec,
)
from app.ingest import get_runtime_status, schedule_feeds, start_scheduler, stop_scheduler, trigger_immediate
from app.overview import get_feed_overview, invalidate_feed_overview


def _warm_feed_domains() -> None:
    for _domain in list_domains():
        init_db(_domain)
    for _domain in list_feed_domains():
        sync_domain_spec(_domain)
    try:
        migrate_legacy_domain_stores(batch_size=250)
    except Exception:
        pass


@asynccontextmanager
async def _lifespan(app: FastAPI):
    threading.Thread(
        target = _warm_feed_domains,
        daemon = True,
        name   = "korefeed-startup-warm",
    ).start()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="MiniFeed",
    description="RSS ingest server for LLM agents",
    lifespan=_lifespan,
)

register_feed_ui(app)

_UI_ELEMENTS_ASSETS = Path(
    os.environ.get(
        "KORE_UIELEMENTS_ASSETS_DIR",
        str(Path(__file__).resolve().parents[3] / "UIElements" / "assets"),
    )
).resolve()
register_suite_shell_routes(
    app,
    service_key            = "korefeed",
    service_label          = "KoreFeed",
    ui_elements_assets_dir = _UI_ELEMENTS_ASSETS,
)

# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------

@app.get("/status", tags=["meta"])
def api_status():
    """Health check used by KoreDataGateway."""
    overview = get_feed_overview()
    return {
        "status":        "ok",
        "service":       "KoreFeed",
        "total_domains": overview["total_domains"],
        "total_feeds":   overview["total_feeds"],
        "total_entries": overview["total_entries"],
        "runtime":       get_runtime_status(),
    }


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return await http_exception_handler(request, exc)


@app.exception_handler(Exception)
async def _generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}"},
    )


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------


class FeedCreate(BaseModel):
    domain: str
    name: str
    url: HttpUrl
    update_rate: int = 60  # minutes
    feed_type: str = "rss"


@app.get("/api/feeds", tags=["feeds"])
def api_list_feeds():
    """Return all configured RSS feeds."""
    return get_feed_overview()["all_feeds"]


@app.post("/api/feeds", status_code=201, tags=["feeds"])
def api_add_feed(body: FeedCreate):
    """Add a new RSS feed to the inventory."""
    feed = add_feed(body.domain, body.name, str(body.url), body.update_rate, feed_type=body.feed_type)
    init_db(body.domain)
    invalidate_feed_overview()
    schedule_feeds()
    trigger_immediate(feed)
    return feed


@app.delete("/api/feeds/{feed_id}", tags=["feeds"])
def api_remove_feed(feed_id: str):
    """Remove a feed from the inventory."""
    if not remove_feed(feed_id):
        raise HTTPException(status_code=404, detail="Feed not found")
    invalidate_feed_overview()
    schedule_feeds()
    return {"deleted": feed_id}


class FeedUpdate(BaseModel):
    name: str
    url: HttpUrl
    update_rate: int = 60
    feed_type: str = "rss"


@app.put("/api/feeds/{feed_id}", tags=["feeds"])
def api_update_feed(feed_id: str, body: FeedUpdate):
    """Update name, url, update_rate and type for an existing feed."""
    updated = update_feed(feed_id, body.name, str(body.url), body.update_rate, body.feed_type)
    if updated is None:
        raise HTTPException(status_code=404, detail="Feed not found")
    invalidate_feed_overview()
    schedule_feeds()
    return updated


@app.get("/api/domains", tags=["domains"])
def api_list_domains():
    """List all domains with entry counts."""
    return get_feed_overview()["domains"]


@app.post("/api/domains", status_code=201, tags=["domains"])
def api_create_domain(domain: str):
    """Create a new empty domain."""
    create_domain(domain)
    init_db(domain)
    invalidate_feed_overview()
    return {"domain": domain}


@app.delete("/api/domains/{domain}", tags=["domains"])
def api_delete_domain(domain: str):
    """Delete a domain, its feed list, and its database."""
    delete_domain_feeds(domain)
    delete_domain_db(domain)
    invalidate_feed_overview()
    schedule_feeds()
    return {"deleted": domain}


@app.post("/api/domains/{domain}/rename", tags=["domains"])
def api_rename_domain(domain: str, new_name: str):
    """Rename a domain."""
    rename_domain_feeds(domain, new_name)
    rename_domain_db(domain, new_name)
    invalidate_feed_overview()
    schedule_feeds()
    return {"renamed": new_name}


@app.post("/api/domains/{domain}/enabled", tags=["domains"])
def api_set_domain_enabled(domain: str, enabled: bool):
    """Enable or disable all feed processing for a domain."""
    if not set_domain_enabled(domain, enabled):
        raise HTTPException(status_code=404, detail="Domain not found")
    invalidate_feed_overview()
    schedule_feeds()
    return {"domain": domain, "enabled": enabled}


@app.get("/api/domains/{domain}/entries", tags=["content"])
def api_get_entries(domain: str, limit: int = 50, offset: int = 0):
    """Paginated list of entries for a domain."""
    try:
        return get_entries(domain, limit=limit, offset=offset)
    except FeedDatabaseError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/domains/{domain}/entries/{entry_id}", tags=["content"])
def api_get_entry(domain: str, entry_id: int):
    """Fetch a single entry by ID."""
    try:
        entry = get_entry(domain, entry_id)
    except FeedDatabaseError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    return entry


@app.get("/api/domains/{domain}/entries/{entry_id}/sentences", tags=["content"])
def api_get_entry_sentences(domain: str, entry_id: int):
    """List the indexed sentences for a single entry."""
    try:
        if not get_entry(domain, entry_id):
            raise HTTPException(status_code=404, detail="Entry not found")
        rows = get_entry_sentences(domain, entry_id, include_deleted=True)
    except FeedDatabaseError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return [
        {
            **row,
            "locator": f"feeds/{domain}/{row['id']}",
        }
        for row in rows
    ]


@app.get("/api/domains/{domain}/sentences/{sentence_id}", tags=["content"])
def api_get_sentence(domain: str, sentence_id: int):
    """Fetch a single indexed sentence by its per-domain sentence ID."""
    try:
        row = get_sentence(domain, sentence_id)
    except FeedDatabaseError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not row:
        raise HTTPException(status_code=404, detail="Sentence not found")
    return {
        **row,
        "locator": f"feeds/{domain}/{row['id']}",
    }


@app.delete("/api/domains/{domain}/entries/{entry_id}", tags=["content"])
def api_delete_entry(domain: str, entry_id: int):
    """Delete a single entry by ID."""
    if not delete_entry(domain, entry_id):
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"deleted": entry_id}


class EntryContentBody(BaseModel):
    page_text: str


class SentenceToggleBody(BaseModel):
    deleted: bool


@app.post("/api/domains/{domain}/entries/{entry_id}/content", tags=["content"])
def api_update_entry_content(domain: str, entry_id: int, body: EntryContentBody):
    try:
        return update_entry_page_text(domain, entry_id, body.page_text)
    except FeedDatabaseError as exc:
        message = str(exc)
        if "not found" in message.lower():
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=500, detail=message) from exc


@app.post("/api/domains/{domain}/sentences/{sentence_id}/deleted", tags=["content"])
def api_set_sentence_deleted(domain: str, sentence_id: int, body: SentenceToggleBody):
    try:
        return set_sentence_deleted(domain, sentence_id, body.deleted)
    except FeedDatabaseError as exc:
        message = str(exc)
        if "not found" in message.lower():
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=500, detail=message) from exc


@app.delete("/api/domains/{domain}/entries", tags=["content"])
def api_delete_entries(
    domain: str,
    feed_name: Optional[str] = None,
    older_than_days: Optional[float] = None,
):
    """Bulk-delete entries by feed name or age (provide exactly one filter)."""
    if feed_name:
        count = delete_entries_by_feed(domain, feed_name)
        return {"deleted": count, "filter": "feed_name", "value": feed_name}
    if older_than_days is not None:
        if older_than_days <= 0:
            raise HTTPException(status_code=400, detail="older_than_days must be > 0")
        count = delete_entries_older_than(domain, older_than_days)
        return {"deleted": count, "filter": "older_than_days", "value": older_than_days}
    raise HTTPException(status_code=400, detail="Provide feed_name or older_than_days")


@app.post("/api/domains/{domain}/sentences/backfill", tags=["content"])
def api_backfill_sentence_index(domain: str):
    """Create sentence rows for entries that do not yet have them."""
    try:
        return backfill_sentence_index(domain)
    except FeedDatabaseError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/domains/{domain}/sentences/rebuild", tags=["content"])
def api_rebuild_sentence_index(domain: str, entry_id: Optional[int] = None):
    """Rebuild sentence rows for an entire domain or one specific entry."""
    try:
        return rebuild_sentence_index(domain, entry_id=entry_id)
    except FeedDatabaseError as exc:
        message = str(exc)
        if "not found" in message.lower():
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=500, detail=message) from exc


@app.post("/api/domains/{domain}/entries/bulk-delete", tags=["content"])
def api_bulk_delete_entries(domain: str, ids: list[int]):
    """Delete multiple entries by ID list."""
    count = delete_entries_by_ids(domain, ids)
    return {"deleted": count}


@app.get("/api/search", tags=["content"])
def api_search(
    q: str,
    domain: Optional[str] = None,
    limit: int = 50,
    full: bool = False,
    since: Optional[str] = None,
    until: Optional[str] = None,
):
    """Full-text search across headline and page text.

    Set full=true to include page_text in each result.
    since / until are ISO 8601 date strings (YYYY-MM-DD) applied against the
    entry published date.  Either or both may be omitted.
    """
    results, failed_domains = search_entries_detailed(
        domain=domain or None,
        query=q,
        limit=limit,
        include_body=full,
        since=since,
        until=until,
    )
    headers: dict[str, str] = {}
    if failed_domains:
        failed_names = [item.get("domain", "") for item in failed_domains if item.get("domain")]
        headers["X-Kore-Failed-Domain-Count"] = str(len(failed_domains))
        headers["X-Kore-Failed-Domains"]      = ",".join(failed_names)
    return JSONResponse(content=results, headers=headers)


@app.get("/api/semantic-search", tags=["content"])
def api_semantic_search(
    q: str,
    domain: Optional[str] = None,
    limit: int = 50,
    min_match: float = 0.4,
):
    """Semantic sentence search across the per-domain Chroma stores."""
    if not chroma_available():
        raise HTTPException(status_code=503, detail="Semantic search unavailable: chromadb is not installed")
    return semantic_search(domain or None, q, limit=limit, min_match=min_match)


@app.get("/api/recent", tags=["content"])
def api_recent(domain: Optional[str] = None, hours: float = 24.0, limit: int = 50):
    """Return entries ingested within the last N hours, newest first.

    Searches all domains unless domain is specified.
    """
    if hours <= 0:
        raise HTTPException(status_code=400, detail="hours must be greater than 0")
    return get_recent_entries(domain or None, hours=hours, limit=limit)


# ---------------------------------------------------------------------------
# Additional API endpoints (used by KoreDataGateway)
# ---------------------------------------------------------------------------


@app.patch("/api/feeds/{feed_id}/rate", tags=["feeds"])
def api_update_feed_rate(feed_id: str, minutes: int):
    if minutes < 1:
        raise HTTPException(status_code=400, detail="minutes must be >= 1")
    if not update_feed_rate(feed_id, minutes):
        raise HTTPException(status_code=404, detail="Feed not found")
    schedule_feeds()
    return {"ok": True, "update_rate": minutes}


@app.post("/api/feeds/{feed_id}/trigger", tags=["feeds"])
def api_trigger_feed(feed_id: str):
    """Manually trigger an immediate fetch for a feed."""
    feed = get_feed(feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    trigger_immediate(feed)
    return {"triggered": feed_id}


@app.get("/api/domains/{domain}/age-settings", tags=["domains"])
def api_get_age_settings(domain: str):
    """Get entry age/date filter settings for a domain."""
    sync_domain_spec(domain)
    return get_domain_age_settings(domain)


class AgeSettingsBody(BaseModel):
    mode: str
    days: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


@app.post("/api/domains/{domain}/age-settings", tags=["domains"])
def api_set_age_settings(domain: str, body: AgeSettingsBody):
    """Set entry age/date filter settings for a domain."""
    valid = {"none", "days_previous", "calendar_period"}
    if body.mode not in valid:
        raise HTTPException(status_code=400, detail=f"mode must be one of: {', '.join(valid)}")
    set_domain_age_settings(
        domain, body.mode,
        days=body.days,
        start_date=body.start_date or None,
        end_date=body.end_date or None,
    )
    update_domain_age_settings_spec(
        domain,
        body.mode,
        days=body.days,
        start_date=body.start_date or None,
        end_date=body.end_date or None,
    )
    return {"ok": True}


@app.get("/api/domains/{domain}/feed-counts", tags=["domains"])
def api_feed_counts(domain: str):
    """Return per-feed entry counts for a domain."""
    return get_feed_counts(domain)


@app.post("/api/domains/{domain}/entries/purge-outside-calendar", tags=["content"])
def api_purge_outside_calendar(domain: str, start_date: str, end_date: str):
    """Delete all entries published outside the given date range."""
    delete_entries_outside_calendar(domain, start_date, end_date)
    return {"ok": True}


# UI routes live in app/endpoint_ui.py.
