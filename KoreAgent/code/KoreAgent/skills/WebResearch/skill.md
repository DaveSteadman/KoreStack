# WebResearch Skill

## Purpose
Search the web, visit multiple relevant pages, extract the useful text, optionally follow promising links, and return a compact evidence-led research bundle.

Use this when the answer is unlikely to be found reliably from a single search result or single page extract.
This skill is designed to reduce orchestration thrash by owning the search frontier internally.

## Interface
- Module: `code/KoreAgent/skills/WebResearch/web_research_skill.py`
- Functions:
  - `research_traverse(query: str, max_search_results: int = 5, max_pages: int = 6, max_hops: int = 1, same_domain_only_for_hops: bool = True, timeout_seconds: int = 15, max_words_per_page: int = 450, max_evidence_quotes: int = 3)`

## Parameters

### `research_traverse(query, max_search_results = 5, max_pages = 6, max_hops = 1, same_domain_only_for_hops = True, timeout_seconds = 15, max_words_per_page = 450, max_evidence_quotes = 3)`
- `query` *(required)* - the research question or investigation prompt.
- `max_search_results` *(optional, default 5)* - number of search results to seed the frontier from.
- `max_pages` *(optional, default 6)* - maximum total number of pages to visit.
- `max_hops` *(optional, default 1)* - how many link-following hops beyond the initial search results are allowed.
- `same_domain_only_for_hops` *(optional, default True)* - when following links found inside pages, stay on the same domain unless set false.
- `timeout_seconds` *(optional, default 15)* - network timeout per fetch.
- `max_words_per_page` *(optional, default 450)* - truncate extracted page text per page to control size.
- `max_evidence_quotes` *(optional, default 3)* - number of best evidence snippets to keep per useful page.

**Frontier size:** the traversal internally caps the queued URL count at `max_pages * 4` before cutting off link expansion. With the default `max_pages=6` this allows up to 24 queued URLs. Increase `max_pages` if you need deeper coverage.

**Evidence quality:** after the traversal, an isolated LLM call re-extracts evidence for the top 3 scoring pages to replace the initial lexical snippets with semantically focused ones. This pass is skipped if no model is currently registered.

## Output
- returns a dict with:
- `query` - original query
- `search_url` - the DuckDuckGo search URL used to seed the traversal (useful for debugging)
- `summary` - short synthesis of the strongest evidence found
- `answer_confidence` - `high` when top page score >= 10, `medium` >= 5, `low` < 5. Score is driven by: title term match (+4.0), URL term match (+2.0), body term frequency (up to +3.0/term), multi-term bonus (+3.0). A focused article typically scores 10-20; shallow index/listing pages score 3-7.
- `visited_count` - number of fetched pages
- `seed_results` - initial search results used to seed the traversal
- `best_pages` - compact list of the most relevant pages with URL, title, score, evidence snippets, and a per-page `scratch_key`
- `page_manifest` - compact manifest of all useful pages, each with URL, score, depth, and per-page `scratch_key`
- `exploration_log` - per-page log showing what was visited and why
- `unvisited_candidates` - discovered but not visited URLs (up to 20 from the remaining frontier)
- `full_report` - compact debug report listing the strongest pages and their `scratch_key` values

## Triggers
Invoke this skill when the prompt contains any of these concepts or phrases:
- `research this`
- `investigate`
- `look into`
- `search and examine`
- `find the answer across multiple pages`
- `follow the links`
- `gather evidence from the web`

## Tool selection guidance

**This is the most expensive web skill - use it only when simpler tools cannot settle the question.**

`research_traverse` owns its own search frontier: it searches, fetches multiple pages, follows
links, and synthesises evidence internally. This makes it powerful but slow and context-heavy.
Prefer a cheaper alternative whenever one of the following applies:

| Situation | Prefer instead |
|---|---|
| Data already stored this session | `scratch_query` / `scratch_load` |
| Stable factual topic (person, place, concept) | `lookup_wikipedia` |
| Answer likely on one known page | `fetch_page_text(url=..., query=...)` |
| Answer likely on one unknown page | `search_web_text` + `fetch_page_text(query=...)` |
| Need to browse a listing/hub page and select items | `get_page_links_text` + `fetch_page_text` per item |
| Multi-source investigation needed | **use `research_traverse`** |

**Escalate to `research_traverse` when initial search returns zero results.**
If `search_web` or `search_web_text` returns "No results" for a topic that should have web
coverage, retry once with a simplified or rephrased query. If still no results, escalate to
`research_traverse` rather than answering from training data - the traverse skill uses an
internal multi-step frontier that can succeed where a single-query search fails.

**When `research_traverse` returns `visited_count=0`, the DuckDuckGo seed failed - not the topic.**
This happens when DuckDuckGo rate-limits or blocks the request. Do not report "no results" to the
user. Instead, fall back through this chain in order, stopping as soon as one succeeds:

1. `lookup_wikipedia(query)` - live Wikipedia lookup.
2. `search_web` or `search_web_text` with a simplified or rephrased version of the query.
3. `fetch_page_text` on any promising URLs found in steps 1-2.

Only report "no results" to the user once all of the above are exhausted.

**Exception - do not retry when the failure is a timeout.**
If earlier `search_web` calls in this session have already returned `title="Search failed"` with
a timeout error for this query, the endpoint is unreachable. Skip steps 2-3 above and go
directly to `lookup_wikipedia` (step 1) only. Do not issue more
`search_web` calls against an endpoint that has already timed out multiple times.

**The manual three-stage chain is often better for structured tasks:**

When the goal is to ingest articles from a known site (e.g. daily news harvest, GitHub trending),
use the manual chain rather than `research_traverse` - it gives the orchestrator visible control
over which items are selected:

```
1. get_page_links_text("https://news.ycombinator.com")   <- survey the listing
2. scratch_query(key, "which links are about AI tools?")  <- semantic selection
3. fetch_page_text(url, query="what is this and how to use it?")  <- read chosen items
4. write_file(path, content)                              <- store results
```

Reserve `research_traverse` for open-ended investigation where the set of sources is
unknown upfront and automated frontier expansion is needed.

**Check the scratchpad before researching.**
If related content is already stored from an earlier step, use `scratch_query` to extract the
specific answer rather than launching a fresh web investigation.

## Scratchpad integration
This skill stores each useful fetched page as its own scratchpad artifact under a deterministic
`research_page_*` key and returns those keys in `best_pages` and `page_manifest`.

Preferred follow-up pattern:
1. call `research_traverse(...)`
2. inspect `best_pages[*].scratch_key`
3. run `scratch_query(key, question)` on one or more specific page artifacts

Avoid `scratch_load` on the entire combined `research_traverse` result unless you explicitly need
the raw manifest for debugging.

## Examples
- `research_traverse("Which Ferrari drivers have won the Monaco Grand Prix?")`
- `research_traverse("What changed in Python 3.14 packaging guidance?", max_pages=8, max_hops=1)`