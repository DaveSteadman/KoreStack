from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO, ClassVar

log: logging.Logger = logging.getLogger("korestack")


SUITE_ROOT = Path(__file__).resolve().parent.parent
STACK_ROOT = Path(__file__).resolve().parent
UI_ELEMENTS_ASSETS = SUITE_ROOT / "UIElements" / "assets"
SUITE_CONFIG_DEFAULT = SUITE_ROOT / "config" / "default.json"
SUITE_CONFIG_LOCAL = SUITE_ROOT / "config" / "local.json"


@dataclass(frozen=True)
class ServiceSpec:
    slug: str
    label: str
    cwd: Path
    script: str
    url: str
    health_url: str
    description: str


SERVICE_META: dict[str, dict[str, object]] = {
    "agent": {
        "label": "KoreAgent",
        "cwd": SUITE_ROOT / "KoreAgent",
        "script": "main.py",
        "port": 8605,
        "url_suffix": "/",
        "health_suffix": "/",
        "port_arg": "--agentport",
        "description": "KoreAgent web UI and orchestration surface.",
    },
    "conversation": {
        "label": "KoreChat",
        "cwd": SUITE_ROOT / "KoreChat",
        "script": "main.py",
        "port": 8630,
        "url_suffix": "/ui",
        "health_suffix": "/status",
        "port_env": "KORECHAT_PORT",
        "description": "Shared conversation-state service for agent and comms flows.",
    },
    "data": {
        "label": "KoreData",
        "cwd": SUITE_ROOT / "KoreData",
        "script": "main.py",
        "port": 8620,
        "url_suffix": "/",
        "health_suffix": "/status",
        "port_env": "KOREDATA_PORT",
        "description": "KoreData gateway with the feed, library, reference, and RAG services behind it.",
    },
    "docs": {
        "label": "KoreDocs",
        "cwd": SUITE_ROOT / "KoreDocs",
        "script": "main.py",
        "port": 8615,
        "url_suffix": "/ui",
        "health_suffix": "/status",
        "port_arg": "--port",
        "description": "KoreDocs file manager plus the doc, sheet, and diagram editors.",
    },
    "code": {
        "label": "KoreCode",
        "cwd": SUITE_ROOT / "KoreCode",
        "script": "main.py",
        "port": 8610,
        "url_suffix": "/ui",
        "health_suffix": "/status",
        "port_arg": "--port",
        "description": "KoreCode lightweight code editor rooted at the KoreStack workspace.",
    },
    "comms": {
        "label": "KoreComms",
        "cwd": SUITE_ROOT / "KoreComms",
        "script": "main.py",
        "port": 8625,
        "url_suffix": "/",
        "health_suffix": "/status",
        "port_env": "KORECOMMS_PORT",
        "description": "KoreComms UI and API for conversation and activity flows.",
    },
}

SERVICE_ICON_KEYS: dict[str, str] = {
    "agent": "koreagent",
    "conversation": "korechat",
    "data": "koredata",
    "docs": "koredocs",
    "code": "korecode",
    "comms": "korecomms",
}


def get_ui_assets_dir() -> Path:
    return UI_ELEMENTS_ASSETS


def _merge_dict(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_suite_config() -> dict:
    config: dict = {}
    for path in (SUITE_CONFIG_DEFAULT, SUITE_CONFIG_LOCAL):
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                config = _merge_dict(config, raw)
        except Exception:
            continue
    return config


def resolve_root_path(value: object, default: str) -> Path:
    return (SUITE_ROOT / str(value or default)).resolve()


def get_stack_paths(config: dict) -> dict[str, Path]:
    paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}
    datacontrol = resolve_root_path(paths.get("datacontrol"), "datacontrol")
    datauser = resolve_root_path(paths.get("datauser"), "datauser")
    return {
        "datacontrol": datacontrol,
        "datauser": datauser,
        "conversation_data": resolve_root_path(paths.get("conversation_data"), "datacontrol/conversations"),
        "comms_data": resolve_root_path(paths.get("comms_data"), "datacontrol/korecomms"),
        "docs_data": resolve_root_path(paths.get("docs_data"), "datauser/KoreFiles"),
        "docs_db": resolve_root_path(paths.get("docs_db"), "datauser/KoreFiles/korefile.db"),
    }


def build_child_env(config: dict) -> dict[str, str]:
    env = dict(os.environ)
    stack_paths = get_stack_paths(config)
    network = config.get("network") if isinstance(config.get("network"), dict) else {}
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    connections = config.get("connections") if isinstance(config.get("connections"), dict) else {}

    env["PYTHONUTF8"] = "1"
    env["KORE_SUITE_ROOT"] = str(SUITE_ROOT)
    env["KORE_SUITE_CONFIG"] = str(SUITE_CONFIG_DEFAULT)
    env["KORE_SUITE_DATACONTROL"] = str(stack_paths["datacontrol"])
    env["KORE_SUITE_DATAUSER"] = str(stack_paths["datauser"])
    env["KORE_UIELEMENTS_ASSETS_DIR"] = str(get_ui_assets_dir())

    conversation = services.get("conversation") if isinstance(services.get("conversation"), dict) else {}
    comms = services.get("comms") if isinstance(services.get("comms"), dict) else {}

    if network.get("host"):
        env["KORECHAT_HOST"] = str(network["host"])
        env["KORECOMMS_HOST"] = str(network["host"])

    if conversation.get("port") is not None:
        env["KORECHAT_PORT"] = str(conversation["port"])
    if comms.get("port") is not None:
        env["KORECOMMS_PORT"] = str(comms["port"])

    if connections.get("korechat"):
        env["KORECOMMS_KORECHAT_URL"] = str(connections["korechat"])

    env["KORECHAT_DATA_DIR"] = str(stack_paths["conversation_data"])
    env["KORECOMMS_DATA_DIR"] = str(stack_paths["comms_data"])
    env["KOREDOCS_DATA_DIR"] = str(stack_paths["docs_data"])
    env["KOREDOCS_DB_PATH"] = str(stack_paths["docs_db"])

    _network_host = str(network.get("host") or "127.0.0.1")
    _korestack_cfg = services.get("korestack") if isinstance(services.get("korestack"), dict) else {}
    _korestack_port = int(_korestack_cfg.get("port", 8600) if _korestack_cfg else 8600)
    _suite_urls: dict[str, str] = {"korestack": f"http://{_network_host}:{_korestack_port}/"}
    for _slug, _meta in SERVICE_META.items():
        _svc_cfg = services.get(_slug) if isinstance(services.get(_slug), dict) else {}
        _port = int(_svc_cfg.get("port", _meta["port"]) if _svc_cfg else _meta["port"])
        _host = str(_svc_cfg.get("host") or _network_host).strip() if _svc_cfg else _network_host
        _key = SERVICE_ICON_KEYS.get(_slug)
        if _key:
            _suite_urls[_key] = f"http://{_host}:{_port}{_meta['url_suffix']}"
    env["KORE_SUITE_URLS"] = json.dumps(_suite_urls)

    return env


def build_services(config: dict) -> dict[str, ServiceSpec]:
    services_cfg = config.get("services") if isinstance(config.get("services"), dict) else {}
    result: dict[str, ServiceSpec] = {}
    for slug, meta in SERVICE_META.items():
        service_cfg = services_cfg.get(slug) if isinstance(services_cfg.get(slug), dict) else {}
        port = int(service_cfg.get("port", meta["port"]))
        host = str(service_cfg.get("host") or "127.0.0.1").strip()
        base_url = f"http://{host}:{port}"
        result[slug] = ServiceSpec(
            slug=slug,
            label=str(meta["label"]),
            cwd=Path(meta["cwd"]),
            script=str(meta["script"]),
            url=base_url + str(meta["url_suffix"]),
            health_url=base_url + str(meta["health_suffix"]),
            description=str(meta["description"]),
        )
    return result


def save_address_to_local_config(slug: str, host: str, port: int) -> None:
    """Persist host+port override for *slug* into config/local.json."""
    config: dict = {}
    if SUITE_CONFIG_LOCAL.exists():
        try:
            config = json.loads(SUITE_CONFIG_LOCAL.read_text(encoding="utf-8"))
        except Exception:
            pass
    if not isinstance(config.get("services"), dict):
        config["services"] = {}
    if not isinstance(config["services"].get(slug), dict):
        config["services"][slug] = {}
    config["services"][slug]["host"] = host
    config["services"][slug]["port"] = port
    if "url" in config["services"][slug]:
        del config["services"][slug]["url"]
    SUITE_CONFIG_LOCAL.parent.mkdir(parents=True, exist_ok=True)
    SUITE_CONFIG_LOCAL.write_text(json.dumps(config, indent=2), encoding="utf-8")


def setup_logging(log_dir: Path) -> None:
    """Route all [korestack] messages to a rolling log file; keep console quiet."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG)
    handler = RotatingFileHandler(
        log_dir / "korestack.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=4,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch and manage the Kore system through KoreStack.")
    parser.add_argument("command", nargs="?", choices=("start", "status"), default="start")
    parser.add_argument(
        "--services",
        default="all",
        help="Comma-separated service list. Valid values: all, agent, conversation, data, docs, code, comms.",
    )
    parser.add_argument("--host", default=None, help="KoreStack landing page bind address.")
    parser.add_argument("--ui-port", type=int, default=None, help="KoreStack landing page port.")
    parser.add_argument("--open-browser", action="store_true", help="Open the KoreStack landing page after startup.")
    parser.add_argument("--no-dashboard", action="store_true", help="Launch child services without starting the KoreStack landing page.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would start without launching child processes.")
    return parser.parse_args()


def resolve_services(raw: str, services: dict[str, ServiceSpec]) -> list[ServiceSpec]:
    selected = [part.strip().lower() for part in raw.split(",") if part.strip()]
    if not selected or selected == ["all"]:
        return [services[key] for key in ("agent", "conversation", "data", "docs", "code", "comms")]

    unknown = [name for name in selected if name not in services]
    if unknown:
        valid = ", ".join(sorted(services))
        raise SystemExit(f"Unknown service selection: {', '.join(unknown)}. Valid values: {valid}, all")

    return [services[name] for name in selected]


def probe_http(url: str, timeout: float = 1.5) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return True, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        if 200 <= exc.code < 500:
            return True, f"HTTP {exc.code}"
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, exc.__class__.__name__


def probe_http_with_retry(url: str, attempts: int = 3, interval: float = 0.8) -> tuple[bool, str]:
    """Probe *url* up to *attempts* times, waiting *interval* seconds between tries."""
    detail = "not tried"
    for _ in range(attempts):
        reachable, detail = probe_http(url)
        if reachable:
            return True, detail
        time.sleep(interval)
    return False, detail


class StackManager:
    def __init__(self, services: list[ServiceSpec], child_env: dict[str, str], stack_paths: dict[str, Path], log_dir: Path) -> None:
        self._services = services
        self._service_map = {spec.slug: spec for spec in services}
        self._child_env = child_env
        self._stack_paths = stack_paths
        self._log_dir = log_dir
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._started_at: dict[str, float] = {}
        self._log_handles: dict[str, IO[bytes]] = {}
        self._lock = threading.Lock()
        self._snapshot_cache: dict | None = None
        self._cache_lock = threading.Lock()
        t = threading.Thread(target=self._refresh_loop, daemon=True)
        t.start()

    def start(self) -> None:
        for spec in self._services:
            self.start_service(spec.slug)

    def start_service(self, slug: str) -> bool:
        spec = self._service_map.get(slug)
        if spec is None:
            raise KeyError(slug)

        with self._lock:
            existing = self._processes.get(slug)
            if existing is not None and existing.poll() is None:
                return False

        reachable, detail = probe_http(spec.health_url)
        if reachable:
            log.info("reusing %s %s url=%s", spec.label, detail, spec.url)
            return False

        script_path = spec.cwd / spec.script
        if not script_path.exists():
            raise SystemExit(f"Missing service entrypoint: {script_path}")

        # Build the spawn command and env, injecting the current port fresh from the spec.
        # This ensures port overrides from local.json are honoured even after set_service_address.
        parsed_url = urllib.parse.urlparse(spec.health_url)
        spawn_port = parsed_url.port
        meta = SERVICE_META.get(slug, {})
        spawn_env = dict(self._child_env)
        port_env_key = meta.get("port_env")
        if port_env_key:
            spawn_env[str(port_env_key)] = str(spawn_port)
        cmd = [sys.executable, spec.script]
        port_arg = meta.get("port_arg")
        if port_arg:
            cmd.extend([str(port_arg), str(spawn_port)])

        self._log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._log_dir / f"{slug}.log"
        log_fh: IO[bytes] = open(log_path, "ab")  # noqa: SIM115
        proc = subprocess.Popen(
            cmd,
            cwd=str(spec.cwd),
            env=spawn_env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )
        with self._lock:
            old_fh = self._log_handles.pop(slug, None)
            self._log_handles[slug] = log_fh
            self._processes[slug] = proc
            self._started_at[slug] = time.time()
        if old_fh is not None:
            try:
                old_fh.close()
            except OSError:
                pass
        log.info("started %s pid=%s cwd=%s log=%s", spec.label, proc.pid, spec.cwd.name, log_path.name)
        return True

    def stop_service(self, slug: str) -> bool:
        spec = self._service_map.get(slug)
        if spec is None:
            raise KeyError(slug)

        with self._lock:
            proc = self._processes.get(slug)
        if proc is None or proc.poll() is not None:
            return False

        log.info("stopping %s pid=%s", slug, proc.pid)
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            log.warning("killing %s pid=%s (terminate timed out)", slug, proc.pid)
            proc.kill()
            proc.wait(timeout=5)
        with self._lock:
            fh = self._log_handles.pop(slug, None)
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass
        return True

    def restart_service(self, slug: str) -> bool:
        self.stop_service(slug)
        return self.start_service(slug)

    def set_service_address(self, slug: str, host: str, port: int) -> bool:
        """Reassign host+port, save to local config, stop and restart the service."""
        spec = self._service_map.get(slug)
        if spec is None:
            raise KeyError(slug)
        meta = SERVICE_META[slug]
        base_url = f"http://{host}:{port}"
        new_spec = ServiceSpec(
            slug=slug,
            label=spec.label,
            cwd=spec.cwd,
            script=spec.script,
            url=base_url + str(meta["url_suffix"]),
            health_url=base_url + str(meta["health_suffix"]),
            description=spec.description,
        )
        save_address_to_local_config(slug, host, port)
        self.stop_service(slug)
        with self._lock:
            for i, s in enumerate(self._services):
                if s.slug == slug:
                    self._services[i] = new_spec
                    break
            self._service_map[slug] = new_spec
        return self.start_service(slug)

    def stop(self) -> None:
        with self._lock:
            items = list(self._processes.items())

        for slug, proc in reversed(items):
            if proc.poll() is not None:
                continue
            log.info("stopping %s pid=%s", slug, proc.pid)
            proc.terminate()

        for slug, proc in reversed(items):
            if proc.poll() is not None:
                continue
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                log.warning("killing %s pid=%s (terminate timed out)", slug, proc.pid)
                proc.kill()
                proc.wait(timeout=5)

        with self._lock:
            handles = list(self._log_handles.values())
            self._log_handles.clear()
        for fh in handles:
            try:
                fh.close()
            except OSError:
                pass

    def _refresh_loop(self) -> None:
        """Background thread: refreshes the probe cache every 3 seconds."""
        while True:
            try:
                fresh = self._compute_snapshot()
                with self._cache_lock:
                    self._snapshot_cache = fresh
            except Exception:
                pass
            time.sleep(3)

    def snapshot(self) -> dict[str, object]:
        """Return the most recently cached snapshot (instant), computing fresh on first call."""
        with self._cache_lock:
            cached = self._snapshot_cache
        if cached is not None:
            return cached
        # First call before background thread has run — compute synchronously.
        fresh = self._compute_snapshot()
        with self._cache_lock:
            self._snapshot_cache = fresh
        return fresh

    def _compute_snapshot(self) -> dict[str, object]:
        entries: list[dict[str, object]] = []
        with self._lock:
            processes = dict(self._processes)

        def _probe_one(spec: ServiceSpec) -> tuple[str, bool, str]:
            reachable, detail = probe_http(spec.health_url)
            return spec.slug, reachable, detail

        with ThreadPoolExecutor(max_workers=len(self._services) or 1) as pool:
            probe_results: dict[str, tuple[bool, str]] = {}
            for slug, reachable, detail in pool.map(_probe_one, self._services):
                probe_results[slug] = (reachable, detail)

        for spec in self._services:
            proc = processes.get(spec.slug)
            running = proc is not None and proc.poll() is None
            reachable, detail = probe_results.get(spec.slug, (False, "unknown"))
            parsed = urllib.parse.urlparse(spec.url)
            started_at = self._started_at.get(spec.slug)
            entries.append(
                {
                    "slug": spec.slug,
                    "label": spec.label,
                    "iconKey": SERVICE_ICON_KEYS.get(spec.slug, "korestack"),
                    "description": spec.description,
                    "url": spec.url,
                    "healthUrl": spec.health_url,
                    "host": parsed.hostname or "-",
                    "port": parsed.port or "-",
                    "cwd": str(spec.cwd.relative_to(SUITE_ROOT)),
                    "running": running,
                    "reachable": reachable,
                    "status": detail,
                    "pid": proc.pid if running else None,
                    "returncode": None if proc is None or running else proc.returncode,
                    "uptimeSec": round(time.time() - started_at, 1) if running and started_at else None,
                }
            )

        running_count = sum(1 for entry in entries if entry["running"])
        reachable_count = sum(1 for entry in entries if entry["reachable"])
        return {
            "stack": {
                "label": "KoreStack",
                "root": str(SUITE_ROOT),
                "configPath": str(SUITE_CONFIG_DEFAULT),
                "uiElementsMounted": get_ui_assets_dir().exists(),
                "services": [spec.slug for spec in self._services],
                "metrics": {
                    "selected": len(entries),
                    "running": running_count,
                    "reachable": reachable_count,
                },
                "paths": {key: str(value) for key, value in self._stack_paths.items()},
            },
            "services": entries,
        }


def _topology_markup(services: list[dict[str, object]]) -> str:
    slots = {
        0: "slot-a",
        1: "slot-b",
        2: "slot-c",
        3: "slot-d",
        4: "slot-e",
    }
    chunks = []
    for index, service in enumerate(services):
        css_state = "up" if service["reachable"] else ("starting" if service["running"] else "down")
        state_label = "Reachable" if service["reachable"] else "Starting" if service["running"] else "Stopped"
        chunks.append(
            f"<article class=\"diagram-node service-{service['slug']} {slots.get(index, 'slot-a')} {css_state}\"><p>{service['label']}</p><strong>{service['host']}:{service['port']}</strong><span>{state_label}</span></article>"
        )
    return "".join(chunks)


def build_suite_urls(manager: StackManager, dashboard_url: str) -> dict[str, str]:
    """Return the topbar URL map keyed by topbar service key (e.g. 'koreagent')."""
    urls: dict[str, str] = {"korestack": dashboard_url}
    for service in manager.snapshot()["services"]:
        topbar_key = SERVICE_ICON_KEYS.get(service["slug"])
        if topbar_key:
            urls[topbar_key] = service["url"]
    return urls


def html_page(manager: StackManager, dashboard_url: str) -> str:
    snapshot = manager.snapshot()
    stack = snapshot["stack"]
    metrics = stack["metrics"]
    suite_urls = build_suite_urls(manager, dashboard_url)
    rows = []
    for service in snapshot["services"]:
        css_state = "up" if service["reachable"] else ("starting" if service["running"] else "down")
        state_label = "Reachable" if service["reachable"] else "Starting" if service["running"] else "Stopped"
        tag_color = "accent" if service["reachable"] else ("warning" if service["running"] else "danger")
        rows.append(
            f"""
                        <article class="service-row service-{service['slug']} {css_state}" data-service-card="{service['slug']}">
                            <div class="service-cell service-glyph" aria-hidden="true" data-suite-icon="{service['iconKey']}"></div>
                            <div class="service-cell service-core">
                                <p class="eyebrow">{service['slug'].upper()}</p>
                                <h2>{service['label']}</h2>
                                <p class="service-copy">{service['description']}</p>
                            </div>
                            <div class="service-cell service-state"><span class="kcui-tag kcui-tag--{tag_color}" data-field="state">{state_label}</span></div>
                            <div class="service-cell address-edit"><input type="text" class="host-input" data-field="host" value="{service['host']}" aria-label="Host for {service['label']}"><input type="number" class="port-input" data-field="port" value="{service['port']}" min="1024" max="65535" aria-label="Port for {service['label']}"><button class="kcui-tag kcui-tag--dim" type="button" data-service="{service['slug']}" data-action="setaddress" title="Save address and restart {service['label']}">set</button></div>
                            <div class="service-cell service-link"><a data-field="url" href="{service['url']}" target="_blank" rel="noreferrer">{service['url']}</a></div>
                            <div class="service-cell service-actions actions">
                                <button type="button" data-service="{service['slug']}" data-action="start" title="Start" aria-label="Start {service['label']}">
                                    <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3.5v9l7-4.5-7-4.5Z" fill="currentColor"/></svg>
                                </button>
                                <button type="button" data-service="{service['slug']}" data-action="stop" title="Stop" aria-label="Stop {service['label']}">
                                    <svg viewBox="0 0 16 16" aria-hidden="true"><rect x="4" y="4" width="8" height="8" fill="currentColor"/></svg>
                                </button>
                                <button type="button" data-service="{service['slug']}" data-action="restart" title="Restart" aria-label="Restart {service['label']}">
                                    <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3a5 5 0 1 1-4.24 2.35" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><path d="M2.8 2.8h3.6v3.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>
                                </button>
                            </div>
                        </article>
            """
        )

    ui_note = "available" if get_ui_assets_dir().exists() else "missing"
    data_rows = "".join(
        f'''
            <div class="path-row">
                <dt>{key}</dt>
                <dd><code>{value}</code></dd>
            </div>
        '''
        for key, value in stack["paths"].items()
    )
    services_json = json.dumps(snapshot)
    suite_urls_json = json.dumps(suite_urls)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>KoreStack</title>
  <link rel="stylesheet" href="/ui-elements/assets/css/chrome.css">
    <link rel="stylesheet" href="/ui-elements/assets/css/workspace.css">
  <style>
    :root {{
            color-scheme: dark;
            --stack-bg: #0b0c10;
            --stack-panel: #0f1117;
            --stack-panel-strong: #0f1117;
            --stack-ink: #c5c8d0;
            --stack-muted: #4e5466;
            --stack-line: #1e2233;
            --stack-accent: #6eb5ff;
            --stack-success: #4af77a;
            --stack-warn: #f0c060;
            --stack-danger: #ff5f5f;
            --service-agent: #66f0c9;
            --service-conversation: #59d7ff;
            --service-data: #a78bfa;
            --service-docs: #ffd166;
            --service-code: #7ee081;
            --service-comms: #ff8fab;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
            font-family: "Cascadia Code", "Fira Code", Consolas, "Courier New", monospace;
            color: var(--stack-ink);
            background: var(--stack-bg);
            min-height: 100vh;
            display: grid;
            grid-template-rows: auto auto auto;
            overflow-x: hidden;
            overflow-y: auto;
        }}
        a {{ color: inherit; }}
        .shell {{
            display: grid;
            grid-template-rows: auto auto;
            gap: 12px;
            padding: 12px 0;
    }}
        .panel {{ background: var(--stack-panel); border: 1px solid var(--stack-line); border-radius: 2px; }}
        .eyebrow {{ margin: 0; color: var(--stack-accent); font-size: 10px; letter-spacing: 0.18em; text-transform: uppercase; }}
        .stack-workspace {{
            grid-template-columns: minmax(0, 1fr);
            grid-template-rows: auto auto;
            gap: 12px;
        }}
        .stack-region {{ min-width: 0; }}
        .paths-panel {{ overflow: hidden; }}
        .paths-header {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; }}
        .paths-header p:last-child {{ margin: 0; color: var(--stack-muted); font-size: 11px; }}
        .paths-list {{ display: grid; gap: 0; margin: 0; }}
        .path-row {{ display: grid; grid-template-columns: minmax(120px, 180px) minmax(0, 1fr); gap: 12px; align-items: start; padding: 8px 0; min-width: 0; border-top: 1px solid var(--stack-line); }}
        .path-row:first-child {{ border-top: 0; padding-top: 0; }}
        .path-row:last-child {{ padding-bottom: 0; }}
        .path-row dt {{ color: var(--stack-muted); margin: 0; font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; }}
        .path-row dd {{ margin: 0; min-width: 0; }}
        .path-row code {{ display: block; color: var(--stack-accent); font-size: 0.9rem; overflow-wrap: anywhere; }}
        .services-panel {{ overflow: hidden; }}
        .services-header {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; }}
        .services-header p:last-child {{ margin: 0; color: var(--stack-muted); font-size: 11px; }}
        .services-grid {{ display: grid; gap: 12px; }}
        .service-row {{ display: grid; grid-template-columns: 60px minmax(180px, 1.45fr) auto auto minmax(220px, 1.4fr) auto; gap: 8px; align-items: stretch; padding: 0 10px 0 0; position: relative; background: var(--stack-panel-strong); border: 1px solid var(--stack-line); border-radius: 2px; min-width: 0; }}
        .service-row::before {{ content: ""; position: absolute; inset: 0 auto 0 0; width: 3px; background: var(--service-accent, var(--stack-accent)); }}
        .service-cell {{ min-width: 0; }}
        .service-glyph {{ width: 100%; height: 100%; min-height: 60px; align-self: stretch; display: inline-flex; align-items: center; justify-content: center; color: var(--service-accent, var(--stack-accent)); border-left: 1px solid color-mix(in srgb, var(--service-accent, var(--stack-accent)) 38%, transparent); border-right: 1px solid color-mix(in srgb, var(--service-accent, var(--stack-accent)) 38%, transparent); background: color-mix(in srgb, var(--service-accent, var(--stack-accent)) 10%, transparent); }}
        .service-glyph svg {{ width: 32px; height: 32px; display: block; }}
        .service-core, .service-state, .address-edit, .service-link, .service-actions {{ display: flex; align-items: center; min-height: 60px; padding-block: 8px; }}
        .service-core {{ display: grid; align-content: center; }}
        .service-state {{ justify-content: center; }}
        .address-edit {{ gap: 4px; }}
        .host-input {{ width: 108px; background: var(--stack-bg); border: 1px solid var(--stack-line); color: var(--stack-ink); font-family: inherit; font-size: 11px; padding: 2px 4px; border-radius: 2px; }}
        .host-input:focus {{ outline: none; border-color: var(--stack-accent); }}
        .port-input {{ width: 58px; background: var(--stack-bg); border: 1px solid var(--stack-line); color: var(--stack-ink); font-family: inherit; font-size: 11px; padding: 2px 4px; border-radius: 2px; }}
        .port-input:focus {{ outline: none; border-color: var(--stack-accent); }}
        .port-input::-webkit-inner-spin-button, .port-input::-webkit-outer-spin-button {{ -webkit-appearance: none; }}
        .port-input {{ -moz-appearance: textfield; }}
        .service-notice {{ position: absolute; bottom: 3px; right: 10px; font-size: 10px; opacity: 0; transition: opacity 0.25s; pointer-events: none; }}
        .service-notice.is-visible {{ opacity: 1; }}
        .service-notice[data-tone="ok"] {{ color: var(--stack-success); }}
        .service-notice[data-tone="warn"] {{ color: var(--stack-warn); }}
        .service-notice[data-tone="error"] {{ color: var(--stack-danger); }}
        .service-core h2 {{ margin: 0; font-size: 0.95rem; }}
        /* service state uses .kcui-tag from UIElements */
        .service-copy {{ color: var(--stack-muted); line-height: 1.3; font-size: 10px; margin: 2px 0 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
        .service-link a {{ display: block; font-size: 11px; text-decoration: none; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
        .actions {{ display: inline-flex; gap: 4px; justify-content: flex-end; }}
        .actions button {{ width: 24px; height: 24px; display: inline-flex; align-items: center; justify-content: center; border: 1px solid color-mix(in srgb, var(--service-accent, var(--stack-accent)) 42%, transparent); border-radius: 2px; padding: 0; background: var(--stack-bg); color: var(--stack-ink); cursor: pointer; font: inherit; font-size: 10px; }}
    .actions button:hover {{ background: color-mix(in srgb, var(--service-accent, var(--stack-accent)) 14%, var(--stack-bg)); }}
        .actions button svg {{ width: 13px; height: 13px; display: block; }}
        .footer-body {{ display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }}
        .footer-body p {{ margin: 0; color: var(--stack-muted); font-size: 11px; }}
        code {{ font-family: inherit; font-size: 0.92rem; color: var(--stack-accent); }}
        .service-agent {{ --service-accent: var(--service-agent); }}
        .service-conversation {{ --service-accent: var(--service-conversation); }}
        .service-data {{ --service-accent: var(--service-data); }}
        .service-docs {{ --service-accent: var(--service-docs); }}
        .service-code {{ --service-accent: var(--service-code); }}
        .service-comms {{ --service-accent: var(--service-comms); }}
    @media (max-width: 860px) {{
            .path-row {{ grid-template-columns: 1fr; gap: 6px; }}
                .service-row {{ grid-template-columns: 1fr; align-items: start; }}
                .service-actions {{ justify-self: start; }}
    }}
  </style>
</head>
<body class="kcui-shell-bg">
    <div id="topbar"></div>
    <div id="app-bar"></div>
    <main class="shell kcui-page kcui-page--narrow">
        <section
            class="kcui-workspace kcui-workspace--dashboard kcui-workspace--stack-sm stack-workspace"
        >
            <div class="kcui-workspace__region stack-region">
                <section class="panel kcui-panel paths-panel">
                    <div class="paths-header kcui-panel-header">
                        <p class="eyebrow">System Paths</p>
                        <p>Shared suite storage and document locations.</p>
                    </div>
                    <div class="kcui-panel-body"><dl class="paths-list">{data_rows}</dl></div>
                </section>
            </div>

            <div class="kcui-workspace__region stack-region">
                <section class="panel kcui-panel services-panel">
                    <div class="services-header kcui-panel-header">
                        <p class="eyebrow">Services</p>
                        <p>Compact live rows with inline controls.</p>
                    </div>
                    <div class="kcui-panel-body"><section class="services-grid">{''.join(rows)}</section></div>
                </section>
            </div>
        </section>

        <section class="panel kcui-panel footer">
            <div class="kcui-panel-body footer-body">
            <p>Updates every 2 seconds without reloading the page.</p>
            <p>Root command: <code>python .\\main.py --services {','.join(stack['services'])}</code></p>
            </div>
        </section>
  </main>
  <script type="module">
    import {{ initAppBar, initTopbar }} from '/ui-elements/assets/js/chrome.js?v=20260501a';
        function topbarIconFor(serviceKey) {{
            return document.querySelector(`.ktopbar-item[data-service="${{serviceKey}}"] .ktopbar-icon`)?.innerHTML || '';
        }}

        function initServicePanelIcons() {{
            const serviceKeyBySlug = {{
                agent: 'koreagent',
                conversation: 'korechat',
                data: 'koredata',
                docs: 'koredocs',
                code: 'korecode',
                comms: 'korecomms',
            }};
            for (const row of document.querySelectorAll('[data-service-card]')) {{
                const serviceKey = serviceKeyBySlug[row.dataset.serviceCard];
                const glyph = row.querySelector('.service-glyph');
                if (!serviceKey || !glyph) continue;
                const iconHtml = topbarIconFor(serviceKey);
                if (iconHtml) glyph.innerHTML = iconHtml;
            }}
        }}

    try {{ localStorage.setItem('kore.suite-urls', JSON.stringify({suite_urls_json})); }} catch (_) {{}}
    initTopbar({{ currentService: 'korestack', urls: {suite_urls_json} }});
      initAppBar({{
          currentService: 'korestack',
          overline: 'Landing page & Config',
          brandLabel: 'KoreStack',
          brandIcon: 'korestack',
          chips: [
              {{ label: 'Running', value: '{metrics['running']} / {metrics['selected']}', tone: 'accent' }},
              {{ label: 'Reachable', value: '{metrics['reachable']}' }},
              {{ label: 'Dashboard', value: '{dashboard_url}' }},
              {{ label: 'UIElements', value: '{ui_note}' }},
          ],
      }});
    initServicePanelIcons();
    window._refreshTopbar = (urls) => {{
        initTopbar({{ currentService: 'korestack', urls }});
        initServicePanelIcons();
    }};
  </script>
  <script>
        let current = {services_json};
        function stateForService(service) {{
            return service.reachable ? 'up' : (service.running ? 'starting' : 'down');
        }}
        function stateLabel(service) {{
            return service.reachable ? 'Reachable' : (service.running ? 'Starting' : 'Stopped');
        }}
        function setText(node, value) {{
            if (node) node.textContent = value;
        }}
        function updateCard(service) {{
            const card = document.querySelector(`[data-service-card="${{service.slug}}"]`);
            if (!card) return;
            card.classList.remove('up', 'starting', 'down');
            card.classList.add(stateForService(service));
            const stateTag = card.querySelector('[data-field="state"]');
            setText(stateTag, stateLabel(service));
            if (stateTag) {{
                stateTag.classList.remove('kcui-tag--accent', 'kcui-tag--warning', 'kcui-tag--danger');
                const STATE_COLOR = {{ up: 'accent', starting: 'warning', down: 'danger' }};
                stateTag.classList.add(`kcui-tag--${{STATE_COLOR[stateForService(service)] || 'dim'}}`);
            }}
            const hostInput = card.querySelector('[data-field="host"]');
            if (hostInput && hostInput !== document.activeElement && !hostInput.dataset.dirty)
                hostInput.value = service.host ?? '-';
            const portInput = card.querySelector('[data-field="port"]');
            if (portInput && portInput !== document.activeElement && !portInput.dataset.dirty)
                portInput.value = String(service.port ?? '');
            const urlLink = card.querySelector('[data-field="url"]');
            if (urlLink) {{
                urlLink.textContent = service.url;
                urlLink.href = service.url;
            }}
        }}
        function applySnapshot(next) {{
            current = next;
            try {{
                const slugToKey = {{agent:'koreagent',conversation:'korechat',data:'koredata',docs:'koredocs',code:'korecode',comms:'korecomms'}};
                const urls = {{korestack: window.location.origin + '/'}};
                for (const svc of next.services) {{ if (slugToKey[svc.slug]) urls[slugToKey[svc.slug]] = svc.url; }}
                const prev = localStorage.getItem('kore.suite-urls');
                localStorage.setItem('kore.suite-urls', JSON.stringify(urls));
                if (prev !== JSON.stringify(urls) && typeof window._refreshTopbar === 'function') {{
                    window._refreshTopbar(urls);
                }}
            }} catch (_) {{}}
            const metrics = next.stack.metrics;
            const running = document.querySelector('[data-stack-field="running"] strong');
            const reachable = document.querySelector('[data-stack-field="reachable"] strong');
            const dashboard = document.querySelector('[data-stack-field="dashboard"] strong');
            const ui = document.querySelector('[data-stack-field="ui"] strong');
            setText(running, `${{metrics.running}} / ${{metrics.selected}}`);
            setText(reachable, String(metrics.reachable));
            setText(dashboard, window.location.href);
            setText(ui, next.stack.uiElementsMounted ? 'available' : 'missing');
            for (const service of next.services) {{
                updateCard(service);
            }}
        }}
    async function refresh() {{
      try {{
        const response = await fetch('/status', {{ cache: 'no-store' }});
        if (!response.ok) return;
        const next = await response.json();
                applySnapshot(next);
      }} catch (_error) {{
      }}
    }}
    function showNotice(card, msg, tone) {{
        if (!card) return;
        let notice = card.querySelector('.service-notice');
        if (!notice) {{
            notice = document.createElement('div');
            notice.className = 'service-notice';
            card.appendChild(notice);
        }}
        notice.textContent = msg;
        notice.dataset.tone = tone || '';
        notice.classList.add('is-visible');
        clearTimeout(notice._timer);
        notice._timer = window.setTimeout(() => notice.classList.remove('is-visible'), 4000);
    }}
    async function serviceAction(service, action) {{
            const buttonSet = document.querySelectorAll(`[data-service="${{service}}"]`);
            const card = document.querySelector(`[data-service-card="${{service}}"]`);
            buttonSet.forEach((button) => button.disabled = true);
      try {{
                const response = await fetch(`/api/services/${{service}}/${{action}}`, {{ method: 'POST' }});
                const result = response.ok ? await response.json().catch(() => null) : null;
                await refresh();
                if (action === 'stop') {{
                    showNotice(card, 'Stopped', 'warn');
                }} else if (action === 'start' || action === 'restart') {{
                    if (result?.reachable) showNotice(card, '✓ Reachable', 'ok');
                    else showNotice(card, '⚠ Starting – check logs', 'warn');
                }}
      }} finally {{
                window.setTimeout(() => buttonSet.forEach((button) => button.disabled = false), 300);
      }}
    }}
    async function setAddressAction(slug, host, port) {{
        const btn = document.querySelector(`[data-service="${{slug}}"][data-action="setaddress"]`);
        const card = document.querySelector(`[data-service-card="${{slug}}"]`);
        const cell = btn?.closest('.address-edit');
        const hostInput = cell?.querySelector('.host-input');
        const portInput = cell?.querySelector('.port-input');
        if (btn) btn.disabled = true;
        try {{
            const response = await fetch(`/api/services/${{slug}}/setaddress`, {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{host, port: parseInt(port, 10)}}),
            }});
            const result = response.ok ? await response.json().catch(() => null) : null;
            if (hostInput) delete hostInput.dataset.dirty;
            if (portInput) delete portInput.dataset.dirty;
            await refresh();
            if (result?.reachable) showNotice(card, '✓ Reachable', 'ok');
            else showNotice(card, '⚠ Starting – check logs', 'warn');
        }} finally {{
            window.setTimeout(() => {{ if (btn) btn.disabled = false; }}, 600);
        }}
    }}
    for (const button of document.querySelectorAll('[data-service][data-action]:not([data-action="setaddress"])')) {{
      button.addEventListener('click', () => serviceAction(button.dataset.service, button.dataset.action));
    }}
    for (const button of document.querySelectorAll('[data-action="setaddress"]')) {{
        button.addEventListener('click', () => {{
            const cell = button.closest('.address-edit');
            const host = cell?.querySelector('.host-input')?.value?.trim();
            const port = cell?.querySelector('.port-input')?.value;
            if (host && port) setAddressAction(button.dataset.service, host, port);
        }});
    }}
    for (const input of document.querySelectorAll('.port-input, .host-input')) {{
        input.addEventListener('input', () => {{ input.dataset.dirty = '1'; }});
        input.addEventListener('blur', () => {{ if (input.dataset.dirty) delete input.dataset.dirty; }});
    }}
    window.setInterval(refresh, 2000);
  </script>
</body>
</html>"""


def build_handler(manager: StackManager, dashboard_url: str):
    class StackHandler(BaseHTTPRequestHandler):
        manager_ref: ClassVar[StackManager] = manager
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
                body = html_page(self.manager_ref, self.dashboard_ref).encode("utf-8")
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
                body = json.dumps(build_suite_urls(self.manager_ref, self.dashboard_ref)).encode("utf-8")
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
                self._serve_ui_asset(request_path.removeprefix("/ui-elements/assets/"))
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

        def _serve_ui_asset(self, relative_path: str) -> None:
            assets_dir = get_ui_assets_dir()
            asset_path = (assets_dir / relative_path).resolve()
            if not assets_dir.exists() or assets_dir.resolve() not in asset_path.parents:
                self.send_error(HTTPStatus.NOT_FOUND, "Asset not available")
                return
            if not asset_path.exists() or not asset_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "Asset not found")
                return

            content_type = "application/octet-stream"
            if asset_path.suffix == ".css":
                content_type = "text/css; charset=utf-8"
            elif asset_path.suffix == ".js":
                content_type = "text/javascript; charset=utf-8"
            elif asset_path.suffix == ".svg":
                content_type = "image/svg+xml"

            data = asset_path.read_bytes()
            self._send_bytes(data, content_type)

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
            # Invalidate cache so the next /status poll reflects the change immediately.
            with self.manager_ref._cache_lock:
                self.manager_ref._snapshot_cache = None
            result: dict = {"ok": True, "service": slug, "action": action, "changed": changed}
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


def print_snapshot(snapshot: dict[str, object]) -> None:
    print("KoreStack status")
    for service in snapshot["services"]:
        state = "up" if service["reachable"] else "starting" if service["running"] else "down"
        print(f"  {service['label']:<6} {state:<8} pid={service['pid'] or '-':<6} probe={service['status']:<16} url={service['url']}")


def serve_dashboard(manager: StackManager, host: str, port: int, stop_event: threading.Event) -> None:
    dashboard_url = f"http://{host}:{port}/"
    httpd = ThreadingHTTPServer((host, port), build_handler(manager, dashboard_url))
    httpd.timeout = 0.5
    print(f"KoreStack  {dashboard_url}  (logs → KoreStack/logs/)")
    log.info("landing page %s", dashboard_url)
    while not stop_event.is_set():
        httpd.handle_request()
    httpd.server_close()


def _get_stack_service_config(config: dict) -> dict:
    services_cfg = config.get("services") if isinstance(config.get("services"), dict) else {}
    stack_cfg = services_cfg.get("korestack") if isinstance(services_cfg.get("korestack"), dict) else {}
    if stack_cfg:
        return stack_cfg
    return services_cfg.get("suite") if isinstance(services_cfg.get("suite"), dict) else {}


def main() -> int:
    args = parse_args()
    suite_config = load_suite_config()
    all_services = build_services(suite_config)
    services = resolve_services(args.services, all_services)
    child_env = build_child_env(suite_config)
    stack_paths = get_stack_paths(suite_config)

    stack_paths["datacontrol"].mkdir(parents=True, exist_ok=True)
    stack_paths["datauser"].mkdir(parents=True, exist_ok=True)
    stack_paths["conversation_data"].mkdir(parents=True, exist_ok=True)
    stack_paths["comms_data"].mkdir(parents=True, exist_ok=True)
    (stack_paths["datacontrol"] / "logs").mkdir(parents=True, exist_ok=True)
    (stack_paths["datacontrol"] / "schedules").mkdir(parents=True, exist_ok=True)
    (stack_paths["datacontrol"] / "test_prompts").mkdir(parents=True, exist_ok=True)
    (stack_paths["datacontrol"] / "test_results").mkdir(parents=True, exist_ok=True)
    (stack_paths["datacontrol"] / "chatsessions").mkdir(parents=True, exist_ok=True)
    (stack_paths["datacontrol"] / "chatsessions" / "named").mkdir(parents=True, exist_ok=True)
    stack_paths["docs_data"].mkdir(parents=True, exist_ok=True)
    stack_paths["docs_db"].parent.mkdir(parents=True, exist_ok=True)

    log_dir = STACK_ROOT / "logs"
    setup_logging(log_dir)

    manager = StackManager(services, child_env, stack_paths, log_dir)

    network_cfg = suite_config.get("network") if isinstance(suite_config.get("network"), dict) else {}
    stack_cfg = _get_stack_service_config(suite_config)
    dashboard_host = args.host or str(network_cfg.get("host") or "127.0.0.1")
    dashboard_port = int(args.ui_port or stack_cfg.get("port") or 8600)

    if args.command == "status":
        print_snapshot(manager.snapshot())
        return 0

    if args.dry_run:
        print("KoreStack dry run")
        for spec in services:
            print(f"  {spec.label:<6} {spec.cwd / spec.script} -> {spec.url}")
        if not args.no_dashboard:
            print(f"  korestack http://{dashboard_host}:{dashboard_port}/")
        return 0

    manager.start()

    stop_event = threading.Event()
    dashboard_thread: threading.Thread | None = None
    if not args.no_dashboard:
        dashboard_thread = threading.Thread(target=serve_dashboard, args=(manager, dashboard_host, dashboard_port, stop_event), daemon=True)
        dashboard_thread.start()
        if args.open_browser:
            webbrowser.open(f"http://{dashboard_host}:{dashboard_port}/")

    def _signal_handler(signum: int, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal_handler)

    try:
        while not stop_event.is_set():
            with manager._lock:
                live = [proc for proc in manager._processes.values() if proc.poll() is None]
            if not live and args.no_dashboard:
                break
            if not live and dashboard_thread is None:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        stop_event.set()
        manager.stop()
        if dashboard_thread is not None:
            dashboard_thread.join(timeout=2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
