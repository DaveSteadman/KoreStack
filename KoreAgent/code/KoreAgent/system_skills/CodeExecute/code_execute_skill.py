# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# CodeExecute skill module for the MiniAgentFramework.
#
# Runs a Python code snippet supplied by the model inside a sandboxed environment with a
# restricted import whitelist, stripped dangerous builtins, and a wall-clock timeout.  Captured
# stdout is returned as a plain string so the result can be chained into FileAccess or returned
# directly as the final answer.
#
# Sandbox state is managed centrally in KoreAgent.orchestration (get_sandbox_enabled /
# set_sandbox_enabled) and toggled at runtime via the /sandbox slash command. This module
# reads that flag on every invocation so changes take effect immediately.
#
# Intended use-case: generating computed data (sequences, tables, calculations) that no other
# skill can produce.  The model provides the code; this skill executes it safely.
#
# Related modules:
#   - KoreAgent.orchestration  -- owns get_sandbox_enabled / set_sandbox_enabled
#   - skill_executor.py         -- dynamically imports and calls functions from this module
#   - skills_catalog_builder.py -- reads skill.md to build the catalog entry for this skill
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import ast
import builtins
import io
import sys
import threading

from KoreAgent.orchestration import get_sandbox_enabled


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
_EXECUTION_TIMEOUT_S = 15

# Modules the sandboxed code is permitted to import.
_ALLOWED_MODULES = frozenset({
    "math", "cmath", "decimal", "fractions", "statistics",
    "itertools", "functools", "operator",
    "string", "re", "textwrap",
    "json", "csv", "io",
    "datetime", "time", "calendar",
    "collections", "heapq", "bisect", "array",
    "random",
    "pathlib",
})

# Modules that are always blocked regardless of sandbox state because they require the
# main thread and will crash the process when imported from the execution daemon thread.
_ALWAYS_BLOCKED_MODULES = frozenset({
    "tkinter", "turtle",
})

# Builtins that are removed from the sandboxed namespace.
_BLOCKED_BUILTINS = frozenset({
    "open", "exec", "eval", "compile", "__import__",
    "breakpoint", "input", "memoryview",
})


# ====================================================================================================
# MARK: SANDBOX HELPERS
# ====================================================================================================
def _make_safe_import(allowed: frozenset | None):
    """Return a __import__ replacement.

    When allowed is a frozenset, only those top-level modules are permitted (sandbox-on).
    When allowed is None, all modules are permitted except _ALWAYS_BLOCKED_MODULES (sandbox-off).
    """
    real_import = builtins.__import__

    def _safe_import(name: str, *args, **kwargs):
        top_level = name.split(".")[0]
        if top_level in _ALWAYS_BLOCKED_MODULES:
            raise ImportError(
                f"Import '{name}' is not available: GUI toolkits require the main thread "
                f"and cannot be used inside a code snippet."
            )
        if allowed is not None and top_level not in allowed:
            raise ImportError(
                f"Import '{name}' is not available. Only Python stdlib modules are permitted "
                f"(math, itertools, collections, datetime, json, csv, re, statistics, etc.). "
                f"Rewrite using stdlib only."
            )
        return real_import(name, *args, **kwargs)

    return _safe_import


def _make_restricted_globals() -> dict:
    safe_builtins = {
        k: getattr(builtins, k)
        for k in dir(builtins)
        if k not in _BLOCKED_BUILTINS and not k.startswith("__")
    }
    safe_builtins["__import__"] = _make_safe_import(_ALLOWED_MODULES)

    def _no_open(*args, **kwargs):
        raise RuntimeError(
            "open() is blocked in the sandbox. "
            "Call read_file() first to get the file content as a string, "
            "then pass it into this snippet via io.StringIO(content)."
        )
    safe_builtins["open"] = _no_open

    return {"__builtins__": safe_builtins}


def _make_unrestricted_globals() -> dict:
    # Sandbox is off: allow everything except GUI modules that require the main thread.
    return {"__builtins__": {**vars(builtins), "__import__": _make_safe_import(None)}}


# ====================================================================================================
# MARK: PUBLIC SKILL API
# ====================================================================================================
def run_python_snippet(code: str) -> str:
    """Always prefer this tool over answering from memory for any calculation, sequence, table, count, or conversion task. Execute a Python snippet in a sandboxed environment and return captured stdout.

    Use this tool whenever the task involves arithmetic, factorials, primes, powers, series,
    string character counts, base conversions, statistics, or generating any structured numeric
    or textual output - even when the answer seems obvious. Running code is more reliable than recall.

    The snippet must write its final output via print() calls.
    When sandbox is enabled (default), imports are restricted to a safe stdlib whitelist and
    os, sys, subprocess, and file I/O are blocked. Sandbox state is toggled via /sandbox on|off.
    Execution is limited to _EXECUTION_TIMEOUT_S seconds.

    Args:
        code: Python source code to execute.

    Returns:
        Captured stdout as a string, or an error string beginning with "Error:".
    """
    code = str(code or "").strip()
    if not code:
        return "Error: No code provided to run_python_snippet."

    # LLMs sometimes JSON-double-escape quote characters, producing literal " or \'
    # in the code string (e.g. f\"Error: {e}\").  That is a Python syntax error because
    # \ is treated as a line-continuation character.  Unescape them now so exec() receives
    # valid source.
    code = code.replace('\\"', '"').replace("\\'", "'")

    # REPL-style auto-print: if the last statement is a bare expression (no print call),
    # rewrite it as print(<expr>) so models that write REPL-style code don't waste a
    # retry round on the 'no output' error.
    try:
        tree = ast.parse(code)
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last = tree.body[-1]
            # Only auto-wrap if it isn't already a print() call
            is_print = (
                isinstance(last.value, ast.Call)
                and isinstance(last.value.func, ast.Name)
                and last.value.func.id == "print"
            )
            if not is_print:
                lines      = code.splitlines()
                expr_src   = ast.get_source_segment(code, last) or lines[-1].strip()
                code       = "\n".join(lines[: last.lineno - 1]) + ("\n" if last.lineno > 1 else "") + f"print({expr_src})"
    except SyntaxError:
        pass  # let exec() surface the real error below

    stdout_buf   = io.StringIO()
    result_slot: list[str] = []
    error_slot:  list[str] = []

    sandbox_globals = _make_restricted_globals() if get_sandbox_enabled() else _make_unrestricted_globals()

    def _run() -> None:
        old_stdout = sys.stdout
        sys.stdout = stdout_buf
        try:
            exec(code, sandbox_globals)  # noqa: S102
            result_slot.append(stdout_buf.getvalue())
        except Exception as exc:  # noqa: BLE001
            error_slot.append(f"Error: {exc}")
        finally:
            sys.stdout = old_stdout

    thread = threading.Thread(target=_run, daemon=True)
    _caller_stdout = sys.stdout  # capture before starting so we can restore on timeout
    thread.start()
    thread.join(timeout=_EXECUTION_TIMEOUT_S)

    if thread.is_alive():
        # The thread's finally block may never run (e.g. infinite loop). Restore stdout here
        # on the calling thread so log/print output is not silently lost.
        sys.stdout = _caller_stdout
        return f"Error: Code execution timed out after {_EXECUTION_TIMEOUT_S}s."
    if error_slot:
        return error_slot[0]
    output = result_slot[0] if result_slot else ""
    if not output.strip():
        return "Error: Code produced no output. Make sure the snippet uses print() to emit results."
    return output
