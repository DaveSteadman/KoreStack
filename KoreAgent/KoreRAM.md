# KoreRAM - Working Memory Design Note

## Purpose

KoreAgent already has a useful scratchpad: a session-scoped key/value store that lets the agent park
tool outputs outside the active prompt and retrieve them later. That is enough for small and medium
intermediate artifacts.

KoreRAM is the next step up. The goal is to give the agent an intuitive way to pick up, park,
transform, and retrieve much larger working sets, potentially many megabytes of text, without
forcing those bytes back through the main context window.

This note is intentionally about working memory, not long-term knowledge storage. The target use case
is multi-step active problem solving:

- fetch or generate a large body of text
- store it under a stable handle
- derive smaller views from it
- hand those views to later steps or delegate runs
- discard or expire the large body when the task ends

---

## Current state

The existing scratchpad already provides several important KoreRAM-like behaviors:

- session-scoped storage keyed by short names (`KoreAgent/app/scratchpad.py`,
  `_SESSION_STORES: dict[str, dict[str, str]]`)
- auto-save of large tool outputs via `tool_loop.py` calling `scratch_save` when content
  exceeds a threshold, with generated keys in the `_tc_` namespace
- separate auto-key namespaces for related concerns: `_tc_*` (tool spillover),
  `_cx_*` (context compaction), `research_page_*` (page fetches), bounded by `MAX_AUTO_KEYS=40`
- prompt-visible key listing instead of full content injection
- isolated `scratch_query(...)` calls so the raw stored content does not re-enter the caller context
- token substitution via `{scratch:key}` for passing stored content into tools when needed
- named (non-auto) scratchpad keys are persisted to KoreChat via
  `_kc_patch("/conversations/{id}", {"scratchpad": named_scratch})`, stored in the
  `conversations.scratchpad` TEXT column; auto-keys are NOT persisted

That is a strong base, but it also has hard limits:

- in-process storage with KoreChat snapshot-only persistence for named keys
- values are plain strings with no metadata beyond key name
- there is no chunk-level indexing or structured retrieval path
- retrieval semantics are still framed as "load this string" or "ask one isolated question"
- eviction is simple and targeted at transient auto-keys, not large durable working sets
- the mental model is still "scratchpad note", not "working dataset"

So the right question is not "replace scratchpad?". The right question is "what layer should sit above
or beside scratchpad when the data volume and workflow become much larger?"

### Adjacent components that already exist

KoreRAM does not land in an empty repo. Three existing pieces matter for the design:

- **KoreRAG** (`KoreData/KoreRAG/`) is already a chunked SQLite + FTS5 store, served over HTTP and
  exposed to the agent via the KoreDataGateway MCP server as one of the `koredata_search` domains.
  It is the long-term reference corpus. KoreRAM should not reinvent chunked-document storage;
  long-text artifacts that earn durability should be promoted into KoreRAG, not kept in KoreRAM forever.
- **KoreDataGateway** (`KoreData/KoreDataGateway/`) federates KoreFeed, KoreReference, KoreLibrary,
  KoreRAG, and KoreGraph behind one MCP tool: `koredata_search(query, domains=...)`. The agent does
  not own that tool signature. Anything that looks like "add a `save_to` parameter to KoreData
  tools" is a cross-repo change to the gateway, not a KoreAgent-local change.
- **KoreChat** (`KoreChat/app/database.py`) already establishes the repo-standard SQLite pattern:
  per-call connection through a contextmanager, WAL mode, foreign keys on, schema declared as a
  module constant. KoreRAM must follow the same pattern, not invent a new one.

These three constraints, taken together, point toward a KoreRAM that is local to KoreAgent, focused
on transient workflow artifacts, and integrated with the existing auto-save path in `tool_loop.py`
rather than with new tool parameters on remote MCP services.

---

## Core model

Use a two-tier model.

### Tier 1: Scratchpad

Keep the current scratchpad for:

- short named facts
- compact intermediate outputs
- auto-saved tool spillover
- prompt-friendly pointers the model can see every turn

### Tier 2: KoreRAM

Add KoreRAM as a larger working-memory store for:

- multi-megabyte raw text
- chunked datasets
- derived extracts and summaries
- manifests describing what is stored
- retrieval operations that return only the slice needed for the next step

In practice, scratchpad should hold references to KoreRAM objects, not the large objects themselves.
That makes the prompt-visible working set small while the real payload lives elsewhere.

Example:

- scratchpad key `budget_report_ram` -> `ram:doc_20260527_001`
- KoreRAM object `doc_20260527_001` -> the actual large text, chunk map, metadata, and derived views

This keeps the current agent behavior intact while making larger workflows possible.

---

## Implementation shape

The simplest effective answer is:

- a local SQLite-backed store
- exposed first as native KoreAgent skills
- optionally wrapped later behind an MCP server if other apps need the same memory surface

### Why SQLite is the right first move

Yes, another SQLite database is the correct default.

Reasons:

- the repo already uses SQLite patterns comfortably
- it is local, simple, portable, and debuggable
- large text blobs plus metadata tables are a good fit
- chunk and manifest tables are straightforward
- FTS5 can support text search if needed
- transactions give safe multi-step writes
- no additional daemon is required for the first version

SQLite is enough for a local working-memory system until at least one of these becomes true:

- multiple independent processes need concurrent high-volume writes
- remote clients must share the same memory service
- retrieval needs move from exact/FTS/chunk lookup into a more advanced distributed store

That is not where KoreAgent is today.

---

## Delivery model

### Recommended sequence

1. Start as an internal KoreAgent subsystem plus a new `KoreRAM` skill.
2. Make the skill names and prompt guidance explicit enough that the agent naturally uses it.
3. Add an MCP wrapper only if KoreChat, KoreCode, KoreDocs, or external tools need the same storage API.

### Why not start with MCP

MCP is useful when the memory surface must be shared across process boundaries or products. But for the
first implementation it adds protocol overhead, tool-registration work, and another abstraction layer
before the behavior is proven.

The real problem here is not transport. It is agent ergonomics.

If the agent does not clearly understand when to use the store, an MCP server will not fix that.
If the behavior works well internally first, wrapping it for MCP later is mechanical.

### Concrete recommendation

- Phase 1: local module + skill
- Phase 2: optional MCP facade reusing the same SQLite store and service functions

---

## Agent interaction model

The hard part is not storage. The hard part is making use obvious and intuitive.

The agent should not have to invent a memory strategy each time. The prompting and tools should present
KoreRAM as a normal workflow.

### Desired mental model

The agent should treat KoreRAM like a warehouse with labeled containers:

- `put` large material into a container
- `inspect` the manifest, not the full content
- `extract` only the needed slice
- `derive` a smaller artifact from a larger one
- `link` a scratchpad key to the container handle
- `drop` the container when done

### What makes this intuitive

Use verbs that describe data handling directly rather than storage internals.

Good tool concepts:

- `ram_put(name, content, content_type, tags)`
- `ram_list()`
- `ram_inspect(ref)`
- `ram_get(ref, max_chars, chunk_ids)`
- `ram_search(ref, query)`
- `ram_extract(ref, query, save_as)`
- `ram_derive(ref, operation, save_as)`
- `ram_link(ref, scratch_key)`
- `ram_delete(ref)`

The names matter. `scratch_save` feels like a note. `ram_put` feels like parking a large working set.

### Prompt guidance the agent needs

The system prompt and skill docs should explicitly teach these rules:

- If a tool returns large text that will be reused, store it in KoreRAM.
- Keep only handles, manifests, and small summaries in scratchpad.
- Prefer `ram_extract` or `ram_search` over loading full raw content.
- Use scratchpad for planning state; use KoreRAM for bulk working material.
- When delegating, pass KoreRAM refs or selected extracts, not whole documents.

Without this guidance, the model will keep falling back to `scratch_load` or repeated fetches.

### Ingestion: auto-route large tool results, do not extend remote tool signatures

The original draft of this note proposed adding `save_to="ram"` parameters to KoreData tools, or
wrapper tools like `koredata_search_to_ram`. Both are wrong for this codebase.

KoreData is reached through MCP. The tool definitions are produced by the KoreDataGateway server
(`KoreData/KoreDataGateway/app/server.py`) and consumed by `KoreAgent/app/mcp_client.py`. Adding a
`save_to` parameter to `koredata_search` requires a cross-repo change to the gateway, has to be
mirrored on every other large-output MCP tool, and creates a coupling where remote services need to
know about a KoreAgent-internal storage layer. That is a leaky design.

The correct insertion point is already present in KoreAgent: `tool_loop.py` already auto-saves
large tool results via `scratch_auto_save`. KoreRAM should hook the same path.

#### Recommended pattern: auto-route at the tool-result boundary

When a tool call returns in `tool_loop.py`:

- if the result is small, inject it into the message thread as today
- if the result is medium and string-shaped, auto-save to scratchpad under `_tc_*` (current behavior)
- if the result is large or structured (list of records, search-bundle shape, long document), route it
  to KoreRAM instead, create an artifact in the active run, bind a stage alias, and return a compact
  manifest plus handle to the message thread

Routing decisions belong in one place: a small classifier in `tool_loop.py` (or a helper next to
`scratch_auto_save`) that inspects:

- estimated character count
- whether the payload parses as a list of records with stable id-like fields
- the source tool name (e.g. `koredata_search` is record-shaped by contract)
- an optional hint passed alongside the tool call result

This keeps the destination decision out of the LLM's hands for the common case, which directly
mitigates the largest design risk (the agent picking the wrong memory path).

#### Explicit shortcuts still exist, but only as agent-facing verbs

The agent should still have explicit verbs for the cases where it knows up front that it wants RAM:

- `ram_put(name, content, content_type, tags, run_id=None)` for things the agent constructs itself
- `ram_ingest_last(name)` to promote the most recent `_tc_*` auto-save into a typed RAM artifact
  when the agent realizes mid-task that it wants to keep working on it

The second verb is important. It is the cheap rescue path when auto-routing classified something as
scratchpad-worthy that turned out to need full RAM treatment. It avoids forcing the agent to
re-fetch from a remote MCP service just to relocate the payload.

### Destination rules

The destination should be explicit and simple.

Good default rule:

- save to scratchpad when the result is already compact and likely to be used directly
- save to KoreRAM when the result is large, structured, multi-record, or likely to go through multiple passes

For the KoreData article workflow, the initial candidate set should go directly to KoreRAM, not
scratchpad.

Scratchpad should then hold only:

- the RAM ref
- a short manifest
- maybe counts or stage labels

Example:

- scratchpad key `article_search_current` -> `ram:article_candidates_raw`
- scratchpad key `article_search_manifest` -> `124 references from KoreData search on topic X`

### Why this is important for the agent

This makes the workflow much more natural:

- retrieve large set
- park it immediately
- work from the handle
- derive smaller sets
- generate final output

That is exactly the kind of behavior KoreRAM is meant to support.

---

## Proposed architecture

### Recommended design pivot

The biggest beneficial shift is this:

- do not treat KoreRAM as a larger scratchpad
- do not treat every stored thing as a chunked text blob
- treat KoreRAM as a typed artifact graph for workflow state

That means the primary unit is not "some saved text". The primary unit is an artifact produced by a
workflow stage.

Examples:

- a KoreData search result set
- a filtered reference set
- a retained evidence set
- a generated draft
- a long raw document

Each artifact should carry:

- a type
- a workflow stage
- a run id
- provenance links
- a current alias binding where relevant
- content stored in the form that best matches the artifact, not forced into one universal model

This is a larger design shift than the earlier blob-first draft, but it reduces several major risks at
once:

- record-oriented collections stop being mangled into plain text
- alias updates become explicit workflow-state updates
- provenance becomes a first-class property
- stale lookup becomes easier to control by run and stage
- prompt-driven filtering can be audited against prior artifacts

### Components and locations

Concrete file layout, matching existing KoreAgent conventions:

1. `KoreAgent/app/koreram_store.py`
   Local SQLite persistence layer for runs, artifacts, aliases, records, decisions, chunks, tags.
   Mirrors the connection pattern in `KoreChat/app/database.py`: per-call `_conn()` contextmanager,
   WAL mode, `PRAGMA foreign_keys=ON`, schema declared as a module constant.

2. `KoreAgent/app/koreram_service.py`
   Higher-level operations: create artifacts, derive new stage outputs, bind aliases transactionally,
   search records, search chunks, extract slices, enforce retention. Reuses `scratch_query`'s
   isolated-LLM-call infrastructure rather than duplicating it; `ram_extract`, `ram_filter`,
   `ram_derive`, and `ram_summarize` all funnel through that same isolated-call surface.

3. `KoreAgent/app/system_skills/KoreRAM/`
   Skill definition and tool functions exposed to the agent, following the existing
   `system_skills/<Name>/skill.md` + `<name>_skill.py` convention used by `Scratchpad`, `Delegate`,
   and `TaskManagement`.

4. `tool_loop.py` integration
   A small classifier next to the existing `scratch_auto_save` call decides whether a tool result
   goes to scratchpad (`_tc_*` keys, current behavior) or to KoreRAM as a typed artifact. This is
   where the "auto-route large MCP results" rule actually executes.

5. Scratchpad integration
   Scratchpad stores only compact refs, manifests, stage aliases, and next-step hints. RAM handles
   live in named scratchpad keys, which means they ride the existing KoreChat persistence path
   (`_kc_patch` to `conversations.scratchpad`) at session boundary without any new persistence code.

6. Database location
   `datacontrol/koreagent/koreram.db`, alongside `datacontrol/koreagent/task_queue.json` already used
   by the scheduler. Single shared DB across sessions; rows scoped by `session_id`.

7. Optional MCP layer later
   Thin wrapper over the same service functions. Only justified once a second app (KoreChat,
   KoreCode, KoreDocs) needs the same memory surface.

8. Workflow-state rules
   A narrow policy layer in `koreram_service.py` defining stage names, alias update rules, retention
   class defaults, and missing-artifact behavior.

### Data model sketch

Suggested SQLite tables:

`ram_runs`

- `id`
- `session_id`
- `workflow_kind`
- `created_at`
- `updated_at`
- `status`
- `root_alias`

This is the top-level unit for one multi-step workflow execution. Every derived artifact should belong
to a run.

`ram_artifacts`

- `id`
- `session_id`
- `run_id`
- `name`
- `artifact_type` - search_result_set, reference_set, evidence_set, draft, raw_document, summary, etc.
- `stage_name` - search_raw, search_pruned, evidence_retained, final_draft, etc.
- `created_at`
- `updated_at`
- `source_tool`
- `source_ref`
- `parent_artifact_id`
- `content_mode` - records, text, mixed
- `item_count`
- `char_count`
- `chunk_count`
- `status`
- `retention_class` - session, run, pinned
- `manifest_json`

`ram_aliases`

- `session_id`
- `run_id`
- `alias`
- `artifact_id`
- `updated_at`

Aliases should be updated transactionally with artifact creation. Session-wide aliases such as
`last_search_results` can exist, but stage aliases such as `current_candidates` should normally be run-scoped.

`ram_artifact_links`

- `parent_artifact_id`
- `child_artifact_id`
- `link_type` - derived_from, filtered_from, extracted_from, summarized_from
- `created_at`

This is more explicit than a single derivation field and supports an artifact graph instead of a flat
list of objects.

`ram_records`

- `id`
- `artifact_id`
- `source_item_id`
- `ordinal`
- `title`
- `source_url`
- `source_kind`
- `published_at`
- `quality_score`
- `status`
- `record_json`

This table is the key design improvement for KoreData-style workflows. A collection of references
should be stored as records first, not as raw concatenated text.

`ram_record_decisions`

- `artifact_id`
- `record_id`
- `decision` - keep, drop, uncertain
- `reason`
- `created_at`

This makes filtering auditable. It is especially important when prompt-driven pruning removes items.

`ram_chunks`

- `id`
- `artifact_id`
- `chunk_index`
- `char_start`
- `char_end`
- `content`

Text chunking still matters, but only for text artifacts or text projections of record sets.

`ram_tags`

- `artifact_id`
- `tag`

`ram_search_cache`

- optional table for cached extracts or repeated expensive queries

### Storage rule by artifact type

This should be explicit:

- reference sets and search results -> records first, optional text projection second
- long documents and page bundles -> text chunks first
- summaries and drafts -> text first with provenance links back to source artifacts
- mixed artifacts -> records plus derived chunked text view when needed

This is the single biggest change that improves KoreData workflows.

### Why chunking matters

Chunking still matters, but it is no longer the entire design.

Without chunking, long-text artifacts are hard to inspect or search. With chunking, the system can:

- inspect size and structure cheaply
- retrieve slices without full reload
- search at chunk granularity
- feed only relevant chunks to isolated LLM extractors
- support derived artifacts that point back to exact source chunks

But chunking alone is not enough for reference-set workflows. Those need record-aware storage,
decision tracking, and provenance-preserving derivation.

### Workflow-state rule

Every important transformation should be a transaction with four outputs:

1. create a new artifact
2. record provenance and parent links
3. update the relevant stage alias
4. emit a compact manifest to scratchpad

If those four steps are not atomic, the design will drift into stale aliases and hard-to-debug state.

---

## Identity, aliases, and cleanup

Two separate needs exist here:

1. remove stale working data cleanly
2. let later prompts find the latest output of an earlier stage without guessing

Those should be designed explicitly.

### 1. Clear operations

Yes, a clear RAM operation makes sense.

In practice there are several useful scopes:

- `ram_clear(ref)` - delete one specific object
- `ram_clear_stage(alias)` - clear the current object bound to a stage alias
- `ram_clear_run(run_id)` - clear all objects created by one workflow run
- `ram_clear_session()` - clear all session-scoped RAM objects
- `ram_prune_expired()` - remove objects past TTL or retention policy

This matters because stale data is not just wasted storage. It can mislead later prompts if old stage
outputs still look plausible.

For agent use, `ram_clear_stage(alias)` is especially valuable because it matches the way workflows are
actually described:

- clear the old candidate set
- replace the current filtered set
- reset the draft output before rerunning

### 2. Follow-on prompt lookup

A later prompt should not have to search vaguely for "the thing we produced earlier".

The best pattern is:

- every important stage writes a named object
- every stage also updates a stable alias
- later prompts resolve the alias first

Example:

- object id: `ram:obj_20260527_001`
- alias: `article_candidates_raw`
- alias `current_candidates` -> `ram:obj_20260527_001`

Then the next stage might create:

- object id: `ram:obj_20260527_002`
- alias: `article_candidates_pruned_1`
- alias `current_candidates` -> `ram:obj_20260527_002`

That means a follow-on prompt can ask for `current_candidates` without needing to know the specific
object id.

### Primary lookup rule: aliases, not time

Use stable aliases as the primary lookup mechanism.

Good examples:

- `current_candidates`
- `current_filtered_references`
- `current_evidence_set`
- `final_draft`
- `last_search_results`

This is better than time-based lookup because it reflects workflow meaning, not storage history.

### Secondary lookup rule: time as metadata

Yes, time should still be stored and queryable, but mainly as secondary metadata.

Useful timestamp fields:

- `created_at`
- `updated_at`
- `last_accessed_at`
- optional `run_id`

Time is useful for:

- picking the most recent object when an alias is missing
- pruning stale data
- auditing workflow history
- answering questions like "what did we produce in the last run?"

Time is not the best primary key for normal agent workflows. If the model must rely on date strings to
find stage outputs, it is too easy to grab the wrong object.

### Recommended fallback behavior

When a follow-on prompt asks for prior-stage output, the system should resolve it in this order:

1. explicit object ref provided by the prompt or scratchpad
2. stage alias such as `current_candidates`
3. run-scoped latest object for that stage
4. session-scoped most recent matching object by kind/tag/time
5. report absence clearly if nothing matches

This makes absence handling explicit rather than forcing the model to improvise.

### How absence should be handled

If a prior-stage output is missing, the system should say so clearly and cheaply.

Examples:

- `No KoreRAM object is currently bound to alias 'current_candidates'.`
- `No stage output found for this run and alias 'final_draft'.`
- `Latest object for kind 'search_bundle' is older than retention policy and was pruned.`

For agent ergonomics, absence responses should also suggest the next corrective step:

- rerun the KoreData search
- rebuild the filtered set
- inspect available aliases
- inspect recent objects

### Practical recommendation

Use both:

- aliases for normal workflow navigation
- timestamps for history, cleanup, and fallback search

So the answer is not "date as part of the index" versus "search based on time".
The better design is:

- workflow alias first
- time second

That gives reliable follow-on behavior while still supporting cleanup and history queries.

---

## Retrieval patterns

KoreRAM should support several retrieval modes.

### 1. Manifest inspection

Return metadata only:

- object name
- size
- source
- tags
- chunk count
- short preview
- derived children

This is what the agent should usually see first.

### 2. Direct slice retrieval

Return a bounded portion by chunk or character range.

Use when the downstream step truly needs raw text.

### 3. Search-first retrieval

Run substring or FTS lookup to identify candidate chunks before any LLM call.

This keeps many extraction tasks cheap and deterministic.

### 4. Isolated extract

Equivalent to `scratch_query`, but operating on selected chunks from a KoreRAM object.

This is likely the most important path in practice. The agent should be able to ask:

- "from this stored dataset, list every row mentioning vendor X"
- "from this large page bundle, extract all dates"
- "from this code scan, show only the functions touching session restore"

### 5. Derive and save

Produce a new KoreRAM object from an existing one.

Examples:

- raw crawl -> filtered evidence set
- full report -> executive summary
- page bundle -> extracted table
- source file corpus -> symbol index

This is how the system avoids redoing heavy steps.

---

## Example workflow: KoreData article processing

This is a good fit for KoreRAM, and it clarifies why a RAM operation primitive is useful.

Start state:

- KoreData contains a large unsorted collection of articles and references
- the user wants a focused output, but only after several passes of filtering and judgement

### Step 1: Gather a candidate set

Run the initial KoreData search and store the result as a KoreRAM object.

Example:

- query KoreData for articles matching topic X
- save result as `ram:article_candidates_raw`

This object may contain:

- article ids
- titles
- snippets or extracts
- source metadata
- relevance hints
- reference links

At this point the right behavior is not to load the whole candidate set into the main prompt. The
agent should inspect the manifest, confirm size and shape, and then operate on it in place.

### Step 2: First pruning pass

Now the prompt asks to discard obviously bad references.

Examples:

- remove duplicates
- remove irrelevant topic matches
- remove malformed or incomplete references
- remove low-trust sources

This should create a new derived object rather than mutating the original.

Example:

- input: `ram:article_candidates_raw`
- operation: "keep only references clearly related to X; remove duplicates and malformed entries"
- output: `ram:article_candidates_pruned_1`

That gives lineage and lets later steps compare what was removed.

### Step 3: Quality filtering pass

The next prompt may apply more subjective judgement:

- remove low-quality sources
- remove weak evidence
- keep only references that directly support the target output
- prefer primary or better-structured secondary sources

Again, the output should be another derived object.

Example:

- input: `ram:article_candidates_pruned_1`
- operation: "keep only high-confidence references that provide direct evidence for the requested output"
- output: `ram:article_candidates_pruned_2`

This is the point where a generic RAM operation is especially useful. The system is not merely
searching text anymore. It is applying prompt-driven judgement to a stored working set and producing
the next working set.

### Step 4: Final shaping pass

Once the list is trimmed to a manageable, higher-quality set, later prompts can produce output from
that refined object.

Examples:

- generate a final summary
- produce a ranked evidence list
- create a report section
- write structured JSON or markdown output

Example:

- input: `ram:article_candidates_pruned_2`
- operation: "produce a final answer using only these retained references"
- output: `ram:article_output_draft`

The final user-visible response may come directly from that output object, or from a compact extract
of it placed into scratchpad for synthesis.

### Why this workflow matters

This pipeline shows that the key KoreRAM behavior is not just storage. It is iterative transformation
of working sets.

The pattern is:

- search -> candidate set
- filter -> reduced set
- filter again -> trusted set
- derive -> final output

Each stage should create a named artifact. That gives:

- reproducibility
- auditability
- the ability to inspect intermediate states
- cheaper retries from a later stage
- easier delegate handoff

### Implication for tool design

For this workflow, a RAM operation primitive does make sense.

Conceptually:

- input ref
- instruction prompt
- output ref

That is the core transformation pattern.

However, it should probably exist in two forms:

1. Internal primitive
   `ram_operate(input_ref, instruction, output_ref, mode, scope, options)`

2. Agent-facing verbs
   `ram_filter`, `ram_extract`, `ram_summarize`, `ram_derive`

The internal primitive keeps the implementation clean. The explicit verbs make tool selection more
reliable for the model.

### Recommended behavior for this workflow

When the agent is working through a pipeline like this:

- keep the original candidate set unchanged
- create derived objects for each filtering stage
- attach short manifests explaining what changed at each stage
- store removal reasons where practical
- only move compact summaries or final slices into scratchpad
- avoid loading the entire refined set into the main thread unless the set is now small enough

That is the operational difference between a basic scratchpad and a real working-memory layer.

---

## Relationship to delegates

KoreRAM is especially useful for delegate workflows.

Today, delegates can see selected scratchpad keys. For large workflows, the better pattern is:

- parent stores bulk material in KoreRAM
- parent passes one or more KoreRAM refs to the child
- child uses `ram_inspect` and `ram_extract`
- child writes compact outputs back to scratchpad or a derived KoreRAM object

That keeps child prompts small and makes delegation more scalable.

This also suggests a future extension:

- `delegate(..., koreram_visible_refs=[...])`

That mirrors the current `scratchpad_visible_keys` pattern cleanly.

---

## Persistence and lifecycle

KoreRAM should be more durable than scratchpad, but still task-oriented.

Recommended default lifecycle:

- session-scoped ownership by default
- explicit TTL or retention policy on object creation
- background cleanup of expired objects
- optional pinning for important artifacts
- optional promotion of selected artifacts into longer-lived stores later

In other words:

- scratchpad is ephemeral working state
- KoreRAM is durable working material
- long-term knowledge stores remain a separate concern

Do not blur KoreRAM into a general RAG corpus. That creates a different product with different
retrieval assumptions.

---

## Design risks and likely failure modes

This design is directionally sound, but there are several places where it is likely to fail if left
implicit.

### 1. The agent may not choose the right memory path consistently

This is the biggest practical risk.

If the distinction between:

- fetch and answer directly
- save to scratchpad
- save to KoreRAM
- derive a new RAM object

is not made extremely explicit in the tool surface and system prompt, the agent will behave
inconsistently. It will sometimes reload large content into the thread, sometimes repeat retrieval,
and sometimes save the wrong artifact at the wrong layer.

Failure symptom:

- expensive re-fetches
- oversized prompts
- inconsistent workflow state

Mitigation:

- clear routing rules by result size and shape
- direct-ingest save targets on large-output tools
- explicit agent-facing verbs, not only a generic `ram_operate`

### 2. Alias drift can cause prompts to use the wrong stage output

The alias model is necessary, but it is also a likely failure point.

If aliases are updated loosely, a follow-on prompt may resolve `current_candidates` or `final_draft`
to the wrong object. That is worse than a missing object because it looks valid.

Failure symptom:

- later stages operate on an older or unrelated artifact without noticing

Mitigation:

- aliases should be updated transactionally with object creation
- aliases should be scoped at least by session, and often by run
- manifests should include stage name, source object, and creation time

### 3. Prompt-driven filtering can silently drop good evidence

The article-processing workflow depends on prompt-driven judgement over candidate sets. That is useful,
but it is also where recall can degrade badly.

If filtering steps do not preserve removal reasons and provenance, the system can discard important
references and there will be no clear way to explain why.

Failure symptom:

- good sources disappear between stages
- final answer becomes overconfident but under-supported

Mitigation:

- preserve parent-child lineage for every derived object
- store short removal reasons where practical
- keep the original candidate set immutable
- prefer record-aware filtering over free-text filtering when possible

### 4. Text chunking alone may be the wrong storage model for reference sets

This note currently leans text-first, but many KoreData workflows are record-oriented rather than
document-oriented.

An article/reference set is not just a blob of text. It has items, fields, identifiers, and source
metadata. If KoreRAM stores these only as chunked text, search and extraction will become fragile.

Failure symptom:

- duplicate detection is unreliable
- filtering by source quality is inconsistent
- extraction loses row boundaries or item identity

Mitigation:

- support record-oriented manifests for collection-like objects
- store item ids and source metadata separately from chunk text
- treat "bundle of references" differently from "single long document"

### 5. A generic `ram_operate` can become too vague to debug

The primitive is useful internally, but if the public tool surface is too generic, behavior becomes
harder to predict and harder to test.

Failure symptom:

- the same tool is used for filtering, extraction, summarization, and transformation with inconsistent
   output quality

Mitigation:

- keep `ram_operate` as an internal primitive
- expose narrower verbs such as `ram_filter`, `ram_extract`, `ram_summarize`, and `ram_derive`
- make output contracts different for each verb

### 6. Lifecycle semantics may not match user expectations

The design currently says KoreRAM is more durable than scratchpad but still task-oriented. That is
reasonable, but it leaves an expectation gap.

Users may assume a useful derived artifact will still be there tomorrow. The system may prune it.
Or the opposite: stale run artifacts may hang around and confuse later work.

Failure symptom:

- users cannot find outputs they thought were durable
- later prompts accidentally pick up stale artifacts from an old run

Mitigation:

- define default retention clearly
- distinguish session, run, and pinned durability classes
- make prune behavior inspectable and reversible where practical

### 7. Cross-session and cross-run contamination is easy to introduce

If refs, aliases, or fallback searches are not scoped carefully, the agent can resolve a plausible
artifact from the wrong workflow.

Failure symptom:

- a prompt in one session picks up artifacts created for another session or another run

Mitigation:

- session scoping by default
- run id on all derived artifacts
- fallback search rules that refuse weak ambiguous matches

### 8. Provenance may be too weak for trustworthy synthesis

The final stages of the workflow assume the system can say "use only these retained references".
That only works if the retained set still carries strong provenance to underlying sources.

Failure symptom:

- final output cites a refined set, but the path from output back to original evidence is weak

Mitigation:

- every derived object should retain source refs and parent lineage
- manifests should summarize not only size and tags, but evidence ancestry
- final output generation should prefer structured retained-reference sets over raw text bundles

### Summary judgement

The strongest part of the design is the two-tier memory model.

The weakest parts are:

- agent routing reliability
- alias correctness
- record-oriented versus text-oriented storage
- provenance preservation through filtering passes

If those are handled well, KoreRAM will likely work. If they are left loose, the system will appear to
work for simple demos and then fail on exactly the larger, messier workflows it is meant to support.

---

## Implementation phases

### Phase 1: Minimal useful KoreRAM

Build the smallest version that changes agent behavior materially while locking in the right model.

Include:

- SQLite database for runs, artifacts, aliases, records, and chunks
- transactional artifact creation with alias update
- record-first storage for search/reference sets
- chunked text storage for long-document artifacts
- `ram_list`, `ram_inspect`, `ram_get`, `ram_search`, `ram_extract`, `ram_filter`, `ram_delete`
- scratchpad refs to active aliases and manifests, not raw stored payloads
- system prompt guidance teaching when to use RAM versus scratchpad

Do not include yet:

- embeddings
- vector search
- MCP server
- cross-app sharing
- sophisticated ranking
- broad automatic inference of workflow stages

If Phase 1 is good, the agent will already be able to handle much larger multi-step tasks without
locking the design into blob-first storage.

### Phase 2: Better ergonomics

Add:

- `ram_derive`
- `ram_operate` as an internal primitive only
- delegate visibility controls for refs
- retention policies and cleanup
- richer manifests, lineage tracking, and decision trails
- better auto-routing from large tool outputs into KoreRAM instead of scratchpad
- run templates for common workflows such as search -> prune -> retain -> draft

### Phase 3: Optional shared service

Only after the behavior is proven:

- expose the same service through MCP
- allow KoreChat, KoreCode, and other apps to share the memory surface
- decide whether a common KoreStack memory service is justified

---

## Decisions summary

The consolidated answer, in one place:

- **Where it lives.** New module in `KoreAgent/app/` (`koreram_store.py`, `koreram_service.py`),
  new skill at `KoreAgent/app/system_skills/KoreRAM/`, single SQLite database at
  `datacontrol/koreagent/koreram.db` shared across sessions and scoped by `session_id`.
- **Storage model.** Typed artifact graph (runs, artifacts, aliases, records, decisions, chunks),
  not a chunked blob store. Records-first for KoreData-style reference sets; chunks-first only for
  long single documents.
- **Boundary with KoreRAG.** KoreRAM is transient workflow memory. KoreRAG is the durable reference
  corpus. Long-text artifacts that earn durability get promoted to KoreRAG; they are not retained in
  KoreRAM forever.
- **Boundary with scratchpad.** Scratchpad keeps small facts, handles, and manifests. RAM handles
  live in named scratchpad keys, so they ride the existing KoreChat persistence path with no new
  persistence code. `_tc_*`, `_cx_*`, and `research_page_*` auto-key namespaces stay where they are;
  KoreRAM handles use a `ram_*` naming convention to avoid collision.
- **Ingestion.** Auto-route large tool results inside `tool_loop.py`, next to the existing
  `scratch_auto_save` hook. Do not add `save_to` parameters to remote MCP tools. Provide
  `ram_ingest_last(name)` so the agent can promote a recent `_tc_*` auto-save into a typed RAM
  artifact without re-fetching.
- **Agent surface.** Narrow verbs (`ram_put`, `ram_inspect`, `ram_get`, `ram_search`, `ram_extract`,
  `ram_filter`, `ram_derive`, `ram_link`, `ram_delete`, `ram_clear_stage`, `ram_clear_run`). The
  generic `ram_operate(input_ref, instruction, output_ref, ...)` exists as an internal primitive
  only, reused by the narrow verbs; it is not exposed to the LLM in Phase 1.
- **Isolated LLM calls.** `ram_extract`, `ram_filter`, `ram_derive`, and `ram_summarize` reuse the
  same isolated-call infrastructure as `scratch_query` rather than duplicating it.
- **Navigation.** Workflow-stage aliases first (`current_candidates`, `final_draft`), timestamps
  second. Aliases are updated transactionally with artifact creation, scoped by session and run.
- **MCP.** Not in Phase 1. Add only when a second consumer exists (KoreChat, KoreCode, KoreDocs).

## Bottom line

KoreRAM should not replace scratchpad. It should complete it.

The clean design is:

- scratchpad = small prompt-visible working state, with named keys already persisted via KoreChat
- KoreRAM = local SQLite-backed working datasets and derived artifacts, organized as a typed
  artifact graph, auto-fed from `tool_loop.py`
- KoreRAG = durable long-term reference corpus, the promotion target for RAM artifacts that earn
  durability
- MCP = optional later transport layer once a second consumer exists, not the starting point

If implemented this way, KoreAgent gets a much stronger working-memory model that fits the existing
local-first architecture instead of cutting across it.