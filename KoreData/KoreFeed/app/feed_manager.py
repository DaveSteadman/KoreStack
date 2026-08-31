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
# MARK: FUNCTIONS
# Function inventory:
# - _domain_file: Implements the  domain file operation for this module.
# - _state_file: Implements the  state file operation for this module.
# - _feed_identity: Implements the  feed identity operation for this module.
# - _load_domain_state: Implements the  load domain state operation for this module.
# - _write_json_atomic: Implements the  write json atomic operation for this module.
# - _save_domain_state: Implements the  save domain state operation for this module.
# - _normalise_age_settings: Implements the  normalise age settings operation for this module.
# - _normalise_domain_enabled: Implements the  normalise domain enabled operation for this module.
# - _read_domain_spec: Implements the  read domain spec operation for this module.
# - _apply_domain_age_settings: Implements the  apply domain age settings operation for this module.
# - _load_domain_file: Implements the  load domain file operation for this module.
# - _save_domain_file: Implements the  save domain file operation for this module.
# - _build_export_feed: Implements the  build export feed operation for this module.
# - _build_export_spec: Implements the  build export spec operation for this module.
# - _clean_feed_for_import: Implements the  clean feed for import operation for this module.
# - _normalise_import_feeds: Implements the  normalise import feeds operation for this module.
# - load_feeds: Loads feeds for this module.
# - load_feeds_for_domain: Loads feeds for domain for this module.
# - list_feed_domains: Lists feed domains for this module.
# - get_feed: Returns feed for this module.
# - add_feed: Implements the add feed operation for this module.
# - remove_feed: Implements the remove feed operation for this module.
# - create_domain: Creates domain for this module.
# - update_domain_age_settings_spec: Updates domain age settings spec for this module.
# - sync_domain_spec: Implements the sync domain spec operation for this module.
# - delete_domain_feeds: Deletes domain feeds for this module.
# - update_feed_last_fetched: Updates feed last fetched for this module.
# - update_feed_status: Updates feed status for this module.
# - update_feed_rate: Updates feed rate for this module.
# - update_feed: Updates feed for this module.
# - rename_domain_feeds: Implements the rename domain feeds operation for this module.
# - get_domain_enabled: Returns domain enabled for this module.
# - set_domain_enabled: Sets domain enabled for this module.
# ====================================================================================================
import json
import logging
import os
import re
import threading
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import cfg
from app.database import get_domain_age_settings, init_db, rename_domain_db, rename_feed_entries, set_domain_age_settings

FEEDS_DIR = Path(cfg["data_dir"])
LOG       = logging.getLogger("korefeed.feed_manager")

_state_cache: dict[Path, dict[str, dict]] = {}
_state_cache_lock = threading.RLock()


def validate_domain_name(domain: str) -> str:
    """Return a URL- and filesystem-safe domain identifier."""
    name = str(domain or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
        raise ValueError("Domain names may contain only letters, digits, underscores, and hyphens")
    return name


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
    with _state_cache_lock:
        cached = _state_cache.get(path)
        if cached is not None:
            return deepcopy(cached)

    if not path.exists():
        state: dict[str, dict] = {}
    else:
        try:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except json.JSONDecodeError as exc:
            LOG.warning("Ignoring unreadable feed state file %s: %s", path, exc)
            raw = {}
        state = {
            str(key): value
            for key, value in raw.items()
            if isinstance(value, dict)
        } if isinstance(raw, dict) else {}

    with _state_cache_lock:
        _state_cache[path] = state
    return deepcopy(state)


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError as exc:
            LOG.warning("Could not remove temporary feed file %s: %s", temp_path, exc)


def remove_orphaned_temp_files() -> int:
    """Remove stale atomic-write files left by an interrupted previous process."""
    FEEDS_DIR.mkdir(exist_ok=True)
    removed = 0
    for temp_path in FEEDS_DIR.glob("*.tmp"):
        try:
            temp_path.unlink()
            removed += 1
        except OSError as exc:
            LOG.warning("Could not remove stale temporary feed file %s: %s", temp_path, exc)
    return removed


def _save_domain_state(domain: str, state: dict[str, dict]) -> None:
    FEEDS_DIR.mkdir(exist_ok=True)
    path = _state_file(domain)
    _write_json_atomic(path, state)
    with _state_cache_lock:
        _state_cache[path] = deepcopy(state)


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


def _normalise_domain_enabled(raw_enabled: object) -> bool:
    if isinstance(raw_enabled, bool):
        return raw_enabled
    if isinstance(raw_enabled, (int, float)):
        return bool(raw_enabled)
    if isinstance(raw_enabled, str):
        value = raw_enabled.strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
    return True


def _read_domain_spec(domain: str) -> tuple[str, list, dict, bool]:
    path = _domain_file(domain)
    if not path.exists():
        return domain, [], {"mode": "none", "days": None, "start_date": None, "end_date": None}, True

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        LOG.warning("Could not read feed domain spec %s: %s", path, exc)
        return domain, [], {"mode": "none", "days": None, "start_date": None, "end_date": None}, True

    if not raw_text.strip():
        LOG.warning("Feed domain spec is empty; treating as empty domain: %s", path)
        return domain, [], {"mode": "none", "days": None, "start_date": None, "end_date": None}, True

    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        LOG.warning("Feed domain spec is invalid JSON; treating as empty domain: %s (%s)", path, exc)
        return domain, [], {"mode": "none", "days": None, "start_date": None, "end_date": None}, True

    if isinstance(raw, dict):
        raw_domain        = str(raw.get("domain") or domain).strip() or domain
        raw_feeds         = raw.get("feeds", [])
        raw_age_settings  = _normalise_age_settings(raw.get("age_settings"))
        raw_enabled       = _normalise_domain_enabled(raw.get("enabled", True))
    elif isinstance(raw, list):
        raw_domain        = domain
        raw_feeds         = raw
        raw_age_settings  = {"mode": "none", "days": None, "start_date": None, "end_date": None}
        raw_enabled       = True
    else:
        return domain, [], {"mode": "none", "days": None, "start_date": None, "end_date": None}, True

    if not isinstance(raw_feeds, list):
        raw_feeds = []
    return raw_domain, raw_feeds, raw_age_settings, raw_enabled


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
    raw_domain, raw_feeds, _raw_age_settings, raw_enabled = _read_domain_spec(domain)

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
            "id":             feed_id,
            "domain":         raw_domain,
            "domain_enabled": raw_enabled,
            "name":           name,
            "url":            url,
            "update_rate":    int(item.get("update_rate") or 60),
            "type":           str(item.get("type") or "rss").strip() or "rss",
        }
        if feed["update_rate"] < 1:
            feed["update_rate"] = 60
        feed.update(state.get(feed_id, {}))
        feeds.append(feed)
    return feeds


def _save_domain_file(domain: str, feeds: list[dict]) -> None:
    FEEDS_DIR.mkdir(exist_ok=True)
    enabled      = get_domain_enabled(domain)
    age_settings = get_domain_age_settings(domain)
    _write_json_atomic(
        _domain_file(domain),
        _build_export_spec(domain, feeds, age_settings, enabled),
    )


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


def _build_export_spec(domain: str, feeds: list[dict], age_settings: Optional[dict] = None, enabled: bool = True) -> dict:
    return {
        "domain":       domain,
        "enabled":      bool(enabled),
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
    _write_json_atomic(
        path,
        {
            "domain":       domain,
            "enabled":      True,
            "age_settings": {"mode": "none", "days": None, "start_date": None, "end_date": None},
            "feeds":        [],
        },
    )
    return True


def update_domain_age_settings_spec(
    domain: str,
    mode: str,
    days: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> None:
    _, raw_feeds, _, enabled = _read_domain_spec(domain)
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
                "enabled":      enabled,
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
    spec_domain, raw_feeds, age_settings, enabled = _read_domain_spec(domain)
    _apply_domain_age_settings(spec_domain, age_settings)
    FEEDS_DIR.mkdir(exist_ok=True)
    with open(_domain_file(spec_domain), "w", encoding="utf-8") as handle:
        json.dump(
            _build_export_spec(
                spec_domain,
                _normalise_import_feeds(raw_feeds, spec_domain),
                get_domain_age_settings(spec_domain),
                enabled,
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
    with _state_cache_lock:
        _state_cache.pop(state_path, None)
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
    old = validate_domain_name(old)
    new = validate_domain_name(new)
    if old == new:
        return True
    old_path = _domain_file(old)
    if not old_path.exists():
        return False
    new_path = _domain_file(new)
    if new_path.exists():
        raise FileExistsError(f"Domain '{new}' already exists")
    _, _, age_settings, enabled = _read_domain_spec(old)
    feeds = _load_domain_file(old)
    for f in feeds:
        f["domain"] = new
    old_state_path = _state_file(old)
    new_state_path = _state_file(new)
    if new_state_path.exists():
        raise FileExistsError(f"Domain state for '{new}' already exists")

    old_path.replace(new_path)
    try:
        _write_json_atomic(new_path, _build_export_spec(new, feeds, age_settings, enabled))
        if old_state_path.exists():
            old_state = _load_domain_state(old)
            new_state = {
                _feed_identity(new, feed["name"], feed["url"]): old_state.get(_feed_identity(old, feed["name"], feed["url"]), {})
                for feed in feeds
            }
            _save_domain_state(new, new_state)
            old_state_path.unlink()
    except Exception:
        if new_path.exists() and not old_path.exists():
            new_path.replace(old_path)
        raise
    with _state_cache_lock:
        _state_cache.pop(old_state_path, None)
    return True


def rename_domain(old: str, new: str) -> bool:
    """Rename all feed-domain artifacts, restoring the feed specification on failure."""
    old = validate_domain_name(old)
    new = validate_domain_name(new)
    if not rename_domain_feeds(old, new):
        return False
    try:
        rename_domain_db(old, new)
    except Exception:
        rename_domain_feeds(new, old)
        raise
    return True


def get_domain_enabled(domain: str) -> bool:
    _, _, _, enabled = _read_domain_spec(domain)
    return enabled


def set_domain_enabled(domain: str, enabled: bool) -> bool:
    spec_domain, raw_feeds, age_settings, _ = _read_domain_spec(domain)
    path = _domain_file(domain)
    FEEDS_DIR.mkdir(exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(
            _build_export_spec(
                spec_domain,
                _normalise_import_feeds(raw_feeds, spec_domain),
                age_settings,
                enabled,
            ),
            handle,
            indent=2,
        )
    return True
