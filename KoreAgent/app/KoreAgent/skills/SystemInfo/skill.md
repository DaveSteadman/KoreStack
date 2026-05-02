# SystemInfo Skill

## Purpose
Provide runtime system information including OS name, Python and Ollama versions, RAM usage, and disk usage. Use this for any prompt about the machine, hardware, runtime environment, available resources, or version details. Do not use this for web or file queries.

## Trigger keyword: system info, RAM or disk space, available memory, or OS and runtime version details

## Interface
- Module: `code/KoreAgent/skills/SystemInfo/system_info_skill.py`
- Functions:
  - `get_system_info_dict()`

## Parameters

### `get_system_info_dict()`
No parameters.

## Output
- `get_system_info_dict()` - returns a dict with individually addressable fields:
  - `os` (str) - OS name, e.g. `"Windows"`
  - `python_version` (str) - e.g. `"3.10.11"`
  - `ollama_version` (str) - e.g. `"0.18.0"`
  - `ram_used_gb` (float) - RAM in use in GiB
  - `ram_available_gb` (float) - RAM free in GiB
  - `disk_used_gb` (float) - disk used in GiB
  - `disk_available_gb` (float) - disk free in GiB

## Triggers
Invoke this skill when the prompt contains any of these concepts or phrases:
- `system info`, `system health`, `machine info`, `specs`, `resource usage`
- `RAM`, `memory usage`, `available memory`, `how much RAM`
- `disk space`, `free space`, `disk available`, `storage`
- `Python version`, `Ollama version`, `what OS`, `operating system`
- `can we fit`, `do we have enough`, `is there enough space`

## Scratchpad integration
Not typically applicable - output is a small dict.  If system info is one step in a larger
report-building chain (e.g. fetch stats, compute headroom, write to file), park the formatted
summary string with `scratch_save` so it can be included in the final assembled output via
`{scratch:key}` without re-invoking the skill.

## Examples
- `get_system_info_dict()` - retrieve all system metrics
  - Returns: `{"os": "Windows", "python_version": "3.10.11", "ollama_version": "0.18.2", "ram_used_gb": 12.34, "ram_available_gb": 19.66, "disk_used_gb": 110.25, "disk_available_gb": 401.75}`
- "how much RAM is available?" - call `get_system_info_dict()`, read `ram_available_gb`
- "do we have enough disk space to add a 50 GB file?" - call `get_system_info_dict()`, read `disk_available_gb`, compare to 50
- "write system info to a file" - call `get_system_info_dict()`, then pass the result to a FileAccess write call
