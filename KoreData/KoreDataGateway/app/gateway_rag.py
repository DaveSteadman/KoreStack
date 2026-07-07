from __future__ import annotations


def _service_not_ready_error() -> dict:
    return {"error": "KoreDataGateway is still starting up — retry in a moment"}


async def get_rag_chunk(
    client,
    *,
    chunk_id: int,
) -> dict:
    if client is None:
        return _service_not_ready_error()
    response = await client.get(f"/chunks/{chunk_id}", timeout=10.0)
    if response.status_code == 404:
        return {"error": f"RAG chunk not found: id={chunk_id}"}
    if response.status_code != 200:
        return {"error": f"HTTP {response.status_code}"}
    return response.json()
