# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# ToolCallResult dataclass: the value returned by skill_executor.execute_tool_call().
#
# Bundles tool name, function, module path, arguments, result, status, and error message
# into one object with dict-compatible .get() / __getitem__ access so callers can treat
# results as either an object or a dict.  .display_name() produces a readable log label.
#
# Related modules:
#   - skill_executor.py  -- constructs ToolCallResult on every skill invocation
#   - tool_loop.py       -- consumes ToolCallResult to build LLM tool-result messages
# ====================================================================================================
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ToolCallResult:
    tool: str
    function: str
    module: str
    arguments: dict[str, Any]
    result: Any
    status: str = "ok"
    error: str = ""

    @property
    def is_error(self) -> bool:
        return self.status != "ok"

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "function": self.function,
            "module": self.module,
            "arguments": self.arguments,
            "result": self.result,
            "status": self.status,
            "error": self.error,
            "is_error": self.is_error,
        }

    def display_name(self) -> str:
        module = Path(self.module).stem
        return f"{self.tool} -> {module}.{self.function}()" if self.tool else f"{module}.{self.function}()"
