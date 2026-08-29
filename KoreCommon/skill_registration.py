"""Manifest-backed registration client for KoreStack services."""

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from KoreCommon.suite_paths import load_suite_config


def _agent_registration_url() -> str:
    config = load_suite_config()
    host = str(config.get("network", {}).get("host") or "127.0.0.1")
    port = int(config.get("services", {}).get("koreagent", {}).get("port") or 19601)
    return f"http://{host}:{port}/api/skill-manager/register"


def register_manifest(manifest_path: Path, *, service_base_url: str, attempts: int = 6) -> dict[str, Any]:
    """Register one service's reviewed manifest with KoreAgent, retrying while Agent starts."""
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    service = str(raw.get("service") or "").strip()
    skills = raw.get("skills") if isinstance(raw.get("skills"), list) else []
    payload_skills = []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        invoke_path = str(skill.get("invoke_path") or "").strip()
        if not invoke_path.startswith("/"):
            raise ValueError(f"{manifest_path}: skill invoke_path must start with '/'")
        payload_skills.append({**skill, "invoke_url": service_base_url.rstrip("/") + invoke_path})
    payload = {
        "service": service,
        "service_label": str(raw.get("service_label") or service),
        "skills": payload_skills,
    }
    request = urllib.request.Request(
        _agent_registration_url(),
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(min(5, attempt + 1))
    raise RuntimeError(f"could not register '{service}' skills with KoreAgent: {last_error}")


def start_manifest_registration(manifest_path: Path, *, service_base_url: str, logger_name: str) -> threading.Thread:
    """Register a service manifest in the background while its HTTP server starts."""
    def _register() -> None:
        try:
            result = register_manifest(manifest_path, service_base_url=service_base_url)
            logging.getLogger(logger_name).info("registered %s skills with KoreAgent", result["count"])
        except Exception as exc:
            logging.getLogger(logger_name).warning("KoreAgent skill registration failed: %s", exc)

    thread = threading.Thread(target=_register, daemon=True, name=f"{manifest_path.parent.parent.name.lower()}-skill-registration")
    thread.start()
    return thread
