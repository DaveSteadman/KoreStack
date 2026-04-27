import copy
import threading
import time

from KoreAgent.scratchpad import scratch_save as scratch_auto_save
from KoreAgent.session_runtime import get_active_session_id
from KoreAgent.utils.workspace_utils import trunc


_delegate_tls: threading.local = threading.local()
MAX_DELEGATE_DEPTH: int = 2


def get_delegate_runtime_tls() -> threading.local:
    return _delegate_tls


def push_delegate_runtime(*, logger, delegate_depth: int, config) -> tuple[object, int, object]:
    previous = (
        getattr(_delegate_tls, "logger", None),
        getattr(_delegate_tls, "delegate_depth", 0),
        getattr(_delegate_tls, "config", None),
    )
    _delegate_tls.logger = logger
    _delegate_tls.delegate_depth = delegate_depth
    _delegate_tls.config = config
    return previous


def pop_delegate_runtime(previous: tuple[object, int, object]) -> None:
    _delegate_tls.logger, _delegate_tls.delegate_depth, _delegate_tls.config = previous


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
    prompt = str(prompt or "").strip()
    instructions = str(instructions or "").strip()
    if not prompt:
        return {"status": "error", "answer": "delegate() requires a non-empty prompt.", "delegate_prompt": "", "depth": 0, "max_iterations": max_iterations}

    logger = getattr(_delegate_tls, "logger", None)
    depth = int(getattr(_delegate_tls, "delegate_depth", 0))
    config = getattr(_delegate_tls, "config", None)
    if logger is None or config is None:
        return {
            "status": "error",
            "answer": "Delegate runtime context is not available. Was delegate_subrun called outside an orchestration run?",
            "delegate_prompt": prompt,
            "depth": depth,
            "max_iterations": max_iterations,
        }
    if depth >= MAX_DELEGATE_DEPTH:
        return {
            "status": "error",
            "answer": f"Maximum delegation depth ({MAX_DELEGATE_DEPTH}) reached. Cannot delegate further.",
            "delegate_prompt": prompt,
            "depth": depth,
            "max_iterations": max_iterations,
        }

    child_prompt = f"{instructions}\n\n{prompt}".strip() if instructions else prompt
    child_iterations = max(1, min(int(max_iterations), 8))
    allowlist_set = set(tools_allowlist) if tools_allowlist else None
    parent_session_id = get_active_session_id()

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

    # Check stop state before starting the child run - the parent may have been stopped
    # while this delegate was queued.  Use a lazy import to avoid a circular dependency.
    try:
        from KoreAgent.orchestration import is_stop_requested as _is_stop_requested
        if _is_stop_requested():
            return {
                "status": "error",
                "answer": "[Run stopped by /stoprun - delegate did not execute.]",
                "delegate_prompt": child_prompt,
                "depth": depth + 1,
                "max_iterations": child_iterations,
            }
    except ImportError:
        pass

    previous = push_delegate_runtime(logger=logger, delegate_depth=depth + 1, config=child_config)
    _start = time.monotonic()
    try:
        answer, _, _, run_success, _ = orchestrate_prompt_fn(
            user_prompt=child_prompt,
            config=child_config,
            logger=logger,
            conversation_history=None,
            session_context=None,
            quiet=True,
            delegate_depth=depth + 1,
            scratchpad_visible_keys=scratchpad_visible_keys,
            bound_session_id=parent_session_id,
        )
        status = "ok" if run_success else "error"
    except Exception as exc:
        answer = f"Delegate child run failed: {exc}"
        status = "error"
    finally:
        pop_delegate_runtime(previous)
    elapsed = time.monotonic() - _start
    logger.log_file_only(f"[delegate] child done: depth={depth + 1} status={status} elapsed={elapsed:.1f}s prompt={trunc(child_prompt, 80)}")

    if output_key and status == "ok":
        try:
            out_key = str(output_key).strip()
            scratch_auto_save(out_key, answer)
            # Return only the save notification - full content is in the scratchpad.
            # This keeps large delegate outputs out of the parent tool-call message thread.
            answer = f"[Result saved to scratchpad key '{out_key.lower()}'. Use scratch_load('{out_key.lower()}') or {{scratch:{out_key.lower()}}} to access it.]"
        except Exception as exc:
            logger.log_file_only(f"[delegate] Warning: could not save result to scratchpad key '{out_key}': {exc}")

    return {"status": status, "answer": answer, "delegate_prompt": child_prompt, "depth": depth + 1, "max_iterations": child_iterations}
