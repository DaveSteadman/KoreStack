# KoreGraph — Design

## Purpose
A database of named concept connections that helps surface indirect relationships between search terms — expanding what would otherwise be a single-keyword search into a richer network of related concepts.

## Guiding Principle

**KoreGraph is not storing knowledge. It is storing connectivity.**

The graph sits in front of document stores, expanding a search term into a richer set of candidate terms before document retrieval begins:

```
keyword → find matching concept → expand neighbourhood (depth N)
        → return connected concept names as strings
        → search documents with expanded term list
```

## Foundation: String-Transparent API

**All external callers interact with KoreGraph using string terms only.**

Integer `concept_id` values are an internal implementation detail of the `vocab` table and are never required by any external caller. The API resolves strings to concept_ids transparently, creating new vocab entries on demand.

This means:
- Agents create connections by providing three strings: start, connection type, end
- Agents expand a concept by providing a search term string
- KoreDataGateway receives connection results as strings, never integers
- The `concept_id` numbering is a private deduplication mechanism

---

## Schema (SQLite, 2 tables)

### `vocab`
The single place raw strings live. Maps string terms to integer concept IDs.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | row identity for CRUD |
| `concept_id` | INTEGER NOT NULL | shared number — all aliases for a concept share one |
| `term` | TEXT NOT NULL UNIQUE | the raw string |

Multiple rows can share a `concept_id` — those are effectively aliases of the same concept. All other tables use `concept_id` integers only; no raw strings anywhere else.

Seeded predicates (terms that exist from first run): `works_for`, `founded`, `part_of`, `related_to`, `opposed_to`, `located_in`, `successor_of`, `predecessor_of`, `alias_of`, `owns`, `parent_of`, `member_of`.

### `relations`

Pure triple store. All three positions are `concept_id` integers from the vocab table.

| Column | Type | Notes |
|---|---|---|
| `subject_concept_id` | INTEGER | "start" concept |
| `predicate_concept_id` | INTEGER | connection type concept |
| `object_concept_id` | INTEGER | "end" concept |
| `state` | INTEGER | 0=proposed 1=active 2=deprecated 3=rejected |
| `score` | INTEGER | 0–255 importance heat; accumulates on re-insert |

The `score` field accumulates: re-inserting an existing (start, connection, end) triple adds the new score to the existing value (capped at 255). Repeated observation of the same relationship reinforces its weight automatically.

**`CONCEPT_BLACKLIST`**: a frozenset of stop words and common prepositions in `database.py`. Terms in the blacklist are silently rejected before insertion into `vocab`.

---

## Use Cases

1. **Search expansion** — given a keyword, return a ranked list of connected terms to broaden document search in KoreData.
2. **Connection discovery** — given two terms, find what connects them in the graph.
3. **Agent augmentation** — KoreAgent queries KoreGraph to understand the relationship landscape before formulating a research plan.

---

## Integration with KoreData Search

KoreDataGateway's `POST /api/search` can include `"graph"` as a search domain. KoreGraph's `/api/expand-by-term?q=keyword` returns connected concept names as strings — no concept_ids visible to the caller. The gateway returns connection data alongside document search results.

---

## Agent Population

The graph is populated primarily by a **KoreAgent trawl skill** using the string-based creation endpoint. The agent extracts entity-relationship-entity triples from source documents and posts them as plain strings.

**Population flow:**
```
for each KoreData source document:
    extract entity pairs and their relationship from context
    POST /api/connections/by-name { start, connection, end, state: 0, score: N }
    KoreGraph resolves/creates vocab entries transparently
    score accumulates if the same triple is seen again
```

All proposals land as `state=0` (proposed). A curation pass promotes to `state=1` (active) and assigns or confirms a score.

---

## Service Architecture

KoreGraph follows the same pattern as KoreFeed, KoreLibrary, KoreRAG and KoreReference.

### Port
Configured via `services.koregraph.port` in `config/korestack_config.json`. Proxied behind KoreDataGateway at `/graph` prefix.

### Module layout
```
KoreGraph/
    main.py              # startup banner, uvicorn launch
    app/
        config.py        # cfg (host, port, data_dir, ui_prefix from KG_UI_PREFIX env var)
        database.py      # all DB operations
        server.py        # FastAPI app + MCP server
        templates/
            base.html
            vocab.html
            connections.html
```

### URL structure

| Path | Purpose |
|---|---|
| `/` | redirect → `/ui/vocab` |
| `/ui/vocab` | vocabulary management page |
| `/ui/connections` | connection management page |
| `/api/vocab` | vocab CRUD |
| `/api/connections` | connection CRUD |
| `/api/connections/by-name` | string-based connection creation (agents use this) |
| `/api/expand-by-term` | expand concept by string term (returns strings only) |
| `/api/search` | vocab keyword search |
| `/api/expand` | expand concept sub-graph by concept_id |
| `/status` | health check |
| `/mcp` | Streamable HTTP MCP transport |

---

## MCP Tools

Mounted at `/mcp` for agent consumption. All tools use string terms only — no integer IDs required.

| Tool | Args | Returns |
|---|---|---|
| `search_vocab` | `q: str, limit?: int` | matching vocab terms |
| `expand_concept_by_term` | `term: str, depth?: int, min_score?: int` | `{matched, nodes, edges}` — all strings |
| `create_connection` | `start, connection, end, state?, score?` | created connection as strings |



