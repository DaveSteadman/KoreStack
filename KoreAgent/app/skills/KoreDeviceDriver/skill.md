# KoreDeviceDriver Skill

## Purpose
Create, inspect, edit, generate, and run `KoreDeviceDriver` entries through the KoreDeviceDriver service. Use this when the user wants a reusable driver entry with executable Python code rather than a one-off answer in chat.

## Trigger keyword: device driver, driver code, hardware adapter, or KoreDeviceDriver entry

## Interface
- Module: `KoreAgent/app/skills/KoreDeviceDriver/device_driver_skill.py`
- Functions:
  - `device_driver_list()`
  - `device_driver_get(name: str)`
  - `device_driver_create(name: str, display_name: str = "", vendor: str = "", protocol: str = "", transport_address: str = "", poll_interval_sec: int = 0, enabled: bool = False, description: str = "", python_snippet: str = "")`
  - `device_driver_update(name: str, display_name: str = "", vendor: str = "", protocol: str = "", transport_address: str = "", poll_interval_sec: int = 0, enabled: bool = False, description: str = "", python_snippet: str = "")`
  - `device_driver_set_code(name: str, python_snippet: str)`
  - `device_driver_run(name: str, display_name: str = "", vendor: str = "", protocol: str = "", transport_address: str = "", poll_interval_sec: int = 0, enabled: bool = False, description: str = "", python_snippet: str = "")`
  - `device_driver_generate_from_prompt(name: str, prompt: str, display_name: str = "", vendor: str = "", protocol: str = "", transport_address: str = "", poll_interval_sec: int = 0, enabled: bool = False, description: str = "", save: bool = True, run_after_generate: bool = False, max_attempts: int = 3)`

## Parameters

### `device_driver_get(name: str)`
- `name` - exact driver entry name.

### `device_driver_create(name: str, display_name: str = "", vendor: str = "", protocol: str = "", transport_address: str = "", poll_interval_sec: int = 0, enabled: bool = False, description: str = "", python_snippet: str = "")`
- `name` - unique driver entry name.
- `display_name` - operator-facing label for the driver.
- `vendor` - vendor or product family label.
- `protocol` - protocol or adapter type, for example `modbus-tcp` or `local-python`.
- `transport_address` - endpoint, host, or address string for the target device.
- `poll_interval_sec` - polling interval in seconds; use `0` to fall back to the service default.
- `enabled` - whether the driver should be marked enabled.
- `description` - short human description of what the driver does.
- `python_snippet` - Python code that must define `read_driver(context)`.

### `device_driver_update(name: str, display_name: str = "", vendor: str = "", protocol: str = "", transport_address: str = "", poll_interval_sec: int = 0, enabled: bool = False, description: str = "", python_snippet: str = "")`
- `name` - existing driver entry name.
- `display_name` - replacement display label.
- `vendor` - replacement vendor label.
- `protocol` - replacement protocol label.
- `transport_address` - replacement transport address.
- `poll_interval_sec` - replacement polling interval in seconds; use `0` to leave the service default behavior.
- `enabled` - replacement enabled state.
- `description` - replacement description text.
- `python_snippet` - replacement Python code defining `read_driver(context)`.

### `device_driver_set_code(name: str, python_snippet: str)`
- `name` - existing driver entry name.
- `python_snippet` - replacement Python code defining `read_driver(context)`.

### `device_driver_run(name: str, display_name: str = "", vendor: str = "", protocol: str = "", transport_address: str = "", poll_interval_sec: int = 0, enabled: bool = False, description: str = "", python_snippet: str = "")`
- `name` - existing driver entry name to execute.
- `display_name` - optional runtime override for the display label.
- `vendor` - optional runtime override for the vendor label.
- `protocol` - optional runtime override for the protocol label.
- `transport_address` - optional runtime override for the address.
- `poll_interval_sec` - optional runtime override for the polling interval.
- `enabled` - optional runtime override for the enabled flag.
- `description` - optional runtime override for the description.
- `python_snippet` - optional runtime override for the Python snippet. When supplied, the run uses this code even if it is not saved yet.

### `device_driver_generate_from_prompt(name: str, prompt: str, display_name: str = "", vendor: str = "", protocol: str = "", transport_address: str = "", poll_interval_sec: int = 0, enabled: bool = False, description: str = "", save: bool = True, run_after_generate: bool = False, max_attempts: int = 3)`
- `name` - driver entry name to create or update.
- `prompt` - natural-language description of the driver behavior to generate.
- `display_name` - optional display label to save with the entry.
- `vendor` - optional vendor label to save with the entry.
- `protocol` - optional protocol label to save with the entry.
- `transport_address` - optional address to save with the entry.
- `poll_interval_sec` - optional poll interval to save with the entry.
- `enabled` - whether the saved entry should be enabled.
- `description` - description text to save; when blank the generation prompt is reused.
- `save` - when true, save the generated code into the driver entry.
- `run_after_generate` - when true and `save` is false, run an unsaved preview of the generated code. When `save` is true, the saved entry is always executed as part of validation.
- `max_attempts` - maximum number of generate-save-run repair cycles before the tool fails.

## Output
- `device_driver_list()` - returns `list[dict]` of known driver entries.
- `device_driver_get(...)` - returns one driver entry dict including `python_snippet`.
- `device_driver_create(...)` - returns the created driver entry dict.
- `device_driver_update(...)` - returns the updated driver entry dict.
- `device_driver_set_code(...)` - returns the updated driver entry dict after replacing the snippet.
- `device_driver_run(...)` - returns a dict including `ok`, `result`, `stdout`, and `error`.
- `device_driver_generate_from_prompt(...)` - returns a dict including `generated_code`, whether it was saved, `run_result`, whether the saved entry validated, and the attempt count. On repeated failure it raises an error instead of pretending the driver is usable.

## Triggers
Invoke this skill when the prompt contains any of these concepts or phrases:
- `create a device driver`, `write a driver`, `make a driver entry`
- `edit this driver`, `update the driver code`, `change the device driver`
- `run the driver`, `execute the driver snippet`, `test this driver`
- `generate a driver from this prompt`, `write a KoreDeviceDriver entry for`

## Tool selection guidance
Use this skill when the user wants reusable driver code stored in `KoreDeviceDriver`.

Preferred flow for natural-language creation requests:
1. `device_driver_generate_from_prompt(...)`
2. Trust the result only if the saved entry validated by running through the service with no runtime snippet override
3. `device_driver_get(...)` only when you need to inspect the saved entry afterward

If the user already provides concrete Python code, skip generation and use:
1. `device_driver_create(...)` or `device_driver_set_code(...)`
2. `device_driver_run(...)` on the saved entry

## Validation rule
Generated driver code is not acceptable merely because it parses or because an unsaved override runs once. A generated `KoreDeviceDriver` entry is only valid when the saved entry executes successfully through the real `KoreDeviceDriver` service path. If the first saved version fails, the skill must repair the code using the observed run failure and retry until it succeeds or the attempt limit is reached.

When the request is only for current machine metrics, prefer `get_system_info_dict()` instead of creating a driver entry. Use `KoreDeviceDriver` only when the user explicitly wants a reusable driver artifact.
