"""Guard helpers for tool-runtime policy enforcement."""

import json
import re


def extract_raw_json_tool_call(text: str) -> dict | None:
    stripped = (text or "").strip()
    if not stripped.startswith("{"):
        return None
    try:
        obj = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    tool_name = obj.get("tool") or obj.get("name") or obj.get("function")
    arguments = obj.get("arguments") or obj.get("parameters") or obj.get("args") or {}
    if not tool_name or not isinstance(tool_name, str):
        return None
    if not isinstance(arguments, dict):
        return None
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", tool_name):
        return None
    return {
        "id": f"raw_json_{tool_name}",
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": json.dumps(arguments),
        },
    }


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


__all__ = ["extract_graph_connection_batch_from_text", "extract_raw_json_tool_call"]
