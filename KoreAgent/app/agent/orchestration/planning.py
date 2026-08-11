from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Lightweight per-prompt planning.  This is deliberately an advisory action outline:
# it helps the model orient itself, is logged for inspection, and selects a small
# tool bundle.  It does not create a second execution state machine or completion
# contract; persistent Workflow owns those responsibilities when explicitly used.
# ====================================================================================================

import json
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from agent.orchestration.context_window import choose_context_window
from scratchpad import scratchpad_save


MAX_ACTIONS             = 6
MAX_ACTION_TOOLS        = 12
MAX_ACTIVATION_TOOLS    = 24
TASK_PLAN_SCRATCHPAD_KEY = "task_plan"


@dataclass(frozen=True)
class TaskPlan:
    objective:      str
    actions:        list[dict[str, Any]]
    planner_status: str
    created_at:     str

    def payload(self) -> dict[str, Any]:
        return asdict(self)

    def activation_tools(self) -> list[str]:
        """Return the unique union of the tools named by the action outline."""
        tools: list[str] = []
        for action in self.actions:
            for tool_name in action.get("tools") or []:
                name = str(tool_name or "").strip()
                if name and name not in tools:
                    tools.append(name)
                if len(tools) >= MAX_ACTIVATION_TOOLS:
                    return tools
        return tools


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_string_list(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    values: list[str] = []
    for item in value:
        cleaned = str(item or "").strip()
        if cleaned and cleaned not in values:
            values.append(cleaned)
        if len(values) >= limit:
            break
    return values


def _extract_json_object(text: str) -> dict[str, Any] | None:
    source = str(text or "")
    start  = source.find("{")
    if start < 0:
        return None
    try:
        payload, _end = json.JSONDecoder().raw_decode(source[start:])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _validated_actions(raw: object, *, known_tool_names: set[str]) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []

    actions: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, item in enumerate(raw[:MAX_ACTIONS], start=1):
        if not isinstance(item, dict):
            continue
        instruction = str(item.get("action") or item.get("instruction") or "").strip()[:400]
        if not instruction:
            continue
        action_id = str(item.get("id") or f"action_{index}").strip()[:80]
        if not action_id or action_id in used_ids:
            action_id = f"action_{index}"
        used_ids.add(action_id)
        tools = [
            name
            for name in _as_string_list(item.get("tools"), limit=MAX_ACTION_TOOLS)
            if name in known_tool_names
        ]
        actions.append({"id": action_id, "action": instruction, "tools": tools})
    return actions


def validate_task_plan(raw: dict[str, Any], *, known_tool_names: set[str]) -> TaskPlan:
    """Validate a planner response without inferring missing work on its behalf."""
    objective = str(raw.get("objective") or "").strip()[:500] or "Understand and complete the request."
    actions = _validated_actions(raw.get("actions", raw.get("steps")), known_tool_names=known_tool_names)
    return TaskPlan(
        objective      = objective,
        actions        = actions,
        planner_status = "planned",
        created_at     = _utc_now(),
    )


def build_planning_prompt(
    *,
    user_prompt: str,
    capability_catalog: list[dict[str, Any]],
    planning_context: str = "",
    workflow_task_contract: dict[str, Any] | None = None,
) -> str:
    """Build a compact prompt from the tools already active in this chat.

    The full catalog is intentionally absent.  The normal tool loop exposes
    ``tools_catalog_list`` and ``tools_active_add`` if the active set proves
    insufficient, avoiding a large planning-only context payload.
    """
    capabilities = [
        {
            "name":        str(item.get("name") or ""),
            "description": str(item.get("description") or "")[:220],
            "param_names": [str(name or "") for name in (item.get("param_names") or [])[:8]],
        }
        for item in capability_catalog
        if str(item.get("name") or "").strip()
    ]
    schema = {
        "objective": "short restatement of the requested outcome",
        "actions": [
            {
                "id": "short_unique_identifier",
                "action": "one concrete action that may be useful for this run",
                "tools": ["exact active capability names"],
            }
        ],
    }
    workflow_context = json.dumps(workflow_task_contract, ensure_ascii=False) if workflow_task_contract else ""
    return "\n".join(
        [
            "You are the KoreAgent lightweight planner. Interpret the request semantically.",
            "Return a short action outline for this one prompt. It is advisory only: do not create phases, completion gates, output requirements, validation requirements, or a persistent workflow.",
            "Choose only names in ACTIVE_CAPABILITIES. If the active set may be insufficient, include tools_catalog_list and tools_active_add as an action; the execution model can discover and activate more capabilities.",
            "Do not invent tool names. Keep ordinary answers to zero or one action and substantive work to at most six actions.",
            "If WORKFLOW_TASK_CONTRACT is present, it is the explicit persistent task definition. Do not add or replace its deliverables; merely outline useful work for its instruction.",
            "Return exactly one JSON object and no markdown.",
            "",
            "[TASK_PLAN_SCHEMA]",
            json.dumps(schema, ensure_ascii=True),
            "[/TASK_PLAN_SCHEMA]",
            "",
            "[ACTIVE_CAPABILITIES]",
            json.dumps(capabilities, ensure_ascii=True),
            "[/ACTIVE_CAPABILITIES]",
            "",
            "[USER_REQUEST]",
            str(user_prompt or ""),
            "[/USER_REQUEST]",
            "",
            "[EXECUTION_CONTEXT]",
            str(planning_context or "")[:4000],
            "[/EXECUTION_CONTEXT]",
            "",
            "[WORKFLOW_TASK_CONTRACT]",
            workflow_context[:4000],
            "[/WORKFLOW_TASK_CONTRACT]",
        ]
    )


def fallback_task_plan(*, user_prompt: str, reason: str) -> TaskPlan:
    return TaskPlan(
        objective = str(user_prompt or "").strip()[:500] or "Understand and complete the request.",
        actions = [
            {
                "id":     "discover_capabilities",
                "action": "Identify any additional capability needed to complete the request.",
                "tools":  ["tools_catalog_list", "tools_active_add"],
            }
        ],
        planner_status = f"fallback: {str(reason or 'planning unavailable')[:300]}",
        created_at     = _utc_now(),
    )


def create_task_plan(
    *,
    user_prompt: str,
    planning_context: str = "",
    workflow_task_contract: dict[str, Any] | None = None,
    capability_catalog: list[dict[str, Any]],
    known_tool_names: set[str],
    call_llm_chat,
    model_name: str,
    num_ctx: int,
) -> TaskPlan:
    prompt = build_planning_prompt(
        user_prompt            = user_prompt,
        capability_catalog     = capability_catalog,
        planning_context       = planning_context,
        workflow_task_contract = workflow_task_contract,
    )
    try:
        response = call_llm_chat(
            model_name = model_name,
            messages   = [{"role": "user", "content": prompt}],
            tools      = None,
            num_ctx    = choose_context_window(num_ctx, [{"role": "user", "content": prompt}]),
        )
        raw = _extract_json_object(getattr(response, "response", ""))
        if raw is None:
            raise ValueError("planner did not return a JSON object")
        return validate_task_plan(raw, known_tool_names=known_tool_names)
    except Exception as exc:
        return fallback_task_plan(user_prompt=user_prompt, reason=f"Planning unavailable: {exc}")


def persist_task_plan(plan: TaskPlan) -> None:
    """Mirror the current prompt outline for the user and log viewer to inspect."""
    scratchpad_save(
        TASK_PLAN_SCRATCHPAD_KEY,
        json.dumps(plan.payload(), ensure_ascii=False, separators=(",", ":")),
    )


def format_task_plan_context(plan: TaskPlan) -> str:
    return "\n".join(
        [
            "[LIGHTWEIGHT_TASK_OUTLINE]",
            f"Objective: {plan.objective}",
            f"Actions: {json.dumps(plan.actions, ensure_ascii=False, separators=(',', ':'))}",
            "This outline is advisory. Complete the user's request using the available evidence and tools; it does not impose artificial completion checks.",
            "[/LIGHTWEIGHT_TASK_OUTLINE]",
        ]
    )
