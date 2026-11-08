# KoreReference

An encyclopedia of interlinked data — a Wikipedia-style knowledge base.

## Purpose
Store and serve structured, interlinked reference articles. Content is considered long-lived
and relationships between articles are first-class data.

## What it does

- Stores article-scale reference content
- Preserves inter-article relationships as part of the model, not just search text
- Fits encyclopedia or reference-style corpora better than feed or book storage

## How it fits the suite

KoreReference is the KoreData home for linked reference material where navigation and relationship structure matter alongside full-text retrieval.

## Troubleshooting

Imports remain active while resolving article links after fetching finishes.
The progress panel shows **Finalizing** during this step, and **Complete** only
after the worker has released its import slot. Links to articles not yet present
remain unresolved for a later import; they do not prevent completion.

Link resolution visits unresolved links in forward batches, without rescanning
missing targets from earlier batches. The panel reports links checked and resolved.
Stop requests interrupt fetching, pacing, and SQLite link-resolution work; committed
batches are retained and remaining links can be resolved by a later import.
**Stopping** remains active until the worker exits; only then can another import
start. SQLite lock waits remain subject to the database busy timeout. Unexpected
worker failures are shown as **Failed**.

| Problem | What to check |
|---|---|
| Reference searches are empty | Confirm the reference dataset has actually been imported |
| Linked navigation is missing | Check whether the source content includes the expected relationship structure |

## Status
In development.
