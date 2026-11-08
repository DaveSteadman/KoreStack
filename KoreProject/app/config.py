from pathlib import Path

from KoreCommon.suite_paths import get_suite_datacontrol_dir, load_suite_config

SERVICE_KEY = "koreproject"
SERVICE_LABEL = "KoreProject"


def service_config() -> dict:
    config = load_suite_config()
    network = config.get("network", {})
    service = config.get("services", {}).get(SERVICE_KEY, {})
    return {
        "host": str(service.get("host") or network.get("host") or "127.0.0.1"),
        "port": int(service.get("port") or 29619),
    }


def data_dir() -> Path:
    return get_suite_datacontrol_dir() / SERVICE_KEY
