# Tool Selection

Select named Skills to make all of their tools available to the current conversation. Skills are
groupings only: after selection, their tools enter the normal active FIFO list and may later age out
independently.

## Interface

- Module: `KoreAgent/app/system_skills/ToolSelection/tool_selection_skill.py`
- Functions:
  - `skills_list()` — list exact Skill names and tool counts.
  - `select_skills(skill_names: list[str])` — add every tool belonging to each named Skill.
  - `tools_catalog_list()` — list exact individual tool names.
  - `tools_active_add(tool_names: list[str])` — add individual tools when needed.

Use `skills_list()` followed by `select_skills(...)` as the normal route. System tools are already
active and are not listed as selectable Skills. Use direct tool activation only when selecting the
whole Skill would be inappropriate.
