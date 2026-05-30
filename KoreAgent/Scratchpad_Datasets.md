# Scratchpad Datasets - Design Note

## Purpose

Give KoreAgent a "production line" for record-shaped data: pull a collection from a tool, filter it
across several prompts, then derive an output. The intermediate sets must survive between prompts and
between sessions.

This note replaces the earlier KoreRAM proposal. The KoreRAM design tried to build a generic
workflow engine; the real requirement is narrower and fits as an extension to the existing
scratchpad rather than as a new subsystem.

The motivating workflow:

1. Pull KoreFeed entries on a topic (~100 records, hundreds of KB).
2. Drop duplicates and off-topic items.
3. Keep only items with strong sources.
4. Write an article using the retained items as evidence.

Each step happens in a separate prompt, possibly across sessions.

---

## Why scratchpad alone is not enough today

Scratchpad ([KoreAgent/app/scratchpad.py](app/scratchpad.py)) already provides:

- session-scoped key/value storage
- auto-save of large tool results under `_tc_*` keys via `scratch_auto_save` in
  [tool_loop.py](app/tool_loop.py)
- isolated LLM calls over stored content via `scratch_query`
- token substitution via `{scratch:key}`
- KoreChat persistence of named (non-auto) keys through `_kc_patch` writing the
  `conversations.scratchpad` JSON column (see [KoreChat/app/database.py](../KoreChat/app/database.py))

For the production-line workflow, three gaps remain:

1. **Records get flattened to strings.** A list of 80 feed items is concatenated to text by the
   auto-saver. Per-record dedupe and filtering become text-mashing instead of structured operations.
2. **Auto-keys do not survive.** `_tc_*` is in-process only. If the user reopens the chat tomorrow,
   the working set is gone unless it was promoted to a named key.
3. **No vocabulary for "the current filtered set".** The agent has to invent key names per turn and
   remember which one is current. The user thinks in stages; the system has no matching shape.

These three gaps are the entire scope of this design.

---

## Concept: datasets as a second scratchpad namespace

A **dataset** is a named, ordered list of records. Each record is a dict with arbitrary fields. A
dataset carries a small embedded history of operations applied to it.

Datasets live next to string scratchpad keys, in the same module, and share the same skill, the same
session/conversation scoping, and the same KoreChat persistence path. They are not a separate tier.

There are now two kinds of scratchpad entry:

- string entries (existing) - facts, drafts, summaries, handles, page bundles
- dataset entries (new) - record-shaped collections that get transformed iteratively

The agent learns one new content type, not a new subsystem.

---

## Data model

One logical record per dataset:

| Field          | Type            | Notes                                                   |
|----------------|-----------------|---------------------------------------------------------|
| `name`         | str             | unique within session                                   |
| `session_id`   | str             | session/conversation scope                              |
| `records`      | list[dict]      | the structured data                                     |
| `schema`       | list[str]\|None | observed field names                                    |
| `source_tool`  | str\|None       | e.g. `koredata_search`                                  |
| `source_args`  | dict\|None      | original tool args, for replay                          |
| `parent_name`  | str\|None       | set when forked via `save_as=`                          |
| `history`      | list[dict]      | append-only log of operations (see below)               |
| `created_at`   | iso8601         |                                                         |
| `updated_at`   | iso8601         |                                                         |

History entries are intentionally small:

```jsonc
{
  "op":      "filter",            // save | rename | drop_where | filter | map | reduce
  "prompt":  "Keep items about X",// truncated; null for programmatic ops
  "kept":    41,
  "dropped": 26,
  "reason":  null,                // optional one-line summary
  "at":      "2026-05-27T08:42:11Z"
}
```

This is the entire audit trail. No separate decisions table, no link table, no run table.

---

## Storage layering

Datasets must persist across sessions without bloating KoreChat's named scratchpad payload or
mixing two different state models into one conversation field.

Two-tier storage, transparent to the agent:

- **Inline.** Datasets whose serialized JSON is under ~50KB are stored inside the dedicated
  `conversations.datasets` JSON field.

- **Spillover.** Larger datasets live in a single local SQLite file at
  `datacontrol/koreagent/datasets.db`. The `datasets` field holds only a handle manifest:

  ```json
  { "feed_items_raw": { "__handle": "ds:42", "count": 80, "size": 380000 } }
  ```

The promotion threshold is checked on every write. A dataset that shrinks back below the threshold
through filtering stays in spillover until the session ends; this avoids thrashing between tiers.

SQLite layer follows the established repo pattern from
[KoreChat/app/database.py](../KoreChat/app/database.py):

- per-call `_conn()` contextmanager
- `PRAGMA journal_mode=WAL`
- `PRAGMA foreign_keys=ON`
- schema declared as a module constant

One table is enough:

```sql
CREATE TABLE datasets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    records_json  TEXT    NOT NULL,
    meta_json     TEXT    NOT NULL,   -- schema, source_tool, source_args, parent_name, history
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    UNIQUE(session_id, name)
);
```

A small index on `(session_id, name)` is implicit via the unique constraint. No FTS5 in Phase 1.

---

## Tool surface

Narrow, verb-first, ten tools. The agent should rarely need anything outside this set.

```
dataset_save(name, records, source_tool=None, source_args=None, replace=False)
dataset_rename(old_name, new_name)
dataset_drop_where(name, predicate)        # programmatic: "duplicate by url",
                                           #   "missing field body", "regex source ~= aggregator"
dataset_filter(name, prompt, save_as=None) # LLM per-record keep/drop with reason recorded
dataset_map(name, prompt, save_as)         # produce one output per record (e.g. summarise body)
dataset_reduce(name, prompt)               # produce one output from the whole set (e.g. the article)
dataset_inspect(name)                      # count, schema, sample, recent history
dataset_get(name, indices=None, max_records=None, fields=None)
dataset_list()
dataset_delete(name)
```

Notes on each:

- `dataset_filter` calls the LLM once per record or in small batches against the prompt. It records
  `kept`, `dropped`, and an optional one-line reason in the dataset's `history`. `save_as=None`
  refines in place and appends history; `save_as="..."` forks a new dataset with `parent_name` set.
- `dataset_map` always requires `save_as` because it changes record shape (one input -> one output).
  The output dataset's records are `{"input_id": ..., "output": ...}` unless the prompt produces
  structured fields.
- `dataset_reduce` returns a string. The agent typically `scratch_save`s it under a named string key.
- `dataset_drop_where` accepts a small DSL for programmatic conditions so the LLM does not have to
  loop over records for trivial filters. Supported initially:
  - `"duplicate by <field>"`
  - `"missing field <field>"`
  - `"<field> ~= <regex>"`
  - `"<field> < <value>"`, `"<field> > <value>"` for ISO dates and numbers
- `dataset_inspect` returns a structured manifest, not raw records. This is the cheap default
  inspection path.
- All operations are session-scoped. Cross-session access requires the same conversation.

All LLM-driven operations (`dataset_filter`, `dataset_map`, `dataset_reduce`) reuse the same
isolated-LLM-call surface as `scratch_query`. No new infrastructure.

---

## Auto-routing in `tool_loop.py`

Today, [tool_loop.py](app/tool_loop.py) auto-saves large tool results via `scratch_auto_save` under
`_tc_*` keys. Extend that classifier with one branch:

1. If the tool result parses as a list of dicts of length >= 5 with at least two stable id-like
   fields (any of `id`, `url`, `guid`, `slug`, `title`, `published_at`) -> call
   `dataset_save(auto_name, records, source_tool=tool_name, source_args=tool_args)` and inject a
   manifest into the thread:

   ```
   Dataset 'koredata_search_3' created: 80 records,
     fields=[title,url,published_at,source,body], size 380KB.
   ```

2. Else if the result is a long string -> existing `_tc_*` behavior.

3. Else -> inject the result inline as today.

Auto-names use a stable scheme: `<source_tool>_<seq>` per session. The agent renames immediately to
something meaningful with `dataset_rename`.

This is the single point at which the destination decision is made. The LLM does not have to choose
between string scratchpad and datasets for routine tool results, which removes the largest behavioral
risk from the earlier KoreRAM design.

---

## Skill guidance for the agent

Two sentences in the system prompt are enough:

- Use datasets for collections where each item has structure. Use scratchpad string keys for facts,
  drafts, handles, and summaries.
- Refer to datasets by name. To fork a working set, pass `save_as`; to refine in place, omit it.

A short Skill block lists the ten verbs and the auto-router behavior. No "memory tier" vocabulary,
no aliases, no runs.

---

## Walkthrough: KoreFeed -> filtered set -> article

**Turn 1.** User: "Pull last week's feed entries on topic X."

- Agent calls `koredata_search(domain="feeds", query="X", since="2026-05-20")`.
- MCP returns 80 records totalling ~380KB.
- Auto-router in `tool_loop.py` classifies as records -> `dataset_save("koredata_search_1", ...)`.
- Thread sees: `Dataset 'koredata_search_1' created: 80 records, ...`
- Agent calls `dataset_rename("koredata_search_1", "feed_items_raw")` and replies.

**Turn 2.** User: "Drop duplicates and anything off-topic."

- Agent calls `dataset_drop_where("feed_items_raw", "duplicate by url")` -> 80 -> 67.
- Agent calls `dataset_filter("feed_items_raw",
    prompt="Keep items genuinely about topic X. Drop tangential, off-topic, or promotional items.",
    save_as="feed_items_relevant")` -> 67 -> 41.
- `feed_items_relevant.history` now contains both ops with kept/dropped counts.

**Turn 3.** New session, next day. User: "Continue. Keep only items with strong sources."

- Agent calls `dataset_list()` -> sees `feed_items_raw`, `feed_items_relevant` persisted.
- Agent calls `dataset_filter("feed_items_relevant",
    prompt="Keep only items whose source is a primary publisher or original reporting; drop aggregators and opinion blogs.",
    save_as="feed_items_strong")` -> 41 -> 18.

**Turn 4.** User: "Write a 600-word article using those."

- Agent calls `dataset_reduce("feed_items_strong",
    prompt="Write a 600-word article on topic X using only these items as evidence. Cite by url.")`
  -> returns article string.
- Agent calls `scratch_save("article_draft_v1", article)` and replies to the user.

Each named dataset persists across sessions through the existing KoreChat path. The agent navigates
by names that match how the user describes the work. There is no separate workflow engine.

---

## Persistence and lifecycle

- **Inline datasets** persist with the conversation, identical to named string keys.
- **Spillover datasets** persist in `datasets.db`, keyed by `(session_id, name)`. They survive
  process restarts.
- **Auto-named datasets** (those created by the auto-router and never renamed) are treated as
  transient: pruned after `MAX_AUTO_KEYS` is reached or after a configurable idle period, using the
  same mechanics as `_tc_*` cleanup.
- **Explicit deletion** via `dataset_delete(name)` removes both the spillover row and the inline
  handle from `conversations.scratchpad`.
- No retention classes, no TTLs, no pinning. If those become necessary, add them later; do not
  pre-build them.

---

## Relationship to delegates

The existing delegate runner supports `scratchpad_visible_keys`. Extend it with
`datasets_visible` (list of dataset names). A delegate receives:

- string scratchpad keys it is allowed to see
- dataset handles it is allowed to see (resolved through the same `dataset_*` tools)

The delegate writes its outputs to either namespace. The parent inspects the result.

No new delegation concept is needed.

---

## What this design intentionally does not do

To stay focused, the following are out of scope:

- runs, workflow ids, or stage graphs (the dataset name *is* the stage)
- alias tables (the name is the alias)
- per-record decision tables (`history` covers it)
- typed artifact links beyond `parent_name` (forks form a tree, not a graph)
- retention classes
- FTS5 over datasets (records are queryable by field; full-text comes later if needed)
- a separate MCP server (no second consumer exists today)
- promotion to KoreRAG (different lifecycle; can be added as a one-off skill if needed)
- embeddings or vector search

If any of these turn out to be necessary, they become incremental additions to a working system
rather than upfront commitments.

---

## Implementation outline

Phase 1, single PR:

1. `KoreAgent/app/datasets.py` - in-memory store, JSON serialization, history append, parent links.
2. `KoreAgent/app/datasets_store.py` - SQLite spillover (one table, WAL, per-call `_conn`).
3. Persist dataset manifests through the dedicated KoreChat `datasets` field so scratchpad and
  datasets remain distinct in storage, API payloads, and the inspector UI.
4. Classifier branch in `KoreAgent/app/tool_loop.py` next to `scratch_auto_save`.
5. New skill at `KoreAgent/app/system_skills/Datasets/` with `skill.md` and `datasets_skill.py`,
   following the existing `Scratchpad` skill layout.
6. Two-sentence addition to the system prompt; skill block listing the verbs and auto-router.
7. Regression tests covering: auto-route on `koredata_search`-shaped results, inline/spillover
   threshold, fork via `save_as`, `dataset_drop_where` DSL, cross-session persistence through the
   KoreChat path.

Phase 2 (only if needed):

- field-aware retrieval (`dataset_get(name, where=...)`)
- batched `dataset_filter` for very large sets
- promotion to KoreRAG for archival

Phase 3 is unspecified by design.

---

## Decisions summary

- **Where it lives.** `KoreAgent/app/datasets.py` + `datasets_store.py`, new skill at
  `system_skills/Datasets/`, spillover DB at `datacontrol/koreagent/datasets.db`.
- **Storage model.** Named list of records with embedded history. One SQLite table. Inline under
  ~50KB, spillover above.
- **Persistence.** Rides the existing KoreChat `conversations.scratchpad` path for handles; spillover
  rows persist in their own SQLite file. No KoreChat changes required.
- **Ingestion.** Auto-route in `tool_loop.py` based on result shape. No `save_to=` parameters on
  remote MCP tools.
- **Agent surface.** Ten narrow verbs. LLM operations reuse the `scratch_query` isolated-call path.
- **Navigation.** Names. The dataset name *is* the stage and the alias.
- **Out of scope.** Runs, alias tables, link tables, decision tables, retention classes, FTS,
  embeddings, MCP layer, KoreRAG promotion.

---

## Bottom line

The KoreFeed -> filter -> article workflow does not need a memory subsystem. It needs scratchpad to
understand record-shaped collections, persist them across sessions, and offer a small set of
transformation verbs.

Building that as an extension to scratchpad delivers the production-line workflow without adding a
new tier the agent has to choose between, without a workflow-engine schema, and without committing
to MCP or cross-app sharing before there is a concrete second consumer.

If this turns out to be too small for some future workflow, the upgrade path is incremental. If it
turns out to be enough, the system stays simple.
