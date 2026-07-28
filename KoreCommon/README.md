# KoreCommon

KoreCommon is the shared support package used across the suite. It is not a standalone service. It exists so the runnable subsystems can reuse the same path resolution, config loading, service helpers, and utility code.

## Why it exists

Without KoreCommon, each subsystem would duplicate the same suite-level plumbing and drift out of sync. This package keeps cross-service conventions in one place.

## What it includes

- Shared suite path and config helpers
- Logging and service-app utilities
- Common data and indexing helpers
- Shared slash-command support modules where appropriate

## How to use it

There is no direct startup command. KoreCommon is imported by the runnable services.

When troubleshooting a path, config, or shared-service issue, this is often the first place to inspect.

## Troubleshooting

| Problem | What to check |
|---|---|
| Different services disagree on paths | Confirm they resolve the same suite root and data root through `suite_paths.py` |
| A service ignores config changes | Check whether the service reads from the shared config loader or overrides values locally |
| Shared UI or URL wiring is inconsistent | Inspect the common path and service helper modules before patching individual services |

## Related docs

- Root overview: [../README.md](../README.md)