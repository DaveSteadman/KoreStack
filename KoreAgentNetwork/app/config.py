from __future__ import annotations

from KoreCommon.suite_paths import get_suite_datauser_dir, get_suite_urls_map, load_suite_config


SERVICE_KEY = "koreagentnetwork"
SERVICE_LABEL = "KoreAgentNetwork"
NETWORKS_DIR = (get_suite_datauser_dir() / "KoreNetworks").resolve()
RUN_TIMEOUT_SECONDS = 300
NODE_TIMEOUT_SECONDS = 60


def service_config() -> dict:
    """Return the checked-in service configuration with safe local defaults."""
    config   = load_suite_config()
    network  = config.get("network") if isinstance(config.get("network"), dict) else {}
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    service  = services.get(SERVICE_KEY) if isinstance(services.get(SERVICE_KEY), dict) else {}
    return {
        "host": str(service.get("host") or network.get("host") or "127.0.0.1"),
        "port": int(service.get("port") or 29617),
    }


def suite_services() -> dict[str, str]:
    """Return the named service bases available to embedded node code."""
    config   = load_suite_config()
    network  = config.get("network") if isinstance(config.get("network"), dict) else {}
    services = config.get("services") if isinstance(config.get("services"), dict) else {}
    host     = str(network.get("host") or "127.0.0.1").strip()
    urls     = {
        name: url.rstrip("/")
        for name, url in get_suite_urls_map().items()
        if name and url and name != SERVICE_KEY
    }
    for name, service in services.items():
        if name == SERVICE_KEY or not isinstance(service, dict) or not service.get("enabled", True):
            continue
        try:
            urls.setdefault(str(name), f"http://{service.get('host') or host}:{int(service['port'])}")
        except (KeyError, TypeError, ValueError):
            continue
    return urls
