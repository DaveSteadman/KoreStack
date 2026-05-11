# HANSARD.md — Parliamentary Record Integration

*Design exploration document. Status: idea stage.*

---

## What Hansard Is

Hansard is the official verbatim record of UK Parliamentary proceedings, published at [hansard.parliament.uk](https://hansard.parliament.uk/). It covers:

- **Commons Chamber** and **Westminster Hall** debates
- **Lords Chamber** and **Grand Committee** debates
- **Written Ministerial Statements**
- **Divisions** (recorded votes) for both Houses
- **Oral Questions** (including Prime Minister's Questions)
- Historical records back to the early 19th century; daily updates published the next working day

It is licensed under the [Open Parliament Licence](https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/), meaning it is freely usable for this purpose.

---

## Why It's Not a Service

Hansard is deeply UK-political in focus — not general-interest reference content. It belongs in the system as a **curated datafile** (like a local database the agent can query), not as a live-fetching general-interest service the way KoreFeed handles RSS. The right home is within **KoreData**.

---

## What Parliament Actually Provides

This is the key discovery: Parliament runs a full structured **developer API** at [developer.parliament.uk](https://developer.parliament.uk/). This eliminates the need for HTML scraping of most structured data:

| API | Base URL | What it provides |
|-----|----------|-----------------|
| **Members** | `members-api.parliament.uk` | Current and historical MPs/Lords, biography, party, constituency, voting history |
| **Commons Votes** | `commonsvotes-api.parliament.uk` | All division records, how each MP voted |
| **Lords Votes** | `lordsvotes-api.parliament.uk` | Lords division records |
| **Oral Questions** | `oralquestionsandmotions-api.parliament.uk` | Tabled oral questions, motions |
| **Bills** | `bills-api.parliament.uk` | All Bills for both Houses, stages, amendments |
| **Written Questions** | `questions-statements-api.parliament.uk` | Written Q&A, ministerial statements |
| **Committees** | `committees-api.parliament.uk` | Committee membership, inquiries, publications |

All APIs return **JSON**, are versioned, and carry no authentication requirement.

For **debate text** specifically, the Hansard site itself exposes:
- `GET /debates/GetDebateAsText/{uuid}` — clean plain-text download of a single debate item
- `GET /pdf/Commons/{date}` / `GET /pdf/Lords/{date}` — full-day PDF
- `GET /search/Debates?house=commons&searchTerm=...&startDate=...&endDate=...` — debate search (HTML, scrapeable)

The URL structure for a sitting day is predictable:
```
/Commons/2026-04-29                                          # day index
/Commons/2026-04-29/debates/{uuid}/{slug}                   # individual debate
```

Each debate has an item number, UUID, and slug, all visible in the day-index page.

---

## Where It Fits in KoreData

KoreData currently contains:

| Module | Content type | Model |
|--------|-------------|-------|
| KoreFeed | Live RSS/web content | Short-term, source-linked |
| KoreReference | Wikipedia-style encyclopaedia | Articles, links, categories |
| KoreLibrary | Long-form ebooks | Catalog → book → full text |
| KoreRAG | User-managed text chunks | Flat chunks + FTS5 |

Hansard is a **curated datafile** — not live content (KoreFeed), not encyclopaedia articles (KoreReference), not ebooks (KoreLibrary). It belongs in **KoreRAG**, which is extended to support multiple named databases. This follows the same pattern KoreLibrary already uses for catalogs: separate SQLite files, all sharing the same schema.

Building a dedicated KoreHansard module would be too narrow a use case for the general-purpose system. The point is to have a datasource the agent can extract value from, not to build a bespoke Parliamentary archive viewer.

---

## Design Challenges

### 1. Speech text parsing

The `/debates/GetDebateAsText/{uuid}` endpoint produces plain text, not structured JSON. Speaker turns follow a pattern roughly like `Member Name (Constituency):` then speech body — but this formatting is not guaranteed to be consistent across a decade of records, let alone the pre-2010 OCR-scanned material. Parsing is regex-based and will produce noise: procedural announcements ("The House met at half-past Eleven"), unattributed passages (Speaker interjections, points of order), and formatting artefacts. Historical records (pre-2010, added from scanned volumes) will have outright OCR errors and more inconsistent formatting. A parse quality score per-debate (% of text successfully attributed to a named speaker) would help identify problem records.

### 2. Member disambiguation

The Members API provides clean integer IDs and canonical names. The debate text provides display names with honourifics, constituency suffixes, and title changes over time: *"Mr Blair"*, *"The Prime Minister"*, *"Sir Keir Starmer (Holborn and St Pancras)"*, *"The Lord Speaker"*, *"Baroness Jones of Moulsecoomb"*. Matching these to Members API IDs requires fuzzy matching plus a fallback: if a speaker name can't be confidently resolved, it's stored as a raw string without a `member_id` tag. Unresolved names can be manually corrected later, but a high unresolved rate undermines the member-navigation feature.

### 3. Scale and tag-search performance

Ten years of Commons + Lords debates is roughly 2,500 sitting days, each with 15–50 debates, each with 10–40 speeches: potentially **1–3 million speech chunks**. FTS5 at this scale is fine for keyword search. What breaks down is the freeform tag approach: `WHERE tags LIKE '%member:Keir Starmer%'` is a substring scan over millions of rows — it works but degrades. At scale the tag approach needs either dedicated indexed columns or a navigation index (see below).

### 4. The navigation vs. search tension — the core problem

This is the most important design challenge. **The chunk model is a good fit for agent search and a poor fit for human navigation.**

KoreReference achieves its hyperlink navigation because articles contain `[[wikilinks]]` embedded in their text. At import, those links are extracted to a `links` table. The UI then has a first-class query: *"give me all articles that link to X"*. The navigation is backed by a proper index.

Hansard's natural hyperlink graph is:
```
sitting-day ──→ list of debates (ordered by item_number)
debate ──────→ list of speeches (ordered, sequential)
speech ──────→ member page
member ──────→ all their speeches (any date, any debate)
member ──────→ all their votes (in divisions)
division ────→ debate it arose from
division ────→ full voter matrix (each voter → their member page)
```

None of these links exist in chunk text. They are all structural relationships. The chunk model has no way to express *"next speech in this debate"* or *"all debates this member spoke in"* without reverting to tag-substring queries over millions of rows — which is slow, fragile, and not how a navigation UI should work.

**The chunk model alone cannot deliver KoreReference-quality navigation.** This needs to be stated clearly.

### 5. Incremental sync reliability

Parliament API pagination, rate limits, and occasional downtime mean an ingest run may fail mid-way. The UUID-based deduplication handles restarts, but you need to track which dates are *complete* (all debates fetched) vs *partial* (ingest interrupted). A `sync_log` of some kind is needed, otherwise incremental re-runs may incorrectly skip a partially-ingested date.

### 6. Historical data quality

The Parliament API is rich from roughly 2010 onwards. Pre-2010 content was added by scanning physical bound volumes — OCR errors, missing debates, inconsistent name formatting. Historical import should be treated as a separate lower-quality tier, with the expectation that search recall will be lower for older records.

---

## Is KoreReference-Style Navigation Realistic?

**Yes — but it requires relational tables alongside the chunks.**

The reason KoreReference achieves rich navigation is that it *has* a relational schema: articles, links, categories, sections. The question is not whether to have relational tables, but where to put them and who queries them.

The key insight is that **the objection to a KoreHansard module was about module proliferation, not about having structured data**. You can have structured relational tables without a dedicated service by treating `hansard.db` as a hybrid file: standard KoreRAG chunk tables (used by the KoreRAG service API) plus a set of Hansard-specific navigation tables (ignored by KoreRAG, queried directly by the browse UI).

### The hybrid model

`datacontrol/koredata/RAG/databases/hansard.db` contains two layers:

**Layer 1 — chunk store (KoreRAG-standard, unchanged)**
```sql
chunks      -- standard chunk rows (id, title, source, tags, content, word_count, created_at)
chunks_fts  -- FTS5 index (title, source, tags, content)
```
Serves the agent via the KoreRAG search API. The KoreRAG service is completely unaware of Layer 2.

**Layer 2 — navigation index (Hansard-specific, written by ingestor)**
```sql
h_sittings  (date TEXT PK, house TEXT, volume INTEGER, debate_count INTEGER)

h_debates   (uuid TEXT PK, sitting_date TEXT, house TEXT,
             title TEXT, debate_type TEXT, item_number INTEGER, url TEXT)

h_speeches  (chunk_id INTEGER PK REFERENCES chunks,  -- back-link to the chunk
             debate_uuid TEXT, member_id INTEGER,
             speech_order INTEGER)

h_members   (member_id INTEGER PK,  -- Parliament member ID
             display_name TEXT, house TEXT, party TEXT,
             constituency TEXT, start_date TEXT, end_date TEXT)

h_divisions (division_id INTEGER PK, division_date TEXT, house TEXT,
             title TEXT, debate_uuid TEXT,
             ayes INTEGER, noes INTEGER, result TEXT)

h_votes     (division_id INTEGER, member_id INTEGER,
             vote TEXT,  -- 'Aye' | 'No' | 'Teller Aye' | 'Teller No'
             PRIMARY KEY (division_id, member_id))
```

The ingestor writes to both layers in the same SQLite transaction. The browse UI queries Layer 2 for navigation, Layer 1 (via the KoreRAG API) for search. The KoreRAG service never touches the `h_*` tables.

### What navigation this enables

| User action | Query |
|-------------|-------|
| Browse calendar | `SELECT date, house, debate_count FROM h_sittings ORDER BY date DESC` |
| Click a sitting day | `SELECT * FROM h_debates WHERE sitting_date=? ORDER BY item_number` |
| Click a debate | Load chunks WHERE source LIKE `%{uuid}%` via KoreRAG API, ordered by h_speeches.speech_order |
| Click a member name | `SELECT * FROM h_members WHERE member_id=?` → their h_speeches, h_votes |
| Member contributions list | `SELECT h_debates.title, h_speeches.speech_order FROM h_speeches JOIN h_debates... WHERE member_id=?` |
| Division page | `SELECT * FROM h_votes JOIN h_members... WHERE division_id=?` |
| Voting record for a member | `SELECT h_divisions.*, h_votes.vote FROM h_votes JOIN h_divisions... WHERE member_id=?` |

This is the same quality of navigation as KoreReference. The hyperlinks work: member names in speech displays resolve to `/member/{id}`; debate titles in the member page link back to `/sitting/{date}/{house}/debate/{uuid}`.

### What this means for KoreRAG multi-database

KoreRAG multi-database support still needs building (the `?db=hansard` routing, named database discovery). But the named databases need not all share the *same* schema — the constraint is that every database must have the standard `chunks` + `chunks_fts` tables. Additional tables in a named database are simply ignored by the KoreRAG service. This is a light requirement: don't break if unknown tables exist.

---

## Implementation: KoreRAG Multi-Database Extension

### What changes in KoreRAG

KoreRAG currently operates against a single `rag.db`. The extension adds **named databases** (following KoreLibrary's catalog pattern):

- Default database: `{data_dir}/rag.db` (unchanged, existing behaviour)
- Named databases: `{data_dir}/databases/{name}.db` (must have `chunks` + `chunks_fts` tables; may have additional tables the service ignores)
- All databases are discovered at startup by scanning the `databases/` folder
- Search can target a specific database (`?db=hansard`) or span all databases
- The browse/edit UI gains a database selector

The schema requirement is deliberately minimal: every named database must have the standard chunk tables. Any extra tables (such as Hansard's `h_*` navigation tables) are invisible to the KoreRAG service.

---

### Database Descriptor Files

Each named database has a companion `{name}.json` file alongside its `.db` file:

```
datacontrol/koredata/RAG/databases/
    hansard.db
    hansard.json
    future_source.db
    future_source.json
```

The descriptor is the **control file for the database**. It tells KoreRAG and the browse UI everything they need to know about that database without hardcoding anything. Adding a new datasource next week means writing a new `.json` and a new ingestor script — nothing in KoreRAG itself changes.

#### What the descriptor controls

| Field | Used by | Purpose |
|-------|---------|---------|
| `display_name` | UI | Human-readable name in the database selector |
| `description` | UI | Shown on the database info panel |
| `managed_by` | UI | `"user"` shows Add/Edit/Delete controls; `"ingestor"` hides them |
| `ingestor` | Scheduler | Script name to call for sync; `null` for user-managed |
| `schedule` | Scheduler | `"daily"` / `"weekly"` / `null` |
| `chunk_types` | UI | Structured add-chunk forms; tag validation hints |
| `navigation.type` | UI | Which browse template to render; `null` = simple list only |
| `navigation.tables` | UI | Which `h_*` tables exist so UI can query them safely |
| `sync` | UI + ingestor | Last run timestamp and status — written back by the ingestor |

#### Example: `hansard.json`

```json
{
  "id": "hansard",
  "display_name": "Hansard — UK Parliamentary Record",
  "description": "Official verbatim record of UK Parliamentary debates, divisions, and member contributions. Sourced from developer.parliament.uk APIs and hansard.parliament.uk debate text.",
  "source_url": "https://hansard.parliament.uk/",
  "licence": "Open Parliament Licence",
  "managed_by": "ingestor",
  "ingestor": "ingest_hansard.py",
  "schedule": "daily",
  "chunk_types": [
    {
      "type": "speech",
      "required_tags": ["house", "member", "date", "type", "debate"],
      "optional_tags": ["party", "constituency", "member_id"]
    },
    {
      "type": "division",
      "required_tags": ["house", "date", "type", "result", "ayes", "noes"]
    },
    {
      "type": "member",
      "required_tags": ["house", "party", "member_id", "status"],
      "optional_tags": ["constituency"]
    }
  ],
  "navigation": {
    "type": "hansard",
    "tables": ["h_sittings", "h_debates", "h_speeches", "h_members", "h_divisions", "h_votes"]
  },
  "sync": {
    "last_run": null,
    "last_date_ingested": null,
    "status": "pending",
    "total_chunks": 0
  }
}
```

#### Example: a plain user-managed database (no navigation, no ingestor)

```json
{
  "id": "research_notes",
  "display_name": "Research Notes",
  "description": "Personal reference chunks added manually.",
  "managed_by": "user",
  "ingestor": null,
  "schedule": null,
  "chunk_types": [],
  "navigation": null,
  "sync": null
}
```

#### Example: a future structured datasource

When a new datasource arrives — say, UK court judgments from The National Archives API — the process is:

1. Create `judgments.json` with `managed_by: "ingestor"`, appropriate `chunk_types`, and a `navigation.type` of `"judgments"` (a new browse template)
2. Write `ingest_judgments.py` which populates chunks + whatever navigation tables the judgments dataset needs
3. Wire into the scheduler via the `schedule` field
4. Implement the `"judgments"` browse template in the UI

KoreRAG itself is untouched. The descriptor file is the contract between the datasource and the system.

#### How KoreRAG uses the descriptor

At startup, KoreRAG scans `databases/` for `.json` files. For each:
- Registers the database with its `display_name` for the UI database selector
- Reads `managed_by` to determine whether to show edit controls for that database
- Reads `chunk_types` to provide tag validation / structured add-chunk forms
- Reads `navigation.type` to know which browse template to load
- Exposes descriptor metadata at `GET /api/databases/{name}/info`

If no `.json` is present for a `.db` file, the database is registered with defaults (`managed_by: "user"`, no navigation, display name = the file stem). This means existing `rag.db` needs no descriptor unless you want to customise it.

---

### Chunk design for Hansard

The chunk schema (id, title, source, tags, content) maps naturally to Hansard content with disciplined use of `tags` in `key:value` format:

**Speech chunks** (one chunk per speech):
```
title:   "NHS Funding — Keir Starmer — 2026-04-29"
source:  "https://hansard.parliament.uk/Commons/2026-04-29/debates/{uuid}/NHSFunding"
tags:    "house:Commons member:Keir Starmer party:Labour constituency:Holborn date:2026-04-29 type:chamber debate:NHS Funding"
content: [speech text, zlib-compressed as normal]
```

**Division chunks** (one chunk per division):
```
title:   "Division: Renters Rights Bill 2nd Reading — 2026-03-15 — Agreed To"
source:  "https://hansard.parliament.uk/Commons/2026-03-15/divisions/{id}"
tags:    "house:Commons date:2026-03-15 result:Agreed ayes:324 noes:212"
content: "Result: Agreed To (Ayes 324, Noes 212)\n\nAyes: Alice Smith, Bob Jones ...\nNoes: ..."
```

**Member profile chunks** (one chunk per member, upserted on sync):
```
title:   "MP: Keir Starmer — Labour — Holborn and St Pancras"
source:  "https://members-api.parliament.uk/api/Members/4514"
tags:    "house:Commons party:Labour constituency:Holborn member_id:4514 status:current"
content: [biography synopsis from Members API]
```

The FTS5 index covers all four fields, so `search?q=NHS+Starmer` finds relevant speeches; `search?q=Renters+Rights+Agreed+To` finds the division record. The agent can request filtered results via tag values.

### Trade-offs accepted

The chunk model handles agent search. The `h_*` navigation tables handle browse UI. Together they cover all use cases — the only real trade-off is that the ingestor must write to both layers, and the browse UI must query them separately (KoreRAG API for search results; direct SQLite for navigation). This is manageable: the browse UI is co-located with KoreDataGateway, which has direct filesystem access to the database files.

---

### Ingestion Script

A standalone ingestion script (`datacontrol/koredata/RAG/ingest_hansard.py` or similar) handles the full pipeline. It writes into a KoreRAG database named `hansard`. It is not part of the KoreRAG service itself — it's a data-population tool that calls the KoreRAG API or writes directly to the database file.

```
Step 1 — Members sync (Members API)
  → GET members-api.parliament.uk/api/Members/Search (paginated, both Houses)
  → Upsert one member-profile chunk per member into the hansard database

Step 2 — Sittings discovery (date range)
  → Iterate dates (configurable: --from / --to)
  → GET hansard.parliament.uk/Commons/{date} and /Lords/{date}
  → Parse debate list: uuid, slug, title, type, item_number
  → Skip debate UUIDs already present in the database

Step 3 — Debate text ingestion
  → For each new debate UUID:
    GET hansard.parliament.uk/debates/GetDebateAsText/{uuid}
  → Split into per-speaker speech blocks (regex on "Member Name (constituency)" pattern)
  → Store one chunk per speech

Step 4 — Divisions sync (Votes APIs)
  → GET commonsvotes-api.parliament.uk/data/Divisions (paginated, by date range)
  → GET lordsvotes-api.parliament.uk equivalent
  → For each division, fetch vote detail; store one division chunk

Step 5 — FTS refresh
  → INSERT INTO chunks_fts(chunks_fts) VALUES('optimize')
```

**Incremental mode**: run daily, targeting yesterday's date. Full historical import configurable via `--from 2015-01-01`.

**Rate limiting**: 1-second delay between requests; `User-Agent` header identifying the application. Parliament's open data licence requires attribution but places no API rate limit.

---

## Alternative Approaches Considered

### A. Dedicated KoreHansard module *(rejected)*

A standalone FastAPI service with a normalised schema (sittings → debates → speeches → members, divisions with vote-level rows). Provides rich relational queryability and proper browse UI.

**Rejected**: Too specific a use case to justify a new general-purpose module. Adds significant surface area (schema, service, UI) for a single dataset. The chunk model is sufficient for the agent's actual query patterns.

### B. KoreReference "Hansard catalog" *(wrong shape)*

Hansard sittings don't map to encyclopaedia articles. KoreReference's schema (articles → links → categories) would require schema forks to accommodate speeches, members, and divisions.

### C. Pure HTML scraping *(unnecessary for structured data)*

The Parliament Developer APIs provide clean JSON for members, votes, bills, and oral questions. HTML parsing is retained only for debate text (where no structured API exists). Scraping JSON-available data from HTML is strictly worse.

### D. Third-party pre-processed datasets *(useful for historical depth)*

TheyWorkForYou and PublicWhip have pre-processed historical Hansard. Useful as a bulk-import source for pre-2010 content where the Parliament API is thin. Worth noting for a later phase.

---

## Relationship to KoreDataGateway

KoreDataGateway is the agent-facing entry point. Because Hansard lives in a named KoreRAG database, KoreDataGateway uses the standard KoreRAG search API with `db=hansard`. No new tool surface is strictly required — but named wrappers make the agent's prompts cleaner:

```python
search_hansard(query, house=None, from_date=None, to_date=None)
  → KoreRAG search against db=hansard, optionally filtering tags for house: and date:

search_divisions(query, from_date=None, to_date=None)
  → KoreRAG search against db=hansard, tags filtered for type:division

get_member(name)
  → KoreRAG search against db=hansard, tags filtered for type:member
```

These give an agent the ability to answer questions like:
- *"What has Keir Starmer said about NHS funding this year?"*
- *"How did the Commons vote on the Renters' Rights Bill?"*
- *"What debates took place on 29 April 2026?"*
- *"Which MPs voted against their party on the free schools motion?"*

---

## Open Questions / Decisions Needed

1. **KoreRAG multi-database design** — The `db` parameter and named-database scanning needs to be designed and implemented in KoreRAG before Hansard ingestion begins. This is the prerequisite piece of work.

2. **Scope of historical import** — How far back to ingest? 2010 (reliable API data)? 2015? Suggest starting from 2015 as a sensible default; the ingestion script takes `--from` / `--to` date args.

3. **Storage estimate** — A decade of speeches is substantial (~50–100 GB uncompressed). KoreRAG already zlib-compresses chunk content, which should bring this down to a manageable size.

4. **Scheduler integration** — KoreAgent already has a task queue. The daily Hansard sync should plug into that as a scheduled task rather than running its own cron/timer.

5. **Member disambiguation** — Speaker name matching between debate text and the Members API is imperfect (titles, name changes, Lords vs MPs). Need a fuzzy-match pass during ingestion; unmatched names are stored without a `member_id:` tag and can be corrected manually.

6. **Bill and Committee data** — The Bills API and Committees API are rich but add ingestion complexity. Defer to a later phase; add as additional chunk types in the same database.

7. **Tag convention** — The `key:value` tag format assumed here (`house:Commons`, `date:2026-04-29`, `member:Keir Starmer`, `type:speech`) needs to be documented as the Hansard ingestion convention. KoreRAG's general-purpose tags remain freeform; only the Hansard ingestor enforces structure.

---

## Recommended Next Steps

1. **Phase 0 — KoreRAG multi-database**: Add named-database support to KoreRAG (`databases/{name}.db`, `?db=` query parameter on search/list, database selector in UI). Requirement: named databases may contain extra tables; KoreRAG service must tolerate unknown tables gracefully.

2. **Phase 1 — Proof of concept ingestion**: Write a standalone Python script that fetches one sitting day, downloads debate text for each debate UUID, parses into speeches, and writes both chunk rows and `h_*` nav rows into a `hansard.db`. Validate speech parsing quality and member name resolution rate.

3. **Phase 2 — Full ingestion pipeline**: Members API sync, Divisions API sync, configurable date range, incremental mode with sync log. Wire daily increment into KoreAgent scheduler.

4. **Phase 3 — Browse UI**: Add a Hansard browse view to KoreDataGateway: date calendar → sitting → debate → speech sequence with clickable member names. Member pages with contribution history and voting record. Division pages with full voter matrix. All navigation backed by `h_*` tables; full-text search backed by KoreRAG chunk API.

5. **Phase 4 — KoreDataGateway agent tools**: Implement the named wrappers (`search_hansard`, `search_divisions`, `get_member`) as agent-callable tools backed by KoreRAG search.

---

*Licensed source data: [Open Parliament Licence](https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/)*
