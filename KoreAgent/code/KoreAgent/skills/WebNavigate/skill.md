# WebNavigate Skill

## Purpose
Extract all navigable hyperlinks from a web page and return them as a numbered list with anchor text and resolved absolute URLs. Use this when you land on a hub or listing page (news front page, GitHub topic, forum index, search results page) and need to see what links are available before deciding which ones to read. This is the middle link in the web navigation chain - between `search_web` (discovery) and `fetch_page_text` (reading content). Navigation chrome (menus, login, subscribe, cookie notices) is filtered automatically.

## Trigger keyword: links, navigate

## Interface
- Module: `code/KoreAgent/skills/WebNavigate/web_navigate_skill.py`
- Functions:
  - `get_page_links(url: str, filter_text: str = "", max_links: int = 30, timeout_seconds: int = 15)`
  - `get_page_links_text(url: str, filter_text: str = "", max_links: int = 30, timeout_seconds: int = 15)`

## Parameters

### `get_page_links(url, filter_text = "", max_links = 30, timeout_seconds = 15)`
- `url` *(required)* - full HTTP or HTTPS URL of the listing or hub page to extract links from.
- `filter_text` *(optional)* - case-insensitive substring; only links whose anchor text or URL contains this string are returned. Use for coarse pre-filtering when you already know a keyword. For semantic filtering ("which links are about open source models?") use `scratch_query` on the parked result instead.
- `max_links` *(optional, default 30)* - maximum number of links to return, 1-100.
- `timeout_seconds` *(optional, default 15)* - network timeout, 5-60.

### `get_page_links_text(url, filter_text = "", max_links = 30, timeout_seconds = 15)`
Same parameters as `get_page_links`.

## Output
- `get_page_links(...)` - returns `list[dict]`, each entry `{"text": str, "url": str}`. On error: single-entry list `{"text": "Error", "url": ..., "error": "..."}`.
- `get_page_links_text(...)` - returns a formatted plain-text block:
  ```
  "Hacker News" (https://news.ycombinator.com)  [30 links]
  1. [Article title here] https://example.com/article/123
  2. [Another story] https://other.com/story
  ```
  The page `<title>` is shown in the header when available. Returns a string beginning with `"Error:"` on failure.

**Note:** `<base href>` declarations in the fetched HTML are honoured when resolving relative links.

## Triggers
Invoke this skill when the prompt contains any of these concepts or phrases:
- `get links from`, `list links on`, `what links are on`
- `navigate to`, `follow the links on`, `find links on this page`
- `what is on the front page of`, `what stories are on`
- `hub page`, `listing page`, `index page`, `forum page`, `news front page`
- `find articles on`, `what pages link to`

## The web navigation chain

This skill is the middle step of a three-stage chain. Understanding the chain is essential for correct tool selection:

```
STAGE 1 - Discovery (find entry-point URLs by topic)
  search_web_text("AI tools this week")
  → returns: titles, URLs, snippets from DuckDuckGo

STAGE 2 - Navigation (see what is on a listing/hub page)
  get_page_links_text("https://news.ycombinator.com")
  → returns: numbered link list with anchor text and absolute URLs
  [result auto-parks in scratchpad if large]

  scratch_query("_tc_r2_get_page_links_text", "which links are about open source AI tools?")
  → returns: "Items 3, 7, 14 look relevant - ..."

STAGE 3 - Content (read selected pages)
  fetch_page_text("https://...", query="what does this project do?")
  → returns: compact extracted answer
```

**When to use each stage:**
- Skip Stage 2 if you already know the specific article URL - go straight to `fetch_page_text`.
- Use Stage 2 when you need to survey a listing page and select items: HN front page, GitHub trending, any news index.
- Use Stage 1 when you do not have a starting URL and need to discover one.

## Scratchpad integration

`get_page_links_text` output for a typical listing page (20-30 links) will exceed the auto-park threshold and be saved automatically by the orchestration layer. The key format is `_tc_r{round}_get_page_links_text`.

**Recommended pattern for site navigation:**

```
Step 1: get_page_links_text("https://news.ycombinator.com")
        → result auto-parks to _tc_r1_get_page_links_text

Step 2: scratch_query("_tc_r1_get_page_links_text",
        "which items mention AI, LLM, machine learning, or open source models?")
        → returns a compact filtered list of matching item numbers

Step 3: scratch_peek("_tc_r1_get_page_links_text", "Item 7")
        → returns the URL for that item

Step 4: fetch_page_text(url, query="what is this project and how would a developer use it?")
```

**Using filter_text for coarse pre-filtering:**
If the listing page is very large (100+ links) and you already know a keyword, pass `filter_text="AI"` to trim the result before it parks. This keeps the parked content more focused for `scratch_query`. Do not over-filter - `scratch_query` handles semantic matching better than substring matching.

Do not use placeholder values such as `"Skip"` or `"Next"` for `filter_text` unless you literally want links containing those words. Omitting `filter_text` is the correct default when surveying a hub page for article candidates.

For news harvesting tasks:
- Use this skill on topic pages, category pages, front pages, and search-result pages.
- Extract concrete article/detail URLs here before calling `fetch_page_text`.
- If the parent task asks for article URLs, links returned from this skill are the candidates; the hub page URL itself is not the article.

## Examples
- `get_page_links_text("https://news.ycombinator.com")` - get today's HN front page links
  - Returns: `'"Hacker News" (https://news.ycombinator.com)  [30 links]\n1. [Show HN: ...] https://...'`
- `get_page_links_text("https://github.com/trending", filter_text="language:python")` - pre-filter GitHub trending
- `get_page_links("https://techcrunch.com")` - returns structured list[dict] for programmatic use
- `get_page_links_text("https://lobste.rs", max_links=20)` - top 20 links from Lobsters front page

## Going deeper from Stage 3

When `fetch_page_text` returns an article that itself contains links worth following (e.g. a
Wikipedia article linking to primary sources, a blog post linking to referenced studies), you
can re-enter Stage 2 without a new network fetch by calling `get_page_links_text` on the same
URL a second time - the result will be served from the in-process URL cache at no extra cost.

If you already hold the HTML from a previous fetch (e.g. from WebResearch internals), the
helper `extract_urls_from_html(html_text, base_url)` in this module extracts a clean,
noise-filtered URL list from the cached HTML string without re-fetching. This helper is not
exposed as a tool but can be imported directly by other skill modules.
