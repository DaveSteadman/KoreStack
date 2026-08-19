# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# gateway reference module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Function inventory:
# - _service_not_ready_error: Implements the  service not ready error operation for this module.
# - get_reference_article: Returns reference article for this module.
# - get_reference_sentence: Returns reference sentence for this module.
# ====================================================================================================

from __future__ import annotations

from urllib.parse import quote


def _service_not_ready_error() -> dict:
    return {"error": "KoreDataGateway is still starting up — retry in a moment"}


async def get_reference_article(
    client,
    *,
    title: str,
) -> dict:
    if client is None:
        return _service_not_ready_error()
    response = await client.get(f"/articles/{quote(title, safe='')}", timeout=10.0)
    if response.status_code == 404:
        return {"error": f"Reference article not found: {title!r}"}
    if response.status_code != 200:
        return {"error": f"HTTP {response.status_code}"}
    return response.json()


async def get_reference_sentence(
    client,
    *,
    database: str,
    sentence_id: int,
) -> dict:
    if client is None:
        return _service_not_ready_error()
    response = await client.get(f"/api/sentences/{sentence_id}", timeout=10.0)
    if response.status_code == 404:
        return {"error": f"Sentence not found: locator={'reference/' + database + '/' + str(sentence_id)!r}"}
    if response.status_code != 200:
        return {"error": f"HTTP {response.status_code}"}
    data = response.json()
    if isinstance(data, dict) and "locator" not in data:
        data["locator"] = f"reference/{database}/{sentence_id}"
    return data
