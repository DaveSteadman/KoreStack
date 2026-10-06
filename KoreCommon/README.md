# KoreCommon

KoreCommon is the shared support package used across the suite. It is not a standalone service. It exists so the runnable subsystems can reuse the same path resolution, config loading, service helpers, and utility code.

## Why it exists

Without KoreCommon, each subsystem would duplicate the same suite-level plumbing and drift out of sync. This package keeps cross-service conventions in one place.

## What it includes

| Module | Role |
|---|---|
| `suite_paths.py`, `suite_config.py` | Suite root, data root, datacontrol and datauser paths, and the shared config loader |
| `datauser_fs.py`, `datauser_script_runner.py` | User-space file access and script running |
| `service_app.py`, `service_logging.py` | Common FastAPI app setup, `/status`, and logging |
| `endpoint_manifest.py`, `skill_service.py`, `skill_registration.py` | Endpoint and skill manifests that services publish to KoreAgent |
| `stack_watchdog.py` | Supervision helper used by KoreStack |
| `dbutil.py`, `sentence_index.py`, `compress.py` | Database, indexing, and compression helpers |

Because every service imports it, KoreCommon is what makes the separate processes behave as one system: they agree on paths, ports, and the HTTP contract here.

## How to use it

KoreCommon is imported by the services and is never started itself.

When troubleshooting a path, config, or shared-service issue, this is often the first place to inspect.

## Troubleshooting

| Problem | What to check |
|---|---|
| Different services disagree on paths | Confirm they resolve the same suite root and data root through `suite_paths.py` |
| A service ignores config changes | Check whether the service reads from the shared config loader or overrides values locally |
| Shared UI or URL wiring is inconsistent | Inspect the common path and service helper modules before patching individual services |

## Related docs

- Root overview: [../README.md](../README.md)