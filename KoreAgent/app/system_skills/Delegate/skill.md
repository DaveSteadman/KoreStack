# Delegate Skill

## Purpose
Create a fresh child orchestration context for a focused sub-task. The child gets its own
isolated reasoning and tool-calling loop, runs independently, and returns a compact answer
to the parent. Use this when a sub-problem would benefit from multi-step investigation
without polluting the parent context with intermediate tool chatter.

## Trigger keyword: delegate

## Interface
- Module: `KoreAgent/app/system_skills/Delegate/delegate_skill.py`
- Functions:
  - `delegate(prompt: str, instructions: str = "", max_iterations: int = 3, output_key: str = "", scratchpad_visible_keys: list[str] | None = None, scratchpad_prefix: str | None = None, tools_allowlist: list[str] | None = None)`

## Parameters

### `delegate(prompt, instructions = "", max_iterations = 3, output_key = "", scratchpad_visible_keys = None, scratchpad_prefix = None, tools_allowlist = None)`
- `prompt` *(required)* - the child task to execute. Must be a complete, self-contained question or instruction.
- `instructions` *(optional)* - extra steering prepended to the child prompt, e.g. "research thoroughly and return a concise answer with evidence".
- `max_iterations` *(optional, default 3)* - maximum tool-calling rounds for the child run, 1-8 recommended.
- `output_key` *(optional)* - scratchpad key name to save the child's final answer under automatically.
  Mirrors `scratchpad_query`'s `save_result_key`. The parent can then use `scratchpad_query(output_key, ...)` or
  `{scratchpad:output_key}` downstream without capturing the answer from the return dict inline.
- `scratchpad_visible_keys` *(optional)* - list of scratchpad key names the child can see in its system prompt.
  When omitted (default), the child sees **no** parent scratchpad keys — this prevents silent leakage of
  all auto-saved `_tc_*` noise into the child context.
  Pass an explicit list to hand the child exactly the data it needs: e.g. `["search_hits", "page_draft"]`.
- `scratchpad_prefix` *(optional)* - pass all scratchpad keys whose names start with this string to the child.
  Complements `scratchpad_visible_keys` — both lists are merged (deduped). Useful when auto-saved keys follow
  a naming convention: e.g. `scratchpad_prefix="turn_3_"` passes every key saved during turn 3, or
  `scratchpad_prefix="_tc_r2"` passes all tool-call auto-saves from round 2 of the parent run.
- `tools_allowlist` *(optional)* - list of function names the child is permitted to call.
  When provided, the child's tool set is restricted to only skills that expose those functions. Use to create
  focused sub-loops: e.g. `["fetch_page_text", "scratchpad_save"]` for a child whose only job is to fetch and
  store, or `["search_web", "lookup_wikipedia", "fetch_page_text"]` for a web-research-only child.

## Output
Returns a dictionary with:
- `status` - "ok" or "error"
- `answer` - compact final answer from the child run
- `delegate_prompt` - the child prompt actually used
- `depth` - delegation depth of the child run
- `max_iterations` - child iteration budget used

## Planning strategy

Delegation is a divide-and-conquer primitive, not a reactive tool call.
Decide the decomposition BEFORE calling any tools: identify the independent sub-problems,
then fire one delegate per part and synthesise the results at the parent level.

Prefer width over depth: multiple sibling delegates from the parent is safer and cleaner
than a chain of delegates spawning delegates. Child delegates cannot spawn further delegates
(Delegate is excluded from the child toolset by default). Only the top-level agent can delegate.

## Triggers
Invoke this skill when:
- the task contains a clear sub-problem that should be solved independently
- intermediate tool chatter from the sub-problem would pollute the parent context
- a sub-problem needs its own multi-step tool-calling loop (more than one tool in sequence)
- you want a focused, isolated sub-investigation before final synthesis

Do NOT use for trivial one-step actions - prefer direct tool calls instead.
If the subtask is a single `search`, `fetch`, or `lookup`, call that tool directly.

## Critical: never describe a tool call as text
Do NOT write the delegate call as a JSON literal in your response text, e.g.:
  `{"tool": "delegate", "arguments": {"prompt": "..."}}`
This is a hallucination - writing the action instead of doing it. Always invoke `delegate(...)` via
the tool-call mechanism. If you intend to delegate, emit a tool call, not text.

## List-processing workflows
- Prefer one delegate over the whole batch when the child can iterate internally.
- If you truly need parallel delegates, launch siblings from the parent only.
- Do not ask a child delegate to spawn more delegates for each list item unless recursion is essential.
- If the task is mostly `search -> fetch -> save`, direct tool calls in sequence are usually more reliable than per-item delegation.
