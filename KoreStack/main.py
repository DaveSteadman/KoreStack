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
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO

try:
    from .dashboard import serve_dashboard
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from dashboard import serve_dashboard

log: logging.Logger = logging.getLogger("korestack")


SUITE_ROOT = Path(__file__).resolve().parent.parent
STACK_ROOT = Path(__file__).resolve().parent
STACK_STATIC_DIR = STACK_ROOT / "static"
UI_ELEMENTS_ASSETS = SUITE_ROOT / "UIElements" / "assets"
SUITE_CONFIG_FILE = SUITE_ROOT / "config" / "korestack_config.json"


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
    "koreagent": {
        "label": "KoreAgent",
        "cwd": SUITE_ROOT / "KoreAgent",
        "script": "main.py",
        "port": 9601,
        "url_suffix": "/",
        "health_suffix": "/",
        "port_arg": "--agentport",
        "description": "KoreAgent web UI and orchestration surface.",
    },
    "korechat": {
        "label": "KoreChat",
        "cwd": SUITE_ROOT / "KoreChat",
        "script": "main.py",
        "port": 9602,
        "url_suffix": "/ui",
        "health_suffix": "/status",
        "port_env": "KORECHAT_PORT",
        "description": "Shared conversation-state service for agent and comms flows.",
    },
    "koredatagateway": {
        "label": "KoreData",
        "cwd": SUITE_ROOT / "KoreData",
        "script": "main.py",
        "port": 9603,
        "url_suffix": "/",
        "health_suffix": "/status",
        "port_env": "KOREDATA_PORT",
        "description": "KoreData gateway with the feed, library, reference, and RAG services behind it.",
    },
    "koredevicegateway": {
        "label": "KoreDevice",
        "cwd": SUITE_ROOT / "KoreDevice",
        "script": "main.py",
        "port": 9613,
        "url_suffix": "/",
        "health_suffix": "/status",
        "port_env": "KOREDEVICE_PORT",
        "description": "KoreDevice gateway with child services for hardware values, logs, and device-side tools.",
    },
    "koredocs": {
        "label": "KoreDocs",
        "cwd": SUITE_ROOT / "KoreDocs",
        "script": "main.py",
        "port": 9610,
        "url_suffix": "/ui",
        "health_suffix": "/status",
        "port_arg": "--port",
        "description": "KoreDocs file manager plus the doc, sheet, and diagram editors.",
    },
    "korecode": {
        "label": "KoreCode",
        "cwd": SUITE_ROOT / "KoreCode",
        "script": "main.py",
        "port": 9611,
        "url_suffix": "/ui",
        "health_suffix": "/status",
        "port_arg": "--port",
        "description": "KoreCode lightweight code editor rooted at the KoreStack workspace.",
    },
    "korecomms": {
        "label": "KoreComms",
        "cwd": SUITE_ROOT / "KoreComms",
        "script": "main.py",
        "port": 9609,
        "url_suffix": "/",
        "health_suffix": "/status",
        "port_env": "KORECOMMS_PORT",
        "description": "KoreComms UI and API for conversation and activity flows.",
    },
}

SERVICE_ICON_KEYS: dict[str, str] = {
    "koreagent":        "koreagent",
    "korechat":         "korechat",
    "koredatagateway":  "koredata",
    "koredevicegateway": "koredevice",
    "koredocs":         "koredocs",
    "korecode":         "korecode",
    "korecomms":        "korecomms",
}


def get_ui_assets_dir() -> Path:
    return UI_ELEMENTS_ASSETS


def _service_cfg(config: dict, slug: str) -> dict:
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    service_cfg = services.get(slug)
    return service_cfg if isinstance(service_cfg, dict) else {}


def is_service_enabled(config: dict, slug: str) -> bool:
    service_cfg = _service_cfg(config, slug)
    enabled = service_cfg.get("enabled")
    return bool(enabled) if enabled is not None else True


def _service_host(config: dict, slug: str) -> str:
    network = config.get("network") if isinstance(config.get("network"), dict) else {}
    network_host = str(network.get("host") or "127.0.0.1").strip()
    service_cfg = _service_cfg(config, slug)
    if slug == "koreagent":
        return network_host
    return str(service_cfg.get("host") or network_host).strip()


def _merge_dict(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_suite_config() -> dict:
    if not SUITE_CONFIG_FILE.exists():
        return {}
    try:
        raw = json.loads(SUITE_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("failed to parse suite config %s: %s", SUITE_CONFIG_FILE, exc)
        return {}
    return raw if isinstance(raw, dict) else {}


def resolve_root_path(value: object, default: str) -> Path:
    return (SUITE_ROOT / str(value or default)).resolve()


def get_stack_paths(config: dict) -> dict[str, Path]:
    paths = config.get("paths") if isinstance(config.get("paths"), dict) else {}
    datacontrol = resolve_root_path(paths.get("datacontrolroot"), "datacontrol")
    datauser    = resolve_root_path(paths.get("datauserroot"),    "datauser")

    def _dc(key: str, default: str) -> Path:
        return (datacontrol / str(paths.get(key) or default)).resolve()

    def _du(key: str, default: str) -> Path:
        return (datauser / str(paths.get(key) or default)).resolve()

    return {
        "path config":    SUITE_CONFIG_FILE,
        "datacontrol":    datacontrol,
        "datauser":       datauser,
        "conversation_data": _dc("korechat",  "korechat"),
        "comms_data":        _dc("korecomms", "korecomms"),
        "koredata_data":     _dc("koredata",  "koredata"),
        "koredevice_data":   _dc("koredevice","koredevice"),
        "koreagent_data":    _dc("koreagent", "koreagent"),
        "koredocs":          _dc("koredocs",  "koredocs"),
        "docs_data":         _du("docs_data", "KoreFiles"),
    }


def build_child_env(config: dict) -> dict[str, str]:
    env = dict(os.environ)
    stack_paths = get_stack_paths(config)
    network = config.get("network") if isinstance(config.get("network"), dict) else {}
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    connections = config.get("connections") if isinstance(config.get("connections"), dict) else {}

    env["PYTHONUTF8"] = "1"
    env["KORE_SUITE_ROOT"] = str(SUITE_ROOT)
    env["KORE_SUITE_CONFIG"] = str(SUITE_CONFIG_FILE)
    env["KORE_SUITE_DATACONTROL"] = str(stack_paths["datacontrol"])
    env["KORE_SUITE_DATAUSER"] = str(stack_paths["datauser"])
    env["KORE_UIELEMENTS_ASSETS_DIR"] = str(get_ui_assets_dir())

    korechat = services.get("korechat") if isinstance(services.get("korechat"), dict) else {}
    korecomms = services.get("korecomms") if isinstance(services.get("korecomms"), dict) else {}

    if network.get("host"):
        env["KORECHAT_HOST"] = str(network["host"])
        env["KORECOMMS_HOST"] = str(network["host"])

    _network_host = str(network.get("host") or "127.0.0.1")

    if korechat.get("port") is not None:
        env["KORECHAT_PORT"] = str(korechat["port"])
    if korecomms.get("port") is not None:
        env["KORECOMMS_PORT"] = str(korecomms["port"])

    if connections.get("korechat"):
        env["KORECOMMS_KORECHAT_URL"] = str(connections["korechat"])
    else:
        _korechat_port = int(korechat.get("port", 9602))
        env["KORECOMMS_KORECHAT_URL"] = f"http://{_network_host}:{_korechat_port}"

    env["KORECHAT_DATA_DIR"] = str(stack_paths["conversation_data"])
    env["KORECOMMS_DATA_DIR"] = str(stack_paths["comms_data"])
    env["KOREDATA_DATA_DIR"] = str(stack_paths["koredata_data"])
    env["KOREDEVICE_DATA_DIR"] = str(stack_paths["koredevice_data"])
    env["KOREAGENT_DATA_DIR"] = str(stack_paths["koreagent_data"])
    env["KOREDOCS_CONTROL_DIR"] = str(stack_paths["koredocs"])
    env["KOREDOCS_DATA_DIR"] = str(stack_paths["docs_data"])
    _korestack_cfg = services.get("korestack") if isinstance(services.get("korestack"), dict) else {}
    _korestack_port = int(_korestack_cfg.get("port", 9600) if _korestack_cfg else 9600)
    _suite_urls: dict[str, str] = {"korestack": f"http://{_network_host}:{_korestack_port}/"}
    for _slug, _meta in SERVICE_META.items():
        if not is_service_enabled(config, _slug):
            continue
        _svc_cfg = services.get(_slug) if isinstance(services.get(_slug), dict) else {}
        _port = int(_svc_cfg.get("port", _meta["port"]) if _svc_cfg else _meta["port"])
        _host = _service_host(config, _slug)
        _key = SERVICE_ICON_KEYS.get(_slug)
        if _key:
            _suite_urls[_key] = f"http://{_host}:{_port}{_meta['url_suffix']}"
    env["KORE_SUITE_URLS"] = json.dumps(_suite_urls)

    return env


def build_services(config: dict) -> dict[str, ServiceSpec]:
    services_cfg = config.get("services") if isinstance(config.get("services"), dict) else {}
    result: dict[str, ServiceSpec] = {}
    for slug, meta in SERVICE_META.items():
        if not is_service_enabled(config, slug):
            continue
        service_cfg = services_cfg.get(slug) if isinstance(services_cfg.get(slug), dict) else {}
        port = int(service_cfg.get("port", meta["port"]))
        host = _service_host(config, slug)
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
        help="Comma-separated service list. Valid values: all, korechat, koreagent, koredatagateway, koredevicegateway, koredocs, korecode, korecomms.",
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
        preferred_order = (
            "korechat",
            "koreagent",
            "koredatagateway",
            "koredevicegateway",
            "koredocs",
            "korecode",
            "korecomms",
        )
        return [services[key] for key in preferred_order if key in services]

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
        self._refresh_stop = threading.Event()
        self._refresh_thread = threading.Thread(target=self._refresh_loop, daemon=True)
        self._refresh_thread.start()
        self._watch_thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._watch_thread.start()

    def get_service_spec(self, slug: str) -> ServiceSpec | None:
        return self._service_map.get(slug)

    def invalidate_snapshot_cache(self) -> None:
        with self._cache_lock:
            self._snapshot_cache = None

    def get_live_processes(self) -> list[subprocess.Popen[bytes]]:
        with self._lock:
            return [proc for proc in self._processes.values() if proc.poll() is None]

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

    def stop(self) -> None:
        self._refresh_stop.set()
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
        self._refresh_thread.join(timeout=2)
        self._watch_thread.join(timeout=2)

    def _watch_loop(self) -> None:
        """Background thread: polls service .py files and auto-restarts on change."""
        cwd_to_slug: dict[Path, str] = {
            spec.cwd.resolve(): spec.slug for spec in self._services
        }

        def _scan() -> dict[Path, float]:
            result: dict[Path, float] = {}
            for cwd in cwd_to_slug:
                for py_file in cwd.rglob("*.py"):
                    if "__pycache__" in py_file.parts:
                        continue
                    try:
                        result[py_file] = py_file.stat().st_mtime
                    except OSError:
                        pass
            return result

        def _slug_for(py_file: Path) -> str | None:
            for cwd, slug in cwd_to_slug.items():
                try:
                    py_file.relative_to(cwd)
                    return slug
                except ValueError:
                    continue
            return None

        mtimes = _scan()
        while not self._refresh_stop.is_set():
            self._refresh_stop.wait(2.0)
            if self._refresh_stop.is_set():
                break
            new_mtimes = _scan()
            changed_slugs: set[str] = set()
            for py_file, mtime in new_mtimes.items():
                if mtimes.get(py_file) != mtime:
                    slug = _slug_for(py_file)
                    if slug:
                        changed_slugs.add(slug)
                        try:
                            rel = py_file.relative_to(SUITE_ROOT)
                        except ValueError:
                            rel = py_file
                        log.info("source changed: %s", rel)
            mtimes = new_mtimes
            if changed_slugs:
                self._refresh_stop.wait(0.5)  # debounce cascading saves
                for slug in sorted(changed_slugs):
                    spec = self._service_map.get(slug)
                    label = spec.label if spec else slug
                    log.info("auto-restarting %s (source changed)", label)
                    print(f"[watcher] restarting {label} ...", flush=True)
                    try:
                        self.restart_service(slug)
                    except Exception as exc:
                        log.warning("auto-restart %s failed: %s", slug, exc)
                self.invalidate_snapshot_cache()

    def _refresh_loop(self) -> None:
        """Background thread: refreshes the probe cache every 3 seconds."""
        while not self._refresh_stop.is_set():
            try:
                fresh = self._compute_snapshot()
                with self._cache_lock:
                    self._snapshot_cache = fresh
            except Exception as exc:
                log.warning("snapshot refresh failed: %s", exc)
            self._refresh_stop.wait(3)

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
                "configPath": str(SUITE_CONFIG_FILE),
                "uiElementsMounted": get_ui_assets_dir().exists(),
                "services": [spec.slug for spec in self._services],
                "metrics": {
                    "selected": len(entries),
                    "running": running_count,
                    "reachable": reachable_count,
                },
                "paths": {k: str(self._stack_paths[k]) for k in ("path config", "datacontrol", "datauser")},
            },
            "services": entries,
        }


def print_snapshot(snapshot: dict[str, object]) -> None:
    print("KoreStack status")
    for service in snapshot["services"]:
        state = "up" if service["reachable"] else "starting" if service["running"] else "down"
        print(f"  {service['label']:<6} {state:<8} pid={service['pid'] or '-':<6} probe={service['status']:<16} url={service['url']}")


def _get_stack_service_config(config: dict) -> dict:
    services_cfg = config.get("services") if isinstance(config.get("services"), dict) else {}
    stack_cfg = services_cfg.get("korestack") if isinstance(services_cfg.get("korestack"), dict) else {}
    if stack_cfg:
        return stack_cfg
    return services_cfg.get("suite") if isinstance(services_cfg.get("suite"), dict) else {}


def _bootstrap_data_dirs(stack_paths: dict[str, Path]) -> None:
    """Create every directory the suite needs before any child process starts.

    Uses parents=True and exist_ok=True throughout so this is always safe to
    call — it is a no-op when the directories already exist, and on a fresh
    install it builds the full tree in one shot.
    """
    dc = stack_paths["datacontrol"]
    du = stack_paths["datauser"]
    kd = stack_paths["koredata_data"]
    kv = stack_paths["koredevice_data"]

    fresh = not dc.exists()

    dirs = [
        dc,
        du,
        # KoreChat
        stack_paths["conversation_data"],
        # KoreComms
        stack_paths["comms_data"],
        # KoreAgent
        stack_paths["koreagent_data"],
        # KoreData gateway + sub-services
        kd,
        kd / "Feeds",
        kd / "Library",
        kd / "Reference",
        kd / "RAG",
        kd / "Graph",
        # KoreDevice gateway + sub-services
        kv,
        kv / "Numbers",
        # KoreDocs
        stack_paths["koredocs"],
        # Shared datacontrol tree
        dc / "logs",
        dc / "schedules",
        dc / "test_prompts",
        dc / "test_results",
        dc / "chatsessions",
        dc / "chatsessions" / "named",
        # User data
        stack_paths["docs_data"],
    ]

    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    if fresh:
        print(
            f"Fresh install: created data directories under\n"
            f"  datacontrol: {dc}\n"
            f"  datauser:    {du}",
            flush=True,
        )


def main() -> int:
    args = parse_args()
    suite_config = load_suite_config()
    all_services = build_services(suite_config)
    services = resolve_services(args.services, all_services)
    child_env = build_child_env(suite_config)
    stack_paths = get_stack_paths(suite_config)

    _bootstrap_data_dirs(stack_paths)

    log_dir = STACK_ROOT / "logs"
    setup_logging(log_dir)

    manager = StackManager(services, child_env, stack_paths, log_dir)

    network_cfg = suite_config.get("network") if isinstance(suite_config.get("network"), dict) else {}
    stack_cfg = _get_stack_service_config(suite_config)
    dashboard_host = args.host or str(network_cfg.get("host") or "127.0.0.1")
    dashboard_port = int(args.ui_port or stack_cfg.get("port") or 9600)

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

    print("Starting...", flush=True)
    manager.start()

    stop_event = threading.Event()
    dashboard_thread: threading.Thread | None = None
    if not args.no_dashboard:
        dashboard_thread = threading.Thread(
            target=serve_dashboard,
            args=(manager, dashboard_host, dashboard_port, stop_event),
            kwargs={
                "stack_static_dir": STACK_STATIC_DIR,
                "ui_assets_dir": get_ui_assets_dir(),
                "service_icon_keys": SERVICE_ICON_KEYS,
                "probe_http_with_retry": probe_http_with_retry,
            },
            daemon=True,
        )
        dashboard_thread.start()
        print(f"\nKoreStack: http://{dashboard_host}:{dashboard_port}/\n", flush=True)
        if args.open_browser:
            webbrowser.open(f"http://{dashboard_host}:{dashboard_port}/")

    def _signal_handler(signum: int, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal_handler)

    try:
        while not stop_event.is_set():
            live = manager.get_live_processes()
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
