from __future__ import annotations

import copy
import threading
import time

from app.database import get_entry_count, list_domains
from app.feed_manager import get_domain_enabled, list_feed_domains, load_feeds

_CACHE_TTL_SECONDS = 2.0

_cache_lock    = threading.Lock()
_cache_until   = 0.0
_cached_result: dict | None = None


def _compute_overview() -> dict:
    db_domains   = set(list_domains())
    feed_domains = set(list_feed_domains())
    all_domains  = sorted(db_domains | feed_domains)
    all_feeds    = load_feeds()
    domains      = [
        {
            "domain":      domain,
            "entry_count": get_entry_count(domain),
            "enabled":     get_domain_enabled(domain),
        }
        for domain in all_domains
    ]
    return {
        "domains":       domains,
        "all_feeds":     all_feeds,
        "total_domains": len(domains),
        "total_feeds":   len(all_feeds),
        "total_entries": sum(item["entry_count"] for item in domains),
    }


def get_feed_overview(*, force: bool = False) -> dict:
    global _cache_until, _cached_result
    now = time.monotonic()
    with _cache_lock:
        if not force and _cached_result is not None and now < _cache_until:
            return copy.deepcopy(_cached_result)
        result         = _compute_overview()
        _cached_result = result
        _cache_until   = now + _CACHE_TTL_SECONDS
        return copy.deepcopy(result)


def invalidate_feed_overview() -> None:
    global _cache_until, _cached_result
    with _cache_lock:
        _cache_until   = 0.0
        _cached_result = None
