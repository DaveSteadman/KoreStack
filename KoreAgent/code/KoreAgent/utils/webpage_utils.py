# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Shared web utilities for MiniAgentFramework skills.
#
# Provides common HTTP fetching, HTML extraction, and text manipulation utilities used across
# the web skill modules (WebSearch).  Centralising these
# removes ~200 lines of near-identical code that was previously duplicated across skill files.
#
# Public API:
#   HTTP_HEADERS          -- standard browser-impersonation request headers
#   BS4_AVAILABLE         -- True if beautifulsoup4 is installed (checked once at import time)
#   SKIP_TAGS             -- HTML tags whose entire subtree is discarded during extraction
#   BLOCK_TAGS            -- HTML tags that produce paragraph boundaries in extracted text
#   NOISE_HINTS           -- attribute substrings that identify noisy layout containers
#   MIN_PARA_WORDS        -- minimum words per extracted paragraph; below this = boilerplate
#
#   fetch_html(url, timeout=15)         -> (html_text: str, final_url: str)
#   is_url_cached(url)                  -> bool
#   dedup_paragraphs(paragraphs)        -> list[str]
#   extract_content(html_text)          -> (page_title: str, body_text: str)  -- structured Markdown
#   truncate_to_words(text, max_words)  -> str
#
# Related modules:
#   code/utils/workspace_utils.py        -- workspace root path management
#   code/skills/WebSearch/               -- uses fetch_html
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import gzip
import html as _html
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
from collections import OrderedDict
from html.parser import HTMLParser

try:
    from bs4 import BeautifulSoup
    from bs4 import XMLParsedAsHTMLWarning
    import warnings
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

try:
    import certifi as _certifi
    _SSL_CTX: ssl.SSLContext = ssl.create_default_context(cafile=_certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
HTTP_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",  # "br" (brotli) is intentionally omitted - we can only decompress gzip/deflate; add the 'brotli' package and br handling before advertising it here
    "Connection":      "close",
}

_SPACE_RE = re.compile(r"\s+")
_TAG_RE   = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

# Tags whose entire subtree should be discarded during extraction.
SKIP_TAGS = frozenset({
    "script", "style", "noscript", "meta", "link", "nav",
    "header", "footer", "aside", "form", "button", "svg",
    "picture", "iframe", "figure",
})

# Tags that delimit paragraph boundaries in the extracted text.
BLOCK_TAGS = frozenset({
    "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "td", "th", "blockquote", "pre",
    "div", "section", "article", "main",
})

# Attribute substrings that identify noisy layout containers (bs4 extraction path only).
NOISE_HINTS = frozenset({
    "nav", "menu", "header", "footer", "breadcrumb", "cookie", "consent",
    "signin", "login", "register", "newsletter", "share", "social",
    "advert", "ads", "sidebar", "related", "subscribe", "promo", "popup",
    "modal", "overlay", "banner", "paywall", "sticky", "tag-list", "tagslist",
    "byline", "dateline", "author-bio", "read-more", "more-articles",
    "pagination", "pager", "widget", "skip-link",
})

# Minimum words per extracted paragraph; shorter runs are treated as boilerplate.
MIN_PARA_WORDS = 15

# Markdown prefix for each HTML heading level.
_HEADING_PREFIX = {
    "h1": "# ",
    "h2": "## ",
    "h3": "### ",
    "h4": "#### ",
    "h5": "##### ",
    "h6": "###### ",
}

_DEFAULT_TIMEOUT = 15

# HTTP status codes that indicate a transient server-side error worth retrying once.
_RETRY_ON_STATUS  = frozenset({429, 500, 502, 503, 504})
_MAX_FETCH_RETRIES = 1

# In-process LRU URL cache - avoids re-fetching the same URL within a single session
# (e.g. when a seed search result also appears as a hop target in research_traverse).
_HTML_CACHE_MAX  = 32
_html_cache:      OrderedDict[str, tuple[str, str]] = OrderedDict()
_html_cache_lock: threading.Lock = threading.Lock()


# ====================================================================================================
# MARK: HTTP FETCH
# ====================================================================================================
def is_url_cached(url: str) -> bool:
    with _html_cache_lock:
        return url in _html_cache


# ----------------------------------------------------------------------------------------------------
def fetch_html(url: str, timeout: float = _DEFAULT_TIMEOUT, no_cache: bool = False) -> tuple[str, str]:
    """Fetch a URL and return (html_text, final_url).

    Handles charset detection from the Content-Type header.
    Retries once on transient 5xx / 429 responses (linear 1-second back-off).
    Results are cached in-process (up to 32 URLs) to avoid redundant round-trips.
    Raises on persistent network error - never silently swallows exceptions.

    no_cache: skip the in-process cache entirely for this call (both read and write).
    Use this when retrying a URL whose previously cached response is known to be unusable
    (e.g. a DDG search page that returned zero results due to rate-limiting).
    """
    if not no_cache:
        with _html_cache_lock:
            if url in _html_cache:
                return _html_cache[url]

    last_exc: Exception | None = None
    for attempt in range(_MAX_FETCH_RETRIES + 1):
        try:
            request = urllib.request.Request(url=url, headers=HTTP_HEADERS, method="GET")
            with urllib.request.urlopen(request, timeout=timeout, context=_SSL_CTX) as response:
                final_url    = response.url
                content_type = response.headers.get("Content-Type", "")
                charset      = "utf-8"
                for part in content_type.split(";"):
                    part = part.strip()
                    if part.startswith("charset="):
                        charset = part[8:].strip() or "utf-8"
                        break
                content_encoding = response.headers.get("Content-Encoding", "")
                raw = response.read()
            if "gzip" in content_encoding:
                raw = gzip.decompress(raw)
            elif "deflate" in content_encoding:
                import zlib
                raw = zlib.decompress(raw)
            # Prefer strict UTF-8 decode when the raw bytes are valid UTF-8,
            # regardless of what the Content-Type header declares.  Many sites
            # serve UTF-8 content but label it as windows-1252 or latin-1,
            # which causes the â€" / â€˜ corruption pattern when decoded naively.
            # Only fall back to the declared charset when the bytes are not
            # valid UTF-8 (e.g. legacy Latin / CP1252 pages that really do use
            # single-byte encodings).
            try:
                result = raw.decode("utf-8", errors="strict"), final_url
            except (UnicodeDecodeError, LookupError):
                try:
                    result = raw.decode(charset, errors="replace"), final_url
                except LookupError:
                    result = raw.decode("utf-8", errors="replace"), final_url

            # Store in cache, evicting the oldest entry if at capacity.
            # Skip caching when no_cache is set so callers can re-fetch freely.
            if not no_cache:
                with _html_cache_lock:
                    if len(_html_cache) >= _HTML_CACHE_MAX:
                        _html_cache.popitem(last=False)
                    _html_cache[url] = result
            return result

        except urllib.error.HTTPError as exc:
            if exc.code in _RETRY_ON_STATUS and attempt < _MAX_FETCH_RETRIES:
                time.sleep(float(attempt + 1))
                last_exc = exc
                continue
            raise

    raise last_exc  # type: ignore[misc]


# ====================================================================================================
# MARK: PARAGRAPH DEDUPLICATION
# ====================================================================================================
def dedup_paragraphs(paragraphs: list[str]) -> list[str]:
    """Remove near-duplicate paragraphs using the first 80 normalised characters as a key.

    Handles responsive-layout pages (e.g. BBC News) that repeat the same content blocks
    multiple times in the HTML for different viewport sizes.
    """
    seen:   set[str]  = set()
    result: list[str] = []
    for p in paragraphs:
        key = _SPACE_RE.sub(" ", p.lower().strip())[:80]
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result


# ====================================================================================================
# MARK: STDLIB FALLBACK HTML EXTRACTOR
# ====================================================================================================
class _FallbackExtractor(HTMLParser):
    """Pure-stdlib HTML-to-text extractor used when BeautifulSoup is unavailable."""

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth  = 0
        self._in_body     = False
        self._heading_tag: str | None = None
        self._buf:         list[str]  = []
        self._paragraphs:  list[str]  = []

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        if tag == "body":
            self._in_body = True
        if tag in SKIP_TAGS:
            self._skip_depth += 1
        elif tag in BLOCK_TAGS:
            self._flush()
            if tag in _HEADING_PREFIX:
                self._heading_tag = tag

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if tag in SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in BLOCK_TAGS:
            self._flush()
            if tag in _HEADING_PREFIX:
                self._heading_tag = None

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0 or not self._in_body:
            return
        cleaned = _SPACE_RE.sub(" ", data).strip()
        if cleaned:
            self._buf.append(cleaned)

    def _flush(self) -> None:
        text = " ".join(self._buf).strip()
        self._buf = []
        if not text:
            return
        if self._heading_tag:
            # Headings: emit if at least 2 words (avoids single-word nav labels).
            if len(text.split()) >= 2:
                self._paragraphs.append(_HEADING_PREFIX[self._heading_tag] + text)
        elif len(text.split()) >= MIN_PARA_WORDS:
            self._paragraphs.append(text)

    def get_text(self) -> str:
        self._flush()
        return "\n\n".join(dedup_paragraphs(self._paragraphs))


# ====================================================================================================
# MARK: BEAUTIFULSOUP EXTRACTOR
# ====================================================================================================
def _attrs_lower(tag) -> str:
    """Concatenate id, class, and role attribute values for noise-hint matching."""
    parts   = []
    tag_id  = tag.get("id")
    classes = tag.get("class")
    role    = tag.get("role")
    if tag_id:
        parts.append(str(tag_id).lower())
    if isinstance(classes, list):
        parts.extend(c.lower() for c in classes)
    elif classes:
        parts.append(str(classes).lower())
    if role:
        parts.append(str(role).lower())
    return " ".join(parts)


def _prune_noise_bs4(soup) -> None:
    """Remove known-noisy tags and heuristically identified layout containers in-place."""
    for tag in list(soup.find_all(list(SKIP_TAGS))):
        # Void elements (link, meta) are self-closing in spec but html.parser treats them as
        # open containers, mis-adopting subsequent content as children.  unwrap() preserves
        # those children in the tree; decompose() would silently destroy them.
        if tag.contents:
            tag.unwrap()
        else:
            tag.decompose()
    for tag in list(soup.find_all(True)):
        if not hasattr(tag, "attrs") or tag.attrs is None:
            continue
        if any(hint in _attrs_lower(tag) for hint in NOISE_HINTS):
            tag.decompose()


def _extract_structured_bs4(container) -> str:
    """Walk headings, paragraphs, and tables in document order, emitting structured text.

    Each <h1>-<h6> becomes a Markdown heading; each <p> becomes a paragraph if it
    meets MIN_PARA_WORDS.  Each top-level <table> is emitted as pipe-separated rows
    so that list/table data (e.g. Wikipedia winners tables) survives the extraction pass
    and is available to the query-mode LLM filter.  Walking in document order preserves
    the heading-then-body structure of multi-article pages so distinct topics are never merged.
    """
    _HEADING_NAMES = list(_HEADING_PREFIX.keys())
    items:  list[str] = []
    seen:   set[str]  = set()

    for el in container.find_all(_HEADING_NAMES + ["p", "table"]):
        tag = el.name

        # ---- TABLE: emit as pipe-separated rows ----
        if tag == "table":
            # Skip tables nested inside another table already being processed.
            if el.find_parent("table"):
                continue

            rows: list[str] = []

            # Header cells (all <th> in the table, typically the first row).
            header_cells = [
                _SPACE_RE.sub(" ", th.get_text(" ", strip=True)).strip()
                for th in el.find_all("th")
            ]
            header_cells = [c for c in header_cells if c]
            if header_cells:
                rows.append(" | ".join(header_cells))

            # Data rows.
            for tr in el.find_all("tr"):
                cells = [
                    _SPACE_RE.sub(" ", td.get_text(" ", strip=True)).strip()
                    for td in tr.find_all("td")
                ]
                cells = [c for c in cells if c]
                if cells:
                    rows.append(" | ".join(cells))

            if rows:
                table_text = "\n".join(rows)
                key = table_text[:80].lower()
                if key not in seen:
                    seen.add(key)
                    items.append(table_text)
            continue

        # ---- HEADING / PARAGRAPH ----
        text = _SPACE_RE.sub(" ", el.get_text(" ", strip=True)).strip()
        if not text:
            continue
        key = text.lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        if tag in _HEADING_PREFIX:
            # Only emit headings with 2-25 words to skip single nav labels and
            # multi-sentence mis-tagged blocks.
            wc = len(text.split())
            if 2 <= wc <= 25:
                items.append(_HEADING_PREFIX[tag] + text)
        else:
            if len(text.split()) >= MIN_PARA_WORDS:
                items.append(text)

    return "\n\n".join(items)


def _extract_with_bs4(html_text: str) -> tuple[str, str]:
    """Return (page_title, body_text) using BeautifulSoup."""
    soup  = BeautifulSoup(html_text, "html.parser")
    title = (soup.title.string or "").strip() if soup.title else ""

    # Prefer semantic containers for focused, cleaner extraction.
    for selector in ["article", "main", "[role='main']"]:
        container = soup.select_one(selector)
        if container:
            _prune_noise_bs4(container)
            body = _extract_structured_bs4(container)
            if len(body.split()) >= 60:
                return title, body

    # Fall back to full-page structured scan.
    _prune_noise_bs4(soup)
    body = _extract_structured_bs4(soup)
    if not body:
        body = _SPACE_RE.sub(" ", soup.get_text(separator="\n\n", strip=True)).strip()
    return title, body


def _extract_with_stdlib(html_text: str) -> tuple[str, str]:
    """Return (page_title, body_text) using stdlib HTMLParser only."""
    title_match = _TITLE_RE.search(html_text)
    title       = _html.unescape(_TAG_RE.sub("", title_match.group(1))).strip() if title_match else ""
    extractor   = _FallbackExtractor()
    try:
        extractor.feed(html_text)
    except Exception:
        pass
    return title, extractor.get_text()


# ====================================================================================================
# MARK: PUBLIC: CONTENT EXTRACTION
# ====================================================================================================
def extract_content(html_text: str) -> tuple[str, str]:
    """Dispatch to the best available extractor and return (page_title, body_text).

    Uses BeautifulSoup when available; falls back to the stdlib html.parser extractor.
    """
    if BS4_AVAILABLE:
        return _extract_with_bs4(html_text)
    return _extract_with_stdlib(html_text)


# ====================================================================================================
# MARK: PUBLIC: TEXT UTILITIES
# ====================================================================================================
def truncate_to_words(text: str, max_words: int) -> str:
    """Return text truncated to at most max_words words, appending '...[truncated]' if cut."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "\n\n...[truncated]"
