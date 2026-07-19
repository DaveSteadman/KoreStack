from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Endpoint manifest helpers for Kore services.
# Builds the small metadata payloads used to describe service endpoints consistently across the suite.
# ====================================================================================================

from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import Mount


def _type_name(value: Any) -> str:
    if value is None:
        return "any"
    if isinstance(value, str):
        return value
    name = getattr(value, "__name__", None)
    if isinstance(name, str) and name:
        return name
    return str(value)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, dict)):
        return value
    return str(value)


def _param_info(param: Any) -> dict[str, Any]:
    field_info = getattr(param, "field_info", None)
    annotation = getattr(param, "annotation", None)
    if annotation is None:
        annotation = getattr(param, "type_", None)
    default = getattr(param, "default", None)
    return {
        "name":        str(getattr(param, "name", "")),
        "required":    bool(getattr(param, "required", False)),
        "type":        _type_name(annotation),
        "default":     None if getattr(param, "required", False) else _json_safe(default),
        "description": str(getattr(field_info, "description", "") or ""),
    }


def _body_info(route: APIRoute) -> list[dict[str, Any]]:
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return []
    params = getattr(dependant, "body_params", None) or []
    return [_param_info(param) for param in params]


def _route_kind(path: str, methods: list[str], include_in_schema: bool) -> str:
    if path.startswith("/ui-elements/assets/") or path.startswith("/static/") or path == "/suite-config.js":
        return "asset"
    if path == "/mcp" or path.startswith("/mcp/"):
        return "api"
    if "STREAM" in methods or path.endswith("/stream"):
        return "stream"
    if path == "/status" or path.startswith("/status"):
        return "meta"
    if path == "/" or path.startswith("/ui") or path.startswith("/conversation/") or path.startswith("/connections"):
        return "ui"
    if path.startswith("/api/") or include_in_schema:
        return "api"
    return "other"


def build_endpoint_manifest(app: FastAPI, *, service_key: str, service_label: str) -> dict[str, Any]:
    routes: list[dict[str, Any]] = []
    hidden_count = 0

    for route in app.routes:
        if not isinstance(route, APIRoute):
            if isinstance(route, Mount):
                mount_path = str(getattr(route, "path", "") or "")
                mount_name = str(getattr(route, "name", "") or "")
                methods    = ["MOUNT"]
                routes.append(
                    {
                        "path":              mount_path,
                        "methods":           methods,
                        "name":              mount_name,
                        "summary":           "Mounted sub-application",
                        "description":       f"Mounted application at {mount_path}.",
                        "include_in_schema": True,
                        "kind":              _route_kind(mount_path, methods, True),
                        "path_params":       [],
                        "query_params":      [],
                        "body_params":       [],
                    }
                )
            continue

        methods = sorted(method for method in (route.methods or set()) if method not in {"HEAD", "OPTIONS"})
        include_in_schema = bool(getattr(route, "include_in_schema", False))
        if not include_in_schema:
            hidden_count += 1

        dependant = getattr(route, "dependant", None)
        path_params = getattr(dependant, "path_params", None) or []
        query_params = getattr(dependant, "query_params", None) or []

        routes.append(
            {
                "path":              str(getattr(route, "path_format", route.path)),
                "methods":           methods,
                "name":              str(getattr(route, "name", "") or ""),
                "summary":           str(getattr(route, "summary", "") or ""),
                "description":       str(getattr(route, "description", "") or ""),
                "include_in_schema": include_in_schema,
                "kind":              _route_kind(route.path, methods, include_in_schema),
                "path_params":       [_param_info(param) for param in path_params],
                "query_params":      [_param_info(param) for param in query_params],
                "body_params":       _body_info(route),
            }
        )

    routes.sort(key=lambda item: (item["path"], ",".join(item["methods"])))
    return {
        "service": {
            "key":   service_key,
            "label": service_label,
        },
        "routes": routes,
        "stats": {
            "count":        len(routes),
            "hidden_count": hidden_count,
        },
    }
