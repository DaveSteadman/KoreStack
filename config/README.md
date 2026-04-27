# config

Top-level KoreStack configuration for shared paths, ports, service URLs, and MCP endpoints.

This folder is the first-pass replacement for using a single subsystem config file as the
de facto source of truth for the entire suite.

Current status:

- `default.json` defines the intended KoreStack-managed suite contract.
- individual services still retain their own local config and may not consume this file
  directly yet.
- KoreStack reads this file first and passes resolved
  values down during startup.