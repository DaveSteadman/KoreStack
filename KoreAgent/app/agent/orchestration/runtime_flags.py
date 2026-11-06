# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Service-wide feature switches for orchestration policy. The API controls are not associated with
# a session, so their values intentionally apply to every conversation in this Agent process.
# Callers should snapshot the policy at the start of a run rather than reading mutable state while
# executing it.
# MARK: FUNCTIONS
# Function inventory:
# - get_skill_guidance_enabled: Returns skill guidance enabled for this module.
# - set_skill_guidance_enabled: Sets skill guidance enabled for this module.
# - get_sandbox_enabled: Returns sandbox enabled for this module.
# - set_sandbox_enabled: Sets sandbox enabled for this module.
# - get_web_skills_enabled: Returns web skills enabled for this module.
# - set_web_skills_enabled: Sets web skills enabled for this module.
# - filter_web_skills: Filters web skills for this module.
# ====================================================================================================

import threading
from dataclasses import dataclass

from web_tools_state import is_web_tool_name


@dataclass(frozen=True)
class RuntimePolicy:
    """Immutable snapshot of service-wide orchestration controls."""

    skill_guidance_enabled: bool
    sandbox_enabled: bool
    web_skills_enabled: bool


_policy = RuntimePolicy(
    skill_guidance_enabled=False,
    sandbox_enabled=True,
    web_skills_enabled=True,
)
_policy_lock = threading.RLock()


def get_runtime_policy() -> RuntimePolicy:
    """Return an immutable snapshot of the current service-wide policy."""
    with _policy_lock:
        return _policy


def get_skill_guidance_enabled() -> bool:
    return get_runtime_policy().skill_guidance_enabled


def set_skill_guidance_enabled(enabled: bool) -> None:
    global _policy
    with _policy_lock:
        _policy = RuntimePolicy(
            skill_guidance_enabled=bool(enabled),
            sandbox_enabled=_policy.sandbox_enabled,
            web_skills_enabled=_policy.web_skills_enabled,
        )


def get_sandbox_enabled() -> bool:
    return get_runtime_policy().sandbox_enabled


def set_sandbox_enabled(enabled: bool) -> None:
    global _policy
    with _policy_lock:
        _policy = RuntimePolicy(
            skill_guidance_enabled=_policy.skill_guidance_enabled,
            sandbox_enabled=bool(enabled),
            web_skills_enabled=_policy.web_skills_enabled,
        )


def get_web_skills_enabled() -> bool:
    return get_runtime_policy().web_skills_enabled


def set_web_skills_enabled(enabled: bool) -> None:
    global _policy
    with _policy_lock:
        _policy = RuntimePolicy(
            skill_guidance_enabled=_policy.skill_guidance_enabled,
            sandbox_enabled=_policy.sandbox_enabled,
            web_skills_enabled=bool(enabled),
        )


def filter_web_skills(payload: dict) -> dict:
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

    return {**payload, "skills": filtered}


__all__ = [
    "filter_web_skills",
    "get_runtime_policy",
    "get_sandbox_enabled",
    "get_skill_guidance_enabled",
    "get_web_skills_enabled",
    "RuntimePolicy",
    "set_sandbox_enabled",
    "set_skill_guidance_enabled",
    "set_web_skills_enabled",
]
