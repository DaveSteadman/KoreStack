from __future__ import annotations

import argparse
import json
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
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar


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
        "port": 8000,
        "url_suffix": "/",
        "health_suffix": "/",
        "description": "KoreAgent web UI and orchestration surface.",
    },
    "conversation": {
        "label": "KoreConversation",
        "cwd": SUITE_ROOT / "KoreConversation",
        "script": "main.py",
        "port": 8700,
        "url_suffix": "/ui",
        "health_suffix": "/status",
        "description": "Shared conversation-state service for agent and comms flows.",
    },
    "data": {
        "label": "KoreData",
        "cwd": SUITE_ROOT / "KoreData",
        "script": "main.py",
        "port": 8800,
        "url_suffix": "/",
        "health_suffix": "/status",
        "description": "KoreData gateway with the feed, library, reference, and RAG services behind it.",
    },
    "docs": {
        "label": "KoreDocs",
        "cwd": SUITE_ROOT / "KoreDocs",
        "script": "main.py",
        "port": 5500,
        "url_suffix": "/kf",
        "health_suffix": "/kf",
        "description": "KoreDocs file manager plus the doc, sheet, and diagram editors.",
    },
    "comms": {
        "label": "KoreComms",
        "cwd": SUITE_ROOT / "KoreComms",
        "script": "main.py",
        "port": 8900,
        "url_suffix": "/",
        "health_suffix": "/status",
        "description": "KoreComms UI and API for conversation and activity flows.",
    },
}


def get_ui_assets_dir() -> Path:
    return UI_ELEMENTS_ASSETS


def suite_icon_svg(key: str, size: int = 24) -> str:
        icon_key = key.lower()
        s = f'width="{size}" height="{size}"'
        icons = {
                "korestack": f"""<svg {s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
    <path d="M3 6.5h14M3 10h14M3 13.5h14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
    <rect x="4" y="3" width="4" height="4" rx="1" fill="currentColor" opacity=".95"/>
    <rect x="12" y="8" width="4" height="4" rx="1" fill="currentColor" opacity=".75"/>
    <rect x="7" y="13" width="4" height="4" rx="1" fill="currentColor" opacity=".55"/>
</svg>""",
                "agent": f"""<svg {s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
    <rect x="5" y="5" width="10" height="10" rx="2" stroke="currentColor" stroke-width="1.6"/>
    <path d="M8 2.8 10 5l2-2.2" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>
    <circle cx="8" cy="10" r="1" fill="currentColor"/>
    <circle cx="12" cy="10" r="1" fill="currentColor"/>
    <path d="M8 12.8h4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
</svg>""",
                "conversation": f"""<svg {s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
    <rect x="2.5" y="3" width="15" height="11" rx="2" stroke="currentColor" stroke-width="1.6"/>
    <path d="M6 14v3l3.3-3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
    <circle cx="7" cy="8.5" r=".9" fill="currentColor"/>
    <circle cx="10" cy="8.5" r=".9" fill="currentColor"/>
    <circle cx="13" cy="8.5" r=".9" fill="currentColor"/>
</svg>""",
                "data": f"""<svg {s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
    <ellipse cx="10" cy="5" rx="5.5" ry="2.5" stroke="currentColor" stroke-width="1.4"/>
    <path d="M4.5 5v7c0 1.4 2.46 2.5 5.5 2.5s5.5-1.1 5.5-2.5V5" stroke="currentColor" stroke-width="1.4"/>
    <path d="M4.5 8.5c0 1.4 2.46 2.5 5.5 2.5s5.5-1.1 5.5-2.5" stroke="currentColor" stroke-width="1.4"/>
</svg>""",
                "docs": f"""<svg {s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
    <path d="M5 3.5h8l2 2v10.5a1.5 1.5 0 0 1-1.5 1.5h-8A1.5 1.5 0 0 1 4 16V5a1.5 1.5 0 0 1 1-1.5Z" stroke="currentColor" stroke-width="1.5"/>
    <path d="M13 3.5V6h2.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    <path d="M7 9h6M7 12h6M7 15h4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
</svg>""",
                "comms": f"""<svg {s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
    <path d="M4 6.5h12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
    <path d="M4 10h7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
    <path d="M4 13.5h12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
    <path d="M13.5 8.5 16.5 10l-3 1.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
</svg>""",
        }
        return icons.get(icon_key, icons["korestack"])


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

    env["KORE_SUITE_ROOT"] = str(SUITE_ROOT)
    env["KORE_SUITE_CONFIG"] = str(SUITE_CONFIG_DEFAULT)
    env["KORE_SUITE_DATACONTROL"] = str(stack_paths["datacontrol"])
    env["KORE_SUITE_DATAUSER"] = str(stack_paths["datauser"])
    env["KORE_UIELEMENTS_ASSETS_DIR"] = str(get_ui_assets_dir())

    conversation = services.get("conversation") if isinstance(services.get("conversation"), dict) else {}
    comms = services.get("comms") if isinstance(services.get("comms"), dict) else {}

    if network.get("host"):
        env["KORECONVERSATION_HOST"] = str(network["host"])
        env["KORECOMMS_HOST"] = str(network["host"])

    if conversation.get("port") is not None:
        env["KORECONVERSATION_PORT"] = str(conversation["port"])
    if comms.get("port") is not None:
        env["KORECOMMS_PORT"] = str(comms["port"])

    if connections.get("koreconversation"):
        env["KORECOMMS_KORECONVERSATION_URL"] = str(connections["koreconversation"])

    env["KORECONVERSATION_DATA_DIR"] = str(stack_paths["conversation_data"])
    env["KORECOMMS_DATA_DIR"] = str(stack_paths["comms_data"])
    env["KOREDOCS_DATA_DIR"] = str(stack_paths["docs_data"])
    env["KOREDOCS_DB_PATH"] = str(stack_paths["docs_db"])
    return env


def build_services(config: dict) -> dict[str, ServiceSpec]:
    services_cfg = config.get("services") if isinstance(config.get("services"), dict) else {}
    result: dict[str, ServiceSpec] = {}
    for slug, meta in SERVICE_META.items():
        service_cfg = services_cfg.get(slug) if isinstance(services_cfg.get(slug), dict) else {}
        port = int(service_cfg.get("port", meta["port"]))
        base_url = str(service_cfg.get("url") or f"http://127.0.0.1:{port}").rstrip("/")
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch and manage the Kore system through KoreStack.")
    parser.add_argument("command", nargs="?", choices=("start", "status"), default="start")
    parser.add_argument(
        "--services",
        default="all",
        help="Comma-separated service list. Valid values: all, agent, conversation, data, docs, comms.",
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
        return [services[key] for key in ("agent", "conversation", "data", "docs", "comms")]

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


class StackManager:
    def __init__(self, services: list[ServiceSpec], child_env: dict[str, str], stack_paths: dict[str, Path]) -> None:
        self._services = services
        self._service_map = {spec.slug: spec for spec in services}
        self._child_env = child_env
        self._stack_paths = stack_paths
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._started_at: dict[str, float] = {}
        self._lock = threading.Lock()

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
            print(f"[korestack] reusing {spec.label:<6} {detail} url={spec.url}")
            return False

        script_path = spec.cwd / spec.script
        if not script_path.exists():
            raise SystemExit(f"Missing service entrypoint: {script_path}")

        proc = subprocess.Popen([sys.executable, spec.script], cwd=str(spec.cwd), env=self._child_env)
        with self._lock:
            self._processes[slug] = proc
            self._started_at[slug] = time.time()
        print(f"[korestack] started {spec.label:<6} pid={proc.pid} cwd={spec.cwd.name}")
        return True

    def stop_service(self, slug: str) -> bool:
        spec = self._service_map.get(slug)
        if spec is None:
            raise KeyError(slug)

        with self._lock:
            proc = self._processes.get(slug)
        if proc is None or proc.poll() is not None:
            return False

        print(f"[korestack] stopping {slug} pid={proc.pid}")
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            print(f"[korestack] killing {slug} pid={proc.pid}")
            proc.kill()
            proc.wait(timeout=5)
        return True

    def restart_service(self, slug: str) -> bool:
        self.stop_service(slug)
        return self.start_service(slug)

    def stop(self) -> None:
        with self._lock:
            items = list(self._processes.items())

        for slug, proc in reversed(items):
            if proc.poll() is not None:
                continue
            print(f"[korestack] stopping {slug} pid={proc.pid}")
            proc.terminate()

        for slug, proc in reversed(items):
            if proc.poll() is not None:
                continue
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                print(f"[korestack] killing {slug} pid={proc.pid}")
                proc.kill()
                proc.wait(timeout=5)

    def snapshot(self) -> dict[str, object]:
        entries: list[dict[str, object]] = []
        with self._lock:
            processes = dict(self._processes)

        for spec in self._services:
            proc = processes.get(spec.slug)
            running = proc is not None and proc.poll() is None
            reachable, detail = probe_http(spec.health_url)
            parsed = urllib.parse.urlparse(spec.url)
            started_at = self._started_at.get(spec.slug)
            entries.append(
                {
                    "slug": spec.slug,
                    "label": spec.label,
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


def html_page(manager: StackManager, dashboard_url: str) -> str:
    snapshot = manager.snapshot()
    stack = snapshot["stack"]
    metrics = stack["metrics"]
    suite_urls = {
        "korestack": dashboard_url,
        "koreagent": "http://127.0.0.1:8000/",
        "koreconversation": "http://127.0.0.1:8700/ui",
        "koredata": "http://127.0.0.1:8800/",
        "koredocs": "http://127.0.0.1:5500/kf",
        "korecomms": "http://127.0.0.1:8900/",
    }
    rows = []
    for service in snapshot["services"]:
        suite_urls[service["slug"]] = service["url"]
        css_state = "up" if service["reachable"] else ("starting" if service["running"] else "down")
        state_label = "Reachable" if service["reachable"] else "Starting" if service["running"] else "Stopped"
        service_icon = suite_icon_svg(service["slug"], 34)
        rows.append(
            f"""
                        <article class="service-row service-{service['slug']} {css_state}" data-service-card="{service['slug']}">
                            <div class="service-cell service-glyph" aria-hidden="true">{service_icon}</div>
                            <div class="service-cell service-core">
                                <p class="eyebrow">{service['slug'].upper()}</p>
                                <h2>{service['label']}</h2>
                                <p class="service-copy">{service['description']}</p>
                            </div>
                            <div class="service-cell service-state"><span class="pill" data-field="state">{state_label}</span></div>
                            <div class="service-cell stack-pair"><span class="stack-key">Host</span><span class="stack-val" data-field="host">{service['host']}</span></div>
                            <div class="service-cell stack-pair"><span class="stack-key">Port</span><span class="stack-val" data-field="port">{service['port']}</span></div>
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
            --service-comms: #ff8fab;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
            font-family: "Cascadia Code", "Fira Code", Consolas, "Courier New", monospace;
            color: var(--stack-ink);
            background: var(--stack-bg);
      min-height: 100vh;
        }}
        a {{ color: inherit; }}
        .shell {{
            min-height: 100vh;
            display: grid;
            grid-template-rows: auto auto 1fr auto;
            gap: 12px;
    }}
        .panel {{ background: var(--stack-panel); border: 1px solid var(--stack-line); border-radius: 2px; }}
        .eyebrow {{ margin: 0; color: var(--stack-accent); font-size: 10px; letter-spacing: 0.18em; text-transform: uppercase; }}
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
        .service-row {{ display: grid; grid-template-columns: 60px minmax(180px, 1.45fr) auto minmax(74px, 0.52fr) minmax(62px, 0.42fr) minmax(220px, 1.4fr) auto; gap: 8px; align-items: stretch; padding: 0 10px 0 0; position: relative; background: var(--stack-panel-strong); border: 1px solid var(--stack-line); border-radius: 2px; min-width: 0; }}
        .service-row::before {{ content: ""; position: absolute; inset: 0 auto 0 0; width: 3px; background: var(--service-accent, var(--stack-accent)); }}
        .service-cell {{ min-width: 0; }}
        .service-glyph {{ width: 100%; height: 100%; min-height: 60px; align-self: stretch; display: inline-flex; align-items: center; justify-content: center; color: var(--service-accent, var(--stack-accent)); border-left: 1px solid color-mix(in srgb, var(--service-accent, var(--stack-accent)) 38%, transparent); border-right: 1px solid color-mix(in srgb, var(--service-accent, var(--stack-accent)) 38%, transparent); background: color-mix(in srgb, var(--service-accent, var(--stack-accent)) 10%, transparent); }}
        .service-glyph svg {{ width: 32px; height: 32px; display: block; }}
        .service-core, .service-state, .stack-pair, .service-link, .service-actions {{ display: flex; align-items: center; min-height: 60px; padding-block: 8px; }}
        .service-core {{ display: grid; align-content: center; }}
        .service-state {{ justify-content: center; }}
        .stack-pair {{ display: grid; align-content: center; gap: 2px; }}
        .service-core h2 {{ margin: 0; font-size: 0.95rem; }}
        .pill {{ border-radius: 2px; padding: 4px 7px; font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; background: color-mix(in srgb, var(--service-accent, var(--stack-accent)) 12%, transparent); color: var(--service-accent, var(--stack-accent)); border: 1px solid color-mix(in srgb, var(--service-accent, var(--stack-accent)) 42%, transparent); }}
        .service-row.down .pill {{ background: rgba(255, 95, 95, 0.10); color: var(--stack-danger); border-color: rgba(255, 95, 95, 0.24); }}
        .service-row.starting .pill {{ background: rgba(240, 192, 96, 0.10); color: var(--stack-warn); border-color: rgba(240, 192, 96, 0.22); }}
        .service-copy {{ color: var(--stack-muted); line-height: 1.3; font-size: 10px; margin: 2px 0 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
        .stack-key {{ color: var(--stack-muted); font-size: 9px; letter-spacing: 0.08em; text-transform: uppercase; }}
        .stack-val {{ font-size: 11px; overflow-wrap: anywhere; }}
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
        .service-comms {{ --service-accent: var(--service-comms); }}
    @media (max-width: 860px) {{
            .shell {{ grid-template-rows: auto auto auto auto; }}
            .path-row {{ grid-template-columns: 1fr; gap: 6px; }}
                .service-row {{ grid-template-columns: 1fr; align-items: start; }}
                .service-actions {{ justify-self: start; }}
    }}
  </style>
</head>
<body class="kcui-shell-bg">
    <div id="suite-topbar"></div>
    <div id="app-bar"></div>
    <main class="shell kcui-page kcui-page--narrow kcui-stack">
        <section class="panel kcui-panel paths-panel">
            <div class="paths-header kcui-panel-header">
                <p class="eyebrow">System Paths</p>
                <p>Shared suite storage and document locations.</p>
            </div>
            <div class="kcui-panel-body"><dl class="paths-list">{data_rows}</dl></div>
    </section>

        <section class="panel kcui-panel services-panel">
            <div class="services-header kcui-panel-header">
                <p class="eyebrow">Services</p>
                <p>Compact live rows with inline controls.</p>
            </div>
            <div class="kcui-panel-body"><section class="services-grid">{''.join(rows)}</section></div>
        </section>

    <section class="panel kcui-panel footer">
            <div class="kcui-panel-body footer-body">
            <p>Updates every 2 seconds without reloading the page.</p>
            <p>Root command: <code>python .\\main.py --services {','.join(stack['services'])}</code></p>
            </div>
    </section>
  </main>
  <script type="module">
      import {{ initAppBar, initSuiteTopbar }} from '/ui-elements/assets/js/topbar.js';
      initSuiteTopbar({{ currentService: 'korestack', urls: {suite_urls_json} }});
      initAppBar({{
          currentService: 'korestack',
          overline: 'Local Control Plane',
          brandLabel: 'KoreStack',
          brandIcon: 'korestack',
          chips: [
              {{ label: 'Running', value: '{metrics['running']} / {metrics['selected']}', tone: 'accent' }},
              {{ label: 'Reachable', value: '{metrics['reachable']}' }},
              {{ label: 'Dashboard', value: '{dashboard_url}' }},
              {{ label: 'UIElements', value: '{ui_note}' }},
          ],
      }});
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
            setText(card.querySelector('[data-field="state"]'), stateLabel(service));
            setText(card.querySelector('[data-field="host"]'), service.host ?? '-');
            setText(card.querySelector('[data-field="port"]'), String(service.port ?? '-'));
            const urlLink = card.querySelector('[data-field="url"]');
            if (urlLink) {{
                urlLink.textContent = service.url;
                urlLink.href = service.url;
            }}
        }}
        function applySnapshot(next) {{
            current = next;
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
    async function serviceAction(service, action) {{
            const buttonSet = document.querySelectorAll(`[data-service="${{service}}"]`);
            buttonSet.forEach((button) => button.disabled = true);
      try {{
                await fetch(`/api/services/${{service}}/${{action}}`, {{ method: 'POST' }});
                await refresh();
      }} finally {{
                window.setTimeout(() => buttonSet.forEach((button) => button.disabled = false), 300);
      }}
    }}
    for (const button of document.querySelectorAll('[data-service][data-action]')) {{
      button.addEventListener('click', () => serviceAction(button.dataset.service, button.dataset.action));
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
            if self.path in ("/", ""):
                body = html_page(self.manager_ref, self.dashboard_ref).encode("utf-8")
                self._send_bytes(body, "text/html; charset=utf-8")
                return

            if self.path == "/status":
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

            if self.path.startswith("/ui-elements/assets/"):
                self._serve_ui_asset(self.path.removeprefix("/ui-elements/assets/"))
                return

            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:  # noqa: N802
            if self.path.startswith("/api/services/"):
                parts = [part for part in self.path.split("/") if part]
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
            if length:
                self.rfile.read(length)

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

            body = json.dumps({"ok": True, "service": slug, "action": action, "changed": changed}).encode("utf-8")
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
    print(f"[korestack] landing page {dashboard_url}")
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

    manager = StackManager(services, child_env, stack_paths)

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
