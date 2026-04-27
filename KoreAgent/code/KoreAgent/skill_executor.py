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
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import importlib.util
import sys

import KoreAgent.mcp_client as _mcp_client
from KoreAgent.prompt_tokens import resolve_tokens
from KoreAgent.tool_result import ToolCallResult
from KoreAgent.utils.workspace_utils import get_workspace_root
from KoreAgent.utils.workspace_utils import normalize_module_path


# ====================================================================================================
# MARK: MODULE LOADER
# ====================================================================================================
# Cache of already-loaded callables: (absolute_path_str, function_name) -> callable.
# Avoids re-executing module-level code on every skill invocation within a session.
_callable_cache: dict[tuple[str, str], object] = {}


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
    if dynamic_module_name in sys.modules:
        module = sys.modules[dynamic_module_name]
    else:
        spec   = importlib.util.spec_from_file_location(dynamic_module_name, absolute_module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load module spec for: {module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[dynamic_module_name] = module
        spec.loader.exec_module(module)

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
    index: dict[str, tuple[str, str]] = {}

    for skill in skills_payload.get("skills", []):
        module = normalize_module_path(skill.get("module", ""))

        for function_sig in skill.get("functions", []):
            function_name = str(function_sig).split("(")[0].strip()
            if module and function_name:
                index[function_name] = (module, function_name)

    return index


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
) -> ToolCallResult:
    """Execute one tool call and return the output record.

    The returned dict has keys: 'function', 'module', 'arguments', 'result'.
    Raises RuntimeError when the function is not allow-listed or cannot be loaded.

    Pass a pre-built catalog_gates dict (from build_catalog_gates) to avoid rebuilding
    the index on every call when executing multiple tools in one round.
    """
    # MCP tools are dispatched to the remote server; they bypass the local allow-list.
    if _mcp_client.is_mcp_tool(tool_name):
        resolved_args = {
            k: (resolve_tokens(v) if isinstance(v, str) else v)
            for k, v in arguments.items()
            if k != "" and v is not None and v != "None" and v != "null"
        }
        result = _mcp_client.call_mcp_tool(tool_name, resolved_args)
        return ToolCallResult(
            tool      = tool_name,
            function  = tool_name,
            module    = "mcp",
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
        raise RuntimeError(f"Tool '{tool_name}' not found in skills catalog")
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
