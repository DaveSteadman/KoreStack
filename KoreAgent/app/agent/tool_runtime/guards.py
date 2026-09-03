# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Input-normalisation helpers for graph-connection data. Tool execution only accepts provider-native
# structured tool calls; this module does not recover executable requests from model text.
# MARK: FUNCTIONS
# Function inventory:
# - _coerce_graph_connection_item: Implements the  coerce graph connection item operation for this module.
# - _coerce_graph_connection_batch: Implements the  coerce graph connection batch operation for this module.
# - extract_graph_connection_batch_from_text: Extracts graph connection batch from text for this module.
# ====================================================================================================

import json
def _coerce_graph_connection_item(item: object) -> dict | None:
    if isinstance(item, (list, tuple)) and len(item) >= 3:
        start, connection, end = item[0], item[1], item[2]
        if str(start).strip() and str(connection).strip() and str(end).strip():
            result = {"start": str(start), "connection": str(connection), "end": str(end)}
            if len(item) >= 4 and isinstance(item[3], int):
                result["state"] = item[3]
            if len(item) >= 5 and isinstance(item[4], int):
                result["score"] = item[4]
            return result
    if isinstance(item, dict):
        start = item.get("start") or item.get("subject") or item.get("source")
        connection = item.get("connection") or item.get("predicate") or item.get("relation") or item.get("relationship")
        end = item.get("end") or item.get("object") or item.get("target")
        if str(start or "").strip() and str(connection or "").strip() and str(end or "").strip():
            result = {"start": str(start), "connection": str(connection), "end": str(end)}
            if isinstance(item.get("state"), int):
                result["state"] = item["state"]
            if isinstance(item.get("score"), int):
                result["score"] = item["score"]
            return result
    return None


def _coerce_graph_connection_batch(value: object) -> list[dict]:
    if isinstance(value, dict):
        for key in ("connections", "triples", "items", "records", "data"):
            nested = value.get(key)
            if isinstance(nested, list):
                return _coerce_graph_connection_batch(nested)
        single = _coerce_graph_connection_item(value)
        return [single] if single else []
    if isinstance(value, list):
        connections: list[dict] = []
        for item in value:
            connection = _coerce_graph_connection_item(item)
            if connection is not None:
                connections.append(connection)
        return connections
    return []


def extract_graph_connection_batch_from_text(text: str) -> list[dict]:
    stripped = (text or "").strip()
    if not stripped:
        return []

    try:
        parsed = json.loads(stripped)
        connections = _coerce_graph_connection_batch(parsed)
        if connections:
            return connections
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char not in "[{":
            continue
        try:
            parsed, _end = decoder.raw_decode(stripped[index:])
        except (json.JSONDecodeError, ValueError):
            continue
        connections = _coerce_graph_connection_batch(parsed)
        if connections:
            return connections
    return []


__all__ = ["extract_graph_connection_batch_from_text"]
