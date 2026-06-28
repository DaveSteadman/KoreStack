import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader

from app.database import (
    delete_domain_db,
    delete_entries_by_ids,
    delete_entries_by_feed,
    delete_entries_older_than,
    delete_entries_outside_calendar,
    delete_entry,
    get_domain_age_settings,
    get_entries,
    get_entry,
    get_entry_count,
    get_feed_counts,
    init_db,
    list_domains,
    rename_domain_db,
    search_entries,
    set_domain_age_settings,
)
from app.feed_manager import (
    add_feed,
    create_domain,
    delete_domain_feeds,
    get_feed,
    list_feed_domains,
    load_feeds,
    remove_feed,
    rename_domain_feeds,
    sync_domain_spec,
    update_domain_age_settings_spec,
    update_feed,
)
from app.ingest import schedule_feeds, trigger_immediate


_FEED_UI_ROOT = Path(
    os.environ.get(
        "KORE_KOREFEED_UI_DIR",
        str(Path(__file__).resolve().parents[3] / "KoreUI" / "KoreData" / "KoreFeed"),
    )
).resolve()
TEMPLATES_DIR = Path(
    os.environ.get(
        "KORE_KOREFEED_TEMPLATES_DIR",
        str(_FEED_UI_ROOT / "templates"),
    )
).resolve()
_SHARED_TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "KoreUI" / "KoreData" / "KoreDataGateway" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.loader = ChoiceLoader([
    FileSystemLoader(str(TEMPLATES_DIR)),
    FileSystemLoader(str(_SHARED_TEMPLATES_DIR)),
])


def _add_next_mins(feeds: list[dict]) -> None:
    now = datetime.utcnow()
    for feed in feeds:
        last = feed.get("last_fetched_at")
        if last:
            try:
                nxt               = datetime.fromisoformat(last) + timedelta(minutes=int(feed.get("update_rate", 60)))
                secs              = int((nxt - now).total_seconds())
                feed["_next_secs"] = max(0, secs)
                feed["_next_mins"] = secs // 60
            except Exception:
                feed["_next_mins"] = None
                feed["_next_secs"] = None
        else:
            feed["_next_mins"] = None
            feed["_next_secs"] = None


def register_feed_ui(app: FastAPI) -> None:
    @app.get("/", include_in_schema=False)
    def route_root():
        return RedirectResponse("/ui/feeds")

    @app.get("/ui", include_in_schema=False)
    def route_ui():
        return RedirectResponse("/ui/feeds")

    @app.get("/ui/feeds", response_class=HTMLResponse, include_in_schema=False)
    def web_index(request: Request):
        db_domains   = set(list_domains())
        feed_domains = set(list_feed_domains())
        domains      = [{"domain": domain, "entry_count": get_entry_count(domain)} for domain in sorted(db_domains | feed_domains)]
        all_feeds    = load_feeds()
        _add_next_mins(all_feeds)
        all_feeds.sort(
            key = lambda feed: (
                0 if feed["_next_mins"] is None else (1 if feed["_next_mins"] <= 0 else 2),
                feed["_next_mins"] if feed["_next_mins"] is not None else 0,
            )
        )
        return templates.TemplateResponse(request, "feed_index.html", {"domains": domains, "all_feeds": all_feeds})

    @app.get("/ui/feeds/search", response_class=HTMLResponse, include_in_schema=False)
    def web_search(
        request: Request,
        q:       str            = "",
        domain:  Optional[str]  = None,
        since:   Optional[str]  = None,
        until:   Optional[str]  = None,
        limit:   int            = 50,
    ):
        results = []
        if q.strip():
            results = search_entries(domain or None, q, limit=limit, include_body=False, since=since, until=until)
        return templates.TemplateResponse(
            request,
            "feed_search.html",
            {
                "results": results,
                "q":       q,
                "domain":  domain,
                "since":   since or "",
                "until":   until or "",
                "limit":   limit,
            },
        )

    @app.get("/ui/feeds/{domain}", response_class=HTMLResponse, include_in_schema=False)
    def web_domain(request: Request, domain: str, limit: int = 50, offset: int = 0):
        entries       = get_entries(domain, limit=limit, offset=offset)
        all_domains   = [{"domain": item, "entry_count": get_entry_count(item)} for item in sorted(set(list_domains()) | set(list_feed_domains()))]
        all_feeds     = load_feeds()
        age_settings  = get_domain_age_settings(domain)
        feed_counts   = get_feed_counts(domain)
        domain_info   = next((item for item in all_domains if item["domain"] == domain), {})
        total         = domain_info.get("entry_count", len(entries))
        feeds         = [feed for feed in all_feeds if feed.get("domain") == domain]
        _add_next_mins(feeds)
        feed_refresh_mins = {feed["id"]: feed.get("_next_mins") for feed in feeds}
        feed_refresh_secs = {feed["id"]: feed.get("_next_secs") for feed in feeds}
        return templates.TemplateResponse(
            request,
            "feed_domain.html",
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

    @app.get("/ui/feeds/{domain}/{entry_id}", response_class=HTMLResponse, include_in_schema=False)
    def web_entry(request: Request, domain: str, entry_id: int):
        entry = get_entry(domain, entry_id)
        if not entry:
            raise HTTPException(status_code=404, detail="Entry not found")
        if entry.get("metadata") and isinstance(entry["metadata"], str):
            try:
                entry["metadata"] = json.loads(entry["metadata"])
            except Exception:
                pass
        return templates.TemplateResponse(request, "feed_entry.html", {"domain": domain, "entry": entry})

    @app.post("/ui/feeds/domains/create", include_in_schema=False)
    def web_create_domain(domain: str = Form(...)):
        create_domain(domain)
        init_db(domain)
        return RedirectResponse("/ui/feeds", status_code=303)

    @app.post("/ui/feeds/domains/{domain}/delete", include_in_schema=False)
    def web_delete_domain(domain: str):
        delete_domain_feeds(domain)
        delete_domain_db(domain)
        schedule_feeds()
        return RedirectResponse("/ui/feeds", status_code=303)

    @app.post("/ui/feeds/domains/{domain}/rename", include_in_schema=False)
    def web_rename_domain(domain: str, new_name: str = Form(...)):
        rename_domain_feeds(domain, new_name)
        rename_domain_db(domain, new_name)
        schedule_feeds()
        return RedirectResponse("/ui/feeds", status_code=303)

    @app.post("/ui/feeds/{domain}/feeds/add", include_in_schema=False)
    def web_add_feed(
        domain:      str,
        name:        str = Form(...),
        url:         str = Form(...),
        update_rate: int = Form(60),
        feed_type:   str = Form("rss"),
    ):
        feed = add_feed(domain, name, url, update_rate, feed_type=feed_type)
        init_db(domain)
        schedule_feeds()
        trigger_immediate(feed)
        return RedirectResponse(f"/ui/feeds/{domain}", status_code=303)

    @app.post("/ui/feeds/{domain}/feeds/{feed_id}/delete", include_in_schema=False)
    def web_delete_feed(domain: str, feed_id: str):
        if not remove_feed(feed_id):
            raise HTTPException(status_code=404, detail="Feed not found")
        schedule_feeds()
        return RedirectResponse(f"/ui/feeds/{domain}", status_code=303)

    @app.post("/ui/feeds/{domain}/feeds/{feed_id}/update", include_in_schema=False)
    def web_update_feed(
        domain:      str,
        feed_id:     str,
        name:        str = Form(...),
        url:         str = Form(...),
        update_rate: int = Form(60),
        feed_type:   str = Form("rss"),
    ):
        updated = update_feed(feed_id, name, url, update_rate, feed_type)
        if updated is None:
            raise HTTPException(status_code=404, detail="Feed not found")
        schedule_feeds()
        return RedirectResponse(f"/ui/feeds/{domain}", status_code=303)

    @app.post("/ui/feeds/{domain}/feeds/{feed_id}/refresh", include_in_schema=False)
    def web_refresh_feed(domain: str, feed_id: str):
        feed = get_feed(feed_id)
        if not feed:
            raise HTTPException(status_code=404, detail="Feed not found")
        trigger_immediate(feed)
        return JSONResponse({"triggered": feed_id})

    @app.post("/ui/feeds/{domain}/entries/{entry_id}/delete", include_in_schema=False)
    def web_delete_entry(domain: str, entry_id: int):
        if not delete_entry(domain, entry_id):
            raise HTTPException(status_code=404, detail="Entry not found")
        return JSONResponse({"deleted": entry_id})

    @app.post("/ui/feeds/{domain}/entries/delete-older-than", include_in_schema=False)
    def web_delete_older_than(domain: str, days: float = Form(...)):
        delete_entries_older_than(domain, days)
        return RedirectResponse(f"/ui/feeds/{domain}", status_code=303)

    @app.post("/ui/feeds/{domain}/entries/delete-by-feed", include_in_schema=False)
    def web_delete_by_feed(domain: str, feed_name: str = Form(...)):
        delete_entries_by_feed(domain, feed_name)
        return RedirectResponse(f"/ui/feeds/{domain}", status_code=303)

    @app.post("/ui/feeds/entries/bulk-delete", include_in_schema=False)
    def web_bulk_delete_entries(request: Request, sel: list[str] = Form(default=[])):
        by_domain: dict[str, list[int]] = {}
        for item in sel:
            parts = item.split(":", 1)
            if len(parts) == 2:
                domain, entry_id = parts
                try:
                    by_domain.setdefault(domain, []).append(int(entry_id))
                except ValueError:
                    pass
        for domain, ids in by_domain.items():
            delete_entries_by_ids(domain, ids)
        ref = request.headers.get("referer", "/ui/feeds/search")
        return RedirectResponse(ref, status_code=303)

    @app.post("/ui/feeds/{domain}/settings/age-mode", include_in_schema=False)
    def web_set_age_mode(
        request:    Request,
        domain:     str,
        mode:       str            = Form(...),
        days:       Optional[int]  = Form(None),
        start_date: Optional[str]  = Form(None),
        end_date:   Optional[str]  = Form(None),
    ):
        set_domain_age_settings(domain, mode, days=days, start_date=start_date, end_date=end_date)
        update_domain_age_settings_spec(domain, mode, days=days, start_date=start_date, end_date=end_date)
        sync_domain_spec(domain)
        if "application/json" in request.headers.get("accept", ""):
            return {"ok": True}
        return RedirectResponse(f"/ui/feeds/{domain}", status_code=303)

    @app.post("/ui/feeds/{domain}/entries/delete-outside-calendar", include_in_schema=False)
    def web_delete_outside_calendar(
        domain:     str,
        start_date: str = Form(...),
        end_date:   str = Form(...),
    ):
        delete_entries_outside_calendar(domain, start_date, end_date)
        return RedirectResponse(f"/ui/feeds/{domain}", status_code=303)
