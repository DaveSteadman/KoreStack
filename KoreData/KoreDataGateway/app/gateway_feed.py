from __future__ import annotations

from urllib.parse import quote


def _service_not_ready_error() -> dict:
    return {"error": "KoreDataGateway is still starting up — retry in a moment"}


async def get_feed_entry(
    client,
    *,
    domain: str,
    entry_id: int,
) -> dict:
    if client is None:
        return _service_not_ready_error()
    response = await client.get(f"/api/domains/{domain}/entries/{entry_id}", timeout=10.0)
    if response.status_code == 404:
        return {"error": f"Feed entry not found: domain={domain!r} id={entry_id}"}
    if response.status_code != 200:
        return {"error": f"HTTP {response.status_code}"}
    return response.json()


async def get_feed_sentence(
    client,
    *,
    domain: str,
    sentence_id: int,
) -> dict:
    if client is None:
        return _service_not_ready_error()
    response = await client.get(
        f"/api/domains/{quote(domain, safe='')}/sentences/{sentence_id}",
        timeout=10.0,
    )
    if response.status_code == 404:
        return {"error": f"Sentence not found: locator={'feeds/' + domain + '/' + str(sentence_id)!r}"}
    if response.status_code != 200:
        return {"error": f"HTTP {response.status_code}"}
    data = response.json()
    if isinstance(data, dict) and "locator" not in data:
        data["locator"] = f"feeds/{domain}/{sentence_id}"
    return data
