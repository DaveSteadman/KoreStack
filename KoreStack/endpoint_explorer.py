from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Endpoint explorer helpers for KoreStack.
# Provides the focused helpers and module-level behaviour grouped into this file.
# ====================================================================================================

import json
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from typing import Any

DEFAULT_TIMEOUT = 2.5

SERVICE_LABELS: dict[str, str] = {
    "korestack":         "KoreStack",
    "koreagent":         "KoreAgent",
    "korechat":          "KoreChat",
    "koredatagateway":   "KoreDataGateway",
    "korefeed":          "KoreFeed",
    "korelibrary":       "KoreLibrary",
    "korerag":           "KoreRAG",
    "korereference":     "KoreReference",
    "korescrape":        "KoreScrape",
    "koregraph":         "KoreGraph",
    "korecomms":         "KoreComms",
    "koredocs":          "KoreDocs",
    "korecode":          "KoreCode",
}

SERVICE_ORDER = [
    "korestack",
    "koreagent",
    "korechat",
    "koredatagateway",
    "korefeed",
    "korelibrary",
    "korerag",
    "korereference",
    "korescrape",
    "koregraph",
    "korecomms",
    "koredocs",
    "korecode",
]


def _service_host(config: dict[str, Any], slug: str) -> str:
    network = config.get("network") if isinstance(config.get("network"), dict) else {}
    default_host = str(network.get("host") or "127.0.0.1").strip()
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    service_cfg = services.get(slug) if isinstance(services.get(slug), dict) else {}
    return str(service_cfg.get("host") or default_host).strip()


def _service_port(config: dict[str, Any], slug: str) -> int | None:
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    service_cfg = services.get(slug) if isinstance(services.get(slug), dict) else {}
    port = service_cfg.get("port")
    return int(port) if port is not None else None


def _service_enabled(config: dict[str, Any], slug: str) -> bool:
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    service_cfg = services.get(slug) if isinstance(services.get(slug), dict) else {}
    enabled = service_cfg.get("enabled")
    return bool(enabled) if enabled is not None else slug == "korestack"


def service_targets(config: dict[str, Any], dashboard_url: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = [
        {
            "key":      "korestack",
            "label":    SERVICE_LABELS["korestack"],
            "base_url": dashboard_url.rstrip("/"),
        }
    ]
    for slug in SERVICE_ORDER:
        if slug == "korestack" or not _service_enabled(config, slug):
            continue
        port = _service_port(config, slug)
        if port is None:
            continue
        host = _service_host(config, slug)
        items.append(
            {
                "key":      slug,
                "label":    SERVICE_LABELS.get(slug, slug),
                "base_url": f"http://{host}:{port}",
            }
        )
    return items


def korestack_manifest() -> dict[str, Any]:
    routes = [
        {"path": "/",                          "methods": ["GET"],  "name": "dashboard",        "summary": "KoreStack landing page",        "description": "", "include_in_schema": True,  "kind": "ui",    "path_params": [], "query_params": [], "body_params": []},
        {"path": "/status",                    "methods": ["GET"],  "name": "status",           "summary": "KoreStack service snapshot",    "description": "", "include_in_schema": True,  "kind": "meta",  "path_params": [], "query_params": [], "body_params": []},
        {"path": "/suite-urls",                "methods": ["GET"],  "name": "suite_urls",       "summary": "Shared suite URL registry",     "description": "", "include_in_schema": True,  "kind": "meta",  "path_params": [], "query_params": [], "body_params": []},
        {"path": "/endpoints",                 "methods": ["GET"],  "name": "endpoints_page",   "summary": "Endpoint explorer page",        "description": "", "include_in_schema": True,  "kind": "ui",    "path_params": [], "query_params": [], "body_params": []},
        {"path": "/api/endpoints/catalog",     "methods": ["GET"],  "name": "endpoint_catalog", "summary": "Cross-service endpoint catalog","description": "", "include_in_schema": True,  "kind": "api",   "path_params": [], "query_params": [], "body_params": []},
        {"path": "/api/endpoints/request",     "methods": ["POST"], "name": "endpoint_request", "summary": "Proxy a request to a suite URL","description": "", "include_in_schema": True,  "kind": "api",   "path_params": [], "query_params": [], "body_params": [{"name": "method", "required": True, "type": "str", "default": None, "description": ""}, {"name": "url", "required": True, "type": "str", "default": None, "description": ""}]},
        {"path": "/api/services/{slug}/{action}", "methods": ["POST"], "name": "service_action", "summary": "Start, stop, or restart a service", "description": "", "include_in_schema": True, "kind": "api", "path_params": [{"name": "slug", "required": True, "type": "str", "default": None, "description": ""}, {"name": "action", "required": True, "type": "str", "default": None, "description": ""}], "query_params": [], "body_params": []},
        {"path": "/static/stack/{asset_path:path}", "methods": ["GET"], "name": "stack_asset", "summary": "KoreStack static asset", "description": "", "include_in_schema": False, "kind": "asset", "path_params": [{"name": "asset_path", "required": True, "type": "str", "default": None, "description": ""}], "query_params": [], "body_params": []},
        {"path": "/ui-elements/assets/{asset_path:path}", "methods": ["GET"], "name": "ui_asset", "summary": "Shared UIElements asset", "description": "", "include_in_schema": False, "kind": "asset", "path_params": [{"name": "asset_path", "required": True, "type": "str", "default": None, "description": ""}], "query_params": [], "body_params": []},
    ]
    return {
        "service": {"key": "korestack", "label": SERVICE_LABELS["korestack"]},
        "routes": routes,
        "stats": {"count": len(routes), "hidden_count": 2},
    }


def _fetch_json(url: str, timeout: float = DEFAULT_TIMEOUT) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def build_catalog(config: dict[str, Any], dashboard_url: str) -> dict[str, Any]:
    services: list[dict[str, Any]] = []

    for target in service_targets(config, dashboard_url):
        key      = target["key"]
        label    = target["label"]
        base_url = target["base_url"].rstrip("/")
        if key == "korestack":
            manifest = korestack_manifest()
            services.append(
                {
                    "key":        key,
                    "label":      label,
                    "base_url":   base_url,
                    "reachable":  True,
                    "error":      None,
                    "manifest":   manifest,
                    "route_count": int(manifest["stats"]["count"]),
                }
            )
            continue

        manifest_url = f"{base_url}/__endpoint_manifest"
        try:
            manifest = _fetch_json(manifest_url)
            services.append(
                {
                    "key":        key,
                    "label":      label,
                    "base_url":   base_url,
                    "reachable":  True,
                    "error":      None,
                    "manifest":   manifest,
                    "route_count": int(manifest.get("stats", {}).get("count", 0)),
                }
            )
        except urllib.error.HTTPError as exc:
            services.append(
                {
                    "key":        key,
                    "label":      label,
                    "base_url":   base_url,
                    "reachable":  False,
                    "error":      f"HTTP {exc.code}",
                    "manifest":   None,
                    "route_count": 0,
                }
            )
        except Exception as exc:
            services.append(
                {
                    "key":        key,
                    "label":      label,
                    "base_url":   base_url,
                    "reachable":  False,
                    "error":      exc.__class__.__name__,
                    "manifest":   None,
                    "route_count": 0,
                }
            )

    total_routes = sum(int(item.get("route_count", 0)) for item in services)
    reachable    = sum(1 for item in services if item.get("reachable"))
    return {
        "services": services,
        "stats": {
            "service_count": len(services),
            "reachable_count": reachable,
            "route_count": total_routes,
        },
    }


def _allowed_base_urls(config: dict[str, Any], dashboard_url: str) -> list[str]:
    return [item["base_url"].rstrip("/") for item in service_targets(config, dashboard_url)]


def _is_allowed_url(url: str, allowed_bases: list[str]) -> bool:
    normalized = url.rstrip("/")
    return any(normalized == base or normalized.startswith(base + "/") or normalized.startswith(base + "?") for base in allowed_bases)


def proxy_request(
    config: dict[str, Any],
    dashboard_url: str,
    *,
    method: str,
    url: str,
    body: str = "",
    content_type: str = "application/json",
) -> dict[str, Any]:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {"ok": False, "status": int(HTTPStatus.BAD_REQUEST), "error": "URL must be absolute."}

    allowed_bases = _allowed_base_urls(config, dashboard_url)
    if not _is_allowed_url(url, allowed_bases):
        return {"ok": False, "status": int(HTTPStatus.FORBIDDEN), "error": "URL is outside the KoreStack service list."}

    payload = body.encode("utf-8") if body else None
    request = urllib.request.Request(url=url, data=payload, method=method.upper())
    if payload is not None:
        request.add_header("Content-Type", content_type or "application/json")

    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT * 2) as response:
            raw_body = response.read()
            headers = dict(response.headers.items())
            return {
                "ok":           True,
                "status":       int(response.status),
                "reason":       str(getattr(response, "reason", "") or ""),
                "headers":      headers,
                "content_type": headers.get("Content-Type", ""),
                "body_text":    raw_body.decode("utf-8", errors="replace"),
            }
    except urllib.error.HTTPError as exc:
        raw_body = exc.read()
        headers = dict(exc.headers.items()) if exc.headers else {}
        return {
            "ok":           False,
            "status":       int(exc.code),
            "reason":       str(exc.reason or ""),
            "headers":      headers,
            "content_type": headers.get("Content-Type", ""),
            "body_text":    raw_body.decode("utf-8", errors="replace"),
        }
    except Exception as exc:
        return {
            "ok":     False,
            "status": int(HTTPStatus.BAD_GATEWAY),
            "error":  f"{exc.__class__.__name__}: {exc}",
        }
