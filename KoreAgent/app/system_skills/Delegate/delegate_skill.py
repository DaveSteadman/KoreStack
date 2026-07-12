# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Public Delegate Gen2 skill surface.
#
# This supersedes the old inline child-subrun interface. Delegation is now queue-native and durable:
#   - delegate(...)         -> spawn one structured child task
#   - delegate_status(...)  -> inspect child task state
#   - delegate_collect(...) -> read the recorded child result
#
# The execution runtime lives in delegate_runtime.py.
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
from system_skills.Delegate.delegate_runtime import delegate_collect as _delegate_collect
from system_skills.Delegate.delegate_runtime import delegate_spawn   as _delegate_spawn
from system_skills.Delegate.delegate_runtime import delegate_status  as _delegate_status


# ====================================================================================================
# MARK: INTERFACE
# ====================================================================================================
def delegate(
    task_in: str,
    data_in: dict | None = None,
    process: dict | None = None,
    data_out: dict | None = None,
) -> dict:
    """Spawn one durable delegated child task from a function-style contract."""
    return _delegate_spawn(
        task_in  = task_in,
        data_in  = data_in,
        process  = process,
        data_out = data_out,
    )


def delegate_status(task_id: str) -> dict:
    """Return the current lifecycle state of a delegated child task."""
    return _delegate_status(task_id)


def delegate_collect(task_id: str) -> dict:
    """Return the recorded result payload of a delegated child task."""
    return _delegate_collect(task_id)
