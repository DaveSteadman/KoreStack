# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Thin skill wrapper for the Delegate orchestration primitive.
#
# Validates arguments and forwards the call to delegate_subrun() in orchestration.py.
# All child-run logic - context isolation, depth capping, iteration budget, tool filtering -
# lives in the core function, not here.
#
# Related modules:
#   - system_skills/Delegate/delegate_runner.py  -- runtime state and child-run execution
#   - orchestration.py                           -- delegate_subrun (public API wrapper)
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
from orchestration import delegate_subrun
from scratchpad import get_store as _get_scratchpad_store


# ====================================================================================================
# MARK: INTERFACE
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def delegate(
    prompt: str,
    instructions: str = "",
    max_iterations: int = 3,
    output_key: str = "",
    scratchpad_visible_keys: list[str] | None = None,
    scratchpad_prefix: str | None = None,
    tools_allowlist: list[str] | None = None,
) -> dict:
    """Spawn an isolated child orchestration context for a focused sub-task.

    Use this when the user explicitly says 'delegate', or when a sub-problem
    needs its own multi-step tool-calling loop without polluting the parent
    context. The child runs independently and returns a compact answer dict
    with keys: status, answer, delegate_prompt, depth, max_iterations.
    Do NOT use for trivial single-tool operations - call the tool directly instead.
    """
    # Expand scratchpad_prefix into matching key names from the current store.
    prefix_keys: list[str] = []
    if scratchpad_prefix:
        prefix = str(scratchpad_prefix).strip()
        if prefix:
            prefix_keys = [k for k in _get_scratchpad_store() if k.startswith(prefix)]

    # Merge explicit key list with prefix-expanded keys (deduped, order preserved).
    merged_keys: list[str] | None = None
    if scratchpad_visible_keys or prefix_keys:
        seen: set[str] = set()
        merged_keys = []
        for k in (list(scratchpad_visible_keys or []) + prefix_keys):
            if k not in seen:
                seen.add(k)
                merged_keys.append(k)

    return delegate_subrun(
        prompt                  = str(prompt or "").strip(),
        instructions            = str(instructions or "").strip(),
        max_iterations          = int(max_iterations or 3),
        output_key              = str(output_key or "").strip(),
        scratchpad_visible_keys = merged_keys,
        tools_allowlist         = list(tools_allowlist) if tools_allowlist else None,
    )
