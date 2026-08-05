# KoreDocs

KoreDocs is the suite document subsystem. It provides browser-based document, spreadsheet, and diagram editing on top of the shared suite filesystem, and it also exposes MCP tools for structured editing.

## Why it exists

KoreStack needs native tools for prose, tables, and diagrams that remain local, inspectable, and easy for agents to work with. KoreDocs provides that layer without introducing a separate storage backend.

## What it includes

| App | Purpose | File type |
|---|---|---|
| `KoreDoc` | Markdown-oriented rich document editing | `.koredoc` |
| `KoreSheet` | Sparse-cell spreadsheets and model sheets | `.koresheet` |
| `KoreDiag` | Diagram editing for node and edge layouts | `.korediag` |

## Service contract

KoreDocs serves both browser editing and programmatic document access.

- `/ui`, `/doc`, `/sheet`, and `/diag` are the main browser entry points
- `/api/files`, `/api/folders`, `/api/search`, and `/api/sheets/...` cover the HTTP editing surface
- `/mcp` exposes typed tools for agent access

The live source of truth is the shared filesystem-backed document tree rather than a separate hidden document store.

## Analytical artefacts

Every `.koredoc` is a self-contained artefact with a stable `artifact_id`, a
delimited JSON header, and an immutable revision history. The header holds the
JSON metadata object and is separate from the Markdown body, so structured
pipeline fields remain reliable when an agent edits the body. KoreSheet and
KoreDiag retain metadata in their own JSON documents.

Use a consistent metadata shape for generated analysis, for example:

```json
{
  "artefact_type": "market_analysis",
  "geography": {"country": "GB"},
  "period": {"year": 2026},
  "status": "reviewed",
  "producer": {"service": "KoreAgent", "prompt_version": "market-v2"},
  "source_refs": [{"kind": "dataset", "dataset_id": "companies-q2"}],
  "input_refs": ["artefact-uuid-here"]
}
```

`POST /api/search/metadata` and `koredocs_files_metadata_search` accept exact
field matches, dotted paths, `contains`, `in`, `exists`, and `gt`/`gte`/`lt`/`lte`
operators, plus `$and`, `$or`, and `$not`. File history is available through
`/api/files/{id}/history` and the corresponding MCP tools.

## How to run it

Normally you start KoreDocs through the suite root:

```powershell
python .\main.py
```

To run only KoreDocs:

```powershell
python .\KoreDocs\main.py
```

KoreDocs serves both the browser UI and the MCP endpoint from the same process.

## Install and configuration

- Install shared dependencies from the repo root with `pip install -r requirements.txt`
- KoreDocs reads suite-level paths from `config/korestack_config.json`
- The live source of truth is the shared `datauser` tree under the configured data root
- Shared UI assets are served from `KoreUI/UIElements/`; keep that folder present when running browser apps

## New user notes

- Start with the UI if you want to inspect files and formats interactively
- Use the MCP endpoint if you want agent tooling to create or edit documents safely
- Treat legacy `korefile.db` content as migration-only; current writes go to the real filesystem

## File formats

- `.koredoc` is markdown-first document content
- `.koresheet` is sparse-cell structured sheet content
- `.korediag` is structured node-and-edge diagram content

## Troubleshooting

| Problem | What to check |
|---|---|
| UI loads but shared chrome is missing | Confirm `KoreUI/UIElements/` assets are available and mounted correctly |
| Files do not appear where expected | Verify the configured `datauser` path under the suite data root |
| MCP clients cannot connect | Check the configured `/mcp` endpoint and whether KoreDocs is running in HTTP or stdio mode |
| Old data is missing | Confirm you are looking at the live filesystem-backed store, not the legacy migration database |
