# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Background RSS/Atom feed polling scheduler for KoreFeed.
#
# Runs a threading-based scheduler that polls each enabled feed on a configurable interval.
# Uses feedparser + trafilatura for article parsing.  Enforces age-based retention rules
# and logs polling activity to the KoreComms activity_log.
#
# Public API:
#   start()   -- launch the background polling thread (called from server.py lifespan)
#   stop()    -- signal the polling thread to exit
#
# Related modules:
#   - app/feed_manager.py  -- loads feed configuration (URLs, domains, enabled state)
#   - app/database.py      -- writes parsed articles to SQLite
#   - app/config.py        -- cfg (poll_interval, retention_days)
# ====================================================================================================
import collections
import html as _html
import queue
import re
import threading
import feedparser
import httpx
import trafilatura
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

from apscheduler.schedulers.background import BackgroundScheduler

from app.database import (
    apply_age_rule,
    get_domain_age_settings,
    insert_entry,
    list_domains,
)
from app.feed_manager import load_feeds, get_feed, update_feed_last_fetched, update_feed_status

scheduler = BackgroundScheduler(
    daemon=True,
    job_defaults={"misfire_grace_time": None, "coalesce": True},
)

_queue: queue.Queue = queue.Queue()
_state_lock = threading.Lock()
_queued_feed_ids: set[str] = set()
_current_feed_id: str | None = None

_HTTP_HEADERS = {
    "User-Agent": "MiniFeed/1.0 RSS Ingest Bot (+https://github.com/minifeed)"
}

_LOG_FILE = Path("actions.log")
_LOG_MAX_LINES = 1000
_log_lock = threading.Lock()
_log_buffer: collections.deque = collections.deque(maxlen=_LOG_MAX_LINES)


def _log_init() -> None:
    """Seed the in-memory log buffer from the existing log file (if any)."""
    if _LOG_FILE.exists():
        try:
            lines = _LOG_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
            _log_buffer.extend(lines)
        except OSError:
            pass


_log_init()


def _log(msg: str) -> None:
    """Append a timestamped line to actions.log via an in-memory deque (no read on write)."""
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n"
    with _log_lock:
        _log_buffer.append(line)
        _LOG_FILE.write_text("".join(_log_buffer), encoding="utf-8")


def _fetch_page_text(url: str) -> str:
    try:
        resp = httpx.get(url, timeout=20, follow_redirects=True, headers=_HTTP_HEADERS)
        resp.raise_for_status()
        return trafilatura.extract(resp.text) or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Web-crawl feed helpers
# ---------------------------------------------------------------------------

class _AnchorParser(HTMLParser):
    """
    Collects <a href> links with semantic zone awareness.

    Priority order:
      1. Links inside <main> or <article> elements (content zone).
      2. Fallback: links outside <nav>, <header>, and <footer> (body links).

    Links inside navigation/header/footer are always excluded so that listing
    pages (e.g. news indexes) don't flood the crawl queue with site-chrome URLs.
    """

    def __init__(self):
        super().__init__()
        self._main_hrefs: list[str] = []  # links inside <main> / <article>
        self._body_hrefs: list[str] = []  # links outside nav/header/footer
        self._main_depth: int = 0         # nesting depth inside content zones
        self._skip_depth: int = 0         # nesting depth inside chrome zones

    @property
    def hrefs(self) -> list[str]:
        """Return content-zone links when available, else body-level fallback."""
        return self._main_hrefs if self._main_hrefs else self._body_hrefs

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("main", "article"):
            self._main_depth += 1
        elif tag in ("nav", "header", "footer"):
            self._skip_depth += 1
        elif tag == "a" and self._skip_depth == 0:
            for name, val in attrs:
                if name == "href" and val:
                    if self._main_depth > 0:
                        self._main_hrefs.append(val)
                    else:
                        self._body_hrefs.append(val)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("main", "article"):
            self._main_depth = max(0, self._main_depth - 1)
        elif tag in ("nav", "header", "footer"):
            self._skip_depth = max(0, self._skip_depth - 1)


def _extract_links(html: str, base_url: str) -> list[str]:
    """Return same-domain absolute hrefs from an HTML page, deduped, no fragments."""
    p = _AnchorParser()
    try:
        p.feed(html)
    except Exception:
        pass
    base_netloc = urlparse(base_url).netloc
    seen: set[str] = {base_url}
    result: list[str] = []
    for href in p.hrefs:
        try:
            abs_url = urljoin(base_url, href)
            parsed = urlparse(abs_url)
        except Exception:
            continue
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc != base_netloc:
            continue
        clean = parsed._replace(fragment="").geturl()
        if clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


_MIN_ARTICLE_WORDS = 200  # word-count floor for calling a page an article


# ---------------------------------------------------------------------------
# JSON listing helpers  (for JS-rendered news indexes)
# ---------------------------------------------------------------------------

def _parse_json_items(data: object, page_url: str) -> list[tuple[str, str, str]]:
    """Extract (absolute_url, title, date_str) tuples from a JSON API response.

    Handles a top-level list, or a dict with items at common key paths one or
    two levels deep (e.g. ``results.items``, ``data``, ``articles`` …).
    """
    origin = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"

    # Locate the items list
    items: list | None = None
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in ("items", "data", "results", "articles", "posts", "news", "entries", "content"):
            val = data.get(key)
            if isinstance(val, list):
                items = val
                break
            if isinstance(val, dict):
                for sub in ("items", "data", "results", "articles", "posts", "entries"):
                    if isinstance(val.get(sub), list):
                        items = val[sub]
                        break
            if items is not None:
                break
    if not items:
        return []

    result: list[tuple[str, str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # URL
        url_val: str | None = None
        for f in ("url", "link", "href", "slug", "permalink"):
            v = item.get(f)
            if v and isinstance(v, str):
                url_val = v
                break
        if not url_val:
            continue
        abs_url = (
            urljoin(origin + "/", url_val.lstrip("/"))
            if url_val.startswith("/")
            else url_val
        )
        if not abs_url.startswith(("http://", "https://")):
            continue
        # Title
        title = ""
        for f in ("title", "name", "headline", "subject", "heading"):
            v = item.get(f)
            if v and isinstance(v, str):
                title = _html.unescape(v).replace("\xa0", " ").strip()
                break
        # Date
        date_str = ""
        for f in ("date", "published", "publishedAt", "published_at",
                  "created_at", "createdAt", "pubDate", "datePublished"):
            v = item.get(f)
            if v and isinstance(v, str):
                date_str = v
                break
        result.append((abs_url, title, date_str))
    return result


_DATE_FORMATS = [
    "%m/%d/%Y %I:%M:%S %p",   # 4/15/2026 12:00:00 AM  (DE&S style)
    "%m/%d/%Y %H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d %B %Y",
    "%B %d, %Y",
]


def _parse_date_flexible(date_str: str) -> str:
    """Parse a date string in various common formats; returns YYYY-MM-DD HH:MM:SS."""
    s = date_str.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%Y-%m-%d 00:00:00")
    except ValueError:
        pass
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _try_json_listing(page_url: str) -> tuple[str, list[tuple[str, str, str]]]:
    """Try a JSON listing API corresponding to a JS-rendered page URL.

    Strategy: inject ``/api`` before the URL path so that, for example,
    ``/news/?page=1&pageSize=25`` becomes ``/api/news?page=1&pageSize=25``.

    Returns ``(api_url, items)`` on success, or ``("", [])`` if not found.
    """
    try:
        parsed = urlparse(page_url)
        path = parsed.path.rstrip("/") or "/"
        api_url = parsed._replace(path=f"/api{path}").geturl()
        r = httpx.get(
            api_url, timeout=10, follow_redirects=True,
            headers={**_HTTP_HEADERS, "Accept": "application/json"},
        )
        if r.status_code != 200:
            return "", []
        ct = r.headers.get("content-type", "")
        if "json" not in ct and r.text.lstrip()[:1] not in ("{", "["):
            return "", []
        return api_url, _parse_json_items(r.json(), page_url)
    except Exception:
        return "", []


def _assess_html(html: str, url: str) -> tuple[bool, str, str, str, dict]:
    """Returns (is_article, title, page_text, published, metadata)."""
    result = trafilatura.bare_extraction(
        html, url=url, include_comments=False, include_tables=True, as_dict=True
    )
    if not result:
        return False, "", "", "", {}
    text = result.get("text") or ""
    title = _html.unescape(result.get("title") or "")
    # Fallback title extraction from <h1> or <title> tag
    if not title:
        m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.IGNORECASE | re.DOTALL)
        if m:
            title = _html.unescape(re.sub(r'<[^>]+>', '', m.group(1)).strip())
    if not title:
        m = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
        if m:
            raw = _html.unescape(m.group(1).strip())
            title = re.split(r'\s[\-|\u2013]\s', raw)[0].strip()
    word_count = len(text.split())
    raw_date = result.get("date") or ""
    try:
        published = (
            datetime.strptime(raw_date[:10], "%Y-%m-%d").strftime("%Y-%m-%d 00:00:00")
            if raw_date else datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        )
    except ValueError:
        published = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    metadata = {
        "author": result.get("author") or "",
        "tags": list(result.get("categories") or []) + list(result.get("tags") or []),
        "summary": text[:300] if text else "",
        "word_count": word_count,
    }
    return word_count >= _MIN_ARTICLE_WORDS, title, text, published, metadata


def ingest_web_feed(feed: dict) -> None:
    t_start = datetime.utcnow()
    _log(f"Web crawl: {feed['name']} ({feed['url']})")

    # Fetch the starting page
    try:
        r0 = httpx.get(feed["url"], timeout=15, follow_redirects=True, headers=_HTTP_HEADERS)
        r0.raise_for_status()
        html0, base_url = r0.text, str(r0.url)
    except httpx.TimeoutException:
        duration = (datetime.utcnow() - t_start).total_seconds()
        update_feed_status(feed["id"], "error", error="Start page timed out (>15s)",
                           duration_s=duration, new_entries=0, content_status="none")
        return
    except Exception as exc:
        duration = (datetime.utcnow() - t_start).total_seconds()
        update_feed_status(feed["id"], "error", error=f"{type(exc).__name__}: {exc}",
                           duration_s=duration, new_entries=0, content_status="none")
        return

    visited: set[str] = {base_url}
    new_entries = pages_assessed = pages_with_content = 0

    def _try_save(url: str, html: str) -> bool:
        nonlocal pages_assessed, pages_with_content, new_entries
        pages_assessed += 1
        is_art, title, text, published, meta = _assess_html(html, url)
        if is_art:
            pages_with_content += 1
            if insert_entry(
                domain=feed["domain"], feed_name=feed["name"],
                headline=title or url, url=url,
                published=published, metadata=meta, page_text=text,
            ):
                new_entries += 1
        return is_art

    l1_links = _extract_links(html0, base_url)[:50]
    _log(f"  {feed['name']}: {len(l1_links)} level-1 candidates")

    # If the HTML has fewer than 5 content-zone links the page is likely JS-rendered
    # (a real listing page would have many article links; 1-4 are just inline references).
    # Auto-probe for a JSON listing API (inserts /api before the path).
    if len(l1_links) < 5:
        api_url, json_items = _try_json_listing(base_url)
        if json_items:
            _log(f"  {feed['name']}: JS-rendered page \u2014 delegating to JSON API fallback ({len(json_items)} candidates)")
            ingest_json_listing_feed({**feed, "url": api_url})
            return
        else:
            _log(f"  {feed['name']}: no links found; page may require JS rendering")

    l2_budget = 30  # total extra requests allowed for level-2 expansion

    for url1 in l1_links:
        if url1 in visited:
            continue
        visited.add(url1)
        try:
            r1 = httpx.get(url1, timeout=12, follow_redirects=True, headers=_HTTP_HEADERS)
            r1.raise_for_status()
            html1, final1 = r1.text, str(r1.url)
        except Exception:
            continue
        is_art = _try_save(final1, html1)
        if not is_art and l2_budget > 0:
            for url2 in _extract_links(html1, final1)[:10]:
                if url2 in visited or l2_budget <= 0:
                    break
                visited.add(url2)
                l2_budget -= 1
                try:
                    r2 = httpx.get(url2, timeout=12, follow_redirects=True, headers=_HTTP_HEADERS)
                    r2.raise_for_status()
                    _try_save(str(r2.url), r2.text)
                except Exception:
                    continue

    content_status = "none" if pages_assessed == 0 else ("good" if pages_with_content > 0 else "poor")
    duration = (datetime.utcnow() - t_start).total_seconds()
    update_feed_last_fetched(feed["id"])
    update_feed_status(feed["id"], "ok", error=None, duration_s=duration,
                       new_entries=new_entries, content_status=content_status)
    _log(f"  {feed['name']}: +{new_entries} articles from {pages_assessed} pages in {duration:.1f}s [{content_status}]")


def ingest_json_listing_feed(feed: dict) -> None:
    """Ingest a feed whose URL is a paginated JSON listing API.

    Fetches the API URL, extracts article URLs from the JSON response,
    then fetches and extracts full text for each article page.
    The JSON title and date are used as fallbacks when trafilatura cannot
    extract them from the article page.
    """
    t_start = datetime.utcnow()
    _log(f"JSON listing: {feed['name']} ({feed['url']})")

    try:
        r0 = httpx.get(
            feed["url"], timeout=15, follow_redirects=True,
            headers={**_HTTP_HEADERS, "Accept": "application/json"},
        )
        r0.raise_for_status()
        data = r0.json()
        base_url = str(r0.url)
    except Exception as exc:
        duration = (datetime.utcnow() - t_start).total_seconds()
        update_feed_status(feed["id"], "error", error=f"{type(exc).__name__}: {exc}",
                           duration_s=duration, new_entries=0, content_status="none")
        return

    candidates = _parse_json_items(data, base_url)
    _log(f"  {feed['name']}: {len(candidates)} article candidates")
    if not candidates:
        duration = (datetime.utcnow() - t_start).total_seconds()
        update_feed_status(feed["id"], "error", error="No article URLs found in JSON response",
                           duration_s=duration, new_entries=0, content_status="none")
        return

    visited: set[str] = set()
    new_entries = pages_assessed = pages_with_content = 0

    for abs_url, json_title, json_date in candidates:
        if abs_url in visited:
            continue
        visited.add(abs_url)
        pages_assessed += 1
        try:
            r = httpx.get(abs_url, timeout=15, follow_redirects=True, headers=_HTTP_HEADERS)
            r.raise_for_status()
            html = r.text
        except Exception:
            continue
        _is_art, title, text, published, meta = _assess_html(html, abs_url)
        # Supplement missing title / date from the JSON listing.
        # JSON API dates are always preferred — trafilatura rarely finds them
        # on JS-rendered sites and defaults to utcnow() which is unreliable.
        if not title and json_title:
            title = json_title
        if json_date:
            published = _parse_date_flexible(json_date)
        if text:
            pages_with_content += 1
            if insert_entry(
                domain=feed["domain"], feed_name=feed["name"],
                headline=title or abs_url, url=abs_url,
                published=published, metadata=meta, page_text=text,
            ):
                new_entries += 1

    content_status = "none" if pages_assessed == 0 else ("good" if pages_with_content > 0 else "poor")
    duration = (datetime.utcnow() - t_start).total_seconds()
    update_feed_last_fetched(feed["id"])
    update_feed_status(feed["id"], "ok", error=None, duration_s=duration,
                       new_entries=new_entries, content_status=content_status)
    _log(f"  {feed['name']}: +{new_entries} articles from {pages_assessed} pages in {duration:.1f}s [{content_status}]")


def ingest_feed(feed: dict) -> None:
    if feed.get("type") == "web":
        ingest_web_feed(feed)
        return
    if feed.get("type") == "json_listing":
        ingest_json_listing_feed(feed)
        return
    _log(f"Ingesting: {feed['name']}")
    t_start = datetime.utcnow()

    age = get_domain_age_settings(feed["domain"])
    now = datetime.utcnow()
    gate_after: datetime | None = None
    gate_before: datetime | None = None
    if age["mode"] == "days_previous" and age["days"]:
        gate_after = now - timedelta(days=age["days"])
    elif age["mode"] == "calendar_period":
        if age["start_date"]:
            gate_after = datetime.strptime(age["start_date"], "%Y-%m-%d")
        if age["end_date"]:
            gate_before = datetime.strptime(age["end_date"], "%Y-%m-%d").replace(
                hour=23, minute=59, second=59
            )

    # Fetch the RSS feed via httpx so we can enforce a timeout
    try:
        rss_resp = httpx.get(
            feed["url"], timeout=15, follow_redirects=True, headers=_HTTP_HEADERS
        )
        rss_resp.raise_for_status()
    except httpx.TimeoutException:
        duration = (datetime.utcnow() - t_start).total_seconds()
        msg = "feed fetch timed out (>15s)"
        _log(f"  {feed['name']}: {msg}")
        update_feed_status(feed["id"], "error", error=msg, duration_s=duration, new_entries=0,
                           content_status="none")
        return
    except Exception as exc:
        duration = (datetime.utcnow() - t_start).total_seconds()
        msg = f"{type(exc).__name__}: {exc}"
        _log(f"  {feed['name']}: {msg}")
        update_feed_status(feed["id"], "error", error=msg, duration_s=duration, new_entries=0,
                           content_status="none")
        return

    try:
        parsed = feedparser.parse(rss_resp.text)
        new_entries = 0
        entries_processed = 0
        pages_with_content = 0
        for entry in parsed.entries:
            headline = entry.get("title", "")
            url = entry.get("link", "")
            pub_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
            if pub_parsed:
                pub_dt = datetime(*pub_parsed[:6])
                published = pub_dt.strftime("%Y-%m-%d %H:%M:%S")
                if gate_after and pub_dt < gate_after:
                    continue
                if gate_before and pub_dt > gate_before:
                    continue
            else:
                published = entry.get("published", entry.get("updated", ""))
            entries_processed += 1
            metadata = {
                "author": entry.get("author", ""),
                "tags": [t.get("term", "") for t in entry.get("tags", [])],
                "summary": entry.get("summary", ""),
            }
            page_text = _fetch_page_text(url) if url else ""
            if page_text:
                pages_with_content += 1
            inserted = insert_entry(
                domain=feed["domain"],
                feed_name=feed["name"],
                headline=headline,
                url=url,
                published=published,
                metadata=metadata,
                page_text=page_text,
            )
            if inserted:
                new_entries += 1
    except Exception as exc:
        duration = (datetime.utcnow() - t_start).total_seconds()
        msg = f"{type(exc).__name__}: {exc}"
        _log(f"  Error ingesting {feed['name']}: {msg}")
        update_feed_status(feed["id"], "error", error=msg, duration_s=duration, new_entries=0)
        return

    if entries_processed == 0:
        content_status = "none"
    elif pages_with_content > 0:
        content_status = "good"
    else:
        content_status = "poor"

    duration = (datetime.utcnow() - t_start).total_seconds()
    update_feed_last_fetched(feed["id"])
    update_feed_status(feed["id"], "ok", error=None, duration_s=duration, new_entries=new_entries,
                       content_status=content_status)
    _log(f"  {feed['name']}: +{new_entries} entries in {duration:.1f}s [{content_status}]")


def _enqueue(feed: dict) -> None:
    """Called by the scheduler; skips queuing if the feed was fetched recently."""
    # Reload from disk so we see the updated last_fetched_at
    current = get_feed(feed["id"])
    if current is None:
        return  # feed was deleted
    last = current.get("last_fetched_at")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            next_due = last_dt + timedelta(minutes=current["update_rate"])
            now = datetime.utcnow()
            if now < next_due:
                mins = int((next_due - now).total_seconds() / 60)
                _log(f"  Skipping {current['name']} — next fetch in ~{mins}m")
                return
        except ValueError:
            pass  # malformed timestamp, fetch anyway

    fid = str(current.get("id", ""))
    if not fid:
        return
    with _state_lock:
        if fid == _current_feed_id or fid in _queued_feed_ids:
            _log(f"  Skipping {current['name']} - already queued/running")
            return
        _queued_feed_ids.add(fid)
    _queue.put(current)


def _enqueue_most_overdue() -> None:
    """Once-per-minute catch-up: enqueue exactly one most-overdue feed."""
    # Let existing queued work drain first.
    if _queue.qsize() > 0:
        return

    now = datetime.utcnow()
    winner: dict | None = None
    winner_overdue_secs: float = -1.0

    for feed in load_feeds():
        last = feed.get("last_fetched_at")
        if not last:
            overdue_secs = float("inf")
        else:
            try:
                next_due = datetime.fromisoformat(last) + timedelta(minutes=int(feed.get("update_rate", 60)))
            except Exception:
                continue
            overdue_secs = (now - next_due).total_seconds()
            if overdue_secs < 0:
                continue

        if overdue_secs > winner_overdue_secs:
            winner = feed
            winner_overdue_secs = overdue_secs

    if winner is None:
        return

    overdue_mins = "unknown" if winner_overdue_secs == float("inf") else str(int(winner_overdue_secs // 60))
    _log(f"Catch-up check: enqueueing most overdue feed {winner.get('name', '?')} (overdue ~{overdue_mins}m)")
    _enqueue(winner)


def _worker() -> None:
    """Single background thread: drains the queue one feed at a time."""
    global _current_feed_id
    while True:
        feed = _queue.get()
        fid = str(feed.get("id", ""))
        if fid:
            with _state_lock:
                _queued_feed_ids.discard(fid)
                _current_feed_id = fid
        try:
            ingest_feed(feed)
        finally:
            if fid:
                with _state_lock:
                    if _current_feed_id == fid:
                        _current_feed_id = None
            _queue.task_done()


def schedule_feeds() -> None:
    """Rebuild the scheduler job list from the current feed inventory."""
    scheduler.remove_all_jobs()
    now = datetime.utcnow()
    for feed in load_feeds():
        # Align the first tick to last_fetched_at + update_rate so the scheduler
        # doesn't drift relative to actual fetch times after a restart.
        next_run = None
        last = feed.get("last_fetched_at")
        if last:
            try:
                next_run = datetime.fromisoformat(last) + timedelta(minutes=feed["update_rate"])
                if next_run < now:
                    next_run = now  # already overdue — fire immediately
            except Exception:
                pass
        scheduler.add_job(
            _enqueue,
            "interval",
            minutes=feed["update_rate"],
            args=[feed],
            id=feed["id"],
            replace_existing=True,
            next_run_time=next_run,
        )


def trigger_immediate(feed: dict) -> None:
    """Push a feed directly onto the ingest queue."""
    fid = str(feed.get("id", ""))
    if fid:
        with _state_lock:
            if fid == _current_feed_id or fid in _queued_feed_ids:
                _log(f"  Skipping manual trigger for {feed.get('name', '?')} - already queued/running")
                return
            _queued_feed_ids.add(fid)
    _queue.put(feed)


def _daily_prune() -> None:
    """Apply each domain's age rule once per day. Called hourly; skips if already done today."""
    for domain in list_domains():
        n = apply_age_rule(domain)
        if n:
            _log(f"Daily prune: {domain} — {n} entries removed")


def start_scheduler() -> None:
    # Start the single worker thread
    t = threading.Thread(target=_worker, daemon=True, name="ingest-worker")
    t.start()

    # Run startup prune in background so uvicorn can respond immediately
    threading.Thread(target=_daily_prune, daemon=True, name="startup-prune").start()

    schedule_feeds()

    # Hourly job: applies age rules for any domain not yet pruned today
    scheduler.add_job(
        _daily_prune,
        "interval",
        hours=1,
        id="daily_prune",
        replace_existing=True,
    )

    # Catch up after sleep/wake: check every minute and run one most-overdue feed.
    scheduler.add_job(
        _enqueue_most_overdue,
        "interval",
        minutes=1,
        id="overdue_catchup",
        replace_existing=True,
    )

    # Enqueue feeds that are due — respects last_fetched_at gate
    for feed in load_feeds():
        _enqueue(feed)

    scheduler.start()
