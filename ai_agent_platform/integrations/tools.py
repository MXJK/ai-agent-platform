from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


class ToolRegistry:
    """Registry for future agent tools such as inventory, map, or combat APIs."""

    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Any]] = {}

    def register(self, name: str, tool: Callable[..., Any]) -> None:
        self._tools[name] = tool

    def call(self, tool_call: ToolCall) -> Any:
        try:
            tool = self._tools[tool_call.name]
        except KeyError as exc:
            raise ValueError(f"unknown tool: {tool_call.name}") from exc
        return tool(**tool_call.arguments)
