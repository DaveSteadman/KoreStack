# KoreData

KoreData is the suite data layer. It presents a unified search and retrieval surface over several local data services so agents and browser tools can query one place instead of integrating each source independently.

## Why it exists

KoreStack needs local, inspectable data sources for research and retrieval. KoreData groups those sources behind a gateway and keeps the storage, scraping, indexing, and retrieval logic inside one subsystem.

## What it includes

| Component | Role | README |
|---|---|---|
| `KoreDataGateway/` | Main gateway and MCP-facing aggregation layer | [KoreDataGateway/README.md](KoreDataGateway/README.md) |
| `KoreFeed/` | RSS ingestion, extraction, storage, and search | [KoreFeed/README.md](KoreFeed/README.md) |
| `KoreLibrary/` | Long-form local library and document corpus | [KoreLibrary/README.md](KoreLibrary/README.md) |
| `KoreReference/` | Reference and encyclopedia-style content service | [KoreReference/README.md](KoreReference/README.md) |
| `KoreGraph/` | Graph-oriented concept connectivity and search expansion | [KoreGraph/README.md](KoreGraph/README.md) |
| `KoreRAG/` | Retrieval-augmented generation support and chunk storage | code and design status only |

## How to run it

Normally you start KoreData through the suite root:

```powershell
python .\main.py
```

To run only the data stack:

```powershell
python .\KoreData\main.py
```

The gateway starts the child services it owns and exposes the primary browser and API entry points.

## Install and configuration

- Install shared dependencies from the repo root with `pip install -r requirements.txt`
- Ports, host binding, and enabled child services are defined in `config/korestack_config.json`
- Shared data paths resolve through `KoreCommon/suite_paths.py`
- Feed, library, reference, and graph data live under the configured suite data root, not necessarily inside the repo checkout

## Planned data shape

When new data sources are added, they should usually extend existing KoreData storage patterns instead of creating a new top-level service.

- live and frequently refreshed web sources belong closer to the feed and web-retrieval path
- long-form static text belongs closer to library storage
- encyclopedic linked content belongs closer to reference storage
- niche but queryable corpora can live as named databases under the RAG/chunk-store model when that keeps the service boundary simpler

One concrete planned example is Hansard-style parliamentary data: the intended direction is a curated named database inside the broader KoreData/RAG model, not a standalone KoreHansard service.

## First places to look

- Gateway overview: [KoreDataGateway/README.md](KoreDataGateway/README.md)
- Feed service details: [KoreFeed/README.md](KoreFeed/README.md)
- Graph expansion details: [KoreGraph/README.md](KoreGraph/README.md)

## Troubleshooting

| Problem | What to check |
|---|---|
| Gateway starts but child services do not respond | Verify the configured service ports are free and enabled |
| Search returns no results | Confirm the underlying service has ingested or imported data |
| Data appears to be missing after restart | Check that the configured data root points to the expected persistent location |
| MCP or cross-service calls fail | Confirm the gateway URL and `/mcp` endpoint wiring in `config/korestack_config.json` |

