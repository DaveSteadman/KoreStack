# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Delegate sub-run execution engine with depth limiting.
#
# Provides run_delegate_subrun() which spawns a nested orchestrator call on behalf of the
# Delegate skill.  A thread-local stack prevents infinite delegation (MAX_DELEGATE_DEPTH=2).
# The delegate uses its own session namespace so its scratchpad and context are isolated
# from the parent run.
#
# Public API:
#   push_delegate_runtime()     -- pushes a new depth entry onto the TLS stack
#   pop_delegate_runtime()      -- pops after the sub-run completes
#   run_delegate_subrun(prompt, config, ...)  -- orchestrates the sub-run and returns a result
#
# Related modules:
#   - system_skills/Delegate/skill.py  -- the Delegate skill that calls run_delegate_subrun
#   - orchestration.py                 -- orchestrate_prompt called by the sub-run
#   - session_runtime.py               -- provides bind_session for session isolation
# ====================================================================================================
import copy
import re
import threading
import time

from datasets import get_prompt_dataset_manifests
from scratchpad import get_store as get_scratchpad_store
from scratchpad import scratchpad_save as scratchpad_auto_save
from session_runtime import get_active_session_id
from utils.workspace_utils import trunc


_delegate_tls: threading.local = threading.local()
MAX_DELEGATE_DEPTH: int = 2

_delegate_branch_lock: threading.Lock = threading.Lock()
_delegate_branch_seq: int = 0

# Child answers longer than this are auto-saved to a scratchpad key to keep the parent thread compact.
_DELEGATE_AUTO_SAVE_THRESHOLD: int = 512
_DATASET_DELEGATE_TOOLS: tuple[str, ...] = ("dataset_get", "dataset_inspect")


def _next_delegate_branch_id() -> str:
    global _delegate_branch_seq
    with _delegate_branch_lock:
        _delegate_branch_seq += 1
        return f"branch-{_delegate_branch_seq:04d}"


def _emit_delegate_event(logger, event: str, branch_id: str, **fields) -> None:
    parts = [f"event={event}", f"branch_id={branch_id}"]
    for key, value in fields.items():
        parts.append(f"{key}={value}")
    logger.log_file_only("[delegate:event] " + " ".join(parts))


def _detect_referenced_datasets(child_prompt: str) -> list[str]:
    prompt_lower = str(child_prompt or "").lower()
    if not prompt_lower:
        return []

    referenced: list[str] = []
    for dataset in get_prompt_dataset_manifests():
        name = str(dataset.get("name") or "").strip().lower()
        if not name:
            continue
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])"
        if re.search(pattern, prompt_lower) and name not in referenced:
            referenced.append(name)
    return referenced


def _matching_dataset_scratchpad_keys(dataset_names: list[str]) -> list[str]:
    if not dataset_names:
        return []

    store = get_scratchpad_store()
    matched: list[str] = []
    for dataset_name in dataset_names:
        prefix = f"_dataset_get_{dataset_name}_"
        for key in store.keys():
            if key.startswith(prefix) and key not in matched:
                matched.append(key)
    return matched


def get_delegate_runtime_tls() -> threading.local:
    return _delegate_tls


def push_delegate_runtime(*, logger, delegate_depth: int, config, conversation_entry=None) -> tuple[object, int, object, object]:
    previous = (
        getattr(_delegate_tls, "logger", None),
        getattr(_delegate_tls, "delegate_depth", 0),
        getattr(_delegate_tls, "config", None),
        getattr(_delegate_tls, "conversation_entry", None),
    )
    _delegate_tls.logger = logger
    _delegate_tls.delegate_depth = delegate_depth
    _delegate_tls.config = config
    _delegate_tls.conversation_entry = conversation_entry
    return previous


def pop_delegate_runtime(previous: tuple[object, int, object, object]) -> None:
    _delegate_tls.logger, _delegate_tls.delegate_depth, _delegate_tls.config, _delegate_tls.conversation_entry = previous


def run_delegate_subrun(
    *,
    prompt: str,
    instructions: str = "",
    max_iterations: int = 3,
    allow_recursive_delegate: bool = False,
    output_key: str = "",
    scratchpad_visible_keys: list[str] | None = None,
    tools_allowlist: list[str] | None = None,
    orchestrate_prompt_fn,
    config_cls,
) -> dict:
    branch_id = _next_delegate_branch_id()
    prompt = str(prompt or "").strip()
    instructions = str(instructions or "").strip()
    if not prompt:
        return {
            "status": "error",
            "answer": "delegate() requires a non-empty prompt.",
            "delegate_prompt": "",
            "depth": 0,
            "max_iterations": max_iterations,
            "branch_id": branch_id,
            "events": ["invalid_prompt"],
        }

    logger = getattr(_delegate_tls, "logger", None)
    depth = int(getattr(_delegate_tls, "delegate_depth", 0))
    config = getattr(_delegate_tls, "config", None)
    conversation_entry = getattr(_delegate_tls, "conversation_entry", None)
    if logger is None or config is None:
        return {
            "status": "error",
            "answer": "Delegate runtime context is not available. Was delegate_subrun called outside an orchestration run?",
            "delegate_prompt": prompt,
            "depth": depth,
            "max_iterations": max_iterations,
            "branch_id": branch_id,
            "events": ["runtime_unavailable"],
        }
    if depth >= MAX_DELEGATE_DEPTH:
        return {
            "status": "error",
            "answer": f"Maximum delegation depth ({MAX_DELEGATE_DEPTH}) reached. Cannot delegate further.",
            "delegate_prompt": prompt,
            "depth": depth,
            "max_iterations": max_iterations,
            "branch_id": branch_id,
            "events": ["depth_rejected"],
        }

    child_prompt = f"{instructions}\n\n{prompt}".strip() if instructions else prompt
    child_iterations = max(1, min(int(max_iterations), 8))
    allowlist_set = set(tools_allowlist) if tools_allowlist else None
    parent_session_id = get_active_session_id()
    referenced_datasets = _detect_referenced_datasets(child_prompt)

    if allowlist_set is not None and referenced_datasets:
        allowlist_set.update(_DATASET_DELEGATE_TOOLS)

    def _skill_in_allowlist(skill: dict) -> bool:
        if allowlist_set is None:
            return True
        for fn_sig in skill.get("functions", []):
            fn_name = fn_sig.split("(")[0].strip()
            if fn_name in allowlist_set:
                return True
        return False

    child_payload = copy.deepcopy(config.skills_payload)
    child_payload["skills"] = [
        skill
        for skill in child_payload.get("skills", [])
        if (allow_recursive_delegate or "Delegate" not in skill.get("skill_name", "")) and _skill_in_allowlist(skill)
    ]

    child_config = config_cls(
        resolved_model=config.resolved_model,
        num_ctx=config.num_ctx,
        max_iterations=child_iterations,
        skills_payload=child_payload,
        skills_catalog_path=None,
        catalog_mtime=0.0,
    )

    logger.log_file_only(f"[delegate] spawning child run: depth={depth + 1} max_iter={child_iterations} prompt={trunc(child_prompt, 80)}")
    _emit_delegate_event(
        logger,
        "task_spawned",
        branch_id,
        depth=depth + 1,
        max_iter=child_iterations,
        prompt=trunc(child_prompt, 80),
    )

    # Check stop state before starting the child run - the parent may have been stopped
    # while this delegate was queued.
    try:
        from orchestration import is_stop_requested as _is_stop_requested
        if _is_stop_requested():
            _emit_delegate_event(logger, "task_aborted", branch_id, reason="stop_requested")
            return {
                "status": "error",
                "answer": "[Run stopped by /stoprun - delegate did not execute.]",
                "delegate_prompt": child_prompt,
                "depth": depth + 1,
                "max_iterations": child_iterations,
                "elapsed_s": 0.0,
                "branch_id": branch_id,
                "events": ["task_spawned", "task_aborted"],
            }
    except ImportError:
        pass

    # Default: child sees no parent scratchpad keys (prevents _tc_* noise leakage).
    # Caller must explicitly list the keys the child is allowed to see. When the child prompt
    # explicitly references a known dataset, also expose matching dataset_get scratchpad keys so a
    # recent parent fetch can be reused without reloading from a truncated preview.
    child_visible_keys = list(scratchpad_visible_keys) if scratchpad_visible_keys is not None else []
    if referenced_datasets:
        for key in _matching_dataset_scratchpad_keys(referenced_datasets):
            if key not in child_visible_keys:
                child_visible_keys.append(key)

    previous = push_delegate_runtime(logger=logger, delegate_depth=depth + 1, config=child_config)
    _start = time.monotonic()
    _emit_delegate_event(logger, "task_started", branch_id, depth=depth + 1)
    events = ["task_spawned", "task_started"]
    try:
        answer, _, _, run_success, _ = orchestrate_prompt_fn(
            user_prompt=child_prompt,
            config=child_config,
            logger=logger,
            conversation_history=None,
            session_context=None,
            quiet=True,
            delegate_depth=depth + 1,
            conversation_entry=conversation_entry,
            scratchpad_visible_keys=child_visible_keys,
            bound_session_id=parent_session_id,
        )
        status = "ok" if run_success else "error"
        events.append("task_completed" if run_success else "task_failed")
    except Exception as exc:
        answer = f"Delegate child run failed: {exc}"
        status = "error"
        events.append("task_failed")
    finally:
        pop_delegate_runtime(previous)
    elapsed = time.monotonic() - _start
    logger.log_file_only(f"[delegate] child done: depth={depth + 1} status={status} elapsed={elapsed:.1f}s prompt={trunc(child_prompt, 80)}")
    _emit_delegate_event(logger, "task_finished", branch_id, depth=depth + 1, status=status, elapsed=f"{elapsed:.2f}s")
    events.append("task_finished")

    if output_key and status == "ok":
        try:
            out_key = str(output_key).strip()
            scratchpad_auto_save(out_key, answer)
            # Return only the save notification - full content is in the scratchpad.
            # This keeps large delegate outputs out of the parent tool-call message thread.
            answer = f"[Result saved to scratchpad key '{out_key.lower()}'. Use scratchpad_load('{out_key.lower()}') or {{scratchpad:{out_key.lower()}}} to access it.]"
        except Exception as exc:
            logger.log_file_only(f"[delegate] Warning: could not save result to scratchpad key '{out_key}': {exc}")
    elif not output_key and status == "ok" and isinstance(answer, str) and len(answer) >= _DELEGATE_AUTO_SAVE_THRESHOLD:
        # No explicit output_key but answer is large - auto-save to keep parent thread compact.
        auto_key = f"_tc_delegate_d{depth + 1}"
        try:
            scratchpad_auto_save(auto_key, answer)
            answer = f"[Answer auto-saved to scratchpad key '{auto_key}' ({len(answer):,} chars). Use scratchpad_load('{auto_key}') or scratchpad_query('{auto_key}', ...) to access it.]"
        except Exception as exc:
            logger.log_file_only(f"[delegate] Warning: could not auto-save large result: {exc}")

    return {
        "status": status,
        "answer": answer,
        "delegate_prompt": child_prompt,
        "depth": depth + 1,
        "max_iterations": child_iterations,
        "elapsed_s": round(elapsed, 2),
        "branch_id": branch_id,
        "events": events,
    }
