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
from pathlib import Path
from typing import Any

from scratchpad import scratchpad_load
from scratchpad import scratchpad_save
from sessions.runtime import get_active_session_id


MAX_PHASE_TOOLS          = 12
MAX_ACTIVATION_TOOLS     = 16
MAX_EXECUTION_STEPS       = 6
MAX_STEP_OUTPUTS          = 6
MAX_EXECUTION_PLAN_CHARS  = 12000
VALID_PHASES             = ("clarify", "inspect", "plan", "act", "validate", "complete")
VALID_OUTPUT_TYPES        = frozenset({"file", "dataset", "scratchpad"})
ALWAYS_ON_TOOL_NAMES     = frozenset({"tools_catalog_list", "tools_active_add"})
TASK_PLAN_SCRATCHPAD_KEY = "task_plan"
_PLAN_TASK_EXECUTION_RE = re.compile(
    r"\b(?:run|execute|continue|rerun)\s+(?:the\s+)?(?:first|second|third|fourth|fifth|\d+(?:st|nd|rd|th)?)?\s*task(?:\s+\d+)?\b"
    r"|\b(?:run|execute|continue|rerun)\s+(?:the\s+)?(?:plan|workflow)(?:\s+to\s+completion)?\b",
    re.IGNORECASE,
)
_TOKEN_RE                = re.compile(r"[a-z0-9_]{3,}", re.IGNORECASE)
_GRAPH_WRITE_INTENT_RE   = re.compile(
    r"\b(?:add|create|insert|save|store|submit|write|load)\b.{0,80}\b(?:graph|koregraph|triple|triples|graph connection|graph connections)\b"
    r"|\b(?:graph|koregraph|triple|triples|graph connection|graph connections)\b.{0,80}\b(?:add|create|insert|save|store|submit|write|load)\b",
    re.IGNORECASE | re.DOTALL,
)

# Task plans retain a controller cache for orchestration and are mirrored to the named
# ``task_plan`` scratchpad entry. KoreChat persists named scratchpad entries with its
# definitive conversation record at the end of a turn.
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
    steps:                  list[dict[str, Any]]
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


def _mirror_task_plan_to_scratchpad(payload: dict[str, Any]) -> None:
    """Persist the current plan snapshot under its stable, user-visible scratchpad key."""
    serialized_payload = json.dumps(
        payload,
        ensure_ascii = False,
        separators    = (",", ":"),
    )
    scratchpad_save(TASK_PLAN_SCRATCHPAD_KEY, serialized_payload)


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


def _validated_execution_steps(raw: object, *, known_tool_names: set[str]) -> list[dict[str, Any]]:
    """Keep the per-run execution outline bounded, typed, and safe to persist."""
    if not isinstance(raw, list):
        return []
    steps: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, item in enumerate(raw[:MAX_EXECUTION_STEPS], start=1):
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip()[:300]
        if not action:
            continue
        step_id = str(item.get("id") or f"step_{index}").strip()[:80]
        if not step_id or step_id in used_ids:
            step_id = f"step_{index}"
        used_ids.add(step_id)
        phase = str(item.get("phase") or "act").strip().lower()
        if phase not in VALID_PHASES:
            phase = "act"
        tools = [name for name in _as_string_list(item.get("tools"), limit=MAX_PHASE_TOOLS) if name in known_tool_names]
        outputs: list[dict[str, Any]] = []
        for output in item.get("outputs") if isinstance(item.get("outputs"), list) else []:
            if not isinstance(output, dict) or len(outputs) >= MAX_STEP_OUTPUTS:
                continue
            output_type = str(output.get("type") or "artifact").strip().lower()[:80]
            target = str(output.get("target") or output.get("path") or output.get("name") or output.get("key") or "").strip()[:240]
            if output_type not in VALID_OUTPUT_TYPES or not target:
                continue
            if output_type == "dataset" and Path(target).suffix:
                output_type = "file"
            normalized = {"type": output_type, "target": target}
            description = str(output.get("description") or "").strip()[:160]
            if description:
                normalized["description"] = description
            minimum_bytes = output.get("minimum_bytes")
            if isinstance(minimum_bytes, int) and minimum_bytes >= 0:
                normalized["minimum_bytes"] = minimum_bytes
            minimum_items = output.get("minimum_items")
            if isinstance(minimum_items, int) and minimum_items >= 0:
                normalized["minimum_items"] = minimum_items
            outputs.append(normalized)
        steps.append(
            {
                "id":                step_id,
                "phase":             phase,
                "action":            action,
                "tools":             tools,
                "outputs":           outputs,
                "completion_checks": _as_string_list(item.get("completion_checks"), limit=8),
            }
        )
    return steps


def _bounded_execution_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Avoid letting a planner response consume an unbounded amount of run context."""
    bounded: list[dict[str, Any]] = []
    used_chars = 0
    for step in steps:
        encoded = json.dumps(step, ensure_ascii=False, separators=(",", ":"))
        if used_chars + len(encoded) > MAX_EXECUTION_PLAN_CHARS:
            break
        bounded.append(step)
        used_chars += len(encoded)
    return bounded


def _add_dataset_access_tools(
    *,
    phase_tools: list[str],
    phase_tool_map: dict[str, list[str]],
    steps: list[dict[str, Any]],
    current_phase: str,
    known_tool_names: set[str],
) -> tuple[list[str], dict[str, list[str]], list[dict[str, Any]]]:
    """Keep a dataset-producing step able to read and verify the data it creates.

    Search tools commonly place their full result in a named dataset.  A lightweight
    plan which can search but cannot call dataset_get then cannot turn that evidence
    into a file or report.  This is a required execution dependency, not a model
    preference, so it is added by the host after validating the planner response.
    """
    access_tools = [
        tool_name
        for tool_name in ("dataset_get", "dataset_inspect")
        if tool_name in known_tool_names
    ]
    if not access_tools:
        return phase_tools, phase_tool_map, steps

    required_phases: set[str] = set()
    updated_steps: list[dict[str, Any]] = []
    for step in steps:
        updated = dict(step)
        step_tools = list(updated.get("tools") or [])
        produces_dataset = any(
            isinstance(output, dict) and output.get("type") == "dataset"
            for output in updated.get("outputs") or []
        )
        if "koredata_search" in step_tools or produces_dataset:
            required_phases.add(str(updated.get("phase") or current_phase).strip().lower())
            for tool_name in access_tools:
                if tool_name not in step_tools and len(step_tools) < MAX_PHASE_TOOLS:
                    step_tools.append(tool_name)
            updated["tools"] = step_tools
        updated_steps.append(updated)

    updated_map = {phase: list(tools) for phase, tools in phase_tool_map.items()}
    for phase in required_phases:
        if phase not in VALID_PHASES:
            continue
        tools = updated_map.setdefault(phase, [])
        for tool_name in access_tools:
            if tool_name not in tools and len(tools) < MAX_PHASE_TOOLS:
                tools.append(tool_name)

    updated_phase_tools = list(phase_tools)
    if current_phase in required_phases:
        for tool_name in access_tools:
            if tool_name not in updated_phase_tools and len(updated_phase_tools) < MAX_PHASE_TOOLS:
                updated_phase_tools.append(tool_name)
    return updated_phase_tools, updated_map, updated_steps


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


def build_planning_prompt(
    *,
    user_prompt: str,
    capability_catalog: list[dict[str, Any]],
    planning_context: str = "",
    workflow_task_contract: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    contract_context = json.dumps(workflow_task_contract, ensure_ascii=False) if workflow_task_contract else ""
    selected_catalog, selection_trace = select_planner_capabilities(
        user_prompt=f"{user_prompt}\n{planning_context}\n{contract_context}",
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
        "steps": [
            {
                "id": "short_unique_identifier",
                "phase": "inspect|plan|act|validate",
                "action": "one concrete action required by this run",
                "tools": ["exact catalog tool names"],
                "outputs": [{"type": "file|dataset|scratchpad", "target": "path, name, or key", "minimum_bytes": 1, "minimum_items": 1}],
                "completion_checks": ["objective checks for this action"],
            }
        ],
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
            "For a substantive request, return a bounded ordered steps list (normally 2 to 6 items). Each executable step must name every exact catalog tool needed to complete it, may declare multiple typed outputs of only file, dataset, or scratchpad, and must state its completion checks. A path ending in a file extension is a file output, not a dataset. When a search creates a dataset that a later file/report step must use, include dataset_get and dataset_inspect in the relevant phase. For a simple answer with no work, steps may be empty.",
            "If WORKFLOW_TASK_CONTRACT is supplied, it is immutable. Use it as the sole definition of the task's required files, datasets, and evidence. Your step outputs may name only temporary scratchpad or working artefacts; never replace, rename, or add competing final deliverables.",
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
            "",
            "[EXECUTION_CONTEXT]",
            str(planning_context or "")[:6000],
            "[/EXECUTION_CONTEXT]",
            "",
            "[WORKFLOW_TASK_CONTRACT]",
            contract_context[:6000],
            "[/WORKFLOW_TASK_CONTRACT]",
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
        steps                   = [{"id": "inspect", "phase": "inspect", "action": "Identify the capabilities and evidence needed to complete the request.", "tools": ["tools_catalog_list", "tools_active_add"], "outputs": [], "completion_checks": ["Required capabilities are identified."]}],
        required_artifacts      = ["source-backed evidence"],
        validation_requirements = ["state what was verified"],
        completion_contract     = "Report grounded results or the precise blocker.",
        rationale               = reason,
        planner_status          = "fallback",
        created_at              = _utc_now(),
    )


def _append_unique_tool_names(target: list[str], names: list[str], *, limit: int = MAX_PHASE_TOOLS) -> list[str]:
    for name in names:
        cleaned = str(name or "").strip()
        if cleaned and cleaned not in target:
            target.append(cleaned)
        if len(target) >= limit:
            break
    return target


def _apply_intent_overrides(plan: TaskPlan, *, user_prompt: str, known_tool_names: set[str]) -> TaskPlan:
    override_phase_tools = list(plan.phase_tools)
    override_phase_tool_map = {
        str(phase or "").strip().lower(): [str(name or "").strip() for name in (tool_names or []) if str(name or "").strip()]
        for phase, tool_names in plan.phase_tool_map.items()
    }

    if _GRAPH_WRITE_INTENT_RE.search(user_prompt or ""):
        graph_tools = [name for name in known_tool_names if name.startswith("graph_connection_")]
        graph_write_tools = [name for name in graph_tools if any(token in name for token in ("create", "add", "write", "save"))]
        if graph_write_tools:
            act_tools = list(override_phase_tool_map.get("act", []))
            validate_tools = list(override_phase_tool_map.get("validate", []))
            _append_unique_tool_names(act_tools, sorted(graph_write_tools))
            _append_unique_tool_names(validate_tools, sorted(graph_tools))
            override_phase_tool_map["act"] = act_tools
            override_phase_tool_map["validate"] = validate_tools
            if plan.current_phase in {"inspect", "plan"}:
                _append_unique_tool_names(override_phase_tools, sorted(graph_write_tools))

    if _PLAN_TASK_EXECUTION_RE.search(user_prompt or ""):
        lifecycle_tools = [
            "workflow_get_task",
            "workflow_set_task_data",
            "workflow_record_task_result",
            "workflow_check_task_contract",
            "workflow_mark_task_ran",
            "workflow_run_to_completion",
            "workflow_get_summary",
        ]
        lifecycle_tools = [name for name in lifecycle_tools if name in known_tool_names]
        _append_unique_tool_names(override_phase_tools, lifecycle_tools)

    return TaskPlan(
        objective               = plan.objective,
        task_class              = plan.task_class,
        confidence              = plan.confidence,
        current_phase           = plan.current_phase,
        workflow                = list(plan.workflow),
        phase_tools             = override_phase_tools,
        phase_tool_map          = override_phase_tool_map,
        steps                   = [dict(step) for step in plan.steps],
        required_artifacts      = list(plan.required_artifacts),
        validation_requirements = list(plan.validation_requirements),
        completion_contract     = plan.completion_contract,
        rationale               = plan.rationale,
        planner_status          = plan.planner_status,
        created_at              = plan.created_at,
    )


def validate_task_plan(raw: dict[str, Any], *, known_tool_names: set[str]) -> TaskPlan:
    phase = str(raw.get("current_phase") or "inspect").strip().lower()
    if phase not in VALID_PHASES:
        phase = "inspect"
    requested_tools = _as_string_list(raw.get("phase_tools"), limit=MAX_PHASE_TOOLS)
    phase_tools     = [name for name in requested_tools if name in known_tool_names]
    phase_tool_map  = _validated_phase_tool_map(raw.get("phase_tool_map"), known_tool_names=known_tool_names)
    steps           = _bounded_execution_steps(_validated_execution_steps(raw.get("steps"), known_tool_names=known_tool_names))
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
    phase_tools, phase_tool_map, steps = _add_dataset_access_tools(
        phase_tools     = phase_tools,
        phase_tool_map  = phase_tool_map,
        steps           = steps,
        current_phase   = phase,
        known_tool_names = known_tool_names,
    )
    return TaskPlan(
        objective               = objective,
        task_class              = str(raw.get("task_class") or "general").strip()[:120] or "general",
        confidence              = confidence,
        current_phase           = phase,
        workflow                = workflow,
        phase_tools             = phase_tools,
        phase_tool_map          = phase_tool_map,
        steps                   = steps,
        required_artifacts      = _as_string_list(raw.get("required_artifacts")),
        validation_requirements = _as_string_list(raw.get("validation_requirements")),
        completion_contract     = str(raw.get("completion_contract") or "Report grounded results or the precise blocker.").strip()[:500],
        rationale               = str(raw.get("rationale") or "").strip()[:500],
        planner_status          = "planned",
        created_at              = _utc_now(),
    )


def _execution_steps_need_repair(plan: TaskPlan) -> bool:
    """Reject a substantive execution outline that cannot perform its declared actions."""
    return any(
        step.get("phase") in {"act", "validate"} and not step.get("tools")
        for step in plan.steps
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
    prompt, selection_trace = build_planning_prompt(
        user_prompt=user_prompt,
        capability_catalog=capability_catalog,
        planning_context=planning_context,
        workflow_task_contract=workflow_task_contract,
    )
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
        if (requested and not plan.phase_tools) or _execution_steps_need_repair(plan):
            repair_prompt = (
                f"{prompt}\n\n[PLANNER_REPAIR]\nThe previous execution outline omitted usable tools. "
                "Every act or validate step must name one or more exact catalog capability names. "
                "Use file outputs for paths with a filename extension. Return the same schema only.\n[/PLANNER_REPAIR]"
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
        return _apply_intent_overrides(plan, user_prompt=user_prompt, known_tool_names=known_tool_names)
    except Exception as exc:
        fallback = fallback_task_plan(user_prompt=user_prompt, reason=f"Planning unavailable: {exc}")
        return _apply_intent_overrides(fallback, user_prompt=user_prompt, known_tool_names=known_tool_names)


def persist_task_plan(plan: TaskPlan) -> None:
    payload = plan.payload()
    with _PLAN_STATE_LOCK:
        selection_trace = _PLANNER_SELECTION_TRACE_BY_SESSION.get(get_active_session_id())
    if isinstance(selection_trace, dict):
        payload["selection_trace"] = selection_trace
    payload["state"] = {
        "status":     "running",
        "phase":      plan.current_phase,
        "used_tools": [],
        "events":     [{"type": "planned", "at": _utc_now(), "detail": plan.rationale}],
    }
    with _PLAN_STATE_LOCK:
        _PLAN_STATE_BY_SESSION[get_active_session_id()] = payload
    _mirror_task_plan_to_scratchpad(payload)


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
        _mirror_task_plan_to_scratchpad(payload)


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
        for step in payload.get("steps") or []:
            if not isinstance(step, dict) or str(step.get("phase") or "").strip().lower() != phase_name:
                continue
            for tool_name in step.get("tools") or []:
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


def get_task_plan_completion_gaps(*, include_declared_outputs: bool = True) -> list[str]:
    """Return objective unmet execution-plan requirements before a final answer is accepted."""
    with _PLAN_STATE_LOCK:
        payload = _PLAN_STATE_BY_SESSION.get(get_active_session_id())
        if not isinstance(payload, dict):
            return []
        state      = dict(payload.get("state") or {})
        steps      = [dict(step) for step in payload.get("steps") or [] if isinstance(step, dict)]
        used_tools = {str(name or "").strip().lower() for name in state.get("used_tools") or []}

    gaps: list[str] = []
    for step in steps:
        step_id = str(step.get("id") or "step")
        step_tools = {str(name or "").strip().lower() for name in step.get("tools") or []}
        if step_tools and not used_tools.intersection(step_tools):
            gaps.append(f"Step '{step_id}' has not used any of its planned tools.")
        if not include_declared_outputs:
            continue
        for output in step.get("outputs") or []:
            if not isinstance(output, dict):
                continue
            output_type = str(output.get("type") or "")
            target      = str(output.get("target") or "")
            if output_type == "file":
                from utils.workspace_utils import get_user_data_dir
                root = get_user_data_dir().resolve()
                path = Path(target)
                candidate = path.resolve() if path.is_absolute() else (root / path).resolve()
                try:
                    candidate.relative_to(root)
                except ValueError:
                    gaps.append(f"Output file '{target}' is outside the permitted data directory.")
                    continue
                minimum_bytes = int(output.get("minimum_bytes") or 1)
                if not candidate.is_file() or candidate.stat().st_size < minimum_bytes:
                    gaps.append(f"Required output file '{target}' is missing or smaller than {minimum_bytes} bytes.")
            elif output_type == "scratchpad":
                value = scratchpad_load(target)
                minimum_bytes = int(output.get("minimum_bytes") or 1)
                if not isinstance(value, str) or value.startswith("Error:") or len(value.encode("utf-8")) < minimum_bytes:
                    gaps.append(f"Required scratchpad output '{target}' is missing or too small.")
            elif output_type == "dataset":
                try:
                    from datasets_pkg import dataset_inspect
                    inspected = json.loads(dataset_inspect(target))
                    minimum_items = int(output.get("minimum_items") or 0)
                    if not inspected.get("ok") or int(inspected.get("count") or 0) < minimum_items:
                        gaps.append(f"Required dataset '{target}' is missing or has fewer than {minimum_items} items.")
                except Exception:
                    gaps.append(f"Required dataset '{target}' could not be verified.")
    return list(dict.fromkeys(gaps))


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
        # A declared planning step can use any real planning capability (for example,
        # a search or a scratchpad write).  The phase-specific evidence check below
        # still requires that the plan's declared tools have actually been used.
        return True

    if phase == "act":
        act_hints = ("write", "create", "delete", "update", "append", "execute", "run", "spawn", "save", "add", "set")
        return any(any(hint in name for hint in act_hints) for name in successful_tool_names)

    if phase == "validate":
        validate_hints = ("validate", "check", "test", "verify", "status", "inspect", "read", "list", "diff")
        return any(any(hint in name for hint in validate_hints) for name in successful_tool_names)

    return True


def _phase_steps_have_evidence(payload: dict[str, Any], state: dict[str, Any], phase: str) -> bool:
    steps = [
        step for step in payload.get("steps") or []
        if isinstance(step, dict) and str(step.get("phase") or "").strip().lower() == phase
    ]
    if not steps:
        return True
    used_tools = {str(name or "").strip().lower() for name in state.get("used_tools") or []}
    return all(
        not step.get("tools")
        or bool(used_tools.intersection(str(name or "").strip().lower() for name in step.get("tools") or []))
        for step in steps
    )


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
        used_tools = list(state.get("used_tools") or [])[-63:]
        for tool_name in successful_tool_names:
            if tool_name not in used_tools:
                used_tools.append(tool_name)
        state["used_tools"] = used_tools[-64:]

        current_phase = str(state.get("phase") or payload.get("current_phase") or "inspect").strip().lower()
        if current_phase not in VALID_PHASES:
            current_phase = "inspect"
        if current_phase == "complete":
            state["phase"] = "complete"
            payload["state"] = state
            _mirror_task_plan_to_scratchpad(payload)
            return "complete"

        if not has_success:
            state["phase"] = current_phase
            payload["state"] = state
            _mirror_task_plan_to_scratchpad(payload)
            return current_phase

        if not _phase_transition_satisfied(current_phase, successful_tool_names) or not _phase_steps_have_evidence(payload, state, current_phase):
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
            _mirror_task_plan_to_scratchpad(payload)
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
        _mirror_task_plan_to_scratchpad(payload)
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
            f"Execution steps: {json.dumps(plan.steps, ensure_ascii=False, separators=(',', ':'))}",
            f"Required artifacts: {'; '.join(plan.required_artifacts) or 'none'}",
            f"Validation: {'; '.join(plan.validation_requirements) or 'none'}",
            f"Completion contract: {plan.completion_contract}",
            "Follow the current phase. Do not repeat evidence collection after it is sufficient.",
            "[/ACTIVE_TASK_PLAN]",
        ]
    )
