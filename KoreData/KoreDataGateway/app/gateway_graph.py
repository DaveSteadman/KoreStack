from __future__ import annotations

import asyncio


def normalise_graph_query_literal(query: str) -> str:
    text = str(query or "").strip()
    while len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    return text


async def search_graph(
    client,
    *,
    query: str,
    limit: int,
) -> dict:
    graph_query = normalise_graph_query_literal(query)
    query_l     = graph_query.lower()
    response    = await client.get("/api/search", params={"q": graph_query, "limit": min(limit, 50)}, timeout=10.0)
    if response.status_code != 200:
        return {"status": "error", "results": [], "error": f"HTTP {response.status_code}", "warnings": []}
    matches = response.json() or []
    if not matches:
        return {"status": "ok", "results": [], "error": "", "warnings": []}

    concept_rows = matches[: min(len(matches), max(1, min(limit, 8)))]
    expand_calls = [
        client.get(
            "/api/expand",
            params={"concept_id": row.get("concept_id"), "depth": 1, "min_score": 0},
            timeout=10.0,
        )
        for row in concept_rows
        if row.get("concept_id") is not None
    ]
    expand_results = await asyncio.gather(*expand_calls, return_exceptions=True)

    seen: set[tuple[str, str, str]] = set()
    edges: list[dict] = []
    for result in expand_results:
        if isinstance(result, Exception) or result.status_code != 200:
            continue
        data = result.json() or {}
        for edge in data.get("edges") or []:
            if edge.get("state", 0) not in (0, 1, 4):
                continue
            key = (
                str(edge.get("start_name", "")),
                str(edge.get("connection_name", edge.get("connection", ""))),
                str(edge.get("end_name", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            start_l      = key[0].lower()
            connection_l = key[1].lower()
            end_l        = key[2].lower()
            bias         = 0.0
            if query_l and (query_l in start_l or query_l in end_l):
                bias += 8.0
            if query_l and query_l in connection_l:
                bias += 4.0
            edges.append({
                "domain":      "graph",
                "type":        "graph_edge",
                "id":          edge.get("id"),
                "start":       key[0],
                "connection":  key[1],
                "end":         key[2],
                "state":       edge.get("state"),
                "source":      edge.get("source"),
                "score":       -(float(edge.get("score", 0) or 0.0) + bias),
            })
    return {"status": "ok", "results": edges[:limit], "error": "", "warnings": []}
