from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class MCPTool:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    permission_level: str = "read_only"
    requires_approval: bool = False


class MCPClient(Protocol):
    def list_tools(self) -> list[MCPTool]:
        ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        ...
