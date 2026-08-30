# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Executes individual Python skill calls requested by the LLM tool-calling pipeline.
#
# Loads skill modules dynamically at runtime using importlib, but only after verifying each call
# against an allow-list derived from the skills_summary catalog. This two-step guard - allow-list
# check then dynamic import - prevents arbitrary code execution if a malformed or adversarial tool
# call is received from the LLM.
#
# Also resolves {{token}} placeholders in string arguments before each function call.
#
# Related modules:
#   - orchestration.py           -- calls execute_tool_call inside the tool-calling loop
#   - skills_catalog_builder.py  -- produces the skills_summary that drives the allow-list
# MARK: FUNCTIONS
# Function inventory:
# - _load_callable_from_module_path: Implements the  load callable from module path operation for this module.
# - build_catalog_gates: Builds catalog gates for this module.
# - _build_unknown_tool_error: Implements the  build unknown tool error operation for this module.
# - is_skill_error: Checks whether skill error is true.
# - execute_tool_call: Implements the execute tool call operation for this module.
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import importlib.util
import sys

from prompt_tokens import resolve_tokens
from skill_manager import skill_manager
from tool_result import ToolCallResult
from utils.workspace_utils import get_workspace_root
from utils.workspace_utils import normalize_module_path


# ====================================================================================================
# MARK: MODULE LOADER
# ====================================================================================================
# Cache of already-loaded callables: (absolute_path_str, function_name) -> callable.
# Avoids re-executing module-level code on every skill invocation within a session.
_callable_cache: dict[tuple[str, str], object] = {}
_catalog_gates_cache: dict[int, dict[str, tuple[str, str]]] = {}


# ----------------------------------------------------------------------------------------------------
def _load_callable_from_module_path(module_path: str, function_name: str):
    workspace_root        = get_workspace_root()

    candidate_module_path = str(module_path).strip()
    if not candidate_module_path.endswith(".py"):
        candidate_module_path = f"{candidate_module_path}.py"

    absolute_module_path  = (workspace_root / candidate_module_path).resolve()

    if not absolute_module_path.exists():
        raise RuntimeError(f"Module path does not exist: {module_path}")

    mtime_ns    = absolute_module_path.stat().st_mtime_ns
    cache_key   = (str(absolute_module_path), function_name, mtime_ns)

    # Evict stale cache entries and the registered sys.modules entry when mtime changes.
    dynamic_module_name = f"skill_module_{absolute_module_path.stem}_{abs(hash(str(absolute_module_path)))}"
    stale_keys = [k for k in _callable_cache if k[0] == str(absolute_module_path) and k[2] != mtime_ns]
    if stale_keys:
        for sk in stale_keys:
            _callable_cache.pop(sk, None)
        sys.modules.pop(dynamic_module_name, None)

    if cache_key in _callable_cache:
        return _callable_cache[cache_key]

    # Generate a stable canonical module name so that if any other importer has already
    # loaded this file, both references share the same module object and module-level state.
    # Re-use an already-registered module rather than exec_module-ing a second copy.
    module = sys.modules.get(dynamic_module_name)
    # A failed earlier import can leave a partially initialised module in
    # sys.modules. Never reuse it: retrying against that object turns the
    # original import error into a misleading "function not found" error.
    if module is not None and not hasattr(module, function_name):
        sys.modules.pop(dynamic_module_name, None)
        module = None

    if module is None:
        spec   = importlib.util.spec_from_file_location(dynamic_module_name, absolute_module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load module spec for: {module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[dynamic_module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            # importlib does not remove a module registered before exec_module
            # raises. Clean it up so a later tool call can load a repaired file.
            if sys.modules.get(dynamic_module_name) is module:
                sys.modules.pop(dynamic_module_name, None)
            raise

    if not hasattr(module, function_name):
        raise RuntimeError(f"Function '{function_name}' not found in module '{module_path}'")

    fn = getattr(module, function_name)
    _callable_cache[cache_key] = fn
    return fn


# ----------------------------------------------------------------------------------------------------
def build_catalog_gates(skills_payload: dict) -> dict[str, tuple[str, str]]:
    """Build the tool-name dispatch index in a single pass over the catalog.

    Returns a dict mapping tool_name -> (module_path, function_name).
    This index lookup is the security gate: unknown names are rejected before any import.
    Callers that invoke execute_tool_call multiple times for the same payload (e.g. the
    orchestration loop) should call this once and pass the result via the catalog_gates
    parameter to avoid rebuilding the index on every tool invocation.
    """
    cache_key = id(skills_payload)
    cached = _catalog_gates_cache.get(cache_key)
    if cached is not None:
        return cached

    index: dict[str, tuple[str, str]] = {}

    for skill in skills_payload.get("skills", []):
        module = normalize_module_path(skill.get("module", ""))

        for function_sig in skill.get("functions", []):
            function_name = str(function_sig).split("(")[0].strip()
            if module and function_name:
                index[function_name] = (module, function_name)

    _catalog_gates_cache.clear()
    _catalog_gates_cache[cache_key] = index
    return index


# ----------------------------------------------------------------------------------------------------
def _build_unknown_tool_error(
    requested_tool_name: str,
    skills_payload: dict,
    active_tool_names: set[str] | None = None,
) -> str:
    requested = str(requested_tool_name or "").strip()
    base_msg = f"Tool '{requested}' not found in skills catalog"
    if not requested:
        return base_msg
    return (
        f"{base_msg}. Inspect `tools_catalog_list()` or `tools_keywords_list()` "
        "and activate an exact tool name."
    )


def _build_inactive_tool_error(requested_tool_name: str) -> str:
    """Return the recovery instruction for a catalogued but inactive tool."""
    requested = str(requested_tool_name or "").strip()
    return (
        f"Tool '{requested}' is not active for this conversation. "
        "Use `tools_catalog_list()` or `tools_keywords_list()`, then activate it "
        "with `tools_active_add`."
    )


def _is_local_system_tool(skills_payload: dict, tool_name: str) -> bool:
    """Return whether a catalogued local function belongs to a permanent system skill."""
    requested = str(tool_name or "").strip()
    return any(
        skill.get("is_system_skill") is True
        and requested in {
            str(signature).split("(", 1)[0].strip()
            for signature in skill.get("functions", [])
        }
        for skill in skills_payload.get("skills", [])
        if isinstance(skill, dict)
    )


# ====================================================================================================
# MARK: ERROR DETECTION
# ====================================================================================================
# String prefixes that skill functions use to signal a failure.  Any result whose stripped text
# starts with one of these is flagged as is_error=True in the execute_tool_call return dict so
# the orchestration layer can prepend [SKILL_ERROR] before feeding the result back to the model.
_SKILL_ERROR_PREFIXES: tuple[str, ...] = (
    "Error:",
    "File not found:",
    "Could not extract",
    "No file path found",
    "Unable to parse",
)


# ----------------------------------------------------------------------------------------------------
def is_skill_error(result: object) -> bool:
    """Return True when result is a plain-string skill error message."""
    if not isinstance(result, str):
        return False
    return result.strip().startswith(_SKILL_ERROR_PREFIXES)


# ====================================================================================================
# MARK: EXECUTION
# ====================================================================================================

def execute_tool_call(
    tool_name: str,
    arguments: dict,
    skills_payload: dict,
    user_prompt: str = "",
    catalog_gates: dict[str, tuple[str, str]] | None = None,
    active_tool_names: set[str] | None = None,
) -> ToolCallResult:
    """Execute one tool call and return the output record.

    The returned dict has keys: 'function', 'module', 'arguments', 'result'.
    Raises RuntimeError when the function is not allow-listed or cannot be loaded.

    Pass a pre-built catalog_gates dict (from build_catalog_gates) to avoid rebuilding
    the index on every call when executing multiple tools in one round.
    """
    requested_tool_name = str(tool_name or "").strip()
    tool_name = requested_tool_name

    # The schema supplied to the model is deliberately conversation-specific.
    # Enforce that same boundary at dispatch time so a hallucinated (or stale)
    # registered tool name cannot bypass the enabled-tool list.
    if (
        active_tool_names is not None
        and tool_name not in active_tool_names
        and not _is_local_system_tool(skills_payload, tool_name)
    ):
        raise RuntimeError(_build_inactive_tool_error(tool_name))

    registered = skill_manager.get_skill(tool_name)
    if registered is not None:
        resolved_args = {
            k: (resolve_tokens(v) if isinstance(v, str) else v)
            for k, v in arguments.items()
            if k != "" and v is not None and v != "None" and v != "null"
        }
        result = skill_manager.invoke(tool_name, resolved_args)
        return ToolCallResult(
            tool      = tool_name,
            function  = tool_name,
            module    = f"service:{registered['service']}",
            arguments = resolved_args,
            result    = result,
            status    = "error" if is_skill_error(result) else "ok",
            error     = str(result) if is_skill_error(result) else "",
        )

    # Use pre-built index when provided; otherwise build it from the payload.
    tool_index = catalog_gates if catalog_gates is not None else build_catalog_gates(skills_payload)

    # Resolve the tool name to its (module, function); fails fast for any unrecognised tool.
    resolved = tool_index.get(tool_name)
    if resolved is None:
        raise RuntimeError(_build_unknown_tool_error(requested_tool_name or tool_name, skills_payload, active_tool_names))
    module_path, function_name = resolved

    # Fill {{today}}, {{yesterday}} etc. in any string argument before passing to the function.
    # Strip blank keys that some models emit for no-argument functions (e.g. {'': ''}).
    resolved_args = {
        k: (resolve_tokens(v) if isinstance(v, str) else v)
        for k, v in arguments.items()
        if k != ""
    }

    # Load (with caching) and invoke the skill function. The index lookup above is the security gate.
    fn     = _load_callable_from_module_path(module_path, function_name)
    result = fn(**resolved_args)

    return ToolCallResult(
        tool=tool_name,
        function=function_name,
        module=module_path,
        arguments=resolved_args,
        result=result,
        status="error" if is_skill_error(result) else "ok",
        error=str(result) if is_skill_error(result) else "",
    )
