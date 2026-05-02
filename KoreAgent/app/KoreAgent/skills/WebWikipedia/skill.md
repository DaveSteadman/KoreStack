# WebWikipedia Skill

## Purpose
Look up a topic on the live Wikipedia REST API and return a plain-text article summary. Use this for authoritative factual reference data about a person, place, concept, event, or technology. For current news or live data, use WebSearch instead.

## Trigger keyword: wikipedia

## Interface
- Module: `code/KoreAgent/skills/WebWikipedia/wikipedia_skill.py`
- Functions:
  - `lookup_wikipedia(topic: str, timeout: int = 15)`

## Parameters

### `lookup_wikipedia(topic, timeout = 15)`
- `topic` *(required)* - subject to look up: a name, term, acronym, or short phrase.
- `timeout` *(optional, default 15)* - network timeout in seconds.

## Output
- `lookup_wikipedia(...)` - returns a plain-text block starting with `"Wikipedia - <article title>"` followed by the article extract (up to 400 words). Returns `"No Wikipedia data found for '<topic>'"` when no matching article is found. Skips disambiguation pages automatically and tries the next candidate.

## Triggers
Invoke this skill when the prompt contains any of these concepts or phrases:
- `what is`, `tell me about`, `who is`
- `look up on Wikipedia`, `Wikipedia article`
- `background on`, `history of`, `definition of`
- `bio`, `biography`, `life of`, `biography of`

## Tool selection guidance

**Prefer `lookup_wikipedia` over `fetch_page_text` for Wikipedia content.**
Never use `fetch_page_text` with a `wikipedia.org` URL. `lookup_wikipedia` calls the Wikipedia
REST API directly, returns a clean pre-parsed extract, and is significantly faster and more
reliable than scraping the HTML page. If the topic has a Wikipedia article, always call
`lookup_wikipedia(topic)` instead of fetching the Wikipedia URL.

**Check the scratchpad before calling Wikipedia.**
If a Wikipedia article or related content was already fetched earlier in this session, it will
be stored in the scratchpad. Use `scratch_query(key, question)` to extract the needed
information from the stored article rather than fetching again.

**Prefer Wikipedia over WebSearch for stable reference topics.**
`lookup_wikipedia` is a single fast call that returns an authoritative, structured summary.
For questions about a person, place, concept, event, or technology with a well-known Wikipedia
article - always try Wikipedia first before issuing a web search.

**When to use WebSearch instead:**
- The topic is current news or a recent event (Wikipedia may not be updated).
- The question is about a very specific niche where Wikipedia coverage is thin.
- The topic requires cross-source corroboration.

## Scratchpad integration
Article extracts can be several hundred words.  When the content will be used in a downstream
step (write to file, summarise, compare with another result), park it with `scratch_save` first.

- `lookup_wikipedia("Python programming language")` → `scratch_save("wikiarticle", <output>)` → reference with `{scratch:wikiarticle}` in later steps
- `write_file("data/article.txt", "{scratch:wikiarticle}")` - write parked article content without a separate `scratch_load`

## Examples
- `lookup_wikipedia("Python programming language")` - returns the Wikipedia summary
  - Returns: `"Wikipedia - Python (programming language)\nPython is a high-level..."`
- `lookup_wikipedia("Eiffel Tower")` - returns the Eiffel Tower article summary
- `lookup_wikipedia("quantum entanglement")` - returns background on the physics concept
