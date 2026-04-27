# <Skill Name> Skill

## Purpose
<1-3 sentences: what this skill covers and when to reach for it. Include any "prefer this over X"
or "do not use for Y" guidance so the LLM routes correctly.>

## Trigger keyword: <keyword or short phrase describing when to invoke this skill>

## Interface
- Module: `code/skills/<Folder>/<module_name>.py`
- Functions:
  - `function_one(param: type, param: type = default)`
  - `function_two(param: type)`

## Parameters

### `function_one(param1, param2)`
- `param1` *(required)* - description. Include valid values or format constraints if applicable.
- `param2` *(required)* - description.

### `function_two(param1, param2 = default)`
- `param1` *(required)* - description.
- `param2` *(optional, default X)* - description.

### `no_arg_function()`
No parameters.

## Output
- `function_one(...)` - description of return type and structure.
- `function_two(...)` - description.
- `no_arg_function()` - description.

## Triggers
Invoke this skill when the prompt contains any of these concepts or phrases:
- `phrase or concept`
- `another phrase`

## Routing metadata
Optional fields that sharpen how the catalog router selects this skill.
Include only the fields that are relevant; omit any that do not apply.

- `routing_priority` - integer (default 0). Higher values make the router prefer this skill
  when multiple candidates score equally. Use for skills that should almost always win a tie
  (e.g. a dedicated weather skill should beat a generic web-fetch skill on weather queries).

- `requires_lookup` - `true` | `false` (default false). Set to `true` when the skill must
  resolve a dynamic value (API key, URL, file path) before it can run. The router uses this
  to surface lookup-dependent skills only when the lookup can actually succeed.

- `triggers_exact` - list of verbatim phrases that, if present in the prompt, are a definitive
  signal to invoke this skill (not just soft evidence). Use sparingly. Example:
  `["run_code", "execute_code"]`.

- `url_filter` - regex pattern. When set, the router only considers this skill for prompts
  that contain a URL matching the pattern. Useful for narrowing broad web skills to a specific
  domain. Example: `"wikipedia\\.org"`.

## Scratchpad integration
<Describe whether and how this skill interacts with the scratchpad.
If outputs are large or reused in downstream steps, show the park-then-reference pattern.
If the skill is not a scratchpad candidate, state that clearly and explain why
(e.g. output is a small struct, runs in a subprocess, or is the scratchpad itself).>

## Examples
- `function_one("arg1", "arg2")` - plain English description of what this achieves
  - Returns: `"expected output string"`
- `function_two("arg")` - description of this variant or edge case
  - Returns: `"expected output string"`
