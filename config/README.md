# config

Top-level KoreStack configuration for shared paths, ports, service URLs, and MCP endpoints.

## Files

### `korestack_config.json` - suite config

Contains the shared KoreStack configuration for paths, ports, service URLs, and MCP
endpoints. This is the single authoritative suite config file.

### `llm_config.json` - LLM bootstrap

Holds the active model name, context window size, and LLM host URL used by KoreAgent at
startup. This is read before the main suite config so the agent can initialise its LLM
connection independently of the rest of the stack.
