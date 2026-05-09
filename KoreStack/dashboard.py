from __future__ import annotations

import html
import json
import logging
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, ClassVar

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
        return "up", "Up", "accent"
    if service["running"]:
        return "starting", "Starting", "warning"
    return "down", "Stopped", "danger"


def _service_row_markup(service: dict[str, object]) -> str:
    css_state, state_label, tag_color = _service_state(service)
    slug = html.escape(str(service["slug"]))
    label = html.escape(str(service["label"]))
    icon_key = html.escape(str(service["iconKey"]))
    description = html.escape(str(service["description"]))
    host = html.escape(str(service["host"]))
    port = html.escape(str(service["port"]))
    url = html.escape(str(service["url"]))
    return f"""
      <article class="service-row service-{slug} {css_state}" data-service-card="{slug}">
        <div class="service-cell service-glyph" aria-hidden="true" data-suite-icon="{icon_key}"></div>
        <div class="service-cell service-core">
          <p class="eyebrow">{slug.upper()}</p>
          <h2>{label}</h2>
          <p class="service-copy">{description}</p>
        </div>
        <div class="service-cell service-state"><span class="kcui-tag kcui-tag--{tag_color}" data-field="state">{state_label}</span></div>
        <div class="service-cell address-edit">
          <input type="text" class="host-input" data-field="host" value="{host}" aria-label="Host for {label}">
          <input type="number" class="port-input" data-field="port" value="{port}" min="1024" max="65535" aria-label="Port for {label}">
          <button class="kcui-tag kcui-tag--dim" type="button" data-service="{slug}" data-action="setaddress" title="Save address and restart {label}">set</button>
        </div>
        <div class="service-cell service-link"><a data-field="url" href="{url}" target="_blank" rel="noreferrer">{url}</a></div>
        <div class="service-cell service-actions actions">
          <button type="button" data-service="{slug}" data-action="start" title="Start" aria-label="Start {label}">
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3.5v9l7-4.5-7-4.5Z" fill="currentColor"/></svg>
          </button>
          <button type="button" data-service="{slug}" data-action="stop" title="Stop" aria-label="Stop {label}">
            <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="4" y="4" width="8" height="8" fill="currentColor"/></svg>
          </button>
          <button type="button" data-service="{slug}" data-action="restart" title="Restart" aria-label="Restart {label}">
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3a5 5 0 1 1-4.24 2.35" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M2.8 2.8h3.6v3.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </button>
        </div>
      </article>
    """


def _path_rows_markup(paths: dict[str, object]) -> str:
    rows = []
    for key, value in paths.items():
        rows.append(
            f"""
      <div class="path-row">
        <dt>{html.escape(str(key))}</dt>
        <dd><code>{html.escape(str(value))}</code></dd>
      </div>
            """
        )
    return "".join(rows)


def _dashboard_bootstrap(snapshot: dict[str, object], suite_urls: dict[str, str], dashboard_url: str) -> dict[str, object]:
    stack = snapshot["stack"]
    metrics = stack["metrics"]
    return {
        "snapshot": snapshot,
        "suiteUrls": suite_urls,
        "chips": [
            {"label": "Running", "value": f"{metrics['running']} / {metrics['selected']}", "tone": "accent"},
            {"label": "Dashboard", "value": dashboard_url},
        ],
    }


def _load_stack_template(stack_static_dir: Path) -> str:
    return (stack_static_dir / "stack" / "index.html").read_text(encoding="utf-8")


def html_page(manager: Any, dashboard_url: str, stack_static_dir: Path, ui_assets_dir: Path, service_icon_keys: dict[str, str]) -> str:
    snapshot = manager.snapshot()
    stack = snapshot["stack"]
    suite_urls = build_suite_urls(manager, dashboard_url, service_icon_keys)
    bootstrap_json = json.dumps(_dashboard_bootstrap(snapshot, suite_urls, dashboard_url)).replace("</", "<\\/")
    root_command = f"python .\\main.py --services {','.join(stack['services'])}"
    return (
        _load_stack_template(stack_static_dir)
        .replace("{{PATH_ROWS}}", _path_rows_markup(stack["paths"]))
        .replace("{{SERVICE_ROWS}}", "".join(_service_row_markup(service) for service in snapshot["services"]))
        .replace("{{ROOT_COMMAND}}", html.escape(root_command))
        .replace("{{BOOTSTRAP_JSON}}", bootstrap_json)
    )


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

            if request_path.startswith("/ui-elements/assets/"):
                self._serve_asset(ui_assets_dir, request_path.removeprefix("/ui-elements/assets/"))
                return

            if request_path.startswith("/static/stack/"):
                self._serve_asset(stack_static_dir / "stack", request_path.removeprefix("/static/stack/"))
                return

            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:  # noqa: N802
            request_path = urllib.parse.urlsplit(self.path).path
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
            length = int(self.headers.get("Content-Length") or 0)
            raw_body = self.rfile.read(length) if length else b""

            try:
                if action == "start":
                    changed = self.manager_ref.start_service(slug)
                elif action == "stop":
                    changed = self.manager_ref.stop_service(slug)
                elif action == "restart":
                    changed = self.manager_ref.restart_service(slug)
                elif action == "setaddress":
                    try:
                        payload = json.loads(raw_body or b"{}")
                        port = int(payload.get("port", 0))
                        host = str(payload.get("host") or "127.0.0.1").strip()
                    except (ValueError, TypeError):
                        self.send_error(HTTPStatus.BAD_REQUEST, "Invalid payload")
                        return
                    if not (1024 <= port <= 65535):
                        self.send_error(HTTPStatus.BAD_REQUEST, "Port out of range (1024-65535)")
                        return
                    if not host:
                        self.send_error(HTTPStatus.BAD_REQUEST, "Host required")
                        return
                    changed = self.manager_ref.set_service_address(slug, host, port)
                else:
                    self.send_error(HTTPStatus.NOT_FOUND, "Unknown action")
                    return
            except KeyError:
                self.send_error(HTTPStatus.NOT_FOUND, "Unknown service")
                return

            probe_reachable: bool | None = None
            probe_detail: str | None = None
            if action in ("start", "restart", "setaddress"):
                spec_after = self.manager_ref._service_map.get(slug)
                if spec_after:
                    probe_reachable, probe_detail = probe_http_with_retry(spec_after.health_url)
            with self.manager_ref._cache_lock:
                self.manager_ref._snapshot_cache = None
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
) -> None:
    dashboard_url = f"http://{host}:{port}/"
    handler = build_handler(
        manager,
        dashboard_url,
        stack_static_dir=stack_static_dir,
        ui_assets_dir=ui_assets_dir,
        service_icon_keys=service_icon_keys,
        probe_http_with_retry=probe_http_with_retry,
    )
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.timeout = 0.5
    print(f"KoreStack  {dashboard_url}  (logs -> KoreStack/logs/)")
    log.info("landing page %s", dashboard_url)
    while not stop_event.is_set():
        httpd.handle_request()
    httpd.server_close()
