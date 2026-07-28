# KoreLibrary

Long-form static and unlinked data — a Project Gutenberg-style ebook store.

## Purpose
Store and serve full-text documents such as ebooks, papers, and other long-form content.
Content is considered static and standalone (no inter-document links required).

## What it does

- Stores long-form text corpora and related metadata
- Serves search and retrieval for book-scale or document-scale content
- Fits data that is durable and standalone rather than link-dense or constantly refreshed

## How it fits the suite

KoreLibrary is the KoreData home for books, papers, manuals, and similar long-form sources that should remain queryable by agents and browser tools.

## Troubleshooting

| Problem | What to check |
|---|---|
| Searches return nothing | Confirm content has actually been imported into the active library store |
| Content seems to be in the wrong place | Verify the shared suite data root and the library-specific storage path |

## Status
In development.
