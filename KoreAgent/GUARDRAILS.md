# Guardrails

This document describes the guardrails currently implemented in `KoreAgent`.

In this codebase, a "guardrail" means one of three things:

1. `Prompt policy`
   Guidance injected into the model's system prompt.
   This influences behavior but does not force it.

2. `Runtime enforcement`
   Logic in the orchestration or tool loop that blocks, rewrites, or redirects a bad step.
   This is the hard form of a guardrail.

3. `Regression coverage`
   Tests that confirm a guardrail continues to behave as intended.


## Mental Model

Most runtime guardrails in `KoreAgent` operate on the sequence of prompt/tool rounds.

They answer questions like:

- "Has the model tried to finish without performing a required tool step?"
- "Has it chosen a tool that is not active or not valid?"
- "Has it repeated the same failing tool call?"
- "Has it tried to claim a write happened without performing the write tool?"

When the answer is "yes", the tool loop injects a corrective message or stops the answer.


## Where Guardrails Live

Primary enforcement files:

- [app/tool_loop.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/tool_loop.py)
- [app/orchestration.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/orchestration.py)
- [app/prompt_builder.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/prompt_builder.py)

Primary test suites:

- [app/testing/test_guardrail_runtime.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/testing/test_guardrail_runtime.py)
- [app/testing/test_guardrail_integration.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/testing/test_guardrail_integration.py)
- [app/testing/test_guardrail_smoke.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/testing/test_guardrail_smoke.py)
- [app/testing/test_guardrail_data.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/testing/test_guardrail_data.py)


## Current Guardrails

### 1. Web Evidence Guard

Type:
- `Runtime enforcement`
- `Prompt policy`

Purpose:
- Prevent `search -> final answer` when the answer is supposed to be web-grounded.

Trigger:
- The user prompt matches a web-facts style intent such as `search the web` or `facts about ...`
- A discovery tool was used:
  - `search_web`
  - `search_web_text`
  - `koredata_search*`
- No evidence-bearing retrieval tool was used:
  - `fetch_page_text`
  - `research_traverse`
  - `lookup_wikipedia`
  - `koredata_get_*`

Effect:
- First attempt to finalize is blocked.
- A corrective message is injected telling the model to fetch source content first.
- If it still fails to fetch evidence, the run is stopped with a failure message.

Implementation:
- [app/tool_loop.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/tool_loop.py)

Related prompt policy:
- [app/prompt_builder.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/prompt_builder.py)

Tests:
- `test_web_evidence_guard_requires_fetch_after_search_for_web_facts_prompt`
- `test_web_evidence_guard_allows_final_answer_after_fetch`
  in [test_guardrail_runtime.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/testing/test_guardrail_runtime.py)


### 2. Graph Write Guard

Type:
- `Runtime enforcement`

Purpose:
- Prevent the model from claiming graph connections were added when no graph write tool was called.

Trigger:
- The user prompt looks like a graph write request.
- No `graph_connection_*` tool has been called yet.
- The model tries to finalize without performing the write.

Effect:
- If parseable triples are present, the loop synthesizes a `graph_connection_create_many` tool call.
- Otherwise it injects a correction telling the model it must perform the graph write.
- If this still fails, the run is stopped instead of accepting a false success answer.

Implementation:
- [app/tool_loop.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/tool_loop.py)


### 3. Task Plan Phase Guard

Type:
- `Runtime enforcement`

Purpose:
- Restrict tool usage to the current task-plan phase.

Trigger:
- Phase enforcement is active.
- The model requests a tool that is outside the active phase allow-list.

Effect:
- The tool call is converted into an error result with `[PLAN_GUARD]`.
- The model is told that the tool is outside the current phase.

Implementation:
- Phase planning in [app/orchestration.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/orchestration.py)
- Enforcement in [app/tool_loop.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/tool_loop.py)


### 4. Tool Recovery Guard

Type:
- `Runtime enforcement`

Purpose:
- Recover when the model asks for a wrong, inactive, misspelled, or ambiguous tool.

Trigger:
- A tool execution fails because the requested tool is not usable in the current runtime.

Effect:
- Classifies the problem:
  - inactive known tool
  - corrected active tool
  - corrected inactive tool
  - ambiguous name
  - unknown name
- Injects a correction telling the model exactly what to do next.
- In some cases, auto-activates the needed tool.
- Blocks final answer while recovery is pending.

Implementation:
- [app/tool_loop.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/tool_loop.py)
- Related tool selection logic:
  [README_TOOL_SELECTOR.md](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/README_TOOL_SELECTOR.md)


### 5. Missing Selected Tool Correction

Type:
- `Runtime enforcement`

Purpose:
- Handle the case where a previously selected tool is no longer present in the runtime inventory.

Trigger:
- The current runtime reports `missing_selected` tools.

Effect:
- Injects a correction explaining that the tool disappeared and another tool must be chosen.

Implementation:
- [app/tool_loop.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/tool_loop.py)


### 6. Duplicate Tool Call Correction

Type:
- `Runtime enforcement`

Purpose:
- Stop the model from repeating the exact same tool call round after round.

Trigger:
- The current round requests the same tool calls with the same arguments as the previous round.

Effect:
- Injects a correction telling the model the result will not change.
- Forces it to answer, choose a different tool, or try a genuinely different query.

Implementation:
- [app/tool_loop.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/tool_loop.py)


### 7. Raw JSON Tool Call Recovery

Type:
- `Runtime enforcement`

Purpose:
- Recover when the model prints a raw JSON tool call instead of using the native tool-call mechanism.

Trigger:
- The assistant response is a bare JSON object like:
  - `{"tool": "...", "arguments": {...}}`
  - `{"name": "...", "arguments": {...}}`

Effect:
- The loop synthesizes a real tool call from that JSON and continues execution normally.

Implementation:
- [app/tool_loop.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/tool_loop.py)


### 8. Malformed Tool Call JSON Correction

Type:
- `Runtime enforcement`

Purpose:
- Recover when the model emits malformed JSON arguments for a tool call.

Trigger:
- Tool-call argument parsing fails.

Effect:
- Injects a correction telling the model not to embed large multi-line content directly in tool arguments.
- Pushes it toward scratchpad-based or file-based workflows instead.

Implementation:
- [app/tool_loop.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/tool_loop.py)


### 9. Terminal Policy and Sandbox Denials

Type:
- `Runtime enforcement`

Purpose:
- Prevent the model from retrying forbidden filesystem or sandbox actions after a hard denial.

Trigger:
- Tool error text indicates:
  - policy boundary / disallowed path
  - sandbox-blocked import or file operation

Effect:
- Appends a terminal marker into the tool result:
  - `[TERMINAL_POLICY_DENIAL]`
  - `[TERMINAL_SANDBOX_DENIAL]`
- The model is explicitly told not to keep probing or searching for a workaround.

Implementation:
- [app/tool_loop.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/tool_loop.py)


### 10. Stop-Run Guard

Type:
- `Runtime enforcement`

Purpose:
- Allow the active run to halt cleanly when `/stoprun` is issued.

Trigger:
- Per-session stop event is set.

Effect:
- The loop stops before the next round.
- Returns a partial-stop message instead of continuing work.

Implementation:
- Registration and stop state in [app/orchestration.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/orchestration.py)
- Check in [app/tool_loop.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/tool_loop.py)


### 11. Web Skills Exposure Guard

Type:
- `Runtime exposure guard`

Purpose:
- Remove KoreLiveWeb-backed tools from the model's visible tool surface when `Web On` is disabled.

Trigger:
- Web skills setting is off.

Effect:
- Web tools are filtered out of:
  - the local payload
  - MCP definitions and index
  - the skills catalog view
  - the selected active tools set

Implementation:
- [app/orchestration.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/orchestration.py)
- [app/input_layer/server.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/input_layer/server.py)
- [app/web_tools_state.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/web_tools_state.py)


### 12. Prompt-Level Retrieval Priority Rules

Type:
- `Prompt policy`

Purpose:
- Tell the model that retrieved evidence outranks internal memory.

Current rules include:
- retrieved content has higher precedence than internal knowledge
- do not contradict or dilute retrieved evidence with memory
- if retrieval is incomplete, prefer another targeted retrieval
- search snippets are discovery aids, not authoritative evidence

Implementation:
- [app/prompt_builder.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/prompt_builder.py)

Important note:
- These rules help, but they are not sufficient on their own.
- The `web-evidence guard` exists because prompt steering alone was not reliable enough.


### 13. Prompt-Level Mandatory Research Routing

Type:
- `Prompt policy`

Purpose:
- Force `research_traverse` for explicit research-style prompts.

Trigger words include:
- `research`
- `investigate`
- `look into`
- `find evidence`
- `deep dive into`

Effect:
- The system prompt tells the model it must call `research_traverse` for those prompts.

Implementation:
- [app/prompt_builder.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/prompt_builder.py)


## Notable Non-Guardrail Mechanisms

These are important, but they are not guardrails in the strict sense:

### Task Planning

Task planning in [app/orchestration.py](/abs/path/C:/Util/GithubRepos/KoreStack/KoreAgent/app/orchestration.py) helps choose and phase work.

This is upstream of some guardrails, but planning itself is not a guardrail.
The guardrail part begins when the runtime refuses an invalid step.


### Tool Selection

Tool selection is the control surface that lets the model activate more tools when needed.

This is a capability-management layer, not itself a guardrail, though several guardrails depend on it for recovery.


### Scratchpad

Scratchpad is a storage mechanism.

It supports some guardrails by preventing oversized or malformed tool usage patterns, but scratchpad itself is not a guardrail.


## How To Read Logs

The easiest way to spot guardrails in run logs is to search for these strings:

- `[web evidence guard]`
- `graph-write guard`
- `[PLAN_GUARD]`
- `[tool recovery correction]`
- `[tool recovery reminder]`
- `[missing tool correction]`
- `[duplicate tool-call correction]`
- `[TERMINAL_POLICY_DENIAL]`
- `[TERMINAL_SANDBOX_DENIAL]`


## Practical Rule Of Thumb

If a behavior is only described in `prompt_builder.py`, it is advisory.

If a behavior is enforced in `tool_loop.py`, it is a real runtime guardrail.

If there is a `test_guardrail_*` test for it, it is part of the intended contract.


## Recommended Next Improvements

Areas that would benefit from more explicit guardrails:

- stronger multi-source requirements for `latest/current/recent` answers
- better guardrails for source attribution when web or KoreData results are used
- guardrails that distinguish typo-resolution from true-topic retrieval
- more explicit guards around dataset completeness and faithful export flows

