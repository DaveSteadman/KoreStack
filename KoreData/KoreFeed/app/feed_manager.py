# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Feed configuration manager for KoreFeed.
#
# Stores and retrieves feed metadata (URL, domain, enabled state, last_fetched, status)
# as JSON files, one per domain, in the data_dir/feeds/ directory.
# Provides load_feeds() and save_feeds() as the single source of truth for feed config.
#
# Related modules:
#   - app/ingest.py  -- reads feed config to drive the polling scheduler
#   - app/server.py  -- CRUD operations on feeds via this module
# ====================================================================================================
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import cfg
from app.database import get_domain_age_settings, init_db, rename_feed_entries, set_domain_age_settings

FEEDS_DIR = Path(cfg["data_dir"])


def _domain_file(domain: str) -> Path:
    """Return the path for a domain's feed file, sanitising the name."""
    safe = re.sub(r"[^\w\-]", "_", domain)
    return FEEDS_DIR / f"{safe}.json"


def _state_file(domain: str) -> Path:
    """Return the path for a domain's runtime-only state file."""
    safe = re.sub(r"[^\w\-]", "_", domain)
    return FEEDS_DIR / f"{safe}.state.json"


def _feed_identity(domain: str, name: str, url: str) -> str:
    """Return a stable internal id derived from the feed spec."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{domain}|{name.strip()}|{url.strip()}"))


def _load_domain_state(domain: str) -> dict[str, dict]:
    path = _state_file(domain)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}


def _save_domain_state(domain: str, state: dict[str, dict]) -> None:
    FEEDS_DIR.mkdir(exist_ok=True)
    with open(_state_file(domain), "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)


def _normalise_age_settings(raw_age_settings: object) -> dict:
    valid_modes = {"none", "days_previous", "calendar_period"}
    if not isinstance(raw_age_settings, dict):
        return {"mode": "none", "days": None, "start_date": None, "end_date": None}

    mode = str(raw_age_settings.get("mode") or "none").strip()
    if mode not in valid_modes:
        mode = "none"

    days = raw_age_settings.get("days")
    try:
        days = int(days) if days not in (None, "") else None
    except (TypeError, ValueError):
        days = None
    if days is not None and days < 1:
        days = None

    start_date = str(raw_age_settings.get("start_date") or "").strip() or None
    end_date   = str(raw_age_settings.get("end_date")   or "").strip() or None

    if mode != "days_previous":
        days = None
    if mode != "calendar_period":
        start_date = None
        end_date   = None

    return {
        "mode":       mode,
        "days":       days,
        "start_date": start_date,
        "end_date":   end_date,
    }


def _read_domain_spec(domain: str) -> tuple[str, list, dict]:
    path = _domain_file(domain)
    if not path.exists():
        return domain, [], {"mode": "none", "days": None, "start_date": None, "end_date": None}

    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)

    if isinstance(raw, dict):
        raw_domain       = str(raw.get("domain") or domain).strip() or domain
        raw_feeds        = raw.get("feeds", [])
        raw_age_settings = _normalise_age_settings(raw.get("age_settings"))
    elif isinstance(raw, list):
        raw_domain       = domain
        raw_feeds        = raw
        raw_age_settings = {"mode": "none", "days": None, "start_date": None, "end_date": None}
    else:
        return domain, [], {"mode": "none", "days": None, "start_date": None, "end_date": None}

    if not isinstance(raw_feeds, list):
        raw_feeds = []
    return raw_domain, raw_feeds, raw_age_settings


def _apply_domain_age_settings(domain: str, age_settings: dict) -> None:
    init_db(domain)
    set_domain_age_settings(
        domain,
        age_settings.get("mode", "none"),
        days=age_settings.get("days"),
        start_date=age_settings.get("start_date"),
        end_date=age_settings.get("end_date"),
    )


def _load_domain_file(domain: str) -> list[dict]:
    raw_domain, raw_feeds, raw_age_settings = _read_domain_spec(domain)
    _apply_domain_age_settings(raw_domain, raw_age_settings)

    state = _load_domain_state(raw_domain)
    feeds: list[dict] = []
    for item in raw_feeds:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        url  = str(item.get("url")  or "").strip()
        if not name or not url:
            continue
        feed_id = _feed_identity(raw_domain, name, url)
        feed = {
            "id":          feed_id,
            "domain":      raw_domain,
            "name":        name,
            "url":         url,
            "update_rate": int(item.get("update_rate") or 60),
            "type":        str(item.get("type") or "rss").strip() or "rss",
        }
        if feed["update_rate"] < 1:
            feed["update_rate"] = 60
        feed.update(state.get(feed_id, {}))
        feeds.append(feed)
    return feeds


def _save_domain_file(domain: str, feeds: list[dict]) -> None:
    FEEDS_DIR.mkdir(exist_ok=True)
    age_settings = get_domain_age_settings(domain)
    with open(_domain_file(domain), "w", encoding="utf-8") as f:
        json.dump(_build_export_spec(domain, feeds, age_settings), f, indent=2)


def _build_export_feed(raw_feed: dict) -> dict:
    export_feed = {
        "name":        str(raw_feed.get("name") or "").strip(),
        "url":         str(raw_feed.get("url")  or "").strip(),
        "update_rate": int(raw_feed.get("update_rate") or 60),
        "type":        str(raw_feed.get("type") or "rss").strip() or "rss",
    }
    if export_feed["update_rate"] < 1:
        export_feed["update_rate"] = 60
    return export_feed


def _build_export_spec(domain: str, feeds: list[dict], age_settings: Optional[dict] = None) -> dict:
    return {
        "domain":       domain,
        "age_settings": _normalise_age_settings(age_settings),
        "feeds":        [
            _build_export_feed(feed)
            for feed in feeds
            if str(feed.get("name") or "").strip() and str(feed.get("url") or "").strip()
        ],
    }


def _clean_feed_for_import(raw_feed: dict, domain: str) -> dict:
    clean_feed = _build_export_feed(raw_feed)
    clean_feed["domain"] = domain
    if clean_feed["update_rate"] < 1:
        clean_feed["update_rate"] = 60
    return clean_feed


def _normalise_import_feeds(raw_feeds: list[dict], domain: str) -> list[dict]:
    result: list[dict] = []
    for raw_feed in raw_feeds:
        if not isinstance(raw_feed, dict):
            continue
        clean_feed = _clean_feed_for_import(raw_feed, domain)
        if clean_feed["name"] and clean_feed["url"]:
            result.append(clean_feed)
    return result


def load_feeds() -> list[dict]:
    """Return all feeds from every domain file."""
    FEEDS_DIR.mkdir(exist_ok=True)
    result: list[dict] = []
    for path in sorted(FEEDS_DIR.glob("*.json")):
        if path.name.endswith(".state.json"):
            continue
        result.extend(_load_domain_file(path.stem))
    return result


def load_feeds_for_domain(domain: str) -> list[dict]:
    return _load_domain_file(domain)


def list_feed_domains() -> list[str]:
    """Return domain names that have a feeds JSON file."""
    FEEDS_DIR.mkdir(exist_ok=True)
    return [
        p.stem
        for p in sorted(FEEDS_DIR.glob("*.json"))
        if not p.name.endswith(".state.json")
    ]


def get_feed(feed_id: str) -> Optional[dict]:
    return next((f for f in load_feeds() if f["id"] == feed_id), None)


def add_feed(domain: str, name: str, url: str, update_rate: int, feed_type: str = "rss") -> dict:
    feeds = _load_domain_file(domain)
    feed = {
        "domain":      domain,
        "name":        name,
        "url":         url,
        "update_rate": update_rate,
        "type":        feed_type,
    }
    feeds.append(feed)
    _save_domain_file(domain, feeds)
    return get_feed(_feed_identity(domain, name, url)) or _clean_feed_for_import(feed, domain)


def remove_feed(feed_id: str) -> bool:
    FEEDS_DIR.mkdir(exist_ok=True)
    for path in FEEDS_DIR.glob("*.json"):
        if path.name.endswith(".state.json"):
            continue
        domain = path.stem
        feeds  = _load_domain_file(domain)
        new_feeds = [f for f in feeds if f["id"] != feed_id]
        if len(new_feeds) < len(feeds):
            _save_domain_file(domain, new_feeds)
            state = _load_domain_state(domain)
            state.pop(feed_id, None)
            _save_domain_state(domain, state)
            return True
    return False


# ---------------------------------------------------------------------------
# Domain lifecycle
# ---------------------------------------------------------------------------

def create_domain(domain: str) -> bool:
    """Create an empty feed file for a new domain. Returns False if it already exists."""
    path = _domain_file(domain)
    FEEDS_DIR.mkdir(exist_ok=True)
    if path.exists():
        return False
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "domain":       domain,
                "age_settings": {"mode": "none", "days": None, "start_date": None, "end_date": None},
                "feeds":        [],
            },
            f,
            indent=2,
        )
    return True


def update_domain_age_settings_spec(
    domain: str,
    mode: str,
    days: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> None:
    _, raw_feeds, _ = _read_domain_spec(domain)
    age_settings = _normalise_age_settings({
        "mode":       mode,
        "days":       days,
        "start_date": start_date,
        "end_date":   end_date,
    })
    FEEDS_DIR.mkdir(exist_ok=True)
    with open(_domain_file(domain), "w", encoding="utf-8") as handle:
        json.dump(
            {
                "domain":       domain,
                "age_settings": age_settings,
                "feeds":        [
                    _build_export_feed(feed)
                    for feed in raw_feeds
                    if isinstance(feed, dict)
                    and str(feed.get("name") or "").strip()
                    and str(feed.get("url") or "").strip()
                ],
            },
            handle,
            indent=2,
        )


def sync_domain_spec(domain: str) -> None:
    spec_domain, raw_feeds, age_settings = _read_domain_spec(domain)
    _apply_domain_age_settings(spec_domain, age_settings)
    FEEDS_DIR.mkdir(exist_ok=True)
    with open(_domain_file(spec_domain), "w", encoding="utf-8") as handle:
        json.dump(
            _build_export_spec(
                spec_domain,
                _normalise_import_feeds(raw_feeds, spec_domain),
                get_domain_age_settings(spec_domain),
            ),
            handle,
            indent=2,
        )


def delete_domain_feeds(domain: str) -> bool:
    """Delete the feed file for a domain. Returns False if it didn't exist."""
    path = _domain_file(domain)
    if not path.exists():
        return False
    path.unlink()
    state_path = _state_file(domain)
    if state_path.exists():
        state_path.unlink()
    return True


def update_feed_last_fetched(feed_id: str) -> None:
    """Record the current UTC time as last_fetched_at for the given feed."""
    FEEDS_DIR.mkdir(exist_ok=True)
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    feed = get_feed(feed_id)
    if not feed:
        return
    state = _load_domain_state(feed["domain"])
    state.setdefault(feed_id, {})["last_fetched_at"] = now
    _save_domain_state(feed["domain"], state)


def update_feed_status(
    feed_id: str,
    status: str,                  # "ok" | "error"
    error: Optional[str] = None,
    duration_s: Optional[float] = None,
    new_entries: Optional[int] = None,
    content_status: Optional[str] = None,  # "good" | "poor" | "none"
) -> None:
    """Persist the outcome of the most recent ingest attempt for a feed."""
    FEEDS_DIR.mkdir(exist_ok=True)
    feed = get_feed(feed_id)
    if not feed:
        return
    state = _load_domain_state(feed["domain"])
    state.setdefault(feed_id, {}).update({
        "last_status":      status,
        "last_error":       error,
        "last_duration_s":  round(duration_s, 1) if duration_s is not None else None,
        "last_new_entries": new_entries,
    })
    if content_status is not None:
        state[feed_id]["content_status"] = content_status
    _save_domain_state(feed["domain"], state)

def update_feed_rate(feed_id: str, minutes: int) -> bool:
    """Update update_rate for a feed and persist. Returns True if found."""
    FEEDS_DIR.mkdir(exist_ok=True)
    for path in FEEDS_DIR.glob("*.json"):
        if path.name.endswith(".state.json"):
            continue
        domain = path.stem
        feeds  = _load_domain_file(domain)
        for feed in feeds:
            if feed["id"] == feed_id:
                feed["update_rate"] = minutes
                _save_domain_file(domain, feeds)
                return True
    return False


def update_feed(feed_id: str, name: str, url: str, update_rate: int, feed_type: str) -> Optional[dict]:
    """Update name, url, update_rate and type for a feed. Returns updated feed or None if not found."""
    FEEDS_DIR.mkdir(exist_ok=True)
    for path in FEEDS_DIR.glob("*.json"):
        if path.name.endswith(".state.json"):
            continue
        domain = path.stem
        feeds  = _load_domain_file(domain)
        for feed in feeds:
            if feed["id"] == feed_id:
                old_name = feed["name"]
                old_id   = feed["id"]
                feed["name"] = name
                feed["url"] = url
                feed["update_rate"] = update_rate
                feed["type"] = feed_type
                _save_domain_file(domain, feeds)
                state = _load_domain_state(domain)
                state.pop(old_id, None)
                _save_domain_state(domain, state)
                if old_name != name:
                    rename_feed_entries(domain, old_name, name)
                return get_feed(_feed_identity(domain, name, url))
    return None


def rename_domain_feeds(old: str, new: str) -> bool:
    """Rename a domain's feed file and update the domain field in every feed entry."""
    old_path = _domain_file(old)
    if not old_path.exists():
        return False
    _, _, age_settings = _read_domain_spec(old)
    feeds = _load_domain_file(old)
    for f in feeds:
        f["domain"] = new
    FEEDS_DIR.mkdir(exist_ok=True)
    with open(_domain_file(new), "w", encoding="utf-8") as handle:
        json.dump(_build_export_spec(new, feeds, age_settings), handle, indent=2)
    old_path.unlink()
    old_state_path = _state_file(old)
    if old_state_path.exists():
        old_state_path.unlink()
    return True
