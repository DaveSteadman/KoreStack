# Tool Selector Proposal

## Objective

Narrow the set of tools the LLM is actively considering on each turn so it is more likely to pick the correct tool.

This is fundamentally a tool-surface throttling design.

The system should be able to connect to a large total inventory of tools, including many MCP tools, without paying that full cost in every prompt. The LLM should see only a small active working set for the current task, while still retaining a clear path to inspect and select from the larger catalog when it needs something new.

Today the model sees the full active tool surface for the session. That works, but it creates two avoidable problems:

1. Too many tool names compete in the schema and in the prompt.
2. Smaller or local models are more likely to choose the wrong tool, repeat a broad tool, or hallucinate a nearby name.

The performance goal is not merely cosmetic prompt cleanup. The point is to let KoreAgent scale to a larger number of attached MCP servers and tools without degrading final prompt quality, routing accuracy, or response latency from an oversized exposed tool schema.

The proposed design introduces two explicit concepts:

- `all tools`: the full runtime inventory.
- `selected tools`: a smaller conversation-scoped working set currently exposed to the model for work.

Recommended shape for `selected tools`:

- fixed maximum size, e.g. `32`
- ordered by most recently used
- tools can be added on demand from the full catalog
- older entries fall off the back of the list automatically


## Current State

Current orchestration behavior is simple and global:

1. KoreAgent loads `app/skills/skills_catalog.json`.
2. KoreAgent optionally removes web skills via `_filter_web_skills(...)`.
3. `build_tool_definitions(...)` builds the tool schema from that payload.
4. `build_catalog_gates(...)` builds the local execution allow-list from that same payload.
5. MCP tool definitions are appended.
6. `/tools` lists the currently exposed tool set.

Current control points:

- `KoreAgent/app/orchestration.py`
  - `active_payload = config.skills_payload if _WEB_SKILLS_ENABLED else _filter_web_skills(config.skills_payload)`
  - `build_tool_definitions(active_payload)`
  - `build_catalog_gates(active_payload)`
- `KoreAgent/app/input_layer/slash_commands.py`
  - `/tools` currently lists the full exposed set for the session.
- `KoreChat/app/db_schema.py`
  - conversations already persist JSON session fields such as `scratchpad`, `datasets`, and `input_history`.

This means the right implementation seam already exists: keep a full catalog in config, then derive a smaller active view before prompt/tool-loop execution.


## Terminology

### 1. All tools

The complete runtime tool inventory available to the system.

Recommended meaning:

- all local tools from `skills_catalog.json`
- plus currently connected MCP tools

Important distinction:

- inclusion in `all tools` means the tool is visible to inspection and selection
- it does not mean the tool is automatically present in `selected tools`

Reason:

- the model chooses from the actual callable runtime surface, not just the static local JSON catalog
- excluding MCP tools from `all tools` would make the selector blind to tools that really are available

For local skills, the source of truth remains `KoreAgent/app/skills/skills_catalog.json`.
For MCP tools, the source of truth remains live enumeration from MCP.


### 2. Selected tools

The conversation-scoped working set of tool names currently exposed to the model.

Recommended semantics:

- bounded list, capped at `32` tool names
- ordered by most recently used, newest at the front
- when a tool is added again or used again, it is promoted to the front
- when the list exceeds the cap, the oldest tail entries are evicted

Selection should operate at the tool function name level, not the skill folder level.

Reason:

- local skills can expose multiple functions
- MCP tools are already flat function names
- the existing `/tools` output is already tool/function oriented

Examples:

- local tool names: `search_web_text`, `fetch_page_text`, `dataset_save`
- MCP tool names: `docs_sheet_range_read`, `graph_connection_create_many`


## Core Proposal

### A. Keep one full catalog in runtime memory

Do not replace `config.skills_payload` with a filtered payload.

Instead:

- keep `config.skills_payload` as the full local catalog
- treat MCP enumeration as the full remote catalog
- derive a new per-conversation active view just before each LLM round


### B. Add a tiny always-on control plane

The model cannot choose tools that are not currently exposed. Therefore the tool-selection mechanism itself must always remain available.

Minimal always-on set for the first version:

- the new `tool_selection` system skill

This is not a peripheral helper. It is the central control skill that makes prompt-surface throttling workable.

`tool_selection` should be prominent in the system-skill layer because it is the mechanism that lets the model move from a small prompt-exposed working set to the larger available tool universe without reopening the full tool surface on every turn.

Everything else is governed by `selected tools`.

This keeps the baseline schema small while still letting the model expand its working set when needed.

The important detail is that the working set should not only grow. It should also self-prune by recency so the prompt surface stays small over long conversations.


### C. Persist selected tools per KoreChat conversation

`selected tools` must be durable conversation state, not just ephemeral process memory.

Recommended storage:

- add `tools_active TEXT NOT NULL DEFAULT '[]'` to `KoreChat.app.db_schema.conversations`

Recommended interpretation of that field:

- JSON array of tool names
- ordered most-recent-first
- maximum logical length of `32`

Why this fits the current model:

- KoreChat already stores conversation-scoped JSON session fields
- `tools_active` is the same kind of state as `scratchpad`, `datasets`, and `input_history`
- the selection should follow the conversation across reloads and background work


## Proposed Runtime Model

### All-tools view

Built from:

- full local skill catalog from `skills_catalog.json`
- full current MCP tool inventory

This view is used for:

- inspection
- validation
- tool selection
- slash command display of `/tools all`

This includes MCP tools in the discovery/catalog surface, but MCP tools still remain inactive until explicitly selected into the active working set.

That distinction is the core scaling property of the design: KoreAgent can attach many MCP tools to the environment without automatically inflating the tool schema shown to the LLM for a specific prompt.


### Active-tools view

Built from:

- always-on control tools
- plus names listed in `conversation.tools_active`, in MRU order
- plus the existing global web-skills filter, if that flag remains enabled

This view is used for:

- tool schema sent to the LLM
- prompt-level skill selection guidance
- local catalog gates / allow-list
- active tool listing for `/tools active`

This is the only tool surface that should count against prompt complexity for a live run.


## New System Skill: `ToolSelection`

This should be a built-in system skill under `KoreAgent/app/system_skills/ToolSelection/`.

Its job is not to do the target work itself. Its job is to help the model inspect the catalog and expand its active working tool set.

It should be treated as a prominent always-on system capability, not as an optional convenience skill.

The design depends on it because the whole point is to keep the normal prompt-exposed tool set small while still allowing the model to discover and pull in additional tools from a much larger attached inventory.

The intended model behavior is:

1. use currently active tools when they already fit the task
2. inspect the all-tools catalog when the needed capability is missing
3. pull one or more new tools into the active working set
4. let older inactive-by-recency tools fall off automatically over time

### Minimum capability

The first version only needs two operations:

1. inspect the all-tools catalog
2. add tool names to `tools_active`

### Recommended tool functions

#### `tools_catalog_list(filter_text: str = "", max_items: int = 100, include_mcp: bool = True)`

Returns a compact list of available tools from the all-tools inventory.

Suggested fields per item:

- `name`
- `origin` (`local` or `mcp`)
- `role`
- `availability`
- short description / first sentence
- whether currently active

This gives the model a way to inspect the larger inventory on demand without placing the entire catalog into every prompt.

This catalog output includes MCP tools when they are currently present, but that does not auto-activate them for the next LLM round.

#### `tools_active_add(tool_names: list[str])`

Validates tool names against the all-tools inventory, deduplicates them, promotes them to the front of the MRU list, truncates the list to the cap, persists it to the current conversation, and returns the updated active list.

Suggested return payload:

- `added`
- `promoted`
- `unknown`
- `active_tools`
- `evicted`

Suggested semantics:

- if a named tool is already present, move it to the front rather than duplicating it
- if adding new tools pushes the list past `32`, drop tail entries
- return the dropped entries so the run log and slash commands can explain why a tool is no longer active

### Not required in the first version

Keep the first pass narrow. These can come later if needed:

- `tools_active_remove(...)`
- `tools_active_clear()`
- `tools_active_replace(...)`
- intent-to-tool automatic expansion without explicit catalog lookup


## Slash Command Changes

Retire the current `/tools` behavior and replace it with explicit subcommands.

### `/tools all`

Shows the full runtime inventory.

Expected content:

- all local catalog tools
- all connected MCP tools
- maybe grouped by origin
- maybe mark items already active

### `/tools active`

Shows the currently selected set for the conversation.

Expected content:

- always-on control tools
- tools named in `conversation.tools_active`, shown in MRU order
- optionally note suppressed tools if web skills are globally disabled
- optionally show the configured cap, e.g. `32`

### `/tools`

Retire the bare command.

Recommended behavior:

- print usage only: `/tools all` or `/tools active`


## KoreChat Persistence Changes

### Schema

Add a new column to `conversations`:

```sql
tools_active TEXT NOT NULL DEFAULT '[]'
```

This column stores the ordered MRU list only. The always-on control tools are runtime policy, not persisted entries in the list.

### Read/write path

Mirror the existing pattern used for other JSON conversation fields:

- decode on read
- accept updates via `conversation_update(...)`
- include in `ConversationPatchRequest`
- include in detail/list/get responses

Recommended invariant:

- enforce the `32`-tool cap in the write path, not only in the caller
- normalize duplicates away while preserving MRU ordering

### Why KoreChat owns this state

Because the selected tool set is part of the conversation itself.

It is not just:

- a temporary LLM preference
- a global process setting
- a stateless prompt optimization

It affects how later turns are interpreted and which tools the model is allowed to call.


## Orchestration Changes

### Current seam

Today, one `active_payload` is built once near the start of `orchestrate_prompt(...)`, then used for:

- `build_tool_definitions(...)`
- `build_system_message(...)`
- `build_catalog_gates(...)`

That is the right seam, but the active view must become conversation-specific.

### Proposed seam

Introduce a helper that derives the active view from:

- `config.skills_payload` as the local all-tools catalog
- current MCP enumeration
- `conversation_entry.tools_active`
- always-on control tools
- global filters such as web-skills off
- configured active-tool cap, e.g. `32`

Pseudo-shape:

```text
all local payload           -> filter selected local tool names -> active local payload
all MCP tool defs/index     -> filter selected MCP tool names   -> active MCP defs/index
always-on ToolSelection     -> always included
```

Then use the resulting active view for:

- `build_tool_definitions(active_local_payload) + active_mcp_defs`
- `build_system_message(..., active_local_payload, ...)`
- `build_catalog_gates(active_local_payload)`

During this derivation step, any tool name still stored in `conversation.tools_active` but no longer present in the current local catalog or current MCP inventory must be removed from the active list before the round proceeds.

### Important implication: refresh during the same run

This is the most important implementation detail.

The current tool loop receives `tool_defs` and `catalog_gates` once for the whole run.
That is not enough for tool selection.

If the model does this in round 1:

1. call `tools_catalog_list(...)`
2. call `tools_active_add(["search_web_text", "fetch_page_text"])`

then round 2 must see the updated tool schema immediately, within the same orchestration run.

Therefore the tool loop must support a schema refresh between rounds.

Recommended approach:

- derive active tool defs and catalog gates before each model round, not once per run
- after any successful `tools_active_add(...)`, refresh the conversation entry and rebuild the active view before the next LLM call
- after any successful real tool execution, optionally promote that tool to the front of the MRU list before the next round
- if a selected tool no longer exists in the current runtime inventory, remove it from `tools_active`, persist the repaired list, and feed back a tool-not-present error so the model can select an alternative and try again

Without this, the selector would only take effect on the next user turn, which is too slow and will feel broken.

The MRU promotion rule matters because `selected tools` should reflect what the conversation is actually using now, not only which tools were manually activated once.


## Prompt Behavior

The system prompt should describe the model accurately but minimally.

Recommended rule:

- the model only has access to the currently active tool set
- if the needed tool is not active, it should use the `tool_selection` skill to inspect the catalog and add the needed tool(s)

Important:

- do not dump the entire all-tools list into the prompt
- do not keep broad skill-selection guidance for inactive tools

The point of this design is to shrink the working tool surface, not move the full catalog from one prompt block to another.

The bounded MRU rule strengthens that outcome: the model can keep reaching for new tools as needed, but the working set remains capped.


## Inactive Tool Recovery

Observed failure mode from the first live logs:

1. the model requested a real tool that exists in the full catalog
2. the tool was not in the current active set
3. execution returned `Tool '<name>' is not active for this conversation`
4. the next round did not reliably pivot into `ToolSelection`
5. the run drifted into a dead-end final answer instead of recovering

This is not a catalog problem and not a normal tool failure.
It is a control-plane recovery problem.

The design should therefore treat an inactive-tool rejection as a special retryable routing event, not as a generic tool error.

### Required next-round behavior

When the model requests a tool that exists in `all tools` but is not currently active, the next round should receive a stronger structured correction block.

Recommended content of that correction block:

- the exact requested tool name
- a statement that the tool exists but is not active in this conversation
- a reminder that `tools_catalog_list(...)` and `tools_active_add(...)` are always available
- an explicit instruction: do not answer the user yet; recover by activating tools first
- a compact list of the currently active tool names
- when known, a compact hint that the requested tool can be activated directly by exact name

Recommended tone:

- procedural and imperative
- short
- free of extra narrative

Suggested shape:

```text
Recovery required: tool `dataset_list` exists in the runtime catalog but is not active for this conversation.
Do not answer the user yet.
Use ToolSelection now.
If you already know the exact tool name, call `tools_active_add(["dataset_list"])`.
Otherwise call `tools_catalog_list(...)` first, then `tools_active_add([...])`, then continue the task.
Currently active tools: tools_catalog_list, tools_active_add, ...
```

This is intentionally stronger than a plain tool error string.
The goal is to convert the event into a deterministic control-flow handoff.

### Differentiate the recovery cases

The retry guidance should depend on which of these cases occurred:

1. requested tool exists in `all tools` but is inactive
2. requested tool was previously selected but no longer exists in the live runtime inventory
3. requested tool name is invalid, but a single high-confidence corrected name exists
4. requested tool name is unknown or ambiguous

Recommended handling:

- case 1: instruct the model to activate that exact tool via `tools_active_add(...)`
- case 2: state that the tool is no longer present, remove it from `tools_active`, and instruct the model to inspect the catalog for an alternative
- case 3: return the corrected tool name explicitly and either suggest activation or pre-emptively activate it, depending on confidence policy
- case 4: state that the name is unknown or ambiguous and instruct the model to inspect the catalog instead of retrying the same name

This distinction matters because cases 1 and 3 can often be recovered in one step, while cases 2 and 4 require discovery.

### Name correction for invalid tool names

This should be blended into the same recovery path rather than handled as a separate unrelated feature.

Observed example from earlier runs:

- requested prefix: `koredec_...`
- real tool family: `koredoc_...`

That is not a capability-selection error. It is a tool-name transcription error.

The runtime should therefore attempt a conservative name-repair pass before classifying the request as fully unknown.

Recommended matching model:

- normalize to lowercase
- split tool names on `_`
- compare token-by-token rather than only as one flat string
- score exact token matches highest
- score short prefix deviations and small edit-distance deviations within each token as weaker but still meaningful
- treat token-order preservation as important

Why underscore-token matching fits this codebase:

- tool names are intentionally phrase-like and underscore-delimited
- many naming mistakes are local token errors such as `koredec` vs `koredoc`, not total name hallucinations
- token-aware matching is less dangerous than broad fuzzy matching across the whole string

Recommended safe policy:

1. if the requested name exactly matches a real tool name that is merely inactive, handle it as case 1
2. otherwise, run token-aware correction against `all tools`
3. if exactly one candidate scores above a strict confidence threshold, treat it as a corrected-name candidate
4. if multiple candidates are close, do not guess; fall back to catalog inspection

Suggested examples:

- `koredec_table_read` -> high-confidence correction candidate `koredoc_table_read`
- `docs_sheet_reed` -> likely correction candidate `docs_sheet_read`
- `graph_get` when multiple `graph_*_get*` tools exist -> ambiguous, do not auto-correct

### Suggestion versus pre-emptive loading

There are two reasonable recovery levels once a single high-confidence corrected name is found.

#### Level 1: explicit suggestion only

Return a structured correction block such as:

```text
Recovery required: requested tool `koredec_table_read` is not a valid tool name.
Closest valid tool: `koredoc_table_read`.
That tool is available but not active for this conversation.
Do not answer the user yet.
Use ToolSelection now.
Call `tools_active_add(["koredoc_table_read"])`, then continue the task.
```

Pros:

- clearer model intent in the log
- safer when tuning the correction heuristics
- preserves explicit control-plane behavior

#### Level 2: pre-emptive corrected activation

If the corrected candidate is unique and high-confidence, the runtime may optionally:

1. record that the requested invalid name resolved to the corrected tool name
2. add the corrected tool name to `tools_active`
3. refresh the active schema
4. feed back a short notice that the corrected tool was activated automatically

Suggested notice shape:

```text
Requested tool `koredec_table_read` is not valid.
Auto-corrected to `koredoc_table_read` and activated for this conversation.
Continue using the corrected name only.
```

Pros:

- fewer wasted rounds for small or local models
- directly addresses stable typo families such as `koredec_` vs `koredoc_`

Cons:

- raises the risk of silently choosing the wrong tool when names are dense
- makes the control plane slightly less explicit

Recommendation:

- start with Level 1 for all corrected-name cases
- consider Level 2 only for unique high-confidence matches
- require that the corrected target either be already active or be a valid inactive tool that can be safely promoted
- never auto-correct when multiple nearby candidates exist

### Confidence rules for correction

Correction should be intentionally stricter than search ranking.

Recommended minimum rules before suggesting or auto-loading a corrected name:

- the candidate must exist in `all tools`
- token count should usually match exactly
- all nontrivial tokens after the family/prefix token should either match exactly or differ by a very small local typo
- the best candidate must beat the second-best candidate by a clear margin
- family-token corrections such as `koredec` -> `koredoc` are acceptable only when the remainder of the name also aligns strongly

Recommended hard stops:

- no candidate above threshold -> classify as unknown
- multiple candidates near the same score -> classify as ambiguous
- corrected candidate exists but belongs to a very different tool family -> do not auto-load

### Interaction with inactive-tool recovery

If a corrected name resolves to a real but inactive tool, the system can blend both steps into one recovery event.

Example:

1. requested tool name `koredec_table_read` is invalid
2. corrected name `koredoc_table_read` is identified confidently
3. corrected tool exists in `all tools` but is inactive
4. recovery response either:
  - suggests `tools_active_add(["koredoc_table_read"])`, or
  - auto-activates `koredoc_table_read` if the stricter policy is enabled

This is the main place where pre-emptive loading makes sense: not for broad semantic guesses, but for stable, local, high-confidence name repairs.

### Prevent premature final answers

An inactive-tool rejection should not immediately allow the run to terminate with a normal final answer unless one of the following is true:

1. the model has already attempted `ToolSelection` and still cannot proceed
2. the user request can genuinely be answered without the tool after the failure
3. the model explicitly asks the user for missing business intent rather than pretending the tool issue is the answer

Recommended loop policy:

- mark inactive-tool rejection as `recovery_pending`
- inject the stronger correction block into the next round
- allow at least one recovery round before accepting a non-tool final answer
- if the next round still ignores recovery, inject one more shorter reminder rather than silently ending the run

This avoids the exact dead-end seen in the log, where the model acknowledged the missing active tool but still stopped instead of using the always-on selector.

### Optional fast-path improvement

The first safe hardening step is stronger guidance only.

If that still proves weak for smaller models, an optional phase-2 behavior is available:

- when the requested tool name is an exact match in `all tools`, the runtime may auto-promote that tool into `tools_active`
- when the requested tool name is invalid but resolves to a single high-confidence corrected tool name, the runtime may auto-promote the corrected tool into `tools_active`
- emit a log note that the tool was auto-activated because the model requested a valid but inactive exact name
- emit a distinct log note when activation happened via corrected-name repair rather than exact-name match
- then continue the run with a refreshed schema

Tradeoff:

- pros: fewer wasted rounds, especially on small local models
- cons: weaker separation between selection and execution, and less observable model intent

Recommendation:

- do not start with broad auto-activation
- first measure whether the stronger structured recovery block is sufficient
- if needed, enable exact-name inactive auto-activation before corrected-name auto-activation
- keep corrected-name auto-activation as the narrowest, highest-confidence fallback only for stable typo families and unique matches

### Logging and diagnostics

Inactive-tool recovery events should be visible in logs as their own category.

Recommended fields:

- requested tool name
- corrected tool name, if any
- classification: `inactive_known`, `missing_selected`, `corrected_inactive`, `corrected_active`, `unknown_name`, or `ambiguous_name`
- active tool list before recovery
- whether the system suggested activation or auto-activated directly
- whether `ToolSelection` was attempted in the following round
- whether recovery succeeded

This makes it possible to distinguish:

- bad catalog contents
- bad active-set defaults
- weak recovery prompting
- models that ignore explicit control-plane instructions


## Local Skill Filtering Detail

Local skills are grouped by skill, but selection should be by tool name.

That means the active local payload should be built by copying only the selected functions from each skill record.

If a skill originally contains:

- `search_web(...)`
- `search_web_text(...)`

and only `search_web_text` is active, the filtered payload should keep the skill record but trim `functions` to only that selected function.

This preserves:

- tool descriptions
- per-tool parameter metadata
- skill classification fields
- prompt guidance relevant to the retained function(s)


## MCP Filtering Detail

MCP tools are already flat tool definitions and flat name indexes.

For MCP, selection is simpler:

- keep full live enumeration for the all-tools view
- filter by tool name for the active-tools view
- do not auto-activate MCP tools just because they are present in the all-tools catalog
- if a previously selected MCP tool disappears on reconnect or config change, remove it from the active set and return a retryable tool-not-present error to the LLM

Recommended runtime behavior for missing MCP tools:

1. detect that the selected MCP tool name is not present in the current live MCP inventory
2. remove that name from `conversation.tools_active`
3. persist the repaired MRU list
4. feed back an error message stating that the selected tool is no longer present and the model should choose another tool from the catalog

This should not be treated as a fatal orchestration error. It is a recoverable tool-selection event.


## Recommended Conversation Defaults

For a new KoreChat conversation:

- `tools_active = []`
- always-on control tools remain available
- active working-set cap = `32`

This means a new conversation starts narrow by default.

If the user asks for something that requires tooling, the model can:

1. inspect the catalog
2. activate a small relevant subset
3. do the real work with that subset
4. naturally age out old tools as the task mix changes

This is the behavior we want.


## Why This Should Improve Tool Selection

Benefits expected from a smaller active schema:

1. fewer semantically similar tool names competing in the function list
2. fewer wrong tool calls from small or local models
3. less prompt clutter from broad guidance blocks
4. clearer human inspection via `/tools active`
5. conversation-specific tool context instead of one global exposed set
6. long conversations stay bounded because old tools automatically fall off
7. a larger MCP estate can be attached without forcing all of it into every prompt
8. prompt-time tool routing cost stays tied to the active working set, not the total installed inventory


## Risks And Mitigations

### Risk: too much friction before real work starts

The model may spend an extra round selecting tools.

Mitigation:

- keep the selector minimal and fast
- allow adding multiple tools in one call
- refresh the schema in the same run

### Risk: stale active lists after catalog changes

Tools may be renamed or removed while old conversations still reference them.

Mitigation:

- drop unknown names when building the active view
- return them in diagnostics from `/tools active`
- pass a retryable tool-not-present error back to the LLM when a now-missing selected tool was needed for the current round
- never fail the whole run because one stored tool name no longer exists

### Risk: useful tools may fall off too aggressively

If the cap is too small, the model may churn tools in and out.

Mitigation:

- start with `32`, not an ultra-small cap
- keep the control plane always on so re-adding a tool is cheap
- observe churn before tuning the cap downward or upward

### Risk: "most recently used" is ambiguous

There are two possible meanings:

- most recently added by the selector
- most recently actually executed

Recommended rule:

- `tools_active_add(...)` promotes named tools immediately
- successful execution of an active tool also promotes it

That makes the list reflect real live usage instead of only earlier selection decisions.

### Risk: local skill guidance remains too broad

Some guidance today is skill-level rather than function-level.

Mitigation:

- start by filtering functions only
- refine guidance granularity later if broad descriptions still leak too much irrelevant routing advice


## Recommended First Implementation Scope

Keep the first implementation narrow.

### Phase 1

1. add conversation persistence for `tools_active`
2. add built-in `ToolSelection` skill
3. add `/tools all` and `/tools active`
4. make orchestration derive active tools from conversation state
5. enforce a bounded MRU active set with cap `32`
6. refresh tool schema between rounds when `tools_active` changes

### Phase 2

Optional follow-up after the basic loop works:

1. add remove/clear operations
2. add UI controls in KoreChat
3. add logging and metrics for tool-selection churn
4. consider lightweight automatic starter-set heuristics


## Recommendation

Proceed with an explicit two-layer model:

- `all tools` = full runtime inventory
- `selected tools` = conversation-scoped MRU working set for the current run

Persist `selected tools` in KoreChat as a capped MRU list, keep `ToolSelection` always active, and refresh the active schema between tool rounds.

That gives a simpler, more robust, and more understandable tool-selection model without changing the existing skill catalog format, while also letting KoreAgent scale to a much larger connected MCP/tool estate without degrading the prompt surface exposed to the LLM on each turn.