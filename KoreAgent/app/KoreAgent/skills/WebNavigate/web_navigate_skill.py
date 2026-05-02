# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# WebNavigate skill for MiniAgentFramework.
#
# Extracts all navigable hyperlinks from a web page and returns them as a structured list or
# formatted text block. Designed as the middle link in the web chain:
#
#   search_web           -> candidate entry-point URLs
#   get_page_links_text  -> link list from a listing/index page (this skill)
#   fetch_page_text      -> clean prose from a specific article/detail page
#
# Use this when you land on a hub page (news front page, GitHub topic, forum index) and need
# to see what links are available so you can decide which ones to follow. The output is
# deliberately large enough to auto-park in the scratchpad, where scratch_query can then
# do semantic selection: "which of these links are about open source LLMs?"
#
# Related modules:
#   - skills/WebSearch/web_search_skill.py  -- upstream: produces candidate URLs
#   - skills/WebFetch/web_fetch_skill.py    -- downstream: reads chosen pages
#   - system_skills/Scratchpad/scratchpad_skill.py -- semantic filtering of parked link lists
#   - webpage_utils.py                      -- shared HTTP fetch utility
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import html as _html
import re
import sys
import urllib.error
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

_code_dir = str(Path(__file__).resolve().parents[3])
if _code_dir not in sys.path:
    sys.path.insert(0, _code_dir)

from KoreAgent.utils.webpage_utils import fetch_html as _fetch_html


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
MAX_LINKS_CAP           = 100
DEFAULT_MAX_LINKS       = 30
DEFAULT_TIMEOUT         = 15
TIMEOUT_CAP             = 60
# Max links sharing the same parent-path prefix before the rest are discarded.
# Applied only when path depth >= 2 so query-varied links (e.g. HN /item?id=...) are unaffected.
# This prevents sidebar floods like GitHub's 180-entry language selector on /trending.
_MAX_SAME_PREFIX_LINKS  = 6

# Anchor texts that are universally navigation noise regardless of site.
_NAV_NOISE = frozenset({
    "skip to content", "skip to main content", "skip navigation",
    "home", "back", "next", "previous", "prev", "close", "menu",
    "sign in", "sign up", "log in", "log out", "login", "logout",
    "register", "subscribe", "newsletter", "privacy policy", "terms",
    "cookie", "cookies", "accessibility", "rss", "sitemap", "contact",
    "about", "advertise", "careers", "help", "faq", "print", "",
    # Common per-item UI actions that appear as links on sites like HN.
    "hide", "flag", "vouch",
    # GitHub per-repo fundraising chrome and sponsor labels.
    "sponsor", "sponsoring",
})

# Minimum anchor text length - shorter strings are usually button labels.
_MIN_TEXT_LEN = 3

# URL path fragments that reliably indicate navigation/utility links rather than content.
_SKIP_PATH_FRAGMENTS = frozenset({
    "/login", "/logout", "/signin", "/signup", "/register",
    "/account", "/profile", "/settings", "/preferences",
    "/cart", "/checkout", "/subscribe", "/unsubscribe",
    "#", "javascript:", "mailto:", "tel:",
    # HN per-story UI chrome: hide/undo action, user profile, domain-filter, vote links.
    "/hide?", "/user?", "/from?",
    # GitHub trending: spoken-language selector renders ~180 <a> tags in static HTML.
    "spoken_language_code",
    # GitHub per-repo quantitative labels (star/fork count pages) and fundraising chrome.
    "/stargazers", "/forks", "/sponsors/",
})

_SPACE_RE = re.compile(r"\s+")

# Anchor text that is purely a numeric counter with an optional parenthetical label,
# e.g. "5,638 (stargazers)" or "667 (forks)". These are display-only count badges.
_COUNTER_LABEL_RE = re.compile(r"^\d[\d,\s]*(?:\([^)]+\))?$")


# ====================================================================================================
# MARK: HTML LINK EXTRACTOR
# ====================================================================================================
class _LinkExtractor(HTMLParser):
    """Stdlib HTMLParser subclass that collects <a href> links with their anchor text.

    Also captures the page <title> and any <base href> so callers can use the correct
    base URL when resolving relative links.
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_skip  = 0         # depth inside tags we must ignore entirely
        self._in_a     = False
        self._in_title = False
        self._buf:    list[str] = []
        self.links:   list[tuple[str, str]] = []  # [(href, text), ...]
        self.base_href: str = ""   # from <base href="..."> if the page declares one
        self.page_title: str = ""  # from <title>...</title>

    # Tags whose entire subtree is ignored (navigation chrome, scripts, etc.)
    _SKIP_TAGS = frozenset({
        "script", "style", "noscript", "nav", "header", "footer",
        "form", "button", "aside", "svg",
    })

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        if tag == "base":
            href = dict(attrs).get("href", "").strip()
            if href and not self.base_href:
                self.base_href = href
            return
        if tag == "title":
            self._in_title = True
            return
        if tag in self._SKIP_TAGS:
            self._in_skip += 1
            return
        if self._in_skip:
            return
        if tag == "a":
            href = dict(attrs).get("href", "")
            self._in_a     = True
            self._buf      = []
            self._cur_href = href or ""

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if tag == "title":
            self._in_title = False
            return
        if tag in self._SKIP_TAGS:
            self._in_skip = max(0, self._in_skip - 1)
            return
        if tag == "a" and self._in_a:
            self._in_a = False
            text = _SPACE_RE.sub(" ", " ".join(self._buf)).strip()
            text = _html.unescape(text)
            if text and self._cur_href:
                self.links.append((self._cur_href, text))
            self._buf      = []
            self._cur_href = ""

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.page_title += data
            return
        if self._in_skip or not self._in_a:
            return
        self._buf.append(data)


# ====================================================================================================
# MARK: URL NORMALISATION
# ====================================================================================================
def _resolve_url(base_url: str, href: str) -> str | None:
    """Resolve href to an absolute URL against base_url. Returns None if unsupported."""
    href = href.strip()
    if not href or href.startswith("javascript:") or href.startswith("mailto:") or href.startswith("tel:"):
        return None
    # Fragment-only anchors: keep only if they have a path component (page sections stripped).
    if href.startswith("#"):
        return None
    resolved = urllib.parse.urljoin(base_url, href)
    parsed   = urllib.parse.urlparse(resolved)
    if parsed.scheme not in ("http", "https"):
        return None
    # Remove fragment from resolved URL.
    clean = urllib.parse.urlunparse(parsed._replace(fragment=""))
    return clean


# Relative-time strings used as anchor text on sites like HN ("1 hour ago", "2 days ago").
_RELATIVE_TIME_RE = re.compile(
    r"^\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago$",
    re.IGNORECASE,
)


# ----------------------------------------------------------------------------------------------------
def _is_noise_link(text: str, url: str) -> bool:
    """Return True if this link is clearly site navigation or utility chrome."""
    if len(text) < _MIN_TEXT_LEN:
        return True
    text_lower = text.lower().strip()
    if text_lower in _NAV_NOISE:
        return True
    if _RELATIVE_TIME_RE.match(text_lower):
        return True
    if _COUNTER_LABEL_RE.match(text_lower):
        return True
    url_lower = url.lower()
    for frag in _SKIP_PATH_FRAGMENTS:
        if frag in url_lower:
            return True
    return False


# ====================================================================================================
# MARK: CORE EXTRACTION
# ====================================================================================================
def _extract_links(html_text: str, base_url: str, filter_text: str, max_links: int) -> tuple[list[dict], str]:
    """Parse HTML and return (filtered_links, page_title).

    The page <base href> is honoured when resolving relative URLs - if the page declares
    a different base the fetched URL is not used for resolution.
    """
    extractor = _LinkExtractor()
    try:
        extractor.feed(html_text)
    except Exception:
        pass  # HTMLParser raises on malformed HTML; take whatever was collected.

    # Honour <base href> declared by the page; fall back to the fetched URL.
    effective_base = extractor.base_href or base_url
    page_title     = _html.unescape(extractor.page_title).strip()

    filter_lower   = filter_text.lower().strip() if filter_text else ""
    seen_urls:       set[str]       = set()
    prefix_counts:   dict[str, int] = {}
    results:         list[dict]     = []

    for href, text in extractor.links:
        if len(results) >= max_links:
            break

        url = _resolve_url(effective_base, href)
        if not url:
            continue

        if _is_noise_link(text, url):
            continue

        if filter_lower and filter_lower not in text.lower() and filter_lower not in url.lower():
            continue

        if url in seen_urls:
            continue

        # Discard links once a path-parent prefix has produced too many entries.
        # Only active when path depth >= 2 (e.g. /trending/python) so single-segment
        # paths with varying query strings (e.g. /item?id=...) are never capped.
        _parsed   = urllib.parse.urlparse(url)
        _parts    = [p for p in _parsed.path.split("/") if p]
        if len(_parts) >= 2:
            _prefix = _parsed.netloc + "/" + "/".join(_parts[:-1])
            prefix_counts[_prefix] = prefix_counts.get(_prefix, 0) + 1
            if prefix_counts[_prefix] > _MAX_SAME_PREFIX_LINKS:
                continue

        seen_urls.add(url)
        results.append({"text": text, "url": url})

    return results, page_title


# ----------------------------------------------------------------------------------------------------
def extract_urls_from_html(html_text: str, base_url: str, max_links: int = 200) -> list[str]:
    # Reusable helper for other skills that already hold fetched HTML and need a clean,
    # noise-filtered URL list without re-fetching the page. Not exposed as a tool.
    links, _ = _extract_links(html_text, base_url, filter_text="", max_links=max(1, int(max_links)))
    return [link["url"] for link in links]


# ====================================================================================================
# MARK: PUBLIC SKILL API
# ====================================================================================================
# MARK: PUBLIC SKILL API
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def _fetch_links_data(
    url: str,
    filter_text: str,
    max_links: int,
    timeout_seconds: int,
) -> dict:
    """Shared core helper - fetches the page and extracts links.

    Returns a dict with keys: links (list[dict]), title (str), final_url (str), error (str|None).
    """
    if not url or not url.strip():
        return {"links": [], "title": "", "final_url": url, "error": "url cannot be empty"}

    parsed_url = urllib.parse.urlparse(url.strip())
    if parsed_url.scheme not in ("http", "https"):
        return {"links": [], "title": "", "final_url": url, "error": f"unsupported URL scheme '{parsed_url.scheme}'"}

    max_links       = max(1, min(int(max_links),       MAX_LINKS_CAP))
    timeout_seconds = max(5, min(int(timeout_seconds), TIMEOUT_CAP))

    try:
        html_text, final_url = _fetch_html(url.strip(), timeout=float(timeout_seconds))
    except urllib.error.HTTPError as exc:
        return {"links": [], "title": "", "final_url": url, "error": f"HTTP {exc.code}"}
    except urllib.error.URLError as exc:
        return {"links": [], "title": "", "final_url": url, "error": str(exc.reason)}
    except Exception as exc:
        return {"links": [], "title": "", "final_url": url, "error": str(exc)}

    links, page_title = _extract_links(html_text, final_url, filter_text, max_links)
    return {"links": links, "title": page_title, "final_url": final_url, "error": None}


# ----------------------------------------------------------------------------------------------------
def get_page_links(
    url: str,
    filter_text: str = "",
    max_links: int = DEFAULT_MAX_LINKS,
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> list[dict]:
    """Fetch a page and return its navigable links as a structured list.

    Each entry is a dict: {"text": "anchor text", "url": "https://absolute.url"}.
    Navigation chrome (menus, login, subscribe, etc.) is filtered automatically.
    Relative URLs are resolved to absolute URLs, honouring any <base href> the page declares.

    filter_text: optional substring - only links whose anchor text or URL contains this
                 string (case-insensitive) are returned. Use for coarse pre-filtering
                 when you know a keyword; use scratch_query for semantic filtering.

    Returns a list[dict]. Returns a single-entry error list on network/parse failure.
    """
    data = _fetch_links_data(url, filter_text, max_links, timeout_seconds)

    if data["error"]:
        return [{"text": "Error", "url": url, "error": data["error"]}]

    if not data["links"]:
        msg = "No links found"
        if filter_text:
            msg += f" matching '{filter_text}'"
        return [{"text": msg, "url": url, "error": "no_links"}]

    return data["links"]


# ----------------------------------------------------------------------------------------------------
def get_page_links_text(
    url: str,
    filter_text: str = "",
    max_links: int = DEFAULT_MAX_LINKS,
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> str:
    """Fetch a page and return its navigable links as formatted plain text.

    Format:
        "Page Title" (https://example.com)  [12 links]
        1. [Anchor text] https://absolute.url
        2. [Another link] https://...

    Page title is included in the header when the fetched HTML provides one.
    Designed for direct LLM consumption and scratchpad parking.
    When the result is large (typical for listing pages), it will be auto-parked by the
    orchestration layer. Use scratch_query on the parked key for semantic filtering.

    Returns an error string (beginning with "Error:") on failure.
    """
    data = _fetch_links_data(url, filter_text, max_links, timeout_seconds)

    if data["error"]:
        return f"Error: {data['error']} - {url}"

    links     = data["links"]
    title     = data["title"]
    final_url = data["final_url"]

    if not links:
        msg = "No links found"
        if filter_text:
            msg += f" matching '{filter_text}'"
        return msg

    title_str = f'"{title}" ' if title else ""
    header    = f"Links from: {title_str}({final_url})"
    if filter_text:
        header += f"  (filtered: '{filter_text}')"
    header += f"  [{len(links)} links]"

    lines = [f"{i + 1}. [{item['text']}] {item['url']}" for i, item in enumerate(links)]
    return header + "\n" + "\n".join(lines)
