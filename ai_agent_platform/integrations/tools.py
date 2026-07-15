from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str | None = None
    source: str = "planner"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    provider: str
    permission_level: str = "read_only"
    requires_approval: bool = False


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    ok: bool
    result: Any = None
    error: str | None = None
    provider: str = "local"
    permission_level: str = "read_only"
    requires_approval: bool = False
    duration_ms: int = 0

    def to_response(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "call_id": self.call_id,
            "name": self.name,
            "ok": self.ok,
            "provider": self.provider,
            "permission_level": self.permission_level,
            "requires_approval": self.requires_approval,
            "duration_ms": self.duration_ms,
        }
        if self.ok:
            payload["result"] = self.result
        else:
            payload["error"] = self.error
        return payload


@dataclass(frozen=True)
class ToolExecutionContext:
    conversation_id: str
    repository_id: str


class ToolRegistry:
    """Registry for local and future MCP-backed agent tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Any]] = {}
        self._specs: dict[str, ToolSpec] = {}

    def register(
        self,
        name: str,
        tool: Callable[..., Any],
        *,
        description: str = "",
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        provider: str = "local",
        permission_level: str = "read_only",
        requires_approval: bool = False,
    ) -> None:
        self._tools[name] = tool
        self._specs[name] = ToolSpec(
            name=name,
            description=description or name,
            input_schema=input_schema or {"type": "object"},
            output_schema=output_schema or {"type": "object"},
            provider=provider,
            permission_level=permission_level,
            requires_approval=requires_approval,
        )

    def list_specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def get_spec(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def call(self, tool_call: ToolCall) -> Any:
        try:
            tool = self._tools[tool_call.name]
        except KeyError as exc:
            raise ValueError(f"unknown tool: {tool_call.name}") from exc
        return tool(**tool_call.arguments)

    def execute(
        self,
        tool_call: ToolCall,
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        call_id = tool_call.call_id or f"tool_{uuid4().hex[:12]}"
        started_at = perf_counter()
        spec = self._specs.get(
            tool_call.name,
            ToolSpec(
                name=tool_call.name,
                description=tool_call.name,
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                provider="unknown",
            ),
        )
        try:
            tool = self._tools[tool_call.name]
        except KeyError:
            duration_ms = int((perf_counter() - started_at) * 1000)
            return ToolResult(
                call_id=call_id,
                name=tool_call.name,
                ok=False,
                error=f"unknown tool: {tool_call.name}",
                provider=spec.provider,
                permission_level=spec.permission_level,
                requires_approval=spec.requires_approval,
                duration_ms=duration_ms,
            )

        try:
            if context is None:
                result = tool(**tool_call.arguments)
            else:
                try:
                    result = tool(context=context, **tool_call.arguments)
                except TypeError as exc:
                    if "context" not in str(exc):
                        raise
                    result = tool(**tool_call.arguments)
        except Exception as exc:
            duration_ms = int((perf_counter() - started_at) * 1000)
            return ToolResult(
                call_id=call_id,
                name=tool_call.name,
                ok=False,
                error=str(exc),
                provider=spec.provider,
                permission_level=spec.permission_level,
                requires_approval=spec.requires_approval,
                duration_ms=duration_ms,
            )

        duration_ms = int((perf_counter() - started_at) * 1000)
        return ToolResult(
            call_id=call_id,
            name=tool_call.name,
            ok=True,
            result=result,
            provider=spec.provider,
            permission_level=spec.permission_level,
            requires_approval=spec.requires_approval,
            duration_ms=duration_ms,
        )
