# KoreDataGateway

The single point of contact for agents and web UIs to interface with all KoreData services.

## Purpose
Provide a unified API gateway that routes requests to KoreFeed, KoreReference, and KoreLibrary.
Handles content addition, management, and search across all services.

## What it does

- Aggregates search and retrieval across enabled KoreData child services
- Provides the agent-facing entry point for cross-domain data lookup
- Proxies or coordinates service-owned data operations behind one URL surface
- Hosts the KoreData MCP-facing tool boundary where configured

## How to run it

Normally this starts as part of `python .\main.py` or `python .\KoreData\main.py`.

## Troubleshooting

| Problem | What to check |
|---|---|
| Gateway responds but a domain is empty | Check the child service for that domain and whether it has ingested data |
| Aggregated search is incomplete | Confirm the relevant domain service is enabled and reachable |
| Agent tools cannot reach KoreData | Verify the configured gateway URL and `/mcp` path |

## Status
Implemented — the gateway exposes search/read tools plus a narrow KoreLibrary edit surface for book metadata/body updates and anchor repair.
