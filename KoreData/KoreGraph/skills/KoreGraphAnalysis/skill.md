# KoreGraphAnalysis Skill

## Purpose

Extract factual connections from a KoreLibrary book and store them in KoreGraph.

## Workflow

1. Call `koredata_find_library_book(title)` and select its `book_id`.
2. Call `graph_connection_import_library_book(book_id)` once. It reads every
   book chunk server-side, extracts supported factual relations, deduplicates
   them, and submits them in a batch.
3. Report the returned `book_id`, `chunks_processed`, `unique_connections`,
   and `submitted` values.

The import tool fails if a chunk cannot be fetched, pagination does not
advance, the maximum chunk count is exceeded, or any graph write is rejected.
Do not install packages, run Python snippets, or claim a completed import
after a tool failure.

## Triggers

- `read a book and add connections`
- `extract connections from book`
- `library book to KoreGraph`
- `graph connections from science book`
