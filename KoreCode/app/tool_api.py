from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Tool api helpers for KoreCode/app.
# Provides the focused helpers and module-level behaviour grouped into this file.
# ====================================================================================================

from dataclasses import dataclass
from typing import Any


MAX_AGENT_TOOL_REQUESTS = 8


@dataclass(frozen=True)
class ToolDefinition:
    name:         str
    category:     str
    description:  str
    args:         dict[str, str]
    mutates_code: bool = False
    risk:          str = "read_only"


TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    ToolDefinition(
        name        = "read_file",
        category    = "read",
        description = "Read a workspace text file and return content plus hash metadata.",
        args        = {"path": "workspace-relative path"},
    ),
    ToolDefinition(
        name        = "read_context",
        category    = "read",
        description = "Read contextual file excerpts, symbol info, and optional workspace context.",
        args        = {
            "path":              "workspace-relative path",
            "start_line":        "optional positive integer",
            "end_line":          "optional positive integer",
            "include_workspace": "optional boolean",
        },
    ),
    ToolDefinition(
        name        = "list_tree",
        category    = "read",
        description = "List directories and text files under a workspace folder.",
        args        = {"path": "optional workspace-relative directory path, default root"},
    ),
    ToolDefinition(
        name        = "search_in_file",
        category    = "read",
        description = "Search plain text within one workspace file.",
        args        = {
            "path":        "workspace-relative path",
            "query":       "plain text to match",
            "max_results": "optional positive integer",
        },
    ),
    ToolDefinition(
        name        = "get_python_function",
        category    = "read",
        description = "Return one Python function or method source block plus content hash.",
        args        = {
            "path":   "workspace-relative Python file path",
            "symbol": "function name or ClassName.method_name",
        },
    ),
    ToolDefinition(
        name        = "check_python",
        category    = "execution",
        description = "Compile one workspace Python file and return syntax diagnostics without running it.",
        args        = {"path": "workspace-relative Python file path"},
        risk        = "process_execution",
    ),
    ToolDefinition(
        name        = "run_python",
        category    = "execution",
        description = "Run one workspace Python script with no command-line arguments and capture stdout/stderr.",
        args        = {
            "path":            "workspace-relative Python file path",
            "timeout_seconds": "optional integer from 1 to 30, default 15",
        },
        risk        = "process_execution",
    ),
    ToolDefinition(
        name         = "replace_python_function",
        category     = "write",
        description  = "Replace one full Python function or method in a file.",
        args         = {
            "path":          "workspace-relative Python file path",
            "symbol":        "function name or ClassName.method_name",
            "expected_hash": "file content hash returned by get_python_function",
            "replacement":   "full replacement source for that function or method",
        },
        mutates_code = True,
        risk         = "staged_write",
    ),
    ToolDefinition(
        name         = "insert_python_function",
        category     = "write",
        description  = "Insert one new Python function or class method into a file.",
        args         = {
            "path":          "workspace-relative Python file path",
            "expected_hash": "file content hash returned by get_python_function or read_file",
            "source":        "full source for the new function or method",
            "after_symbol":  "optional anchor function or method name",
            "into_class":    "optional class name for inserting a new method",
        },
        mutates_code = True,
        risk         = "staged_write",
    ),
)


def tool_guide_payload(allowed_tools: tuple[str, ...] | list[str] | None = None) -> dict[str, dict[str, Any]]:
    allowed = set(allowed_tools) if allowed_tools is not None else None
    return {
        tool.name: {
            "category":     tool.category,
            "description":  tool.description,
            "args":         dict(tool.args),
            "mutates_code": bool(tool.mutates_code),
            "risk":         tool.risk,
        }
        for tool in TOOL_DEFINITIONS
        if allowed is None or tool.name in allowed
    }


def execute_tool_requests(
    *,
    tool_requests: list[dict[str, Any]],
    active_path: str | None,
    workspace_context_enabled: bool,
    read_file_fn,
    read_context_fn,
    list_tree_fn,
    get_python_function_fn,
    run_python_fn,
    replace_python_function_proposal_fn,
    insert_python_function_proposal_fn,
    allowed_tools: tuple[str, ...] | list[str] | None = None,
) -> list[dict[str, Any]]:
    requests = list(tool_requests or [])[:MAX_AGENT_TOOL_REQUESTS]
    allowed  = set(allowed_tools) if allowed_tools is not None else None
    out: list[dict[str, Any]] = []

    def _effective_path(args: dict[str, Any]) -> str:
        return str(args.get("path") or active_path or "").strip()

    def _active_python_path() -> str:
        path = str(active_path or "").strip().replace("\\", "/")
        if not path or path == "." or not path.lower().endswith((".py", ".pyi")):
            raise ValueError("run_python requires an active Python file")
        return path

    def _search_in_file(args: dict[str, Any]) -> dict[str, Any]:
        path        = _effective_path(args)
        query       = str(args.get("query") or "").strip()
        max_results = max(1, min(50, int(args.get("max_results") or 10)))
        if not path:
            raise ValueError("search_in_file requires path")
        if not query:
            raise ValueError("search_in_file requires query")

        payload  = read_file_fn(path)
        lines    = str(payload.get("content") or "").split("\n")
        needle   = query.lower()
        matches  = []
        for idx, line in enumerate(lines, start=1):
            if needle in line.lower():
                matches.append({"line": idx, "preview": line[:220]})
                if len(matches) >= max_results:
                    break
        return {"path": path, "query": query, "matches": matches}

    for index, request in enumerate(requests):
        tool = str((request or {}).get("tool") or "").strip()
        args = request.get("args") if isinstance(request, dict) and isinstance(request.get("args"), dict) else {}
        try:
            if allowed is not None and tool not in allowed:
                raise ValueError(f"Tool is not active for this task: {tool}")
            if tool == "read_file":
                path   = _effective_path(args)
                if not path:
                    raise ValueError("read_file requires path")
                result = read_file_fn(path)
            elif tool == "read_context":
                path = _effective_path(args)
                if not path:
                    raise ValueError("read_context requires path")
                start_line = args.get("start_line")
                end_line   = args.get("end_line")
                result = read_context_fn(
                    path,
                    int(start_line) if isinstance(start_line, int) or str(start_line).isdigit() else None,
                    int(end_line) if isinstance(end_line, int) or str(end_line).isdigit() else None,
                    bool(args.get("include_workspace")) if "include_workspace" in args else bool(workspace_context_enabled),
                )
            elif tool == "list_tree":
                result = list_tree_fn(str(args.get("path") or "").strip())
            elif tool == "search_in_file":
                result = _search_in_file(args)
            elif tool == "get_python_function":
                path   = _effective_path(args)
                symbol = str(args.get("symbol") or "").strip()
                if not path:
                    raise ValueError("get_python_function requires path")
                if not symbol:
                    raise ValueError("get_python_function requires symbol")
                result = get_python_function_fn(path, symbol)
            elif tool == "check_python":
                path = _effective_path(args)
                if not path:
                    raise ValueError("check_python requires path")
                result = run_python_fn(path, "check", None)
            elif tool == "run_python":
                path = _effective_path(args)
                timeout_seconds = max(1, min(30, int(args.get("timeout_seconds") or 15)))
                if not path:
                    raise ValueError("run_python requires path")
                if path.replace("\\", "/") != _active_python_path():
                    raise ValueError("run_python is limited to the active Python file")
                result = run_python_fn(path, "run", timeout_seconds)
            elif tool == "replace_python_function":
                path          = _effective_path(args)
                symbol        = str(args.get("symbol") or args.get("function_name") or "").strip()
                replacement   = str(args.get("replacement") or "")
                expected_hash = str(args.get("expected_hash") or args.get("content_hash") or "").strip()
                if not path:
                    raise ValueError("replace_python_function requires path")
                if not symbol:
                    raise ValueError("replace_python_function requires symbol")
                if not expected_hash:
                    raise ValueError("replace_python_function requires expected_hash")
                if not replacement.strip():
                    raise ValueError("replace_python_function requires replacement")
                result = replace_python_function_proposal_fn(path, symbol, replacement, expected_hash)
            elif tool == "insert_python_function":
                path          = _effective_path(args)
                source        = str(args.get("source") or "")
                expected_hash = str(args.get("expected_hash") or "").strip()
                after_symbol  = str(args.get("after_symbol") or "").strip() or None
                into_class    = str(args.get("into_class") or "").strip() or None
                if not path:
                    raise ValueError("insert_python_function requires path")
                if not expected_hash:
                    raise ValueError("insert_python_function requires expected_hash")
                if not source.strip():
                    raise ValueError("insert_python_function requires source")
                result = insert_python_function_proposal_fn(path, source, expected_hash, after_symbol, into_class)
            else:
                raise ValueError(f"Unknown tool: {tool}")

            out.append(
                {
                    "request_index": index,
                    "tool":          tool,
                    "ok":            True,
                    "result":        result,
                }
            )
        except Exception as exc:
            out.append(
                {
                    "request_index": index,
                    "tool":          tool,
                    "ok":            False,
                    "error":         str(exc),
                }
            )
    return out
