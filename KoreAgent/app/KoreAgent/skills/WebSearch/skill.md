# WebSearch Skill

## Purpose
Search the web using DuckDuckGo and return ranked results with title, URL, and snippet. No API key required. Use `search_web_text` for direct synthesis - results come back as formatted text ready to read inline. Use `search_web` when you need to iterate over individual result fields (url, title, snippet) programmatically or pass them selectively to another skill. This skill only returns results - it does not persist or save anything.

## Trigger keyword: search

## Interface
- Module: `code/KoreAgent/skills/WebSearch/web_search_skill.py`
- Functions:
  - `search_web(query: str, max_results: int = 5, timeout_seconds: int = 15, offset: int = 0, prefer_article_urls: bool = False)`
  - `search_web_text(query: str, max_results: int = 5, timeout_seconds: int = 15, max_chars_per_result: int = 500, offset: int = 0, prefer_article_urls: bool = False)`

## Parameters

### `search_web(query, max_results = 5, timeout_seconds = 15, offset = 0, prefer_article_urls = False)`
- `query` *(required)* - search query string.
- `max_results` *(optional, default 5)* - number of results to return, 1-10.
- `timeout_seconds` *(optional, default 15)* - network timeout in seconds, 5-30.
- `offset` *(optional, default 0)* - skip this many results from the start (multiples of 30 recommended for page 2+). Best-effort GET-based paging - may not return results for all queries.
- `prefer_article_urls` *(optional, default false)* - when true, scans up to 3 DuckDuckGo result pages, promotes concrete article/detail URLs ahead of hub pages, and annotates each result with a `page_kind` field.

### `search_web_text(query, max_results = 5, timeout_seconds = 15, max_chars_per_result = 500, offset = 0, prefer_article_urls = False)`
- `query` *(required)* - search query string.
- `max_results` *(optional, default 5)* - number of results to return, 1-10.
- `timeout_seconds` *(optional, default 15)* - network timeout in seconds, 5-30.
- `max_chars_per_result` *(optional, default 500)* - maximum characters of snippet text per result, 0-2000. Set to 0 to disable truncation.
- `offset` *(optional, default 0)* - skip this many results; use to retrieve page 2+ when the first page was exhausted.
- `prefer_article_urls` *(optional, default false)* - same behavior as `search_web(...)`; when enabled the formatted output also includes each result's `page_kind` tag.

## Output
- `search_web(...)` - returns `list[dict]`, each entry with `rank` (int), `title` (str), `url` (str), `snippet` (str), and `page_kind` (`article`, `hub`, `homepage`, `search-results`, or `other`). On error: single-entry list with `rank=0` and `snippet` describing the failure.
- `search_web_text(...)` - returns a plain-text formatted block with rank, title, URL, snippet, and optional `[page_kind]` tag. Ready for direct LLM consumption.

## Triggers
Invoke this skill when the prompt contains any of these concepts or phrases:
- `search the web for`, `find information about`, `look up`
- `what is the latest news on`, `search for`, `find recent`

## Tool selection guidance

**Check the scratchpad before searching.**
If relevant data from a prior step in this session is already stored, use `scratch_query` or
`scratch_load` rather than issuing a new search. Only search the web when the data is
confirmed absent from the scratchpad.

**Always call the search tool - never answer from training data.**
When the prompt says "search for", "search the web for", "find information about", or "look up",
a tool call is mandatory. The purpose of search prompts is to retrieve current, verified data -
not to recall training knowledge. If the tool returns no results, report that explicitly rather
than substituting an answer from memory.

**"Search failed" vs "No results" - treat these differently.**
- `title="No results"` - DuckDuckGo found nothing for this query. Worth retrying with a
  simplified query or escalating to `research_traverse`.
- `title="Search failed"` with a timeout or URL error in the snippet - this is a connectivity
  failure. The endpoint is unreachable right now. Do NOT retry the same search endpoint with
  alternative query phrasings - it will time out again. Make at most one offline fallback
  attempt (`lookup_wikipedia`), then immediately report no results.

**Choose between `search_web` and `search_web_text`:**
- Use `search_web_text` in almost all cases - returns formatted text ready for direct synthesis,
  no extra processing needed.
- Use `search_web` only when you need to iterate over individual result fields (URL, title,
  snippet) programmatically - for example when passing each URL to a subsequent `fetch_page_text`.

**When the user asks for article URLs, enable article preference.**
- Use `prefer_article_urls=true` for prompts such as `find 5 article URLs`, `collect news articles`,
  `gather article links`, or `build a briefing from recent coverage`.
- Do not treat `hub`, `homepage`, or `search-results` entries as completed article picks.
- If the top search results are still hubs, route them through `get_page_links_text(...)` to extract
  concrete article/detail URLs before reading content.

**The three-stage web chain - when to go beyond a search:**

`search_web` is Stage 1: it finds entry-point URLs. Know which stage you need next:

| What you have after search | Next step | When to use it |
|---|---|---|
| A specific article URL | `fetch_page_text(url, query=...)` | Reading a known article |
| A hub/listing/index URL | `get_page_links_text(url)` | Surveying what is on a front page before choosing items |
| Need multiple sources | `research_traverse(query)` | Full automated investigation |
| Stable reference topic | `lookup_wikipedia(topic)` | Faster than web search for known subjects |

The hub-page pattern - use `get_page_links_text` as an intermediate step when the search
result is a listing page (HN, GitHub trending, news homepage, forum index) rather than a
direct article. Get the links, park them, use `scratch_query` to select, then `fetch_page_text`
on the chosen items.

## Scratchpad integration
Search results can be large.  When the result will be referenced in a later step (summarise,
extract a field, write to file), park it immediately with `scratch_save` so the full text does
not have to be re-fetched or carried as an inline string through subsequent planning rounds.

- `search_web_text("Python 3.14 release notes")` → `scratch_save("searchresult", <output>)` → use `{scratch:searchresult}` in downstream steps
- `write_file("data/results.txt", "{scratch:searchresult}")` - write parked search result without an extra `scratch_load` call

## Examples
- `search_web_text("Python 3.14 release notes", max_results=3)` - top 3 DuckDuckGo results as formatted text
  - Returns: `"Web search results for: Python 3.14 release notes\n\n[1] ..."`
- `search_web("Eiffel Tower height")` - structured result list for programmatic use
- `search_web("recent AI news articles", max_results=5, prefer_article_urls=true)` - prefer concrete article URLs over topic/category pages
