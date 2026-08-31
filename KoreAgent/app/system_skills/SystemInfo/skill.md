# SystemInfo Skill

## Purpose

Provide runtime system information including OS name, Python and Ollama versions, RAM usage, and disk usage. Use it for a current machine, hardware, runtime, or resource reading. Do not use it for web or file queries.

## Interface

- Module: `KoreAgent/app/system_skills/SystemInfo/system_info_skill.py`
- Function: `get_system_info_dict()`

## Output

`get_system_info_dict()` returns a structured object with OS and version details plus RAM and disk values. Resource fields are `null` when the platform cannot provide a reliable value.

## When to call it

Static OS, runtime-version, and date information is already in the system prompt. Call this tool only when the user asks for a current RAM or disk reading, or when a fresh runtime check is required.
