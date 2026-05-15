# KoreGraph

## Purpose
A database of name-connection-name relationships that exposes indirect connections between search keywords — surfacing links that a user might not otherwise think to pursue.

## Guiding Principle
**KoreGraph is not storing knowledge. It is storing connectivity.**

It is a semantic index. The graph does not replace document stores — it sits in front of them, expanding a search term into a richer set of candidate terms before document retrieval begins:

```
keyword → resolve aliases → get entity_id
        → fetch top-N connected entities ordered by score
        → search documents with expanded terms
```

This makes retrieval computationally cheap and fully explainable.

---

## Build Phases

### Phase 1 — Database, API, management UI
- SQLite schema + `database.py`
- REST API (`/api/...`) covering all CRUD for entities, aliases, relation types, relations, evidence
- MCP server (`/mcp`) with trawl + search + expand tools
- Web UI: vocabulary editor, entities table, relations table (with state/score editing)
- No graph canvas

### Phase 2 — Graph canvas
- Extract KoreDiag's canvas primitives into `UIElements/assets/js/graph-canvas.js` (shared module)
- Add KoreGraph's force layout and radial spoke renderer on top
- Add `/ui` graph explorer page

---

## Core Schema (SQLite)

### `entities`
Canonical named entities.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | 4 bytes |
| `name` | TEXT NOT NULL | canonical display name |
| `type` | TEXT | person, org, concept, place, event, … |
| `description` | TEXT | short optional summary |

### `entity_aliases`
Maps alternative names / synonyms back to a canonical entity.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `entity_id` | INTEGER NOT NULL | FK → entities.id |
| `alias` | TEXT NOT NULL | |

### `relation_types`
Vocabulary of labelled relationship kinds.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | 2 bytes (fits UINT16) |
| `label` | TEXT NOT NULL UNIQUE | e.g. `works_for`, `founded`, `related_to` |
| `directed` | INTEGER NOT NULL DEFAULT 1 | 1 = directed, 0 = bidirectional |

The vocabulary is seeded on first run by a single function in `database.py`:

```python
def _seed_relation_types() -> None:
    """Insert the canonical starter vocabulary. Edit here to change the built-in set."""
    SEED = [
        ("works_for",    1),
        ("founded",      1),
        ("part_of",      1),
        ("related_to",   0),
        ("opposed_to",   0),
        ("located_in",   1),
        ("successor_of", 1),
        ("predecessor_of", 1),
        ("alias_of",     0),
    ]
    # INSERT OR IGNORE so re-runs are safe
```

The agent may propose new types; they land in the table and are visible in the vocabulary UI for review.

**Blacklist** — words the trawl skill must never propose as an entity or relation type (stop words, articles, prepositions). Stored as a Python set in `database.py` alongside the seed function:

```python
RELATION_BLACKLIST: frozenset[str] = frozenset({
    "is", "are", "was", "were", "be", "been",
    "a", "an", "the",
    "in", "on", "at", "to", "of", "for", "by", "as", "with",
    "and", "or", "but", "not",
})
```

Any proposed relation label that matches a blacklist entry is silently discarded before insertion.

### `relations`  ← the hot table
The core adjacency list. Kept deliberately minimal.

```sql
CREATE TABLE relations (
    source_entity_id  INTEGER  NOT NULL,
    relation_type_id  INTEGER  NOT NULL,
    target_entity_id  INTEGER  NOT NULL,
    state             INTEGER  NOT NULL DEFAULT 0,
    score             INTEGER  NOT NULL DEFAULT 0,
    UNIQUE (source_entity_id, relation_type_id, target_entity_id)
);
```

**Row layout:**

| Field | Storage | Notes |
|---|---|---|
| `source_entity_id` | 4 bytes | |
| `relation_type_id` | 2 bytes | |
| `target_entity_id` | 4 bytes | |
| `state` | 1 byte (UINT8) | enum — see below |
| `score` | 1 byte (UINT8) | 0–255, "importance heat" |
| **Total** | **12 bytes** | extremely cache-friendly |

**`state` enum:**

| Value | Meaning |
|---|---|
| 0 | proposed |
| 1 | active |
| 2 | deprecated |
| 3 | rejected |

**`score` (UINT8, 0–255):**  
Not a scientific measurement — an importance heat value used only to sort candidate retrieval paths. 256 levels is more than sufficient for this purpose. Floating-point precision would be wasted here.

### `relation_evidence`
Optional supporting citations for a relation. Kept separate so the hot table stays small.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `source_entity_id` | INTEGER NOT NULL | composite FK into relations |
| `relation_type_id` | INTEGER NOT NULL | |
| `target_entity_id` | INTEGER NOT NULL | |
| `evidence` | TEXT | freetext or source URL |

---

## Use Cases
1. **Search expansion** — given a query keyword, return a ranked list of related nodes to broaden or refocus search.
2. **Connection discovery** — given two nodes, find shortest-path or all paths of relationships between them.
3. **Cluster analysis** — identify densely connected sub-graphs (topic clusters) that emerge from user data.
4. **Agent augmentation** — KoreAgent can query KoreGraph to understand the relationship landscape before formulating a research plan.

---

## Agent Population

The graph is populated primarily by a **KoreAgent trawl skill**, not by manual entry. The agent reads across all KoreData sources (KoreFeed articles, KoreRAG chunks, KoreLibrary texts, KoreReference articles) and proposes entity/relation pairs for every co-occurrence or explicit mention it finds.

**Volume expectation:** many thousands to tens of thousands of proposed relations from a single trawl pass. All proposals land as `state=0` (proposed, score=0). A separate curation pass — human review, a scoring agent, or both — promotes relations to `state=1` (active) and assigns a score.

**Trawl skill flow:**
```
for each KoreData source document:
    extract named entities → resolve against entity_aliases or create new entity
    for each co-occurring or explicitly linked entity pair:
        derive relation_type from context ("works for", "founded", "related to", …)
        INSERT OR IGNORE INTO relations (state=0, score=0)
        append evidence row with source document reference
```

The `UNIQUE` constraint on `(source_entity_id, relation_type_id, target_entity_id)` means repeated trawl passes are idempotent — duplicates are silently dropped, not re-inserted.

**Incremental re-trawl:** the skill can be run against only new/updated documents by comparing document modification timestamps against a `last_trawled` watermark stored in the graph database.

**Delivery — MCP tool:** the trawl is exposed as an MCP tool on KoreGraph's `/mcp` endpoint. KoreAgent calls it on demand rather than it running on a schedule. This keeps population explicit and auditable.

```
MCP tool: trawl_koredata
  args: source? (feed|rag|library|reference|all), limit?
  returns: {proposed: N, skipped_blacklist: N, skipped_duplicate: N}
```

---

## Service Architecture

KoreGraph follows the same pattern as KoreFeed, KoreLibrary, KoreRAG and KoreReference.

### Port
**8805** — gateway base 8620 + offset 5.

### Module layout
```
KoreGraph/
    main.py              # startup banner, uvicorn launch
    app/
        __init__.py
        config.py        # cfg — loads [korегraph] section, default port 8805
        database.py      # SQLite schema, all DB operations
        server.py        # FastAPI app, all routes
```

`main.py` follows the existing pattern: insert `CommonCode` on `sys.path`, call `logutil`, print a startup banner showing entity/relation counts and the listen address, then `uvicorn.run`.

### Config section
`config.py` reads from suite config under `[korегraph]`:
```python
_SECTION = "korегraph"
_DEFAULTS = {
    "port": 8805,
    "host": "0.0.0.0",
    "log_level": "info",
    "data_dir": str(get_koredata_dir() / "Graph"),
}
```
SQLite database lives in `data_dir` as `graph.db`.

### URL structure
Follows the suite-wide convention (see service-path-conventions):

| Path | Purpose |
|---|---|
| `/` | redirect → `/ui` |
| `/ui` | browser UI — search and graph explorer |
| `/api/...` | JSON REST API |
| `/status` | health check (returns 200 + JSON) |
| `/mcp` | Streamable HTTP MCP transport |

### UI shell
Uses the shared KoreStack shell from UIElements, identical to other KoreData sub-services:
- `initTopbar({ currentService: 'korеgraph' })`
- `initAppBar` with `overline: 'Data Service'`, `brandLabel: 'KoreGraph'`, `brandIcon: 'korеgraph'`, status dot polling `/status` every 15 s
- Pages extend `base.html` (FastAPI + Jinja2); `<main>` with `max-width: 1320px`

### UI pages *(Phase 1)*

**`/ui`** — *(Phase 2 — graph canvas, deferred)*
See the Node & Edge Rendering Model section below (Phase 2).

**`/ui/vocab`** *(Phase 1)* — Relation type vocabulary editor:
```
┌──────────────────────────────────────────────────────────────────┐
│  Relation Types                                    [+ Type]      │
│  ID │ Label           │ Directed │ Relations                     │
│  ── │ ─────────────── │ ──────── │ ──────────                    │
│  1  │ works_for       │ yes      │ 4 821                         │
│  2  │ founded         │ yes      │ 1 203                         │
├──────────────────────────────────────────────────────────────────┤
│  Blacklist  [is, are, the, a, an, in, of …]       [Edit]        │
└──────────────────────────────────────────────────────────────────┘
```

**`/ui/entities`** *(Phase 1)* — Entity management (add, edit, view aliases):
```
┌──────────────────────────────────────────────────────────────────┐
│  Entities                                          [+ Entity]    │
│  ID │ Name          │ Type    │ Aliases │ Relations              │
│  ── │ ────────────  │ ─────── │ ─────── │ ──────────             │
│  1  │ Elon Musk     │ person  │ 3       │ 12                     │
└──────────────────────────────────────────────────────────────────┘
```

**`/ui/relations`** *(Phase 1)* — Relation management (propose, approve, score, reject).
All states shown simultaneously, differentiated by row styling matching the edge rendering model:
```
┌──────────────────────────────────────────────────────────────────┐
│  Relations  Filter: [all states ▾]              [+ Relation]     │
│  Source       │ Relation    │ Target      │ State    │ Score     │
│  ──────────── │ ────────── │ ────────── │ ──────── │ ────────  │
│  Elon Musk    │ founded     │ Tesla       │ active   │ [200 ±]  │
│  Elon Musk    │ related_to  │ PayPal      │ proposed │ [  0 ±]  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Node & Edge Rendering Model *(Phase 2)*

### Nodes — circles
Each entity is drawn as a circle. Radius scales weakly with connection count so high-degree nodes are visually larger but don't dominate.

### Edge attachment — equal angular division
For a node with **N** connections, the edges leave the circle at equally-spaced angles:

$$\theta_i = \frac{i}{N} \times 360°, \quad i = 0, 1, \ldots, N-1$$

Angles are sorted so that the edge pointing most directly toward each target gets the nearest slot — minimising crossing at source. This gives every node a clean "clock face" of spokes regardless of degree.

```
  2 connections      4 connections      6 connections
      │                  │                  │
  ────●────           ───●───          ──●──
      │               │     │          / │ \
                      │     │         /  │  \
```

### Edge style — score → visual weight
The `score` (UINT8, 0–255) drives all visual style properties of the edge:

| Score range | Line width | Opacity | Dash |
|---|---|---|---|
| 200–255 | 3 px | 1.0 | solid |
| 128–199 | 2 px | 0.8 | solid |
| 64–127  | 1.5 px | 0.6 | solid |
| 1–63    | 1 px | 0.35 | dashed |
| 0       | 1 px | 0.15 | dotted (proposed, unscored) |

Edge colour uses the suite accent palette, desaturating toward `--text-dim` as score falls.

### Node colour — entity type
Node fill colour maps to entity type using suite colour tokens, consistent across all KoreGraph views.

### Layout algorithm
Node positions are computed by a **spring/repulsion loop** running client-side in JS:
- Nodes repel each other (Coulomb-style)
- Edges act as springs pulling connected nodes together, weighted by score
- Higher-score edges pull more strongly → highly-connected, high-scoring clusters contract visually
- Runs for a fixed number of iterations on data load, then settles

The loop operates on the same canvas primitives as KoreDiag — no external library.

**Canvas shared module:** before Phase 2 begins, KoreDiag's rendering primitives are extracted from KoreDocs into `UIElements/assets/js/graph-canvas.js` and re-exported from `chrome.js`. Both KoreDiag and KoreGraph then import from that shared module.

---

## Integration Points
- **KoreDataGateway** — KoreGraph exposes a REST API consumed by the gateway, consistent with other KoreData services.
- **KoreRAG** — edges can be inferred or seeded from RAG chunk tags; a RAG chunk referencing both "X" and "Y" is candidate evidence for an X→Y edge.
- **KoreFeed** — named entities extracted from feed articles can be automatically proposed as new nodes or edge evidence.
- **KoreAgent** — agent skills can query KoreGraph to enrich context before searching other data sources.

---

## Storage
SQLite. No external graph database needed. The schema is small enough that at 100 million relations the storage remains entirely manageable. Traversal queries run against an in-memory NetworkX representation loaded from SQLite on startup (or lazily per query for smaller deployments).

---

## API Sketch

### Agent / MCP endpoints
```
GET  /api/search?q=<term>              → [{id, name, type, score}] entities matching name/alias
GET  /api/expand?id=<id>&depth=<n>    → sub-graph JSON: {nodes:[…], edges:[…]} within N hops
GET  /api/path?from=<id>&to=<id>      → [{nodes, edges}] shortest path(s)
```

The `/api/expand` response is also what the UI graph canvas consumes — Cytoscape.js accepts the `{nodes, edges}` format directly.

### Management endpoints
```
POST /api/entity                       → create entity
PUT  /api/entity/{id}                  → update entity
GET  /api/entity/{id}                  → entity detail + relations
POST /api/relation                     → create/propose relation
PUT  /api/relation                     → update state or score
POST /api/evidence                     → attach evidence to a relation
```

### Health
```
GET  /status                           → {status, entities, relations, db_size_bytes}
```

---

## Open Questions
- How are entities and relations initially populated? (manual entry, agent inference, feed extraction)
- Deduplication strategy when the same real-world entity is added under different names?
- Does KoreGraph need its own UI, or managed solely through KoreDataGateway and agent skills?
- Rescoring strategy — manual only, or can agents propose score adjustments based on usage?
- Should `state=proposed` relations be visible to retrieval, or only `state=active`?
