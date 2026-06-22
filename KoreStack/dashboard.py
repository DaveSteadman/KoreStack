# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreStack suite health dashboard — HTTP server that serves a live status page.
#
# Runs a threading.HTTPServer on a configurable port.  Polls each service's /status
# endpoint at a configurable interval and renders an HTML dashboard showing each
# service as up (green), starting (yellow), or down (red).
#
# Key functions:
#   build_suite_urls(cfg)          -- build the service URL map from config
#   _service_state(url, timeout)   -- probe a service and return its state string
#   _service_row_markup(name, url) -- generate the HTML card for one service
#
# Related modules:
#   - KoreStack/main.py  -- starts the dashboard thread alongside all services
# ====================================================================================================
from __future__ import annotations

import json
import logging
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path
from typing import Any, Callable, ClassVar

from endpoint_explorer import build_catalog, proxy_request

log: logging.Logger = logging.getLogger("korestack")


def build_suite_urls(manager: Any, dashboard_url: str, service_icon_keys: dict[str, str]) -> dict[str, str]:
    """Return the topbar URL map keyed by topbar service key, such as 'koreagent'."""
    urls: dict[str, str] = {"korestack": dashboard_url}
    for service in manager.snapshot()["services"]:
        topbar_key = service_icon_keys.get(service["slug"])
        if topbar_key:
            urls[topbar_key] = service["url"]
    return urls


def _service_state(service: dict[str, object]) -> tuple[str, str, str]:
    if service["reachable"]:
        return "up", "Running", "success"
    if service["running"]:
        return "starting", "Starting", "warning"
    return "down", "Stopped", "danger"


def _service_row_view(service: dict[str, object]) -> dict[str, object]:
    css_state, state_label, tag_color = _service_state(service)
    return {
        "slug":        str(service["slug"]),
        "slug_upper":  str(service["slug"]).upper(),
        "label":       str(service["label"]),
        "icon_key":    str(service["iconKey"]),
        "description": str(service["description"]),
        "url":         str(service["url"]),
        "css_state":   css_state,
        "state_label": state_label,
        "tag_color":   tag_color,
    }


def _path_rows(paths: dict[str, object]) -> list[dict[str, str]]:
    labels = {"path config": "Config", "datacontrol": "Data control", "datauser": "Data user"}
    items: list[dict[str, str]] = []
    for key, value in paths.items():
        items.append({
            "label": labels.get(key, key),
            "path":  str(value),
        })
    return items


def _dashboard_bootstrap(snapshot: dict[str, object], suite_urls: dict[str, str], dashboard_url: str) -> dict[str, object]:
    stack = snapshot["stack"]
    metrics = stack["metrics"]
    return {
        "snapshot": snapshot,
        "suiteUrls": suite_urls,
        "chips": [
            {"label": "Running", "value": f"{metrics['running']} / {metrics['selected']}", "valueId": "stack-running-value", "tone": "accent"},
            {"label": "Reachable", "value": str(metrics["reachable"]), "valueId": "stack-reachable-value", "tone": "dim"},
            {"label": "Dashboard", "value": dashboard_url, "valueId": "stack-dashboard-value", "tone": "dim"},
            {"label": "UI Shell", "value": "available" if stack["uiElementsMounted"] else "missing", "valueId": "stack-ui-value", "tone": "success" if stack["uiElementsMounted"] else "danger"},
        ],
    }


def _template_env(stack_static_dir: Path) -> Environment:
    return Environment(
        loader     = FileSystemLoader(str(stack_static_dir / "stack")),
        autoescape = select_autoescape(["html", "xml"]),
    )


def html_page(manager: Any, dashboard_url: str, stack_static_dir: Path, ui_assets_dir: Path, service_icon_keys: dict[str, str]) -> str:
    snapshot       = manager.snapshot()
    stack          = snapshot["stack"]
    suite_urls     = build_suite_urls(manager, dashboard_url, service_icon_keys)
    bootstrap_json = json.dumps(_dashboard_bootstrap(snapshot, suite_urls, dashboard_url)).replace("</", "<\\/")
    root_command   = f"python .\\main.py --services {','.join(stack['services'])}"
    template       = _template_env(stack_static_dir).get_template("index.html")
    return template.render(
        path_rows      = _path_rows(stack["paths"]),
        service_rows   = [_service_row_view(service) for service in snapshot["services"]],
        root_command   = root_command,
        bootstrap_json = bootstrap_json,
    )


def endpoints_page(
    manager: Any,
    dashboard_url: str,
    stack_static_dir: Path,
    service_icon_keys: dict[str, str],
    suite_config: dict[str, Any],
) -> str:
    snapshot = manager.snapshot()
    suite_urls = build_suite_urls(manager, dashboard_url, service_icon_keys)
    catalog = build_catalog(suite_config, dashboard_url)
    bootstrap_json = json.dumps(
        {
            "snapshot": snapshot,
            "suiteUrls": suite_urls,
            "catalog": catalog,
            "catalogUrl": "/api/endpoints/catalog",
            "requestUrl": "/api/endpoints/request",
            "chips": [
                {"label": "Services", "value": str(catalog["stats"]["service_count"]), "valueId": "endpoint-service-count", "tone": "accent"},
                {"label": "Reachable", "value": str(catalog["stats"]["reachable_count"]), "valueId": "endpoint-reachable-count", "tone": "dim"},
                {"label": "Routes", "value": str(catalog["stats"]["route_count"]), "valueId": "endpoint-route-count", "tone": "dim"},
            ],
        }
    ).replace("</", "<\\/")
    template = _template_env(stack_static_dir).get_template("endpoints.html")
    return template.render(bootstrap_json=bootstrap_json)


def _content_type_for(path: Path) -> str:
    if path.suffix == ".css":
        return "text/css; charset=utf-8"
    if path.suffix == ".js":
        return "text/javascript; charset=utf-8"
    if path.suffix == ".html":
        return "text/html; charset=utf-8"
    if path.suffix == ".svg":
        return "image/svg+xml"
    return "application/octet-stream"


def build_handler(
    manager: Any,
    dashboard_url: str,
    *,
    stack_static_dir: Path,
    ui_assets_dir: Path,
    service_icon_keys: dict[str, str],
    probe_http_with_retry: Callable[[str], tuple[bool, str]],
    suite_config: dict[str, Any],
):
    class StackHandler(BaseHTTPRequestHandler):
        manager_ref: ClassVar[Any] = manager
        dashboard_ref: ClassVar[str] = dashboard_url

        def _send_bytes(self, body: bytes, content_type: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                return

        def do_GET(self) -> None:  # noqa: N802
            request_path = urllib.parse.urlsplit(self.path).path

            if request_path in ("/", ""):
                body = html_page(self.manager_ref, self.dashboard_ref, stack_static_dir, ui_assets_dir, service_icon_keys).encode("utf-8")
                self._send_bytes(body, "text/html; charset=utf-8")
                return

            if request_path == "/endpoints":
                body = endpoints_page(
                    self.manager_ref,
                    self.dashboard_ref,
                    stack_static_dir,
                    service_icon_keys,
                    suite_config,
                ).encode("utf-8")
                self._send_bytes(body, "text/html; charset=utf-8")
                return

            if request_path == "/status":
                body = json.dumps(self.manager_ref.snapshot(), indent=2).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    return
                return

            if request_path == "/suite-urls":
                body = json.dumps(build_suite_urls(self.manager_ref, self.dashboard_ref, service_icon_keys)).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    return
                return

            if request_path == "/api/endpoints/catalog":
                body = json.dumps(build_catalog(suite_config, self.dashboard_ref)).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    return
                return

            if request_path.startswith("/ui-elements/assets/"):
                self._serve_asset(ui_assets_dir, request_path.removeprefix("/ui-elements/assets/"))
                return

            if request_path.startswith("/static/stack/"):
                self._serve_asset(stack_static_dir / "stack", request_path.removeprefix("/static/stack/"))
                return

            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:  # noqa: N802
            request_path = urllib.parse.urlsplit(self.path).path
            if request_path == "/api/endpoints/request":
                self._handle_endpoint_request()
                return
            if request_path.startswith("/api/services/"):
                parts = [part for part in request_path.split("/") if part]
                if len(parts) != 4:
                    self.send_error(HTTPStatus.NOT_FOUND, "Unknown service action")
                    return
                _, _, slug, action = parts
                self._handle_service_action(slug, action)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def log_message(self, format: str, *args: object) -> None:
            return

        def _serve_asset(self, assets_dir: Path, relative_path: str) -> None:
            asset_path = (assets_dir / relative_path).resolve()
            if not assets_dir.exists() or assets_dir.resolve() not in asset_path.parents:
                self.send_error(HTTPStatus.NOT_FOUND, "Asset not available")
                return
            if not asset_path.exists() or not asset_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "Asset not found")
                return
            self._send_bytes(asset_path.read_bytes(), _content_type_for(asset_path))

        def _handle_service_action(self, slug: str, action: str) -> None:
            try:
                if action == "start":
                    changed = self.manager_ref.start_service(slug)
                elif action == "stop":
                    changed = self.manager_ref.stop_service(slug)
                elif action == "restart":
                    changed = self.manager_ref.restart_service(slug)
                else:
                    self.send_error(HTTPStatus.NOT_FOUND, "Unknown action")
                    return
            except KeyError:
                self.send_error(HTTPStatus.NOT_FOUND, "Unknown service")
                return

            probe_reachable: bool | None = None
            probe_detail: str | None = None
            if action in ("start", "restart"):
                spec_after = self.manager_ref.get_service_spec(slug)
                if spec_after:
                    probe_reachable, probe_detail = probe_http_with_retry(spec_after.health_url)
            self.manager_ref.invalidate_snapshot_cache()
            result: dict[str, object] = {"ok": True, "service": slug, "action": action, "changed": changed}
            if probe_reachable is not None:
                result["reachable"] = probe_reachable
                result["probeDetail"] = probe_detail
            body = json.dumps(result).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                return

        def _handle_endpoint_request(self) -> None:
            raw_length = self.headers.get("Content-Length", "0")
            try:
                content_length = int(raw_length)
            except ValueError:
                content_length = 0
            raw_body = self.rfile.read(content_length) if content_length > 0 else b""
            try:
                payload = json.loads(raw_body.decode("utf-8") or "{}")
            except Exception:
                self.send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON body")
                return

            result = proxy_request(
                suite_config,
                self.dashboard_ref,
                method       = str(payload.get("method") or "GET"),
                url          = str(payload.get("url") or ""),
                body         = str(payload.get("body") or ""),
                content_type = str(payload.get("content_type") or "application/json"),
            )
            status = int(result.get("status") or HTTPStatus.OK)
            body = json.dumps(result).encode("utf-8")
            self.send_response(status if 100 <= status <= 599 else HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                return

    return StackHandler


def serve_dashboard(
    manager: Any,
    host: str,
    port: int,
    stop_event: threading.Event,
    *,
    stack_static_dir: Path,
    ui_assets_dir: Path,
    service_icon_keys: dict[str, str],
    probe_http_with_retry: Callable[[str], tuple[bool, str]],
    suite_config: dict[str, Any],
) -> None:
    dashboard_url = f"http://{host}:{port}/"
    handler = build_handler(
        manager,
        dashboard_url,
        stack_static_dir=stack_static_dir,
        ui_assets_dir=ui_assets_dir,
        service_icon_keys=service_icon_keys,
        probe_http_with_retry=probe_http_with_retry,
        suite_config=suite_config,
    )
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.timeout = 0.5
    log.info("KoreStack  %s  (logs -> KoreStack/logs/)", dashboard_url)
    log.info("landing page %s", dashboard_url)
    while not stop_event.is_set():
        httpd.handle_request()
    httpd.server_close()
