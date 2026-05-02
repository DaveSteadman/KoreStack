# WebFetch Skill

## Purpose
Fetch a web page by URL and extract its readable prose content, stripping all HTML markup, navigation, scripts, advertisements, and other non-content noise. Returns clean text ready for LLM synthesis or summarization.

## Trigger keyword: fetch

## Interface
- Module: `code/KoreAgent/skills/WebFetch/web_fetch_skill.py`
- Entry point: `fetch_page_text(url: str, max_words: int = 2000, timeout_seconds: int = 15, query: str | None = None)`

## Parameters

### `fetch_page_text(url, max_words, timeout_seconds, query)`
- `url` *(required)* - full HTTP or HTTPS URL to fetch. Local paths and ftp:// are rejected.
- `max_words` *(optional, default 2000)* - maximum words of body prose to return (range 50-4000).
- `timeout_seconds` *(optional, default 15)* - network timeout in seconds (range 5-60).
- `query` *(optional)* - when provided, runs an isolated LLM extraction pass and returns only the facts relevant to the query. Use when you know exactly what you are looking for on the page.

## Output
Returns a plain `str` containing:
- When `query` is None: the readable body text extracted from the page, up to `max_words` words.
- When `query` is set: a concise LLM-extracted answer targeted at the query, or `"Not found on this page."`
- A string beginning with `"Error:"` if the fetch or parse failed. Never raises.

## Triggers
- fetch the page
- read the content of
- get the article from
- open the URL
- read this URL
- what does the page say

## Tool selection guidance

**Check the scratchpad before fetching.**
If active scratchpad keys are listed in the system prompt, check whether the page content
already exists there before making a network request. Use `scratch_query(key, question)` to
extract a specific answer from stored content without re-fetching.

**This is Stage 3 in the web chain - use it on specific article/detail URLs.**

`fetch_page_text` is designed to read a single known page. If the URL you have is a hub or
listing page (front page, topic index, search results), use `get_page_links_text` first to
survey the available links, then call `fetch_page_text` on the selected items.

If the user asked for article URLs or a news briefing, do not treat a successful fetch of a
hub page title or headline list as proof that the hub itself is the article. Use navigation to
extract specific article/detail URLs first.

| Situation | Correct tool |
|---|---|
| URL is a specific article/repo/doc | `fetch_page_text(url, query=...)` |
| URL is a listing/hub/front page | `get_page_links_text(url)` first, then `fetch_page_text` |
| No URL yet, need to find one | `search_web_text(query)` first |

**Prefer `query=` for narrow factual extraction, but switch to raw+scratchpad for completeness-sensitive pages.**

Raw mode (no `query`) can return up to 4,000 words directly into the orchestration layer. Large
results are auto-saved to the scratchpad, which lets you inspect them later with `scratch_query`
or `scratch_peek` without repeating the network fetch.

Query mode runs an isolated throwaway LLM call and returns only a compact extracted answer when
the question is narrow and page-local.

Use raw mode with a larger `max_words` value (typically 2000-4000) when any of these are true:
- the question asks for `all`, `every`, `complete`, or a historical list across many years
- the URL is a stats/index/table page rather than a single narrative article
- you need broad page context and expect follow-up filtering in the scratchpad

Rule of thumb:
- Fetching a page to answer a question -> always use `query=`
- Fetching a page to store for later inspection -> use raw mode with generous `max_words`
- Exhaustive or history-sensitive extraction from a stats page -> use raw mode first, let it auto-save to scratchpad, then use `scratch_query`

Blocked-page rule of thumb:
- If a fetch returns `Error: ... HTTP 401` or `Error: ... HTTP 403`, treat the page as blocked and move on to another candidate URL.
- If a fetch returns only a bare title from a topic/search page, treat it as thin hub content rather than a substantive article.

## Scratchpad integration
Page text can be large. Use `scratch_save` to store it under a key and reference it with `{scratch:key}` in follow-up steps rather than repeating the full text inline.

Example chain:
1. `search_web("python asyncio tutorial")` - returns list of results with URLs
2. `fetch_page_text("https://example.com/asyncio-guide", query="summarise the key asyncio concepts")` - returns extracted answer
3. `scratch_save("asyncio_article", {result from step 2})` - stores text
4. LLM synthesizes answer from `{scratch:asyncio_article}`

## Examples

Minimal - fetch a known URL:
```
fetch_page_text("https://example.com/article")
```

With word limit:
```
fetch_page_text("https://example.com/article", max_words=500)
```

With targeted extraction - returns only relevant facts, keeps main context compact:
```
fetch_page_text("https://en.wikipedia.org/wiki/Monaco_Grand_Prix", query="Which years did Ferrari win and who was the driver?")
```

With a statistics page where completeness matters - fetch long raw content so it lands in scratchpad:
```
fetch_page_text("https://gpracingstats.com/circuits/imola/", max_words=3000)
```

Then filter the auto-saved scratchpad content:
```
scratch_query("_tc_r2_fetch_page_text", "List every Williams win at Imola with year and driver")
```

Notes:
- Uses `beautifulsoup4` for high-quality extraction; falls back to stdlib html.parser if unavailable.
- Only `http` and `https` schemes are supported.
- Returns an error string on failure so orchestration can continue gracefully.
- Naturally follows `search_web` or `search_web_text` which supply the candidate URLs.
