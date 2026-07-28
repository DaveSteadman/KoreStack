# KoreGraph

KoreGraph is the concept-connectivity service inside KoreData. It does not try to store whole documents. It stores named relationships between terms so other services can expand a search into a richer neighbourhood of related concepts.

## Why it exists

Some tasks need relationship expansion before document retrieval begins. KoreGraph provides that graph layer so the rest of KoreData can search with better candidate terms.

## What it does

- Stores concept-to-concept connections as graph triples
- Resolves string terms to internal concept identifiers transparently
- Expands a term into connected names for downstream search or analysis
- Exposes a browser, API, and MCP surface for graph lookups and writes

## Service contract

- external callers use string terms, not internal concept ids
- `/api/connections/by-name` is the write path used by agents
- `/api/expand-by-term` is the main expansion path for search broadening
- `/status` is the health probe and `/mcp` is the tool transport

## How it fits the suite

KoreGraph is a KoreData subservice that augments retrieval rather than replacing it. The usual pattern is: term in, connected names out, then document search happens elsewhere.

## Troubleshooting

| Problem | What to check |
|---|---|
| Expansion returns nothing | Confirm the graph has been populated with relations for that term |
| Callers expect numeric ids | Use the string-based API paths; internal ids are not part of the external contract |
| Search expansion looks noisy | Review relation curation, relation state, and score weighting |