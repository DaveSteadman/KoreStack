from __future__ import annotations

import math
from typing import Any


def _service_not_ready_error() -> dict:
    return {"error": "KoreDataGateway is still starting up — retry in a moment"}


async def find_library_book(
    client,
    *,
    title: str,
    chunk_size: int,
) -> dict:
    if client is None:
        return _service_not_ready_error()
    response = await client.get("/search", params={"title": title, "limit": 20}, timeout=10.0)
    if response.status_code != 200:
        return {"error": f"HTTP {response.status_code}"}
    books = response.json()
    if not isinstance(books, list):
        books = books.get("value", [])

    query_lower = title.lower()

    def _rank(book: dict) -> int:
        book_title = (book.get("title") or "").lower()
        if book_title == query_lower:
            return 0
        if book_title.startswith(query_lower):
            return 1
        return 2

    books.sort(key=_rank)
    return {
        "count": len(books),
        "matches": [
            {
                "book_id":    book.get("route_id") or f"{book.get('catalog')}:{book.get('id')}",
                "title":      book.get("title"),
                "author":     book.get("author"),
                "year":       book.get("year"),
                "genre":      book.get("genre"),
                "word_count": book.get("word_count"),
                "chunks":     math.ceil((book.get("word_count") or 0) * 5 / chunk_size) or None,
            }
            for book in books
        ],
    }


async def get_library_index(
    client,
    *,
    chunk_size: int,
) -> dict:
    if client is None:
        return _service_not_ready_error()
    response = await client.get("/books", params={"limit": 200, "offset": 0}, timeout=15.0)
    if response.status_code != 200:
        return {"error": f"HTTP {response.status_code}"}
    data  = response.json()
    books = data if isinstance(data, list) else data.get("value", [])
    return {
        "count": len(books),
        "books": [
            {
                "book_id":    book.get("route_id") or f"local:{book.get('id')}",
                "title":      book.get("title"),
                "author":     book.get("author"),
                "year":       book.get("year"),
                "catalog":    book.get("catalog"),
                "genre":      book.get("genre"),
                "word_count": book.get("word_count"),
                "chunks":     math.ceil((book.get("word_count") or 0) * 5 / chunk_size) or None,
            }
            for book in books
        ],
    }


async def get_library_book_chunk(
    client,
    *,
    book_id: str,
    offset_chars: int,
    length_chars: int,
) -> dict:
    if client is None:
        return _service_not_ready_error()
    length_chars = max(100, min(length_chars, 16000))
    offset_chars = max(0, offset_chars)
    response = await client.get(
        f"/books/{book_id}/chunk",
        params={"offset": offset_chars, "length": length_chars},
        timeout=15.0,
    )
    if response.status_code == 404:
        return {"error": f"Library book not found: id={book_id}"}
    if response.status_code != 200:
        return {"error": f"HTTP {response.status_code}"}
    data = response.json()
    if offset_chars > 0:
        return {
            "chunk":        data.get("chunk"),
            "offset_chars": data.get("offset_chars"),
            "next_offset":  data.get("next_offset"),
            "total_chars":  data.get("total_chars"),
            "has_more":     data.get("has_more"),
        }
    return data


async def search_library(
    client,
    *,
    query: str,
    limit: int,
    search_mode: str,
    min_match: float,
    cfg: dict[str, Any],
    build_artifact_ref,
) -> dict:
    if search_mode == "semantic":
        params: dict[str, Any] = {"q": query, "limit": limit, "min_match": min_match}
        response = await client.get("/api/semantic-search", params=params, timeout=10.0)
    else:
        params = {"q": query, "limit": limit}
        response = await client.get("/api/search", params=params, timeout=10.0)
    if search_mode == "semantic" and response.status_code == 503:
        detail = ""
        try:
            detail = str((response.json() or {}).get("detail") or "")
        except Exception:
            detail = ""
        warning = detail or "Semantic search unavailable."
        return {"status": "partial", "results": [], "error": "", "warnings": [f"Library semantic search unavailable: {warning}"]}
    if response.status_code != 200:
        return {"status": "error", "results": [], "error": f"HTTP {response.status_code}", "warnings": []}
    payload = response.json() or []
    if not isinstance(payload, list):
        return {"status": "error", "results": [], "error": "Library search returned a non-list payload.", "warnings": []}
    if search_mode == "semantic":
        return {
            "status": "ok",
            "results": [
                {
                    "domain":           "library",
                    "type":             "library_book",
                    "artifact_ref":     build_artifact_ref("library_book", book_id=book.get("route_id") or book.get("id")),
                    "id":               book.get("route_id") or book.get("id"),
                    "local_id":         book.get("id"),
                    "title":            book.get("title", ""),
                    "author":           book.get("author", ""),
                    "language":         book.get("language", ""),
                    "genre":            book.get("genre", ""),
                    "year":             book.get("year"),
                    "snippet":          book.get("snippet") or "",
                    "url":              f"{cfg['korelibrary_url']}/ui/library/{book.get('route_id') or book.get('id')}",
                    "sentence_id":      book.get("sentence_id"),
                    "sentence_locator": book.get("sentence_locator") or "",
                    "match_score":      book.get("match_score"),
                }
                for book in payload[:limit]
            ],
            "error":    "",
            "warnings": [],
        }
    return {
        "status": "ok",
        "results": [
            {
                "domain":       "library",
                "type":         "library_book",
                "artifact_ref": build_artifact_ref("library_book", book_id=book.get("route_id") or book.get("id")),
                "id":           book.get("route_id") or book.get("id"),
                "local_id":     book.get("id"),
                "catalog":      book.get("catalog"),
                "route_id":     book.get("route_id") or book.get("id"),
                "title":        book.get("title", ""),
                "author":       book.get("author", ""),
                "snippet":      book.get("snippet") or (book.get("notes") or "")[:300],
                "url":          f"{cfg['korelibrary_url']}/ui/library/{book.get('route_id') or book.get('id')}",
                "score":        book.get("score"),
            }
            for book in payload[:limit]
        ],
        "error":    "",
        "warnings": [],
    }
