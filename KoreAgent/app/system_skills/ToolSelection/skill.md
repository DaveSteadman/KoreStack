# ToolSelection Skill

## Purpose
- Inspect the full runtime tool catalog and activate additional tools for the current conversation without exposing the entire tool surface in every prompt.
- Use this when the currently active tool set is too small for the task and you need to pull in a specific tool from the larger local and registered-service inventory.
- This is the control-plane skill for prompt-surface throttling. Prefer it over guessing tool names or asking for unavailable tools directly.

## Trigger keyword: tools

## Interface
- Module: `KoreAgent/app/system_skills/ToolSelection/tool_selection_skill.py`
- Functions:
  - `tools_keywords_list()`
  - `select_tools_by_keyword(keywords: list[str])`
  - `tools_catalog_list()`
  - `tools_active_add(tool_names: list[str])`

## Parameters

### `tools_keywords_list()`
- Lists every reviewed keyword tag in a compact response guaranteed to fit in the model tool-result budget.
- Local tags are maintained in `tool_keywords.json`; registered-service tags come from the live SkillManager registry. This does not inspect or infer keywords from the prompt.

### `select_tools_by_keyword(keywords)`
- `keywords` *(required)* - exact reviewed keyword tags, for example `["file_handling"]`, `["spreadsheet"]`, or `["dataset"]`.
- Activates the union of local tools assigned to the supplied tags. Unknown tags are reported without guessing a match.

### `tools_catalog_list()`
- Returns the complete list of locally available tools. There is no filter, ranking, cap, or MCP lookup.

### `tools_active_add(tool_names)`
- `tool_names` *(required)* - list of exact tool names to add to the active FIFO working set for the current conversation.

## Output
- `tools_catalog_list()` - returns all local and registered exact tool names. Activate one to receive its full schema.
- `tools_keywords_list()` - returns every exact reviewed capability tag and the activation instruction.
- `select_tools_by_keyword(...)` - returns matched and unknown tags, activated tools, each tool's reviewed selection description and parameter names, evictions, and the updated active-tool list.
- `tools_active_add(...)` - returns a dict describing which exact names were added, promoted, unknown, or evicted, plus the updated active-tool list.

## Triggers
Invoke this skill when the prompt contains any of these concepts or phrases:
- `show available tools`
- `list tools`
- `what tools do we have`
- `find the right tool`
- `activate a tool`
- `add this tool`
- `tool catalog`
- `selected tools`

## Tool selection guidance
Use this skill whenever the current active tool set does not contain the capability you need.

Workflow:
1. Call `tools_keywords_list()` if you need to see the reviewed capability tags.
2. Call `select_tools_by_keyword([...])` when one or more reviewed tags describe the capability required. This is exact tag matching, not prompt keyword matching.
3. Use `tools_catalog_list()` when you need a specific named tool or no reviewed tag fits.
4. Call `tools_active_add([...])` with exact individual names only when keyword selection is too broad or needs supplementing.

Do not guess tool names from memory when `tools_catalog_list()` can verify them.
