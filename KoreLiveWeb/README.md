# KoreLiveWeb

KoreLiveWeb is the suite live-web subsystem. It exposes web search, page fetch, navigation, research, and Wikipedia lookup as an isolated MCP-capable service.

## Why it exists

Current web retrieval has different constraints from local document and data access. KoreLiveWeb keeps those web-specific tools isolated, observable, and configurable without mixing them into the local data services.

## What it does

- Exposes MCP tools for web search, search result summarization, page text fetch, navigation, and research flows
- Provides Wikipedia lookup for reference-style retrieval
- Tracks tool usage and outbound requests through a browser UI
- Supports configurable search providers through suite configuration

## How to run it

Normally you start KoreLiveWeb through the suite root:

```powershell
python .\main.py
```

To run KoreLiveWeb on its own:

```powershell
python .\KoreLiveWeb\main.py
```

## Install and configuration

- Install shared dependencies from the repo root with `pip install -r requirements.txt`
- Review `services.koreliveweb` in `config/korestack_config.json` for port and provider settings
- Review the configured MCP connection in the same file if another subsystem needs to call KoreLiveWeb tools

## New user notes

- Treat KoreLiveWeb as a live external-data tool layer, not a long-term store
- Prefer it for current web lookups and fetched page evidence rather than internal suite data
- If you change providers, confirm the UI reflects the active provider before debugging tool behavior

## Troubleshooting

| Problem | What to check |
|---|---|
| Search tools return nothing | Confirm the selected provider is enabled and reachable |
| MCP clients cannot see the tools | Verify the service is running and the configured `/mcp` path is correct |
| Web UI opens but no activity appears | Generate a test search and confirm requests are being logged |
| Service starts with provider errors | Recheck provider-specific settings in `config/korestack_config.json` |

## Related docs

- `KoreLiveWeb/app/server.py` for the mounted routes and MCP tool registration