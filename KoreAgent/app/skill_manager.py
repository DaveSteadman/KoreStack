# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Persistent registry for KoreStack service skills.  Services own reviewed JSON manifests; this
# manager validates their registrations, keeps the live aggregate, and invokes registered HTTP skills.
# ====================================================================================================

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any

from utils.workspace_utils import get_controldata_dir


REGISTRY_FILE = get_controldata_dir() / "koreagent" / "skill_registry.json"
CATALOG_EXPORT_FILE = get_controldata_dir() / "koreagent" / "skill_manager_catalog.json"
CATALOG_EXPORT_INTERVAL_SECONDS = 60
LOCAL_SKILLS_CATALOG_FILE = Path(__file__).parent / "system_skills" / "skills_catalog.json"
LOCAL_TOOL_KEYWORDS_FILE = Path(__file__).parent / "system_skills" / "ToolSelection" / "tool_keywords.json"


class SkillManager:
    """Own the live registered-skill aggregate for KoreAgent."""

    def __init__(self, registry_file: Path = REGISTRY_FILE) -> None:
        self._path = registry_file
        self._lock = threading.RLock()
        self._services: dict[str, dict[str, Any]] = {}
        self._catalog_export_thread: threading.Thread | None = None
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        services = raw.get("services", {}) if isinstance(raw, dict) else {}
        if isinstance(services, dict):
            self._services = {
                str(service_id): value
                for service_id, value in services.items()
                if isinstance(value, dict)
            }

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 1, "services": self._services}
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(self._path)

    @staticmethod
    def _normalise_skill(service_id: str, raw: object) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("each skill must be an object")
        name = str(raw.get("name") or "").strip()
        purpose = str(raw.get("purpose") or "").strip()
        selection_description = str(raw.get("selection_description") or "").strip()
        invoke_url = str(raw.get("invoke_url") or "").strip()
        parameters = raw.get("parameters")
        keywords = raw.get("keywords")
        if not name or not purpose or not selection_description or not invoke_url:
            raise ValueError("each skill requires name, purpose, selection_description, and invoke_url")
        if len(selection_description) > 400:
            raise ValueError(f"skill '{name}' selection_description must be at most 400 characters")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise ValueError(f"skill '{name}' parameters must be a JSON-schema object")
        if not isinstance(keywords, list):
            raise ValueError(f"skill '{name}' keywords must be a list")
        clean_keywords = list(dict.fromkeys(
            "_".join(str(keyword or "").strip().lower().replace("-", "_").split())
            for keyword in keywords
            if str(keyword or "").strip()
        ))
        if not clean_keywords:
            raise ValueError(f"skill '{name}' needs at least one keyword")
        return {
            "name": name,
            "service": service_id,
            "purpose": purpose,
            "selection_description": selection_description,
            "parameters": parameters,
            "keywords": clean_keywords,
            "invoke_url": invoke_url,
            "returns": str(raw.get("returns") or "").strip(),
        }

    def register(self, service_id: str, skills: object, *, service_label: str = "") -> dict[str, Any]:
        service = str(service_id or "").strip().lower()
        if not service:
            raise ValueError("service is required")
        if not isinstance(skills, list):
            raise ValueError("skills must be a list")
        normalised = [self._normalise_skill(service, skill) for skill in skills]
        names = [skill["name"] for skill in normalised]
        if len(names) != len(set(names)):
            raise ValueError("a service cannot register duplicate skill names")
        with self._lock:
            conflicting = {
                skill["name"]: owner
                for owner, record in self._services.items()
                if owner != service
                for skill in record.get("skills", [])
                if isinstance(skill, dict)
            }
            duplicate = next((name for name in names if name in conflicting), None)
            if duplicate:
                raise ValueError(f"skill '{duplicate}' is already registered by '{conflicting[duplicate]}'")
            self._services[service] = {
                "label": str(service_label or service).strip() or service,
                "skills": normalised,
            }
            self._save()
        return {"service": service, "registered": names, "count": len(names)}

    def unregister(self, service_id: str) -> bool:
        service = str(service_id or "").strip().lower()
        with self._lock:
            if service not in self._services:
                return False
            self._services.pop(service)
            self._save()
            return True

    def list_skills(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(
                [dict(skill) for record in self._services.values() for skill in record.get("skills", []) if isinstance(skill, dict)],
                key=lambda skill: skill["name"],
            )

    def get_skill(self, name: str) -> dict[str, Any] | None:
        wanted = str(name or "").strip()
        return next((skill for skill in self.list_skills() if skill["name"] == wanted), None)

    def keyword_map(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for skill in self.list_skills():
            for keyword in skill["keywords"]:
                result.setdefault(keyword, []).append(skill["name"])
        return {keyword: sorted(names) for keyword, names in sorted(result.items())}

    @staticmethod
    def _local_tool_records() -> list[dict[str, Any]]:
        """Read the reviewed local catalog so the export covers every Agent-visible tool."""
        try:
            catalog = json.loads(LOCAL_SKILLS_CATALOG_FILE.read_text(encoding="utf-8"))
            keyword_config = json.loads(LOCAL_TOOL_KEYWORDS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        declared_keywords = keyword_config.get("tools", {}) if isinstance(keyword_config, dict) else {}
        if not isinstance(declared_keywords, dict):
            declared_keywords = {}

        records: list[dict[str, Any]] = []
        for skill in catalog.get("skills", []) if isinstance(catalog, dict) else []:
            if not isinstance(skill, dict):
                continue
            parameter_descriptions = skill.get("param_descriptions") if isinstance(skill.get("param_descriptions"), dict) else {}
            for function_sig in skill.get("functions", []) if isinstance(skill.get("functions"), list) else []:
                name = str(function_sig).split("(", 1)[0].strip()
                if not name:
                    continue
                keywords = declared_keywords.get(name, [])
                records.append(
                    {
                        "name": name,
                        "service": "koreagent",
                        "origin": "local",
                        "purpose": str(skill.get("purpose") or "").strip(),
                        "selection_description": str(skill.get("purpose") or "").strip(),
                        "parameters": parameter_descriptions.get(name, {}),
                        "keywords": [str(keyword) for keyword in keywords if str(keyword).strip()],
                        "returns": list(skill.get("outputs") or []),
                    }
                )
        return records

    def write_catalog_export(self) -> Path:
        """Write the current registered-tool list and complete reviewed keyword map for review."""
        with self._lock:
            local_tools = self._local_tool_records()
            registered_tools = [{**skill, "origin": "registered"} for skill in self.list_skills()]
            tools = sorted(local_tools + registered_tools, key=lambda skill: skill["name"])
            keywords: dict[str, list[str]] = {}
            for skill in tools:
                for keyword in skill.get("keywords", []):
                    keywords.setdefault(str(keyword), []).append(skill["name"])
            payload = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "tools": tools,
                "keywords": {keyword: sorted(names) for keyword, names in sorted(keywords.items())},
            }
        CATALOG_EXPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = CATALOG_EXPORT_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(CATALOG_EXPORT_FILE)
        return CATALOG_EXPORT_FILE

    def start_catalog_exporter(self) -> None:
        """Keep a reviewable JSON view of the SkillManager catalog current once per minute."""
        with self._lock:
            if self._catalog_export_thread is not None and self._catalog_export_thread.is_alive():
                return

            def _export_loop() -> None:
                while True:
                    try:
                        self.write_catalog_export()
                    except OSError:
                        pass
                    threading.Event().wait(CATALOG_EXPORT_INTERVAL_SECONDS)

            self._catalog_export_thread = threading.Thread(
                target=_export_loop,
                name="skill-manager-catalog-exporter",
                daemon=True,
            )
            self._catalog_export_thread.start()

    def invoke(self, name: str, arguments: dict[str, Any], *, timeout: int = 30) -> object:
        skill = self.get_skill(name)
        if skill is None:
            raise KeyError(f"registered skill '{name}' was not found")
        request = urllib.request.Request(
            skill["invoke_url"],
            data=json.dumps({"arguments": arguments}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            raise RuntimeError(f"skill '{name}' invocation failed: {exc}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if isinstance(payload, dict) and payload.get("ok") is False:
            raise RuntimeError(str(payload.get("error") or f"skill '{name}' returned an error"))
        return payload.get("result") if isinstance(payload, dict) and "result" in payload else payload


skill_manager = SkillManager()
