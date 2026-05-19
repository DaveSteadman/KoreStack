# config

Top-level KoreStack configuration for shared paths, ports, service URLs, and MCP endpoints.

## Files

### `default.json` — factory defaults

Contains every configurable parameter for the suite with its default value.  This file is
the authoritative reference for what can be configured and what each setting means.  It
should not be edited on an installed machine — treat it as read-only factory defaults that
ship with the codebase.

### `local.json` — machine overrides

Contains only the values that differ from `default.json` on a specific installation.
Not every key needs to be present — only the ones being overridden.  At runtime the suite
merges `local.json` on top of `default.json`, so any key absent from `local.json` falls
back to the factory default.

This file is machine-specific and should not be committed to version control.

### `llm_config.json` — LLM bootstrap

Holds the active model name, context window size, and LLM host URL used by KoreAgent at
startup.  This is read before the main config merge so the agent can initialise its LLM
connection independently of the rest of the suite.
