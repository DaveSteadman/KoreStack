"""Mutable orchestration runtime flags."""

from web_tools_state import is_web_tool_name

_SKILL_GUIDANCE_ENABLED: bool = False
_SANDBOX_ENABLED: bool = True
_WEB_SKILLS_ENABLED: bool = True
_WEB_SKILLS_FILTER_CACHE: dict[int, dict] = {}


def get_skill_guidance_enabled() -> bool:
    return _SKILL_GUIDANCE_ENABLED


def set_skill_guidance_enabled(enabled: bool) -> None:
    global _SKILL_GUIDANCE_ENABLED
    _SKILL_GUIDANCE_ENABLED = enabled


def get_sandbox_enabled() -> bool:
    return _SANDBOX_ENABLED


def set_sandbox_enabled(enabled: bool) -> None:
    global _SANDBOX_ENABLED
    _SANDBOX_ENABLED = enabled


def get_web_skills_enabled() -> bool:
    return _WEB_SKILLS_ENABLED


def set_web_skills_enabled(enabled: bool) -> None:
    global _WEB_SKILLS_ENABLED
    _WEB_SKILLS_ENABLED = enabled


def filter_web_skills(payload: dict) -> dict:
    cache_key = id(payload)
    cached = _WEB_SKILLS_FILTER_CACHE.get(cache_key)
    if cached is not None:
        return cached

    filtered: list[dict] = []
    for skill in payload.get("skills", []):
        skill_name = str(skill.get("skill_name", "") or "").strip()
        if skill_name.startswith("Web"):
            continue

        functions = skill.get("functions") or []
        if not isinstance(functions, list):
            filtered.append(skill)
            continue

        kept_functions = [
            function_sig
            for function_sig in functions
            if not is_web_tool_name(str(function_sig).split("(", 1)[0].strip())
        ]
        if functions and not kept_functions:
            continue

        copied = dict(skill)
        copied["functions"] = kept_functions
        param_descriptions = copied.get("param_descriptions")
        if isinstance(param_descriptions, dict):
            copied["param_descriptions"] = {
                name: value
                for name, value in param_descriptions.items()
                if not is_web_tool_name(name)
            }
        filtered.append(copied)

    result = {**payload, "skills": filtered}
    _WEB_SKILLS_FILTER_CACHE.clear()
    _WEB_SKILLS_FILTER_CACHE[cache_key] = result
    return result


__all__ = [
    "filter_web_skills",
    "get_sandbox_enabled",
    "get_skill_guidance_enabled",
    "get_web_skills_enabled",
    "set_sandbox_enabled",
    "set_skill_guidance_enabled",
    "set_web_skills_enabled",
]
