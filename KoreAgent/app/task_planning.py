from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# LLM-backed task planning for KoreAgent orchestration. The planner interprets natural language before
# tool execution; host code validates the resulting plan but does not infer task intent from keywords.
# ====================================================================================================

import json
import re
import threading
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from session_runtime import get_active_session_id


MAX_PHASE_TOOLS          = 12
MAX_ACTIVATION_TOOLS     = 16
VALID_PHASES             = ("clarify", "inspect", "plan", "act", "validate", "complete")
ALWAYS_ON_TOOL_NAMES     = frozenset({"delegate", "tools_catalog_list", "tools_active_add"})
_TOKEN_RE                = re.compile(r"[a-z0-9_]{3,}", re.IGNORECASE)

# Task plans are controller state, not agent scratchpad data.  Keeping them here avoids an agent
# reasoning over its own orchestration instructions through scratchpad tools.
_PLAN_STATE_BY_SESSION: dict[str, dict[str, Any]] = {}
_PLANNER_SELECTION_TRACE_BY_SESSION: dict[str, dict[str, Any]] = {}
_PLAN_STATE_LOCK                          = threading.RLock()


@dataclass(frozen=True)
class TaskPlan:
    objective:              str
    task_class:             str
    confidence:             float
    current_phase:          str
    workflow:               list[str]
    phase_tools:            list[str]
    phase_tool_map:         dict[str, list[str]]
    required_artifacts:     list[str]
    validation_requirements: list[str]
    completion_contract:    str
    rationale:              str
    planner_status:         str
    created_at:             str

    def payload(self) -> dict[str, Any]:
        return asdict(self)

    def activation_tools(self) -> list[str]:
        """Return a bounded current-plus-next-phase tool bundle for this run."""
        active_phases = [self.current_phase]
        try:
            index = self.workflow.index(self.current_phase)
        except ValueError:
            index = -1
        if index >= 0 and index + 1 < len(self.workflow):
            active_phases.append(self.workflow[index + 1])

        tools: list[str] = []
        for phase in active_phases:
            for tool_name in self.phase_tool_map.get(phase, []):
                if tool_name not in tools:
                    tools.append(tool_name)
                if len(tools) >= MAX_ACTIVATION_TOOLS:
                    return tools
        for tool_name in self.phase_tools:
            if tool_name not in tools:
                tools.append(tool_name)
            if len(tools) >= MAX_ACTIVATION_TOOLS:
                break
        return tools


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_string_list(value: object, *, limit: int = 12) -> list[str]:
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


def _validated_phase_tool_map(raw: object, *, known_tool_names: set[str]) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, list[str]] = {}
    for phase, tool_names in raw.items():
        normalized_phase = str(phase or "").strip().lower()
        if normalized_phase not in VALID_PHASES:
            continue
        selected = [name for name in _as_string_list(tool_names, limit=MAX_PHASE_TOOLS) if name in known_tool_names]
        if selected:
            result[normalized_phase] = selected
    return result


def _extract_json_object(text: str) -> dict[str, Any] | None:
    source = str(text or "")
    start  = source.find("{")
    if start < 0:
        return None
    decoder = json.JSONDecoder()
    try:
        payload, _end = decoder.raw_decode(source[start:])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _search_tokens(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(str(text or ""))]


def _entry_relevance(entry: dict[str, Any], tokens: list[str]) -> float:
    if not tokens:
        return 1.0
    searchable_parts = [
        str(entry.get("name") or ""),
        str(entry.get("description") or ""),
        str(entry.get("origin") or ""),
        str(entry.get("skill_name") or ""),
    ]
    searchable_parts.extend(str(item or "") for item in (entry.get("triggers") or []))
    searchable_parts.extend(str(item or "") for item in (entry.get("param_names") or []))
    haystack = " ".join(searchable_parts).lower()
    if not haystack:
        return 0.0

    score = 0.0
    for token in tokens:
        if token in haystack:
            score += 1.0
        if token and str(entry.get("name") or "").lower().startswith(token):
            score += 1.0
    return score


def select_planner_capabilities(
    *,
    user_prompt: str,
    capability_catalog: list[dict[str, Any]],
    include_trace: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], dict[str, Any]]:
    """Keep the planner prompt focused by removing clearly irrelevant tools.

    This intentionally avoids a fixed item cap. The selection is semantic-ish lexical
    matching over tool metadata plus always-on/active tools to preserve control-plane access.
    """
    tokens = _search_tokens(user_prompt)
    scored: list[tuple[float, dict[str, Any]]] = []
    trace_rows: list[dict[str, Any]] = []
    for entry in capability_catalog:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        score = _entry_relevance(entry, tokens)
        flags: list[str] = []
        if name in ALWAYS_ON_TOOL_NAMES:
            score = max(score, 1000.0)
            flags.append("always_on")
        elif bool(entry.get("active")):
            score = max(score, 100.0)
            flags.append("active")
        scored.append((score, entry))
        if include_trace:
            trace_rows.append(
                {
                    "name": name,
                    "score": round(score, 3),
                    "origin": str(entry.get("origin") or ""),
                    "flags": flags,
                }
            )

    selected = [entry for score, entry in scored if score > 0.0]
    if selected:
        selected.sort(key=lambda item: (str(item.get("origin") or ""), str(item.get("name") or "")))
        if include_trace:
            trace_rows.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("name") or "")))
            return selected, {
                "tokens": tokens,
                "total_catalog": len(scored),
                "selected_count": len(selected),
                "fallback_all": False,
                "top": trace_rows[:25],
            }
        return selected

    # If lexical matching fails, keep the full catalog rather than starving the planner.
    fallback_selected = [entry for _score, entry in scored]
    if include_trace:
        trace_rows.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("name") or "")))
        return fallback_selected, {
            "tokens": tokens,
            "total_catalog": len(scored),
            "selected_count": len(fallback_selected),
            "fallback_all": True,
            "top": trace_rows[:25],
        }
    return fallback_selected


def build_planning_prompt(*, user_prompt: str, capability_catalog: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    selected_catalog, selection_trace = select_planner_capabilities(
        user_prompt=user_prompt,
        capability_catalog=capability_catalog,
        include_trace=True,
    )
    capabilities = [
        {
            "name":        str(item.get("name") or ""),
            "description": str(item.get("description") or "")[:300],
            "active":      bool(item.get("active")),
            "origin":      str(item.get("origin") or ""),
            "skill_name":  str(item.get("skill_name") or ""),
            "triggers":    [str(x or "") for x in (item.get("triggers") or [])[:6]],
            "param_names": [str(x or "") for x in (item.get("param_names") or [])[:8]],
        }
        for item in selected_catalog
        if str(item.get("name") or "").strip()
    ]
    schema = {
        "objective": "short restatement of the requested outcome",
        "task_class": "free-form task category",
        "confidence": 0.0,
        "current_phase": "clarify|inspect|plan|act|validate|complete",
        "workflow": ["ordered phase names"],
        "phase_tools": ["tool names needed in the current phase only"],
        "phase_tool_map": {"inspect": ["evidence tools"], "act": ["action tools"], "validate": ["verification tools"]},
        "required_artifacts": ["evidence or durable artifacts needed"],
        "validation_requirements": ["checks needed before completion"],
        "completion_contract": "what must be true before reporting completion",
        "rationale": "brief planning rationale",
    }
    return "\n".join(
        [
            "You are the KoreAgent task planner. Interpret the user's request semantically.",
            "Do not use keyword matching as a substitute for understanding the request.",
            "Choose only capabilities present in the catalog. phase_tools is for the current phase; phase_tool_map may name the immediate next phases needed to finish a short workflow.",
            "Never invent a tool name. If no capability is needed, return an empty list rather than a category such as 'catalog'.",
            "Use clarify only when the request cannot be safely interpreted from context.",
            "Use inspect before a file change when current source evidence is needed.",
            "Return exactly one JSON object and no markdown.",
            "",
            "[TASK_PLAN_SCHEMA]",
            json.dumps(schema, ensure_ascii=True),
            "[/TASK_PLAN_SCHEMA]",
            "",
            "[CAPABILITY_CATALOG]",
            json.dumps(capabilities, ensure_ascii=True),
            "[/CAPABILITY_CATALOG]",
            "",
            "[USER_REQUEST]",
            str(user_prompt or ""),
            "[/USER_REQUEST]",
        ]
    ), selection_trace


def fallback_task_plan(*, user_prompt: str, reason: str) -> TaskPlan:
    return TaskPlan(
        objective               = str(user_prompt or "").strip()[:500] or "Understand and complete the request.",
        task_class              = "unclassified",
        confidence              = 0.0,
        current_phase           = "inspect",
        workflow                = ["inspect", "plan", "act", "validate", "complete"],
        phase_tools             = ["tools_catalog_list", "tools_active_add"],
        phase_tool_map          = {"inspect": ["tools_catalog_list", "tools_active_add"]},
        required_artifacts      = ["source-backed evidence"],
        validation_requirements = ["state what was verified"],
        completion_contract     = "Report grounded results or the precise blocker.",
        rationale               = reason,
        planner_status          = "fallback",
        created_at              = _utc_now(),
    )


def validate_task_plan(raw: dict[str, Any], *, known_tool_names: set[str]) -> TaskPlan:
    phase = str(raw.get("current_phase") or "inspect").strip().lower()
    if phase not in VALID_PHASES:
        phase = "inspect"
    requested_tools = _as_string_list(raw.get("phase_tools"), limit=MAX_PHASE_TOOLS)
    phase_tools     = [name for name in requested_tools if name in known_tool_names]
    phase_tool_map  = _validated_phase_tool_map(raw.get("phase_tool_map"), known_tool_names=known_tool_names)
    objective       = str(raw.get("objective") or "").strip()[:500]
    if not objective:
        objective = "Understand and complete the request."
    confidence = raw.get("confidence", 0.5)
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.5
    workflow = [phase_name.lower() for phase_name in _as_string_list(raw.get("workflow"), limit=8)]
    workflow = [phase_name for phase_name in workflow if phase_name in VALID_PHASES]
    if not workflow:
        workflow = [phase, "complete"] if phase != "complete" else ["complete"]
    if phase not in workflow:
        workflow.insert(0, phase)
    if phase_tools and phase not in phase_tool_map:
        phase_tool_map[phase] = list(phase_tools)
    return TaskPlan(
        objective               = objective,
        task_class              = str(raw.get("task_class") or "general").strip()[:120] or "general",
        confidence              = confidence,
        current_phase           = phase,
        workflow                = workflow,
        phase_tools             = phase_tools,
        phase_tool_map          = phase_tool_map,
        required_artifacts      = _as_string_list(raw.get("required_artifacts")),
        validation_requirements = _as_string_list(raw.get("validation_requirements")),
        completion_contract     = str(raw.get("completion_contract") or "Report grounded results or the precise blocker.").strip()[:500],
        rationale               = str(raw.get("rationale") or "").strip()[:500],
        planner_status          = "planned",
        created_at              = _utc_now(),
    )


def create_task_plan(
    *,
    user_prompt: str,
    capability_catalog: list[dict[str, Any]],
    known_tool_names: set[str],
    call_llm_chat,
    model_name: str,
    num_ctx: int,
) -> TaskPlan:
    prompt, selection_trace = build_planning_prompt(user_prompt=user_prompt, capability_catalog=capability_catalog)
    planner_num_ctx = max(4096, int(num_ctx or 0))
    with _PLAN_STATE_LOCK:
        _PLANNER_SELECTION_TRACE_BY_SESSION[get_active_session_id()] = dict(selection_trace)
    try:
        response = call_llm_chat(
            model_name = model_name,
            messages   = [{"role": "user", "content": prompt}],
            tools      = None,
            num_ctx    = planner_num_ctx,
        )
        raw = _extract_json_object(getattr(response, "response", ""))
        if raw is None:
            raise ValueError("planner did not return a JSON object")
        plan = validate_task_plan(raw, known_tool_names=known_tool_names)
        requested = _as_string_list(raw.get("phase_tools"), limit=MAX_PHASE_TOOLS)
        if requested and not plan.phase_tools:
            repair_prompt = (
                f"{prompt}\n\n[PLANNER_REPAIR]\nThe previous tool selection was invalid. "
                "Return the same schema using only exact catalog capability names.\n[/PLANNER_REPAIR]"
            )
            repair = call_llm_chat(
                model_name = model_name,
                messages   = [{"role": "user", "content": repair_prompt}],
                tools      = None,
                num_ctx    = planner_num_ctx,
            )
            repaired_raw = _extract_json_object(getattr(repair, "response", ""))
            if repaired_raw is not None:
                plan = validate_task_plan(repaired_raw, known_tool_names=known_tool_names)
        return plan
    except Exception as exc:
        return fallback_task_plan(user_prompt=user_prompt, reason=f"Planning unavailable: {exc}")


def persist_task_plan(plan: TaskPlan) -> None:
    payload = plan.payload()
    with _PLAN_STATE_LOCK:
        selection_trace = _PLANNER_SELECTION_TRACE_BY_SESSION.get(get_active_session_id())
    if isinstance(selection_trace, dict):
        payload["selection_trace"] = selection_trace
    payload["state"] = {
        "status": "running",
        "phase":  plan.current_phase,
        "events": [{"type": "planned", "at": _utc_now(), "detail": plan.rationale}],
    }
    with _PLAN_STATE_LOCK:
        _PLAN_STATE_BY_SESSION[get_active_session_id()] = payload


def record_task_plan_event(
    event_type: str,
    detail: str = "",
    *,
    phase: str | None = None,
    status: str | None = None,
) -> None:
    with _PLAN_STATE_LOCK:
        payload = _PLAN_STATE_BY_SESSION.get(get_active_session_id())
        if not isinstance(payload, dict):
            return
        state = payload.get("state")
        if not isinstance(state, dict):
            state = {"status": "running", "phase": payload.get("current_phase") or "inspect", "events": []}
        events = list(state.get("events") or [])[-39:]
        events.append({"type": str(event_type or "event"), "at": _utc_now(), "detail": str(detail or "")[:500]})
        state["events"] = events
        if phase in VALID_PHASES:
            state["phase"] = phase
        if status:
            state["status"] = str(status)
        payload["state"] = state


def get_last_planner_selection_trace() -> dict[str, Any]:
    with _PLAN_STATE_LOCK:
        trace = _PLANNER_SELECTION_TRACE_BY_SESSION.get(get_active_session_id())
        return dict(trace) if isinstance(trace, dict) else {}


def get_task_plan_phase() -> str:
    with _PLAN_STATE_LOCK:
        payload = _PLAN_STATE_BY_SESSION.get(get_active_session_id())
        if not isinstance(payload, dict):
            return "inspect"
        state = payload.get("state")
        if isinstance(state, dict):
            phase = str(state.get("phase") or "").strip().lower()
            if phase in VALID_PHASES:
                return phase
        phase = str(payload.get("current_phase") or "inspect").strip().lower()
        return phase if phase in VALID_PHASES else "inspect"


def _phase_activation_tools(payload: dict[str, Any], phase: str) -> list[str]:
    workflow = [str(item or "").strip().lower() for item in (payload.get("workflow") or [])]
    phase_tool_map = payload.get("phase_tool_map") if isinstance(payload.get("phase_tool_map"), dict) else {}
    phase_tools = [str(item or "").strip() for item in (payload.get("phase_tools") or []) if str(item or "").strip()]

    active_phases = [phase]
    try:
        index = workflow.index(phase)
    except ValueError:
        index = -1
    if index >= 0 and index + 1 < len(workflow):
        active_phases.append(workflow[index + 1])

    selected: list[str] = []
    for phase_name in active_phases:
        items = phase_tool_map.get(phase_name) if isinstance(phase_tool_map, dict) else None
        if not isinstance(items, list):
            continue
        for tool_name in items:
            normalized = str(tool_name or "").strip()
            if normalized and normalized not in selected:
                selected.append(normalized)
            if len(selected) >= MAX_ACTIVATION_TOOLS:
                return selected
    for tool_name in phase_tools:
        if tool_name and tool_name not in selected:
            selected.append(tool_name)
        if len(selected) >= MAX_ACTIVATION_TOOLS:
            break
    return selected


def get_task_plan_activation_tools() -> list[str]:
    with _PLAN_STATE_LOCK:
        payload = _PLAN_STATE_BY_SESSION.get(get_active_session_id())
        if not isinstance(payload, dict):
            return list(ALWAYS_ON_TOOL_NAMES)
        phase = get_task_plan_phase()
        selected = _phase_activation_tools(payload, phase)
        for tool_name in ALWAYS_ON_TOOL_NAMES:
            if tool_name not in selected:
                selected.append(tool_name)
        return selected


def _next_workflow_phase(workflow: list[str], phase: str) -> str | None:
    normalized_workflow = [str(item or "").strip().lower() for item in workflow if str(item or "").strip()]
    if phase not in normalized_workflow:
        return None
    index = normalized_workflow.index(phase)
    if index + 1 < len(normalized_workflow):
        nxt = normalized_workflow[index + 1]
        if nxt in VALID_PHASES:
            return nxt
    return None


def _successful_tool_names(round_outputs: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for item in round_outputs:
        if bool(item.get("is_error")):
            continue
        name = str(item.get("tool") or item.get("function") or "").strip().lower()
        if name:
            names.append(name)
    return names


def _phase_transition_satisfied(phase: str, successful_tool_names: list[str]) -> bool:
    if not successful_tool_names:
        return False

    if phase == "inspect":
        inspect_hints = ("read", "list", "find", "search", "inspect", "peek", "query", "get")
        return any(any(hint in name for hint in inspect_hints) for name in successful_tool_names)

    if phase == "plan":
        plan_hints = ("tools_catalog", "tools_active", "task_", "delegate")
        return any(any(hint in name for hint in plan_hints) for name in successful_tool_names)

    if phase == "act":
        act_hints = ("write", "create", "delete", "update", "append", "execute", "run", "spawn", "save", "add", "set")
        return any(any(hint in name for hint in act_hints) for name in successful_tool_names)

    if phase == "validate":
        validate_hints = ("validate", "check", "test", "verify", "status", "inspect", "read", "list", "diff")
        return any(any(hint in name for hint in validate_hints) for name in successful_tool_names)

    return True


def advance_task_plan_phase(round_outputs: list[dict[str, Any]] | None = None) -> str:
    """Advance through the declared workflow when phase-specific criteria are met."""
    outputs = list(round_outputs or [])
    successful_tool_names = _successful_tool_names(outputs)
    has_success = bool(successful_tool_names)

    with _PLAN_STATE_LOCK:
        payload = _PLAN_STATE_BY_SESSION.get(get_active_session_id())
        if not isinstance(payload, dict):
            return "inspect"

        state = payload.get("state")
        if not isinstance(state, dict):
            state = {
                "status": "running",
                "phase": str(payload.get("current_phase") or "inspect"),
                "events": [],
            }

        current_phase = str(state.get("phase") or payload.get("current_phase") or "inspect").strip().lower()
        if current_phase not in VALID_PHASES:
            current_phase = "inspect"
        if current_phase == "complete":
            state["phase"] = "complete"
            payload["state"] = state
            return "complete"

        if not has_success:
            state["phase"] = current_phase
            payload["state"] = state
            return current_phase

        if not _phase_transition_satisfied(current_phase, successful_tool_names):
            events = list(state.get("events") or [])[-39:]
            events.append(
                {
                    "type": "phase_hold",
                    "at": _utc_now(),
                    "detail": f"{current_phase} criteria not met by tools: {', '.join(successful_tool_names)}",
                }
            )
            state["events"] = events
            state["phase"] = current_phase
            payload["state"] = state
            return current_phase

        workflow = payload.get("workflow") if isinstance(payload.get("workflow"), list) else []
        next_phase = _next_workflow_phase(workflow, current_phase)
        if next_phase is None:
            next_phase = "complete" if current_phase == "validate" else current_phase

        if next_phase != current_phase:
            events = list(state.get("events") or [])[-39:]
            events.append(
                {
                    "type": "phase_advanced",
                    "at": _utc_now(),
                    "detail": f"{current_phase} -> {next_phase}",
                }
            )
            state["events"] = events
        state["phase"] = next_phase
        payload["state"] = state
        return next_phase


def format_task_plan_context(plan: TaskPlan) -> str:
    return "\n".join(
        [
            "[ACTIVE_TASK_PLAN]",
            f"Objective: {plan.objective}",
            f"Task class: {plan.task_class} | confidence: {plan.confidence:.2f}",
            f"Current phase: {plan.current_phase}",
            f"Workflow: {' -> '.join(plan.workflow)}",
            f"Phase tools: {', '.join(plan.phase_tools) or 'catalog discovery only'}",
            f"Activation tools: {', '.join(plan.activation_tools()) or 'none'}",
            f"Required artifacts: {'; '.join(plan.required_artifacts) or 'none'}",
            f"Validation: {'; '.join(plan.validation_requirements) or 'none'}",
            f"Completion contract: {plan.completion_contract}",
            "Follow the current phase. Do not repeat evidence collection after it is sufficient.",
            "[/ACTIVE_TASK_PLAN]",
        ]
    )
