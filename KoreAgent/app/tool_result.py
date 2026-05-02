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
