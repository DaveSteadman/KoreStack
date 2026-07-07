from __future__ import annotations

from typing import Any


def _service_not_ready_error() -> dict:
    return {"error": "KoreDataGateway is still starting up — retry in a moment"}


async def get_scrape_chunk(
    client,
    *,
    chunk_id: int,
) -> dict:
    if client is None:
        return _service_not_ready_error()
    response = await client.get(f"/chunks/{chunk_id}", timeout=10.0)
    if response.status_code == 404:
        return {"error": f"Scrape chunk not found: id={chunk_id}"}
    if response.status_code != 200:
        return {"error": f"HTTP {response.status_code}"}
    return response.json()


async def search_scrape(
    client,
    *,
    query: str,
    limit: int,
    cfg: dict[str, Any],
    build_artifact_ref,
) -> dict:
    response = await client.get("/api/search", params={"q": query, "limit": limit}, timeout=10.0)
    if response.status_code != 200:
        return {"status": "error", "results": [], "error": f"HTTP {response.status_code}", "warnings": []}
    payload = response.json() or []
    if not isinstance(payload, list):
        return {"status": "error", "results": [], "error": "Scrape search returned a non-list payload.", "warnings": []}
    return {
        "status": "ok",
        "results": [
            {
                "domain":       "scrape",
                "type":         "scrape_chunk",
                "artifact_ref": build_artifact_ref("scrape_chunk", id=chunk.get("id")),
                "id":           chunk.get("id"),
                "capture_id":   chunk.get("capture_id", ""),
                "title":        chunk.get("page_title", "") or chunk.get("page_url", ""),
                "source":       chunk.get("page_url", ""),
                "captured_at":  chunk.get("captured_at"),
                "snippet":      chunk.get("snippet") or "",
                "url":          f"{cfg['korescrape_url']}/ui/scrape/files/{chunk.get('capture_id', '')}/{chunk.get('page_path', '')}" if chunk.get("capture_id") and chunk.get("page_path") else "",
                "score":        chunk.get("score"),
            }
            for chunk in payload[:limit]
        ],
        "error":    "",
        "warnings": [],
    }
