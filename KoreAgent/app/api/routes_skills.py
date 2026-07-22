from pathlib import Path
import json
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel


class SkillInvokeRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = {}


def _schema_type(schema: dict[str, Any] | None) -> str:
    if not isinstance(schema, dict):
        return ""
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        return str(schema_type[0] or "")
    return str(schema_type or "")


def _placeholder_from_name(name: str) -> Any:
    lowered = str(name or "").strip().lower()
    if "url" in lowered:
        return "https://example.com"
    if "path" in lowered or "file" in lowered:
        return "path/to/file.txt"
    if "query" in lowered or lowered == "q":
        return "example search"
    if "date" in lowered or "since" in lowered or "until" in lowered:
        return "2026-01-01"
    if "limit" in lowered or "count" in lowered or "max" in lowered or "offset" in lowered:
        return 20
    if "timeout" in lowered:
        return 15
    if lowered.startswith("is_") or lowered.startswith("has_") or "enabled" in lowered:
        return True
    if lowered.endswith("_ids") or lowered.endswith("_list") or lowered.endswith("_items"):
        return ["example"]
    return "example"


def _example_from_schema(schema: dict[str, Any] | None, prop_name: str = "") -> Any:
    if not isinstance(schema, dict):
        return _placeholder_from_name(prop_name)
    if "default" in schema and schema.get("default") is not None:
        return schema.get("default")
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        return enum_values[0]
    for branch_key in ("anyOf", "oneOf"):
        branches = schema.get(branch_key)
        if isinstance(branches, list) and branches:
            for branch in branches:
                if isinstance(branch, dict) and str(branch.get("type", "")).lower() not in {"null", "none"}:
                    return _example_from_schema(branch, prop_name)
            return _example_from_schema(branches[0], prop_name)
    schema_type = _schema_type(schema).lower()
    if schema_type == "object" or (not schema_type and isinstance(schema.get("properties"), dict)):
        props = schema.get("properties")
        out: dict[str, Any] = {}
        if isinstance(props, dict):
            for key, value in props.items():
                out[str(key)] = _example_from_schema(value if isinstance(value, dict) else None, str(key))
        return out
    if schema_type == "array":
        items_schema = schema.get("items")
        return [_example_from_schema(items_schema if isinstance(items_schema, dict) else None, prop_name)]
    if schema_type == "integer":
        return 1
    if schema_type == "number":
        return 1.0
    if schema_type == "boolean":
        return True
    if schema_type == "string":
        schema_format = str(schema.get("format") or "").strip().lower()
        if schema_format in {"uri", "url"}:
            return "https://example.com"
        if "date" in schema_format:
            return "2026-01-01"
        return _placeholder_from_name(prop_name)
    return _placeholder_from_name(prop_name)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def register_skills_routes(
    app,
    *,
    config_getter,
    workspace_root: Path,
    get_web_skills_enabled,
    filter_web_skills,
    build_tool_definitions,
    get_selected_tools,
    always_on_tool_names,
    filter_mcp_tool_defs,
    filter_mcp_tool_index,
    mcp_client_module,
    build_catalog_gates,
    execute_tool_call,
) -> None:
    def _get_skills_payload_or_raise() -> dict[str, Any]:
        config = config_getter()
        if config is None:
            raise HTTPException(status_code=503, detail="KoreAgent config is not initialized")
        payload = config.skills_payload if isinstance(config.skills_payload, dict) else {}
        if not payload:
            raise HTTPException(status_code=503, detail="Skills payload is unavailable")
        return payload

    def _safe_read_workspace_file(path_text: str) -> tuple[str, str] | tuple[None, None]:
        candidate_text = str(path_text or "").strip()
        if not candidate_text:
            return None, None
        normalized = candidate_text.replace("\\", "/")
        if normalized.startswith("KoreStack/"):
            normalized = normalized.split("/", 1)[1]
        full_path = (workspace_root / normalized).resolve()
        if full_path != workspace_root and workspace_root not in full_path.parents:
            return None, None
        if not full_path.exists() or not full_path.is_file():
            return None, None
        return normalized, full_path.read_text(encoding="utf-8", errors="replace")

    @app.get("/api/skills/catalog")
    def skills_catalog_get() -> dict[str, Any]:
        payload = _get_skills_payload_or_raise()
        if not get_web_skills_enabled():
            payload = filter_web_skills(payload)
        local_tool_defs = build_tool_definitions(payload)
        local_tool_map: dict[str, dict[str, Any]] = {}
        for tool_def in local_tool_defs:
            fn = tool_def.get("function", {}) if isinstance(tool_def, dict) else {}
            tool_name = str(fn.get("name") or "").strip()
            if tool_name:
                local_tool_map[tool_name] = fn

        selected = set(get_selected_tools()) | set(always_on_tool_names)
        mcp_defs = filter_mcp_tool_defs(mcp_client_module.get_mcp_tool_definitions(), enabled=get_web_skills_enabled())
        mcp_idx = filter_mcp_tool_index(mcp_client_module.get_mcp_tool_index(), enabled=get_web_skills_enabled())

        providers: dict[str, dict[str, Any]] = {}
        entries: list[dict[str, Any]] = []

        def _ensure_provider(key: str, label: str, provider_type: str) -> None:
            if key in providers:
                return
            providers[key] = {"key": key, "label": label, "type": provider_type, "count": 0}

        for skill in payload.get("skills", []):
            is_system = bool(skill.get("is_system_skill"))
            provider_key = "local-system" if is_system else "local-user"
            provider_label = "KoreAgent System Skills" if is_system else "KoreAgent Skills"
            _ensure_provider(provider_key, provider_label, "local")
            module_path = str(skill.get("module") or "").strip()
            md_path = str(skill.get("relative_path") or "").strip()
            skill_name = str(skill.get("skill_name") or "").strip()
            purpose = str(skill.get("purpose") or "").strip()
            for function_sig in skill.get("functions") or []:
                tool_name = str(function_sig).split("(", 1)[0].strip()
                if not tool_name:
                    continue
                tool_meta = local_tool_map.get(tool_name, {})
                parameters_schema = tool_meta.get("parameters") if isinstance(tool_meta.get("parameters"), dict) else None
                entries.append(
                    {
                        "tool_name": tool_name,
                        "function_signature": str(function_sig),
                        "skill_name": skill_name,
                        "purpose": purpose,
                        "description": str(tool_meta.get("description") or purpose),
                        "origin": skill.get("origin", "local"),
                        "provider_key": provider_key,
                        "provider_label": provider_label,
                        "provider_type": "local",
                        "active": tool_name in selected,
                        "module_path": module_path,
                        "skill_md_path": md_path,
                        "call_type": "python" if module_path else "metadata",
                        "parameters_schema": parameters_schema,
                        "invoke_template": _example_from_schema(parameters_schema, tool_name) if parameters_schema else {},
                    }
                )
                providers[provider_key]["count"] += 1

        for tool_def in mcp_defs:
            fn = tool_def.get("function", {}) if isinstance(tool_def, dict) else {}
            tool_name = str(fn.get("name") or "").strip()
            if not tool_name:
                continue
            meta = mcp_idx.get(tool_name, {}) if isinstance(mcp_idx.get(tool_name, {}), dict) else {}
            provider_label = str(meta.get("connection") or meta.get("server") or meta.get("url") or "MCP")
            provider_key = f"mcp:{provider_label}"
            _ensure_provider(provider_key, provider_label, "mcp")
            parameters_schema = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else None
            entries.append(
                {
                    "tool_name": tool_name,
                    "function_signature": f"{tool_name}(...)",
                    "skill_name": provider_label,
                    "purpose": str(fn.get("description") or meta.get("purpose") or ""),
                    "description": str(fn.get("description") or meta.get("purpose") or ""),
                    "origin": "mcp",
                    "provider_key": provider_key,
                    "provider_label": provider_label,
                    "provider_type": "mcp",
                    "active": tool_name in selected,
                    "module_path": "",
                    "skill_md_path": "",
                    "call_type": "mcp",
                    "parameters_schema": parameters_schema,
                    "invoke_template": _example_from_schema(parameters_schema, tool_name) if parameters_schema else {},
                }
            )
            providers[provider_key]["count"] += 1

        entries.sort(key=lambda item: (item.get("provider_label", ""), item.get("tool_name", "")))
        provider_rows = sorted(providers.values(), key=lambda item: item.get("label", ""))
        return {
            "providers": provider_rows,
            "entries": entries,
            "stats": {
                "provider_count": len(provider_rows),
                "entry_count": len(entries),
                "active_count": sum(1 for item in entries if item.get("active")),
            },
        }

    @app.get("/api/skills/source")
    def skills_source_get(tool_name: str, source_kind: str = "module") -> dict[str, Any]:
        payload = _get_skills_payload_or_raise()
        wanted_tool = str(tool_name or "").strip()
        kind = str(source_kind or "module").strip().lower()
        if kind not in {"module", "skill_md"}:
            raise HTTPException(status_code=400, detail="source_kind must be 'module' or 'skill_md'")

        for skill in payload.get("skills", []):
            function_sigs = skill.get("functions") or []
            if not any(str(sig).split("(", 1)[0].strip() == wanted_tool for sig in function_sigs):
                continue
            path_text = str(skill.get("module") if kind == "module" else skill.get("relative_path") or "").strip()
            rel_path, content = _safe_read_workspace_file(path_text)
            if rel_path is None:
                raise HTTPException(status_code=404, detail=f"No readable {kind} source available for tool '{wanted_tool}'")
            return {"tool_name": wanted_tool, "source_kind": kind, "path": rel_path, "content": content}

        raise HTTPException(status_code=404, detail=f"Tool '{wanted_tool}' not found in local skills payload")

    @app.post("/api/skills/invoke")
    def skills_invoke_post(body: SkillInvokeRequest) -> dict[str, Any]:
        payload = _get_skills_payload_or_raise()
        tool_name = str(body.tool_name or "").strip()
        if not tool_name:
            raise HTTPException(status_code=400, detail="tool_name is required")

        arguments = body.arguments if isinstance(body.arguments, dict) else {}
        catalog_gates = build_catalog_gates(payload)
        active_all = set(catalog_gates.keys()) | set(mcp_client_module.get_mcp_tool_index().keys()) | set(always_on_tool_names)
        try:
            output = execute_tool_call(
                tool_name=tool_name,
                arguments=arguments,
                skills_payload=payload,
                user_prompt="",
                catalog_gates=catalog_gates,
                active_tool_names=active_all,
            )
            result_payload = output.to_dict() if hasattr(output, "to_dict") else dict(output)
            return {"ok": True, "tool_name": tool_name, "output": _json_safe(result_payload)}
        except Exception as exc:
            return {"ok": False, "tool_name": tool_name, "error": f"{exc.__class__.__name__}: {exc}"}
