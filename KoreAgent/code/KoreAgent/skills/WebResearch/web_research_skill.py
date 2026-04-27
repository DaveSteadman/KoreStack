# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# ResearchTraverse skill for MiniAgentFramework.
#
# Generic multi-page web research primitive:
# - searches DuckDuckGo
# - fetches candidate pages
# - extracts readable text
# - scores relevance against the user query
# - optionally follows promising links discovered inside fetched pages
# - re-extracts evidence for the top pages via an isolated LLM call (semantic quality)
# - returns a compact summary plus a larger evidence bundle
#
# Built to reduce tool-call thrash and keep bulky evidence out of the main thread.
#
# Related modules:
#   - skills/WebSearch/web_search_skill.py  -- used for initial DuckDuckGo seeding
#   - skills/WebNavigate/web_navigate_skill.py -- used for extracting hop URLs from pages
#   - webpage_utils.py                      -- shared HTTP fetch and HTML extraction
#   - llm_client.py                      -- used for LLM-backed evidence re-extraction
# ====================================================================================================

import html as _html
import hashlib
import re
import urllib.parse
from collections import deque

from KoreAgent.llm_client import call_llm_chat as _call_llm_chat
from KoreAgent.llm_client import get_active_model as _get_active_model
from KoreAgent.llm_client import get_active_num_ctx as _get_active_num_ctx
from KoreAgent.scratchpad import scratch_save as _scratch_save
from KoreAgent.utils.webpage_utils import extract_content as _extract_content
from KoreAgent.utils.webpage_utils import fetch_html as _fetch_html
from KoreAgent.utils.webpage_utils import truncate_to_words as _truncate_to_words

# Cross-skill imports - work as namespace packages (Python 3.3+) once code/ is on sys.path.
from KoreAgent.skills.WebNavigate.web_navigate_skill import extract_urls_from_html as _extract_urls_from_html
from KoreAgent.skills.WebSearch.web_search_skill import search_web


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================

_MAX_SEARCH_RESULTS_CAP          = 10
_MAX_PAGES_CAP                   = 12
_MAX_HOPS_CAP                    = 2
_TIMEOUT_SECONDS_CAP             = 30
_MAX_WORDS_PER_PAGE_CAP          = 1200
_MAX_EVIDENCE_QUOTES_CAP         = 8
# Number of top-scoring pages for which an isolated LLM re-extraction pass is run to
# replace the lexical evidence snippets with semantically focused ones.
_LLM_REEXTRACT_TOP_N             = 3

_URL_RE                          = re.compile(r'https?://[^\s<>"\')]+', re.IGNORECASE)
_SPACE_RE                        = re.compile(r"\s+")
_NON_WORD_RE                     = re.compile(r"[^a-z0-9\s]+", re.IGNORECASE)

_STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "by", "with", "from", "at",
    "is", "are", "was", "were", "be", "been", "being", "what", "which", "who", "when", "where",
    "how", "why", "this", "that", "these", "those", "about", "into", "than", "then", "it",
}


# ====================================================================================================
# MARK: TEXT HELPERS
# ====================================================================================================

def _clean_text(text: str) -> str:
    text = _html.unescape(text or "")
    text = _NON_WORD_RE.sub(" ", text.lower())
    text = _SPACE_RE.sub(" ", text).strip()
    return text


def _query_terms(query: str) -> list[str]:
    tokens = [t for t in _clean_text(query).split() if len(t) >= 3 and t not in _STOPWORDS]
    seen = set()
    result = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            result.append(token)
    return result


def _split_paragraphs(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    return parts


def _sentenceish_chunks(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n\s*\n", text or "")
    return [p.strip() for p in parts if p and p.strip()]


# ====================================================================================================
# MARK: URL HELPERS
# ====================================================================================================

def _normalise_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlparse(url)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        path   = parsed.path or "/"
        query  = parsed.query
        if not scheme or not netloc:
            return ""
        rebuilt = urllib.parse.urlunparse((scheme, netloc, path, "", query, ""))
        return rebuilt
    except Exception:
        return ""


def _same_domain(url_a: str, url_b: str) -> bool:
    try:
        a = urllib.parse.urlparse(url_a).netloc.lower()
        b = urllib.parse.urlparse(url_b).netloc.lower()
        return bool(a) and bool(b) and a == b
    except Exception:
        return False


# ====================================================================================================
# MARK: SCRATCHPAD ARTIFACTS
# ====================================================================================================

def _build_page_scratch_key(query: str, url: str, ordinal: int) -> str:
    digest = hashlib.sha1(f"{query}|{url}".encode("utf-8")).hexdigest()[:10]
    return f"research_page_{ordinal}_{digest}"


def _build_page_artifact_content(query: str, page: dict) -> str:
    lines = [
        f"RESEARCH QUERY: {query}",
        f"TITLE: {page['title']}",
        f"URL: {page['url']}",
    ]
    if page.get("matched_terms"):
        lines.append(f"MATCHED TERMS: {', '.join(page['matched_terms'])}")
    if page.get("evidence"):
        lines.append("EVIDENCE:")
        for ev in page["evidence"]:
            lines.append(f"- {ev}")
    lines.extend([
        "",
        "PAGE EXTRACT:",
        page.get("body_text", ""),
    ])
    return "\n".join(lines).strip()


# ====================================================================================================
# MARK: SCORING
# ====================================================================================================

def _score_text_against_query(query: str, title: str, body_text: str, url: str) -> tuple[float, list[str]]:
    terms = _query_terms(query)
    if not terms:
        return 0.0, []

    title_l = _clean_text(title)
    body_l  = _clean_text(body_text)
    url_l   = _clean_text(url)

    score = 0.0
    hits  = []

    for term in terms:
        term_score = 0.0

        if term in title_l:
            term_score += 4.0
            hits.append(term)

        if term in url_l:
            term_score += 2.0

        body_count = body_l.count(term)
        if body_count > 0:
            term_score += min(3.0, 0.5 * body_count)

        score += term_score

    if len(set(hits)) >= 2:
        score += 3.0

    if title_l and any(x in title_l for x in ("results", "report", "official", "history", "winners", "standings", "docs")):
        score += 1.0

    return score, sorted(set(hits))


def _best_evidence_snippets(query: str, body_text: str, max_items: int) -> list[str]:
    terms = _query_terms(query)
    chunks = _sentenceish_chunks(body_text)

    ranked = []
    for chunk in chunks:
        chunk_l = _clean_text(chunk)
        if len(chunk.split()) < 8:
            continue

        score = 0.0
        matched = 0
        for term in terms:
            if term in chunk_l:
                score += 1.0
                matched += 1

        if matched >= 2:
            score += 2.0

        if score > 0:
            ranked.append((score, chunk.strip()))

    ranked.sort(key=lambda x: x[0], reverse=True)

    out = []
    seen = set()
    for _, chunk in ranked:
        key = chunk[:160].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(chunk)
        if len(out) >= max_items:
            break
    return out


# ====================================================================================================
# MARK: FETCH + EXTRACT
# ====================================================================================================

def _fetch_extract_score(url: str, query: str, timeout_seconds: int, max_words_per_page: int, max_evidence_quotes: int = 3) -> dict:
    try:
        html_text, final_url = _fetch_html(url, timeout=float(timeout_seconds))
        page_title, body_text = _extract_content(html_text)
        body_text = _truncate_to_words(body_text, max_words_per_page)

        score, matched_terms = _score_text_against_query(
            query     = query,
            title     = page_title,
            body_text = body_text,
            url       = final_url,
        )

        evidence = _best_evidence_snippets(query, body_text, max_items=max_evidence_quotes)

        return {
            "ok"            : True,
            "url"           : final_url,
            "title"         : page_title or final_url,
            "score"         : round(score, 2),
            "matched_terms" : matched_terms,
            "evidence"      : evidence,
            "body_text"     : body_text,
            "discovered_urls": _extract_urls_from_html(html_text, final_url),
            "error"         : "",
        }
    except Exception as exc:
        return {
            "ok"            : False,
            "url"           : url,
            "title"         : url,
            "score"         : 0.0,
            "matched_terms" : [],
            "evidence"      : [],
            "body_text"     : "",
            "discovered_urls": [],
            "error"         : str(exc),
        }


# ====================================================================================================
# MARK: LLM EVIDENCE RE-EXTRACTION
# ====================================================================================================

def _llm_reextract_evidence(query: str, body_text: str) -> list[str] | None:
    """Run an isolated LLM call to extract semantically relevant evidence from body_text.

    Returns a list of extracted sentences/paragraphs, or None if no model is registered
    or the call fails (callers fall back to lexical evidence in that case).
    The full page body never enters the main messages thread - only the compact result
    is returned.
    """
    model   = _get_active_model()
    num_ctx = _get_active_num_ctx()
    if not model:
        return None

    inner_messages = [
        {
            "role":    "system",
            "content": (
                "You are a precise evidence extractor. "
                "Read the research question and the page content, then extract the sentences or "
                "short paragraphs that most directly answer or support the question. "
                "Return each piece of evidence on its own line, prefixed with '- '. "
                "Include only text that appears verbatim or near-verbatim on the page. "
                "If no relevant evidence is present, respond with exactly: Not found on this page."
            ),
        },
        {
            "role":    "user",
            "content": f"Research question: {query}\n\nPage content:\n{body_text}",
        },
    ]

    try:
        result    = _call_llm_chat(model_name=model, messages=inner_messages, tools=None, num_ctx=num_ctx)
        extracted = (result.response or "").strip()
        if not extracted or extracted == "Not found on this page.":
            return None
        lines = [ln.lstrip("- ").strip() for ln in extracted.splitlines() if ln.strip() and ln.strip() != "-"]
        return [ln for ln in lines if ln] or None
    except Exception:
        return None


# ====================================================================================================
# MARK: PUBLIC SKILL API
# ====================================================================================================

def research_traverse(
    query: str,
    max_search_results: int           = 5,
    max_pages: int                    = 6,
    max_hops: int                     = 1,
    same_domain_only_for_hops: bool   = True,
    timeout_seconds: int              = 15,
    max_words_per_page: int           = 450,
    max_evidence_quotes: int          = 3,
) -> dict:
    """Use when the prompt says 'research', 'investigate', 'look into', 'find evidence
    across multiple sources', or 'deep dive into'. Searches, fetches multiple pages,
    follows links, and returns an evidence-led summary bundle. Prefer over search_web_text
    when the question requires cross-referencing several sources or when a single search
    returns no results. For a one-source lookup use search_web_text or fetch_page_text.
    """
    query = (query or "").strip()
    if not query:
        return {"summary": "Error: query cannot be empty", "answer_confidence": "low"}

    max_search_results = max(1, min(int(max_search_results), _MAX_SEARCH_RESULTS_CAP))
    max_pages          = max(1, min(int(max_pages), _MAX_PAGES_CAP))
    max_hops           = max(0, min(int(max_hops), _MAX_HOPS_CAP))
    timeout_seconds    = max(5, min(int(timeout_seconds), _TIMEOUT_SECONDS_CAP))
    max_words_per_page = max(80, min(int(max_words_per_page), _MAX_WORDS_PER_PAGE_CAP))
    max_evidence_quotes = max(1, min(int(max_evidence_quotes), _MAX_EVIDENCE_QUOTES_CAP))

    # Capture the DuckDuckGo URL that will be used so it appears in the output for debugging.
    _search_url = f"https://duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"

    seed_results = search_web(
        query           = query,
        max_results     = max_search_results,
        timeout_seconds = timeout_seconds,
    )

    if not isinstance(seed_results, list) or not seed_results:
        return {
            "query"              : query,
            "search_url"         : _search_url,
            "summary"            : "Search returned no usable results.",
            "answer_confidence"  : "low",
            "visited_count"      : 0,
            "seed_results"       : seed_results,
            "best_pages"         : [],
            "exploration_log"    : [],
            "unvisited_candidates": [],
            "full_report"        : f"Research failed early for query: {query}",
        }

    frontier = deque()
    visited  = set()
    queued   = set()
    log      = []

    for result in seed_results:
        url = _normalise_url(str(result.get("url", "")))
        if not url:
            continue
        frontier.append((url, 0, "seed_search_result"))
        queued.add(url)

    useful_pages = []
    rejected_pages = []

    while frontier and len(visited) < max_pages:
        current_url, depth, reason = frontier.popleft()
        if current_url in visited:
            continue
        visited.add(current_url)

        page = _fetch_extract_score(
            url                = current_url,
            query              = query,
            timeout_seconds    = timeout_seconds,
            max_words_per_page = max_words_per_page,
            max_evidence_quotes = max_evidence_quotes,
        )

        page["depth"] = depth
        page["reason"] = reason

        if page["ok"] and page["score"] > 0:
            useful_pages.append(page)
            status = "useful"
        else:
            rejected_pages.append(page)
            status = "rejected"

        log.append({
            "url"          : page["url"],
            "title"        : page["title"],
            "depth"        : depth,
            "reason"       : reason,
            "status"       : status,
            "score"        : page["score"],
            "matched_terms": page["matched_terms"],
            "error"        : page["error"],
        })

        if depth >= max_hops:
            continue

        if not page["ok"]:
            continue

        discovered_urls = page.get("discovered_urls", [])
        for child_url in discovered_urls:
            if child_url in visited or child_url in queued:
                continue

            if same_domain_only_for_hops and not _same_domain(page["url"], child_url):
                continue

            if len(queued) + len(visited) >= max_pages * 4:
                continue

            frontier.append((child_url, depth + 1, f"linked_from:{page['url']}"))
            queued.add(child_url)

    useful_pages.sort(key=lambda p: p.get("score", 0.0), reverse=True)

    # LLM semantic re-extraction pass for the top pages.
    # Replaces the lexical evidence snippets with semantically focused ones so high-scoring
    # pages that repeat query terms but give a shallow answer don't pollute the evidence bundle.
    for page in useful_pages[:_LLM_REEXTRACT_TOP_N]:
        llm_evidence = _llm_reextract_evidence(query, page.get("body_text", ""))
        if llm_evidence:
            page["evidence"] = llm_evidence

    for index, page in enumerate(useful_pages, start=1):
        scratch_key = _build_page_scratch_key(query, page["url"], index)
        _scratch_save(scratch_key, _build_page_artifact_content(query, page))
        page["scratch_key"] = scratch_key

    best_pages = []
    for page in useful_pages[: min(5, len(useful_pages))]:
        best_pages.append({
            "title"         : page["title"],
            "url"           : page["url"],
            "score"         : page["score"],
            "depth"         : page["depth"],
            "matched_terms" : page["matched_terms"],
            "evidence"      : page["evidence"],
            "scratch_key"   : page["scratch_key"],
        })

    page_manifest = []
    for page in useful_pages:
        page_manifest.append({
            "title"       : page["title"],
            "url"         : page["url"],
            "score"       : page["score"],
            "depth"       : page["depth"],
            "scratch_key" : page["scratch_key"],
        })

    if useful_pages:
        top = useful_pages[:3]
        summary_lines = ["Top evidence found:"]
        for page in top:
            summary_lines.append(f"- {page['title']} [{page['score']}]")
            for ev in page["evidence"][:2]:
                summary_lines.append(f"  - {ev}")
        summary = "\n".join(summary_lines)

        top_score  = useful_pages[0]["score"]
        # Confidence bands: high >= 10, medium >= 5, low < 5.
        # Score is driven by: title match +4.0, URL match +2.0, body term frequency up to
        # +3.0 per term, multi-term bonus +3.0.  A focused article typically scores 10-20;
        # index/listing pages with shallow mentions score 3-7.
        confidence = "high" if top_score >= 10 else ("medium" if top_score >= 5 else "low")
    else:
        summary = "No strong evidence found across the visited pages."
        confidence = "low"

    full_report_lines = [
        f"Research query: {query}",
        f"Search URL: {_search_url}",
        f"Visited pages: {len(visited)}",
        f"Useful pages: {len(useful_pages)}",
        "",
        "==== BEST PAGES ====",
    ]

    for page in useful_pages[: min(8, len(useful_pages))]:
        full_report_lines.append(f"TITLE: {page['title']}")
        full_report_lines.append(f"URL:   {page['url']}")
        full_report_lines.append(f"SCORE: {page['score']}")
        full_report_lines.append(f"SCRATCH_KEY: {page['scratch_key']}")
        if page["matched_terms"]:
            full_report_lines.append(f"MATCHED TERMS: {', '.join(page['matched_terms'])}")
        if page["evidence"]:
            full_report_lines.append("EVIDENCE:")
            for ev in page["evidence"]:
                full_report_lines.append(f"- {ev}")
        full_report_lines.append("")
        full_report_lines.append("----")
        full_report_lines.append("")

    unvisited_candidates = []
    while frontier and len(unvisited_candidates) < 20:
        url, depth, reason = frontier.popleft()
        unvisited_candidates.append({
            "url"   : url,
            "depth" : depth,
            "reason": reason,
        })

    return {
        "query"               : query,
        "search_url"          : _search_url,
        "summary"             : summary,
        "answer_confidence"   : confidence,
        "visited_count"       : len(visited),
        "seed_results"        : seed_results,
        "best_pages"          : best_pages,
        "page_manifest"       : page_manifest,
        "exploration_log"     : log,
        "unvisited_candidates": unvisited_candidates,
        "full_report"         : "\n".join(full_report_lines).strip(),
    }
