EXTERNAL_TOOL_ALIASES: dict[str, str] = {
    "koredata_search_mcp": "koredata_search",
}


def catalog_tool_aliases(skills_payload: dict | None) -> dict[str, str]:
    aliases: dict[str, str] = {}
    if not isinstance(skills_payload, dict):
        return aliases

    for skill in skills_payload.get("skills", []):
        if not isinstance(skill, dict):
            continue
        declared = skill.get("tool_aliases", {})
        if isinstance(declared, dict):
            aliases.update(
                {
                    str(alias).strip(): str(canonical).strip()
                    for alias, canonical in declared.items()
                    if str(alias).strip() and str(canonical).strip()
                }
            )
    return aliases


def canonical_tool_name(tool_name: str, skills_payload: dict | None = None) -> str:
    normalized = str(tool_name or "").strip()
    aliases    = catalog_tool_aliases(skills_payload)
    aliases.update(EXTERNAL_TOOL_ALIASES)
    return aliases.get(normalized, normalized)


__all__ = ["EXTERNAL_TOOL_ALIASES", "catalog_tool_aliases", "canonical_tool_name"]
