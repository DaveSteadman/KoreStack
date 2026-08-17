# ToolSelection Skill

## Purpose
- Inspect the full runtime tool catalog and activate additional tools for the current conversation without exposing the entire tool surface in every prompt.
- Use this when the currently active tool set is too small for the task and you need to pull in a specific tool from the larger local or MCP inventory.
- This is the control-plane skill for prompt-surface throttling. Prefer it over guessing tool names or asking for unavailable tools directly.

## Trigger keyword: tools

## Interface
- Module: `KoreAgent/app/system_skills/ToolSelection/tool_selection_skill.py`
- Functions:
  - `tools_catalog_list(filter_text: str = "", max_items: int = 100, include_mcp: bool = True)`
  - `tools_active_add(tool_names: list[str])`
  - `toolsets_list()`
  - `toolsets_activate(set_names: list[str])`

## Parameters

### `tools_catalog_list(filter_text, max_items, include_mcp)`
- `filter_text` *(optional, default "")* - case-insensitive substring filter applied to tool name and short description.
- `max_items` *(optional, default 100)* - maximum number of catalog entries to return, clamped to `1-200`.
- `include_mcp` *(optional, default true)* - when `true`, include currently connected MCP tools in the catalog output.

### `tools_active_add(tool_names)`
- `tool_names` *(required)* - list of exact tool names to add to the active FIFO working set for the current conversation.

### `toolsets_list()`
- Lists the persisted named tool sets whose members are currently available.

### `toolsets_activate(set_names)`
- `set_names` *(required)* - list of named tool sets to activate. Their available members are appended to the current FIFO working set.

## Output
- `tools_catalog_list(...)` - returns a list of tool records with name, origin, availability, role, trust boundary, short description, and whether the tool is currently active.
- `tools_active_add(...)` - returns a dict describing which tools were added, promoted, unknown, or evicted, plus the updated active-tool list.
- `toolsets_list()` - returns the currently available named tool sets, their member tools, and already-active members.
- `toolsets_activate(...)` - returns selected sets, unavailable members, evictions, and the updated active-tool list.

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
1. Call `toolsets_list()` first and activate a suitable named set with `toolsets_activate([...])`.
2. If no set fits, call `tools_catalog_list(...)` to inspect the larger catalog.
3. Call `tools_active_add([...])` with the exact individual tool names you need.
4. Then use those newly active tools in the next round.

Do not guess tool names from memory when `tools_catalog_list(...)` can verify them.
