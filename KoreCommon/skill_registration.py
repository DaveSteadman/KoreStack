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


REGISTRATION_REFRESH_SECONDS = 30


def _agent_registration_url() -> str:
    config = load_suite_config()
    host = str(config.get("network", {}).get("host") or "127.0.0.1")
    port = int(config.get("services", {}).get("koreagent", {}).get("port") or 19601)
    return f"http://{host}:{port}/api/skill-manager/ingest"


def _build_registration(raw: dict[str, Any], *, manifest_path: Path, service_base_url: str) -> dict[str, Any]:
    """Turn a service-owned manifest into the SkillManager registration shape."""
    service       = str(raw.get("service") or "").strip()
    service_label = str(raw.get("service_label") or service).strip()
    source_skills = raw.get("skills") if isinstance(raw.get("skills"), list) else []
    if not service or not source_skills:
        raise ValueError(f"{manifest_path}: service and skills are required")

    if not all(isinstance(item, dict) and isinstance(item.get("tools"), list) for item in source_skills):
        raise ValueError(f"{manifest_path}: each skill requires a tools list")
    skills = source_skills

    payload_skills: list[dict[str, Any]] = []
    for skill in skills:
        if not isinstance(skill, dict):
            raise ValueError(f"{manifest_path}: each skill must be an object")
        tool_records: list[dict[str, Any]] = []
        for tool in skill.get("tools", []):
            if not isinstance(tool, dict):
                raise ValueError(f"{manifest_path}: each tool must be an object")
            invoke_path = str(tool.get("invoke_path") or "/api/skills/{name}/invoke").format(name=tool.get("name", "")).strip()
            if not invoke_path.startswith("/"):
                raise ValueError(f"{manifest_path}: tool invoke_path must start with '/'")
            tool_records.append({
                **{key: value for key, value in tool.items() if key != "invoke_path"},
                "invoke_url": service_base_url.rstrip("/") + invoke_path,
            })
        payload_skills.append({**skill, "tools": tool_records})
    return {"service": service, "service_label": service_label, "skills": payload_skills}


def register_manifest(manifest_path: Path, *, service_base_url: str, attempts: int = 6) -> dict[str, Any]:
    """Submit one service's reviewed manifest to SkillManager, retrying while Agent starts."""
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{manifest_path}: manifest must be an object")
    payload = _build_registration(raw, manifest_path=manifest_path, service_base_url=service_base_url)
    request = urllib.request.Request(
        _agent_registration_url(),
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "text/plain", "Accept": "application/json"},
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


def start_manifest_registration(
    manifest_path: Path,
    *,
    service_base_url: str,
    logger_name: str,
    refresh_seconds: int = REGISTRATION_REFRESH_SECONDS,
) -> threading.Thread:
    """Register immediately, then periodically refresh while the service is running."""
    refresh_delay = max(1, int(refresh_seconds))

    def _register() -> None:
        logger = logging.getLogger(logger_name)
        while True:
            try:
                result = register_manifest(manifest_path, service_base_url=service_base_url)
                logger.info("registered %s skills with KoreAgent", result["count"])
            except Exception as exc:
                logger.warning("KoreAgent skill registration failed: %s", exc)
            time.sleep(refresh_delay)

    thread = threading.Thread(target=_register, daemon=True, name=f"{manifest_path.parent.name.lower()}-skill-registration")
    thread.start()
    return thread
