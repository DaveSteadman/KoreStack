# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# WebFetch skill for the MiniAgentFramework.
#
# Fetches a web page by URL and extracts readable prose text, stripping all HTML markup, navigation,
# scripts, headers, footers, and other noise. Returns clean text suitable for an LLM to synthesize
# or summarize.
#
# Uses BeautifulSoup for high-quality content extraction (installed as a project dependency).
# Falls back gracefully to a stdlib-only html.parser extractor if BeautifulSoup is unavailable.
#
# Typical planner usage:
#   1. WebSearch returns a list of results with URLs.
#   2. WebFetch fetches the most relevant URL and returns readable content.
#   3. The final LLM call synthesizes an answer from that content.
#
# Related modules:
#   - skills/WebSearch/web_search_skill.py -- upstream skill that produces candidate URLs
#   - main.py                              -- orchestration entry point
#   - skills_catalog_builder.py            -- reads skill.md to build the catalog
#   - webpage_utils.py                     -- HTTP fetch, HTML extraction, text utilities (shared)
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import sys
import urllib.error
import urllib.parse
from pathlib import Path

# Ensure code/ is on the path so llm_client is importable when this skill is loaded dynamically.
_code_dir = str(Path(__file__).resolve().parents[3])
if _code_dir not in sys.path:
    sys.path.insert(0, _code_dir)

from KoreAgent.llm_client import call_llm_chat as _call_llm_chat
from KoreAgent.llm_client import get_active_model as _get_active_model
from KoreAgent.llm_client import get_active_num_ctx as _get_active_num_ctx
from KoreAgent.utils.webpage_utils import fetch_html as _fetch_html
from KoreAgent.utils.webpage_utils import extract_content as _extract_content
from KoreAgent.utils.webpage_utils import truncate_to_words as _truncate_to_words


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
MAX_WORDS_CAP     = 4000   # raw return (no query) - kept small to protect main context
QUERY_WORDS_CAP   = 10000  # query-mode - inner LLM handles it, main context only gets the extract
DEFAULT_MAX_WORDS = 2000
DEFAULT_TIMEOUT   = 15
QUERY_FALLBACK_MIN_WORDS = 2500


# ====================================================================================================
# MARK: FALLBACKS
# ====================================================================================================
def _format_raw_fallback(page_title: str, body: str, max_words: int) -> str:
    """Return raw extracted page text as a generic fallback when query-mode extraction misses.

    This keeps the fetch useful for any query type without hard-coding prompt-specific
    heuristics. The caller still gets the real page content and can reason over it.
    """
    raw_words = max(50, min(int(max_words), MAX_WORDS_CAP))
    raw_body  = _truncate_to_words(body, raw_words)
    if page_title:
        return f"# {page_title}\n\n{raw_body}"
    return raw_body


# ----------------------------------------------------------------------------------------------------
def _format_query_fallback(page_title: str, body: str, max_words: int) -> str:
    """Return a larger raw fallback for failed query-mode extraction.

    When the isolated extractor cannot prove an answer, return a longer excerpt so the
    orchestration layer can auto-park it in the scratchpad and the model can inspect it
    with scratch_query / scratch_peek instead of repeating shallow fetches.
    """
    fallback_words = max(int(max_words), QUERY_FALLBACK_MIN_WORDS)
    return _format_raw_fallback(page_title, body, fallback_words)


# ----------------------------------------------------------------------------------------------------
def _extract_query_from_text(page_title: str, body: str, max_words: int, query: str | None) -> str:
    """Run the isolated extractor on already-fetched page text, or return raw fallback."""
    raw_fallback   = _format_raw_fallback(page_title, body, max_words)
    query_fallback = _format_query_fallback(page_title, body, max_words)

    if not query:
        return raw_fallback

    model   = _get_active_model()
    num_ctx = _get_active_num_ctx()
    if not model:
        return raw_fallback

    inner_messages = [
        {
            "role":    "system",
            "content": (
                "You are a precise information extractor. "
                "Read the question and the page content, then decide which mode applies:\n"
                "MODE A - FILTER (use when the question asks for a subset of a list or table): "
                "include only the rows/entries where the relevant column matches the target entity exactly. "
                "Never include rows belonging to a different entity. "
                "List every matching item individually - never group, compress, or summarise into ranges or counts. "
                "Always include the column used to filter so the output is self-verifiable.\n"
                "If the question asks for all / every / full history and you cannot prove the list is complete from the page text, "
                "respond with exactly: Not found on this page.\n"
                "MODE B - EXTRACT (use when the question asks about meaning, description, or explanation): "
                "pull the directly relevant sentences or paragraphs from the page and present them concisely.\n"
                "In both modes: if the answer is genuinely not present on this page, "
                "respond with exactly: Not found on this page."
            ),
        },
        {
            "role":    "user",
            "content": f"Question: {query}\n\nPage content:\n{body}",
        },
    ]

    try:
        result = _call_llm_chat(
            model_name=model,
            messages=inner_messages,
            tools=None,
            num_ctx=num_ctx,
        )
        extracted = (result.response or "").strip()
        if not extracted or extracted == "Not found on this page.":
            return query_fallback
        return extracted
    except Exception:
        return query_fallback


# ====================================================================================================
# MARK: PUBLIC SKILL API
# ====================================================================================================
def fetch_page_text(
    url: str,
    max_words: int = DEFAULT_MAX_WORDS,
    timeout_seconds: int = DEFAULT_TIMEOUT,
    query: str | None = None,
) -> str:
    """Fetch a web page and return its clean readable text, stripped of all HTML markup.

    Removes navigation, scripts, advertisements, and other non-content elements.
    Returns up to max_words words of body prose suitable for LLM consumption.
    Returns a descriptive error string on network/parse failure - never raises.

    When query is provided, the full page text is passed through an isolated LLM call that
    extracts only the information relevant to the query. The returned answer is compact and
    does not burden the caller's context window with raw page content.
    """
    if not url or not url.strip():
        return "Error: url cannot be empty."

    clean_url = url.strip()

    parsed = urllib.parse.urlparse(clean_url)
    if parsed.scheme not in ("http", "https"):
        return f"Error: unsupported URL scheme '{parsed.scheme}'. Only http and https are supported."

    # When extracting for a specific query, fetch a larger cap so the inner LLM has
    # maximum material - complete list pages can be long. For raw return, respect the
    # caller-supplied max_words limit and the smaller cap.
    fetch_words     = QUERY_WORDS_CAP if query else max(50, min(int(max_words), MAX_WORDS_CAP))
    timeout_seconds = max(5,  min(int(timeout_seconds), 60))

    try:
        html_text, _ = _fetch_html(clean_url, timeout=float(timeout_seconds))
    except urllib.error.HTTPError as exc:
        return f"Error fetching page: HTTP {exc.code} - {clean_url}"
    except urllib.error.URLError as exc:
        return f"Error fetching page: {exc.reason} - {clean_url}"
    except Exception as exc:
        return f"Error fetching page: {exc} - {clean_url}"

    page_title, body = _extract_content(html_text)

    if not body.strip():
        return f"Could not extract readable text from: {clean_url}"

    body = _truncate_to_words(body, fetch_words)
    return _extract_query_from_text(page_title, body, max_words, query)
