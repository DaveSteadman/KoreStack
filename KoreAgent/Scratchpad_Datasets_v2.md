# Scratchpad Datasets v2 - Design Note

## Purpose

Give KoreAgent a reliable "production line" for record-shaped data: fetch a collection, keep it
between prompts, refine it across several turns, and eventually produce an output from the retained
set.

The target workflow remains:

1. Pull KoreFeed entries on a topic.
2. Remove obvious junk and duplicates.
3. Apply one or more judgement passes to keep only the best items.
4. Produce an article or report from the final retained set.

The important constraint is that the intermediate sets must survive between prompts and between
sessions.

This v2 note narrows the design further than the first Scratchpad Datasets draft. The goal is not a
general workflow engine. The goal is persistent structured working sets.

---

## What problem this solves

The current scratchpad is good at string-shaped state:

- facts
- short summaries
- draft text
- handles and notes
- large string spillover under `_tc_*`

It is not good at iterative work over record collections.

For the KoreFeed -> filtered set -> article workflow, the actual gaps are:

1. Tool results that are naturally `list[dict]` get flattened into strings.
2. Auto-saved working sets do not survive across sessions unless manually promoted.
3. The agent has no first-class concept for "the current retained set of records".
4. The prompt does not naturally surface those retained sets every turn.

This design addresses those four gaps and no more.

---

## Core idea

Add a second scratchpad content type: **dataset**.

A dataset is a named, persistent, ordered collection of records with minimal metadata and a short
history of transformations.

Datasets are not a new memory tier. They are part of scratchpad.

Scratchpad now holds two kinds of entry:

- string entries: existing facts, drafts, summaries, handles
- dataset entries: structured collections that the agent refines iteratively

This keeps the agent's mental model simple:

- use string keys for text state
- use dataset names for record state

---

## Design corrections from v1

This version applies five corrections.

### 1. Identity is not the same as name

In v1, the dataset name effectively acted as both identity and stage label. That is too fragile.
A rename should not change what the dataset is.

Each dataset therefore has:

- `dataset_id` - immutable internal identifier
- `name` - mutable user-facing label, unique within the session

The user and the agent usually operate by name. The store operates by `dataset_id`.

### 2. Forking is the default

In v1, `save_as=None` meant in-place mutation. That is unsafe for iterative work because it destroys
an earlier stage by default.

In v2:

- transformation tools fork by default
- in-place replacement requires `replace=True`
- every fork records `parent_dataset_id`

The safe default is: preserve the previous stage unless the caller explicitly says otherwise.

### 3. Phase 1 only includes the working-set core

The first draft bundled storage, refinement, mapping, reduction, delegate visibility, and future
archival concerns into one surface.

Phase 1 is now only:

- persist datasets
- list them
- inspect them
- retrieve slices
- delete them
- apply cheap deterministic filters
- apply one LLM-driven record filter pass

Everything else moves out of phase 1.

### 4. LLM operations need field projection

A filter pass should not automatically send full article bodies to the isolated LLM call. Most of the
time the first pass should evaluate only a compact view of each item.

Every LLM-driven dataset operation therefore accepts a projection:

- `fields=[...]`
- optional `excerpt_chars=...`

Default projection for feed-like results should be:

- `title`
- `source`
- `published_at`
- `url`
- a short snippet or excerpt

Full bodies are used only when the agent explicitly asks for them.

### 5. Persistence failure and cleanup must be explicit

The v1 note did not define what happens when a handle exists in scratchpad but the spillover row is
missing, or when a conversation is deleted.

V2 makes that explicit:

- missing spillover row -> return a clear error and suggest re-fetch or dataset deletion
- conversation deletion -> remove all spillover rows for that session
- orphan row cleanup -> background cleanup removes rows whose session handle no longer exists

---

## Data model

One logical dataset record:

| Field               | Type            | Notes                                            |
|---------------------|-----------------|--------------------------------------------------|
| `dataset_id`        | str             | immutable internal id, e.g. `ds_20260527_001`    |
| `session_id`        | str             | session/conversation scope                       |
| `name`              | str             | mutable, unique within session                   |
| `records`           | list[dict]      | structured data                                 |
| `schema`            | list[str]\|None | observed field names                            |
| `source_tool`       | str\|None       | e.g. `koredata_search`                           |
| `source_args`       | dict\|None      | original tool args                               |
| `parent_dataset_id` | str\|None       | set when forked                                  |
| `history`           | list[dict]      | small append-only transformation log             |
| `created_at`        | iso8601         |                                                  |
| `updated_at`        | iso8601         |                                                  |

History stays compact:

```jsonc
{
  "op": "filter",
  "prompt": "Keep items genuinely about topic X",
  "fields": ["title", "source", "published_at", "url", "snippet"],
  "kept": 41,
  "dropped": 26,
  "replaced": false,
  "at": "2026-05-27T08:42:11Z"
}
```

This is enough to explain how one stage became the next. It is not intended as a full provenance
system.

---

## Storage model

Datasets must persist across sessions without bloating KoreChat's `conversations.scratchpad` JSON
column.

Use transparent inline-or-spillover storage.

### Inline datasets

If a dataset serializes below a threshold such as ~50KB, store it directly in the dedicated
conversation `datasets` payload.

Example shape:

```json
{
  "feed_items_shortlist": {
    "dataset_id": "ds_20260527_001",
    "inline": true,
    "count": 12,
    "schema": ["title", "url", "source", "published_at", "snippet"],
    "history_tail": [ ... ],
    "records": [ ... ]
  }
}
```

This rides the dedicated KoreChat datasets persistence path.

### Spillover datasets

If the payload exceeds the threshold, store only a compact handle manifest in `datasets` and place
the full content in a local SQLite store at `datacontrol/koreagent/datasets.db`.

Datasets handle shape:

```json
{
  "feed_items_raw": {
    "dataset_id": "ds_20260527_002",
    "inline": false,
    "count": 80,
    "schema": ["title", "url", "source", "published_at", "body"],
    "size": 380000,
    "history_tail": [ ... ]
  }
}
```

SQLite row stores the canonical payload for spillover datasets.

A dataset that spills over stays in SQLite until explicitly compacted or deleted. Do not thrash it
back and forth on every refinement.

### SQLite schema

One table is still enough for phase 1:

```sql
CREATE TABLE datasets (
    dataset_id    TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    name          TEXT NOT NULL,
    records_json  TEXT NOT NULL,
    meta_json     TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE(session_id, name)
);
```

`meta_json` contains:

- schema
- source_tool
- source_args
- parent_dataset_id
- history

This follows the established local SQLite pattern already used elsewhere in KoreStack:

- per-call connection helper
- WAL mode
- foreign keys on
- schema as a module constant

---

## Prompt visibility

Persistence alone is not enough. The agent must be reminded every turn that datasets exist.

The prompt builder should expose a compact dataset manifest block alongside the existing scratchpad
summary.

Per visible dataset, show:

- `name`
- `count`
- `schema` summary
- `source_tool`
- `updated_at`
- last history entry

Example:

```text
Datasets:
- feed_items_raw        80 records  source=koredata_search  updated=2026-05-27T08:30Z
  last: save from koredata_search(query="X", domain="feeds")
- feed_items_relevant   41 records  source=feed_items_raw   updated=2026-05-27T08:42Z
  last: filter fields=[title,source,published_at,url,snippet]
```

Without this block, the agent will forget the datasets and the design will fail even if storage is
correct.

---

## Tool surface

Phase 1 tool surface is deliberately smaller than v1.

```
dataset_save(name, records, source_tool=None, source_args=None, replace=False)
dataset_rename(name, new_name)
dataset_list()
dataset_inspect(name)
dataset_get(name, indices=None, max_records=None, fields=None)
dataset_delete(name)
dataset_drop_where(name, predicate, save_as=None, replace=False)
dataset_filter(name, prompt, save_as=None, replace=False, fields=None, excerpt_chars=None)
```

### Tool semantics

- `dataset_save`
  Creates a named dataset from structured records. `replace=False` by default.

- `dataset_rename`
  Changes only the user-facing `name`. `dataset_id` stays fixed.

- `dataset_list`
  Returns a compact manifest list. This is the cheap discovery path.

- `dataset_inspect`
  Returns metadata, schema, sample records, and recent history. It should not dump full large bodies
  unless asked.

- `dataset_get`
  Returns specific records or a bounded slice, optionally projecting selected fields.

- `dataset_delete`
  Removes inline data, spillover data, and scratchpad handle.

- `dataset_drop_where`
  Applies deterministic filters without invoking the LLM. Defaults to forking if `save_as` is not
  supplied and `replace=False`.

- `dataset_filter`
  Applies one LLM-driven keep/drop pass over projected record views. Defaults to forking if
  `replace=False`.

### Deterministic predicate DSL

Support only a small initial DSL:

- `duplicate by <field>`
- `missing field <field>`
- `<field> ~= <regex>`
- `<field> < <value>`
- `<field> > <value>`

This keeps obvious cleanup cheap and testable.

### LLM filter behavior

`dataset_filter` should operate over projected records, not raw full records.

Default behavior:

- use `fields` when supplied
- otherwise infer a compact default projection from the schema
- include at most `excerpt_chars` from large text fields
- run in batches if needed, but that batching is an implementation detail

The output is a new dataset unless `replace=True` is explicitly requested.

---

## Auto-routing in `tool_loop.py`

The auto-router is still the right insertion point.

When a tool result returns:

1. If it is a list of dicts of meaningful size, auto-save as a dataset.
2. If it is a large string, keep the existing `_tc_*` scratchpad spillover behavior.
3. Otherwise inject inline as today.

Suggested record-shaped heuristic:

- payload is `list[dict]`
- length >= 5
- records share a broadly consistent key set
- at least one identity or source-like field exists, such as `id`, `url`, `guid`, `slug`, or `title`

Injected thread response should be a manifest, not the full payload.

Example:

```text
Dataset 'koredata_search_1' created: 80 records, fields=[title,url,source,published_at,body]
```

This removes the need for the model to decide where a routine KoreData search result should live.

---

## Walkthrough: KoreFeed -> filtered set -> article

**Turn 1**

User asks for feed entries on topic X.

- Agent calls `koredata_search(domain="feeds", query="X", since="2026-05-20")`
- Auto-router saves `koredata_search_1`
- Agent renames it to `feed_items_raw`

**Turn 2**

User asks to remove duplicates and off-topic items.

- Agent calls `dataset_drop_where("feed_items_raw", "duplicate by url", save_as="feed_items_deduped")`
- Agent calls `dataset_filter(
    "feed_items_deduped",
    prompt="Keep items genuinely about topic X. Drop tangential, promotional, or off-topic items.",
    save_as="feed_items_relevant",
    fields=["title", "source", "published_at", "url", "snippet"],
    excerpt_chars=300
  )`

Now both prior stages still exist.

**Turn 3**

Next day, user asks to keep only strong sources.

- Prompt builder shows existing datasets in the compact manifest block
- Agent calls `dataset_filter(
    "feed_items_relevant",
    prompt="Keep only items from primary publishers or original reporting.",
    save_as="feed_items_strong",
    fields=["title", "source", "published_at", "url", "snippet"],
    excerpt_chars=300
  )`

**Turn 4**

User asks for the article.

- Agent inspects or gets the retained records from `feed_items_strong`
- Agent writes the article using normal generation
- Agent saves the draft with `scratchpad_save("article_draft_v1", article)`

The storage feature ends once the structured retained set is stable. Final article generation is not a
special dataset primitive in phase 1.

---

## Persistence, failure handling, and cleanup

### Normal persistence

- inline datasets persist with the conversation in named scratchpad state
- spillover datasets persist in `datasets.db`
- both survive process restart

### Missing spillover row

If scratchpad contains a dataset handle but the corresponding row is missing from `datasets.db`:

- `dataset_inspect`, `dataset_get`, or `dataset_filter` should return a clear error
- the error should identify the dataset name and `dataset_id`
- the error should suggest either re-fetching the source data or deleting the broken handle

Example:

```text
Dataset 'feed_items_raw' refers to missing spillover row ds_20260527_002.
Re-fetch the source data or delete this dataset handle.
```

Do not silently fall back to empty results.

### Conversation deletion

When a conversation/session is deleted, remove all spillover rows for that `session_id`.

### Orphan cleanup

A periodic cleanup pass can remove spillover rows whose `session_id` no longer exists or whose dataset
handle is no longer present in persisted scratchpad state.

This should be conservative. Cleanup should prefer stale accumulation over accidental data loss.

### Auto-named datasets

Auto-named datasets created by the router and never renamed are transient candidates for pruning.
Named datasets created or renamed by the agent are durable until explicit deletion or conversation
removal.

---

## What phase 1 intentionally does not do

Still out of scope:

- run ids or workflow graphs
- alias tables
- per-record decision tables
- map/reduce style dataset transforms
- dedicated article-writing tools
- delegate visibility extensions
- promotion to KoreRAG
- FTS5 over dataset content
- embeddings or vector search
- separate MCP surface

Those can be added later if real usage demands them.

---

## Implementation outline

Phase 1, single focused implementation slice:

1. `KoreAgent/app/datasets.py`
   In-memory dataset model, naming, history append, projection helpers.

2. `KoreAgent/app/datasets_store.py`
   SQLite spillover store with one table and session-scoped operations.

3. KoreChat conversation persistence
  Store scratchpad and datasets in separate fields so the transport model matches the runtime
  model.

4. `KoreAgent/app/prompt_builder.py`
   Add a compact dataset manifest block to each turn.

5. `KoreAgent/app/tool_loop.py`
   Add the record-shaped auto-router branch next to existing `scratchpad_auto_save` behavior.

6. `KoreAgent/app/system_skills/Datasets/`
   Add `skill.md` and `datasets_skill.py` with the reduced phase 1 surface.

7. Tests
   Cover auto-routing, rename preserving `dataset_id`, fork-vs-replace semantics, field projection,
   missing spillover error, and cross-session restoration through the persisted scratchpad path.

Phase 2, only after phase 1 proves useful:

- batched filter optimization for very large sets
- richer deterministic predicates
- delegate visibility support
- archival/promotion path if a real need emerges

---

## Decisions summary

- **What this is.** A scratchpad extension for persistent structured working sets.
- **Identity model.** Immutable `dataset_id`, mutable session-unique `name`.
- **Persistence.** Inline under a threshold, spillover above it, with scratchpad manifest handles.
- **Mutation model.** Fork by default, replace only when explicitly requested.
- **Prompt model.** Always surface compact dataset manifests each turn.
- **Phase 1 tools.** Save, rename, list, inspect, get, delete, deterministic drop, LLM filter.
- **Out of scope.** Map/reduce transforms, delegate extensions, KoreRAG promotion, workflow graphs.

---

## Bottom line

The KoreFeed -> filter -> article workflow needs one thing above all else: a persistent structured
retained set that the agent can see, refine, and resume across prompts.

Scratchpad datasets can provide that if the design stays disciplined:

- identity separate from naming
- fork by default
- projected LLM filtering instead of raw full-record filtering
- compact manifest visibility in the prompt every turn
- explicit failure handling for spillover persistence

That is enough to materially improve workflow without adding a new memory subsystem or drifting back
into the overbuilt KoreRAM design.