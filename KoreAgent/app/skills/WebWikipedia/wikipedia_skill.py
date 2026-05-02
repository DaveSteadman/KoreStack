# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# WebWikipedia lookup skill for the MiniAgentFramework.
#
# Provides a single LLM-callable tool that accepts a topic string, searches Wikipedia via the
# OpenSearch API to resolve the best matching article title, then retrieves a plain-text extract
# via the REST Summary API.  No API key or third-party library is required - only urllib from
# stdlib.
#
# Lookup flow:
#   1. OpenSearch (`action=opensearch`) returns ranked candidate titles for the query.
#   2. The first candidate is fetched via the REST Summary API (`/api/rest_v1/page/summary/`).
#   3. The `extract` field (pre-cleaned plain text from Wikipedia) is returned, truncated to
#      MAX_EXTRACT_WORDS words so it stays within typical context budgets.
#
# On any network error or missing data the function returns a "No Wikipedia data found" string
# so the orchestrator always receives a concrete value.
#
# Related modules:
#   - skills_catalog_builder.py  -- reads skill.md; function signatures drive orchestrator tool defs
#   - webpage_utils.py           -- HTTP_HEADERS used for fetch_html in other skills (not used here
#                                   because the Wikipedia API returns JSON, not HTML)
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import json
import urllib.parse
import urllib.request


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
_OPENSEARCH_URL = "https://en.wikipedia.org/w/api.php?action=opensearch&format=json&limit=3&search={q}"
_SUMMARY_URL    = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
_HEADERS        = {
    "User-Agent":      "KoreAgent/1.0 (educational AI agent; contact: local)",
    "Accept":          "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}
MAX_EXTRACT_WORDS = 400
DEFAULT_TIMEOUT   = 15


# ====================================================================================================
# MARK: INTERNAL HELPERS
# ====================================================================================================
def _json_get(url: str, timeout: int) -> dict | list | None:
    req = urllib.request.Request(url=url, headers=_HEADERS, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return None


# ----------------------------------------------------------------------------------------------------
def _opensearch_candidates(query: str, timeout: int) -> list[str]:
    url  = _OPENSEARCH_URL.format(q=urllib.parse.quote(query))
    data = _json_get(url, timeout)
    # OpenSearch returns [query, [titles], [descriptions], [urls]]
    if not isinstance(data, list) or len(data) < 2:
        return []
    titles = data[1]
    return [str(t) for t in titles if t] if isinstance(titles, list) else []


# ----------------------------------------------------------------------------------------------------
def _fetch_summary(title: str, timeout: int) -> str:
    url  = _SUMMARY_URL.format(title=urllib.parse.quote(title.replace(" ", "_")))
    data = _json_get(url, timeout)
    if not isinstance(data, dict):
        return ""
    extract = str(data.get("extract") or "").strip()
    # Skip disambiguation pages - they contain no useful extract.
    page_type = str(data.get("type") or "")
    if page_type == "disambiguation" or not extract:
        return ""
    return extract


# ----------------------------------------------------------------------------------------------------
def _truncate_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + " [...]"


# ====================================================================================================
# MARK: PUBLIC SKILL API
# ====================================================================================================
def lookup_wikipedia(topic: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    topic_clean = str(topic or "").strip()
    if not topic_clean:
        return "No Wikipedia data found: topic must not be empty."
    # Coerce timeout defensively - some models send all parameters as strings
    # regardless of the JSON Schema type hint.
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    candidates = _opensearch_candidates(topic_clean, timeout)
    if not candidates:
        return f"No Wikipedia data found for '{topic_clean}'."

    # Try each candidate in rank order and return the first that yields an extract.
    for title in candidates:
        extract = _fetch_summary(title, timeout)
        if extract:
            truncated = _truncate_words(extract, MAX_EXTRACT_WORDS)
            return f"Wikipedia - {title}\n\n{truncated}"

    return f"No Wikipedia data found for '{topic_clean}'."
