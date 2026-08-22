# config

Top-level KoreStack configuration for shared paths, ports, service URLs, and MCP endpoints.

## Files

### `korestack_config.json` - suite config

Contains the shared KoreStack configuration for paths, ports, service URLs, and MCP
endpoints. This is the single authoritative suite config file.

### `koreagent_config.json` - KoreAgent bootstrap

Holds the active model name, context window size, and LLM host URL used by KoreAgent at
startup. This is read before the main suite config so the agent can initialise its LLM
connection independently of the rest of the stack.

### `koreliveweb_config.json` - KoreLiveWeb provider settings

Holds the live web provider selection and provider-specific settings used by KoreLiveWeb,
including the preferred search provider, enabled providers, hosted search endpoint, and
optional API key.

