# KoreDataGateway

The single point of contact for agents and web UIs to interface with all KoreData services.

## Purpose
Provide a unified API gateway that routes requests to KoreFeed, KoreReference, and KoreLibrary.
Handles content addition, management, and search across all services.

## What it does

- Starts/stops KoreTex with the other child services and links its preview-only
  text-processing page from the home panel. KoreTex storage/search is not yet enabled.
- Aggregates search and retrieval across enabled KoreData child services
- Provides the agent-facing entry point for cross-domain data lookup
- Proxies or coordinates service-owned data operations behind one URL surface
- Hosts the KoreData MCP-facing tool boundary where configured


## Role in the suite

Port 29603. KoreStack starts it with the other KoreData services. KoreAgent reaches it through `/mcp`, and the browser UI offers cross-domain search with persistent named SavedSearches.

The gateway's **Uptime** page stores its monitored URL in `Uptime/settings.csv` and one HTTP result per UTC hour in `Uptime/hourly.csv`, beneath the normal KoreData data directory. It treats HTTP 2xx and 3xx responses as available.
The hourly grid shows today and the previous six UTC calendar days, advancing one
day at midnight rather than replacing whole weeks. The daily grid shows the current
month and previous eleven months, oldest first, with month/year labels and 31 day
columns. Nonexistent dates are blank; future dates in the current month are grey.
At each UTC month boundary, the oldest month drops out and the new month is added.
Both windows are calculated when the page loads; reload to see updated results.
The URL field and Save URL button share a compact row, stacking on narrow screens.
Saving temporarily disables both controls and announces progress or errors without
discarding the entered URL. An empty URL disables monitoring.

## Troubleshooting

| Problem | What to check |
|---|---|
| Gateway responds but a domain is empty | Check the child service for that domain and whether it has ingested data |
| Aggregated search is incomplete | Confirm the relevant domain service is enabled and reachable |
| Agent tools cannot reach KoreData | Verify the configured gateway URL and `/mcp` path |

## Status
Implemented — the gateway exposes search/read tools plus a narrow KoreLibrary edit surface for book metadata/body updates and anchor repair.
