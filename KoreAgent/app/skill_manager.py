"""Persistent registry of named Skills and their callable Tools."""

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.workspace_utils import get_controldata_dir


REGISTRY_FILE                   = get_controldata_dir() / "koreagent" / "skill_registry.json"
CATALOG_EXPORT_FILE             = get_controldata_dir() / "koreagent" / "skill_manager_catalog.json"
CATALOG_EXPORT_INTERVAL_SECONDS = 60


class SkillManager:
    """Own the live Skill -> Tool registry.  Tools, never skills, are invoked."""

    def __init__(self, registry_file: Path = REGISTRY_FILE) -> None:
        self._path                  = registry_file
        self._lock                  = threading.RLock()
        self._skills: dict[str, dict[str, Any]] = {}
        self._catalog_export_thread: threading.Thread | None = None
        self._load()

    @staticmethod
    def _normalise_tool(service: str, skill_name: str, raw: object, *, transport: str) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("each tool must be an object")
        name       = str(raw.get("name") or "").strip()
        invoke_url = str(raw.get("invoke_url") or "").strip()
        module     = str(raw.get("module") or "").strip()
        function   = str(raw.get("function") or "").strip()
        parameters = raw.get("parameters") or {"type": "object", "properties": {}}
        if not name:
            raise ValueError("each tool requires a name")
        if transport == "http" and not invoke_url:
            raise ValueError(f"tool '{name}' requires invoke_url")
        if transport == "builtin" and (not module or not function):
            raise ValueError(f"built-in tool '{name}' requires module and function")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise ValueError(f"tool '{name}' parameters must be a JSON-schema object")
        return {
            "name":       name,
            "service":    service,
            "skill_name": skill_name,
            "purpose":    str(raw.get("purpose") or "").strip(),
            "parameters": parameters,
            "invoke_url": invoke_url,
            "returns":    str(raw.get("returns") or "").strip(),
            "transport":  transport,
            "module":     module,
            "function":   function,
        }

    @classmethod
    def _normalise_skill(cls, raw: object, *, service: str, service_label: str = "", transport: str = "http") -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("each skill must be an object")
        name                  = str(raw.get("name") or "").strip()
        purpose               = str(raw.get("purpose") or "").strip()
        selection_description = str(raw.get("selection_description") or "").strip()
        tools                 = raw.get("tools")
        if not name or not purpose or not selection_description:
            raise ValueError("each skill requires name, purpose, and selection_description")
        if len(selection_description) > 400:
            raise ValueError(f"skill '{name}' selection_description must be at most 400 characters")
        if not isinstance(tools, list):
            raise ValueError(f"skill '{name}' tools must be a list")
        clean_tools = [cls._normalise_tool(service, name, tool, transport=transport) for tool in tools]
        if len({tool["name"] for tool in clean_tools}) != len(clean_tools):
            raise ValueError(f"skill '{name}' contains duplicate tool names")
        return {
            "name":                  name,
            "service":               service,
            "service_label":         str(service_label or service).strip() or service,
            "purpose":               purpose,
            "selection_description": selection_description,
            "tools":                 clean_tools,
        }

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for item in raw.get("skills", []) if isinstance(raw, dict) else []:
            if not isinstance(item, dict):
                continue
            try:
                service = str(item.get("service") or "").strip().lower()
                tools = item.get("tools") if isinstance(item.get("tools"), list) else []
                transport = str((tools[0] if tools else {}).get("transport") or "http")
                skill = self._normalise_skill(item, service=service, service_label=str(item.get("service_label") or ""), transport=transport)
            except ValueError:
                continue
            self._skills[skill["name"]] = skill

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_suffix(".tmp")
        temp.write_text(json.dumps({"schema_version": 2, "skills": self.list_skills()}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temp.replace(self._path)

    def _remove_tool_names(self, names: set[str]) -> None:
        for name, skill in list(self._skills.items()):
            self._skills[name] = {**skill, "tools": [tool for tool in skill["tools"] if tool["name"] not in names]}

    def register_skill(self, skill: object, *, service_id: str, service_label: str = "", transport: str = "http") -> dict[str, Any]:
        service = str(service_id or "").strip().lower()
        if not service:
            raise ValueError("service is required")
        item = self._normalise_skill(skill, service=service, service_label=service_label, transport=transport)
        with self._lock:
            self._remove_tool_names({tool["name"] for tool in item["tools"]})
            self._skills[item["name"]] = item
            self._save()
        return dict(item)

    def register_tool(self, skill_name: str, tool: object) -> dict[str, Any]:
        with self._lock:
            skill = self._skills.get(str(skill_name or "").strip())
            if skill is None:
                raise KeyError(f"skill '{skill_name}' was not found")
            transport = str((skill["tools"][0] if skill["tools"] else {}).get("transport") or "http")
            item = self._normalise_tool(skill["service"], skill["name"], tool, transport=transport)
            self._remove_tool_names({item["name"]})
            refreshed = self._skills[skill["name"]]
            self._skills[skill["name"]] = {**refreshed, "tools": refreshed["tools"] + [item]}
            self._save()
        return dict(item)

    def register(self, service_id: str, skills: object, *, service_label: str = "", transport: str = "http") -> dict[str, Any]:
        if not isinstance(skills, list):
            raise ValueError("skills must be a list")
        registered = [self.register_skill(item, service_id=service_id, service_label=service_label, transport=transport) for item in skills]
        return {"service": str(service_id).strip().lower(), "registered": [item["name"] for item in registered], "count": len(registered)}

    def ingest_registration(
        self,
        registration: str | dict[str, Any],
        *,
        source_path: Path | None = None,
    ) -> dict[str, Any]:
        """Validate and apply one JSON registration message from a subsystem."""
        if isinstance(registration, str):
            try:
                payload = json.loads(registration)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid registration JSON: {exc}") from exc
        else:
            payload = registration
        if not isinstance(payload, dict):
            raise ValueError("registration message must be a JSON object")
        indexed_skills = payload.get("skills")
        if (
            payload.get("registration_mode") == "builtin"
            and isinstance(indexed_skills, list)
            and indexed_skills
            and all(isinstance(item, dict) and item.get("catalog_file") for item in indexed_skills)
        ):
            if source_path is None:
                raise ValueError("a split catalog registration requires its source file path")
            tool_records: list[dict[str, Any]] = []
            for index_item in indexed_skills:
                fragment_path = Path(source_path).parent / str(index_item["catalog_file"])
                try:
                    fragment = json.loads(fragment_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise ValueError(f"cannot read Skill catalog '{fragment_path}': {exc}") from exc
                module = str(fragment.get("module") or "").strip()
                for signature in fragment.get("functions", []):
                    function = str(signature).split("(", 1)[0].strip()
                    if function:
                        tool_records.append({"name": function, "module": module, "function": function})
            payload = {
                "service":           str(payload.get("service") or "koreagent"),
                "service_label":     str(payload.get("service_label") or "KoreAgent"),
                "registration_mode": "builtin",
                "skills": [{
                    "name":                  str(payload.get("default_skill_name") or "system_skills"),
                    "purpose":               str(payload.get("purpose") or "KoreAgent built-in tools."),
                    "selection_description": str(payload.get("selection_description") or "Built-in tools available to every conversation by default."),
                    "tools":                 tool_records,
                }],
            }
        return self.register(
            str(payload.get("service") or ""),
            payload.get("skills"),
            service_label=str(payload.get("service_label") or ""),
            transport="builtin" if payload.get("registration_mode") == "builtin" else "http",
        )

    def register_manifest(self, manifest_path: Path) -> dict[str, Any]:
        """Load one subsystem-owned startup registration manifest."""
        try:
            payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid skill registration manifest '{manifest_path}': {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"skill registration manifest '{manifest_path}' must be an object")
        result = self.ingest_registration(payload, source_path=Path(manifest_path))
        return {"manifest": str(manifest_path), **result}

    def register_manifests(self, manifest_paths: list[Path]) -> list[dict[str, Any]]:
        return [self.register_manifest(path) for path in sorted(manifest_paths)]

    def remove_skill(self, name: str) -> bool:
        with self._lock:
            removed = self._skills.pop(str(name or "").strip(), None) is not None
            if removed:
                self._save()
            return removed

    def remove_tool(self, skill_name: str, tool_name: str) -> bool:
        with self._lock:
            skill = self._skills.get(str(skill_name or "").strip())
            if skill is None:
                return False
            tools = [tool for tool in skill["tools"] if tool["name"] != str(tool_name or "").strip()]
            if len(tools) == len(skill["tools"]):
                return False
            self._skills[skill["name"]] = {**skill, "tools": tools}
            self._save()
            return True

    def unregister(self, service_id: str) -> bool:
        service = str(service_id or "").strip().lower()
        with self._lock:
            names = [name for name, skill in self._skills.items() if skill["service"] == service]
            for name in names:
                self._skills.pop(name)
            if names:
                self._save()
            return bool(names)

    def list_skills(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted([{**skill, "tools": [dict(tool) for tool in skill["tools"]]} for skill in self._skills.values()], key=lambda item: item["name"])

    def list_tools(self) -> list[dict[str, Any]]:
        return sorted([dict(tool) for skill in self.list_skills() for tool in skill["tools"]], key=lambda item: item["name"])

    def get_skill(self, name: str) -> dict[str, Any] | None:
        return next((item for item in self.list_skills() if item["name"] == str(name or "").strip()), None)

    def get_tool(self, name: str) -> dict[str, Any] | None:
        return next((item for item in self.list_tools() if item["name"] == str(name or "").strip()), None)

    def write_catalog_export(self) -> Path:
        payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "skills": self.list_skills(), "tools": self.list_tools()}
        CATALOG_EXPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = CATALOG_EXPORT_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temp.replace(CATALOG_EXPORT_FILE)
        return CATALOG_EXPORT_FILE

    def start_catalog_exporter(self) -> None:
        with self._lock:
            if self._catalog_export_thread and self._catalog_export_thread.is_alive():
                return
            def export_loop() -> None:
                while True:
                    try: self.write_catalog_export()
                    except OSError: pass
                    threading.Event().wait(CATALOG_EXPORT_INTERVAL_SECONDS)
            self._catalog_export_thread = threading.Thread(target=export_loop, name="skill-manager-catalog-exporter", daemon=True)
            self._catalog_export_thread.start()

    def invoke(self, name: str, arguments: dict[str, Any], *, timeout: int = 30) -> object:
        tool = self.get_tool(name)
        if tool is None:
            raise KeyError(f"registered tool '{name}' was not found")
        request = urllib.request.Request(tool["invoke_url"], data=json.dumps({"arguments": arguments}).encode("utf-8"), method="POST", headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            raise RuntimeError(f"tool '{name}' invocation failed: {exc}") from exc
        try: payload = json.loads(raw)
        except json.JSONDecodeError: return raw
        if isinstance(payload, dict) and payload.get("ok") is False:
            raise RuntimeError(str(payload.get("error") or f"tool '{name}' returned an error"))
        return payload.get("result") if isinstance(payload, dict) and "result" in payload else payload


skill_manager = SkillManager()
