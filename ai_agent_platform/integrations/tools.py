from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4


SENSITIVE_ARGUMENT_NAMES = {
    "api_key",
    "authorization",
    "password",
    "secret",
    "token",
}
DEFAULT_TOOL_MAX_OUTPUT_CHARS = 8000


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
    accepts_context: bool = False
    risk_summary: str = "Read-only tool with no expected side effects."
    max_output_chars: int = DEFAULT_TOOL_MAX_OUTPUT_CHARS


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
    risk_summary: str = ""
    arguments_summary: dict[str, Any] | None = None
    output_truncated: bool = False

    def to_response(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "call_id": self.call_id,
            "name": self.name,
            "ok": self.ok,
            "provider": self.provider,
            "permission_level": self.permission_level,
            "requires_approval": self.requires_approval,
            "duration_ms": self.duration_ms,
            "risk_summary": self.risk_summary,
            "arguments_summary": self.arguments_summary or {},
            "output_truncated": self.output_truncated,
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
    run_id: str | None = None


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
        accepts_context: bool | None = None,
        risk_summary: str | None = None,
        max_output_chars: int = DEFAULT_TOOL_MAX_OUTPUT_CHARS,
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
            accepts_context=(
                _accepts_context(tool) if accepts_context is None else accepts_context
            ),
            risk_summary=(
                risk_summary
                or _default_risk_summary(
                    permission_level=permission_level,
                    requires_approval=requires_approval,
                    provider=provider,
                )
            ),
            max_output_chars=max_output_chars,
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
        arguments_summary = _summarize_arguments(tool_call.arguments)
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
                risk_summary=spec.risk_summary,
                arguments_summary=arguments_summary,
            )

        validation_error = _validate_arguments(tool_call.arguments, spec.input_schema)
        if validation_error is not None:
            duration_ms = int((perf_counter() - started_at) * 1000)
            return ToolResult(
                call_id=call_id,
                name=tool_call.name,
                ok=False,
                error=validation_error,
                provider=spec.provider,
                permission_level=spec.permission_level,
                requires_approval=spec.requires_approval,
                duration_ms=duration_ms,
                risk_summary=spec.risk_summary,
                arguments_summary=arguments_summary,
            )

        try:
            if context is None:
                result = tool(**tool_call.arguments)
            elif spec.accepts_context:
                result = tool(context=context, **tool_call.arguments)
            else:
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
                risk_summary=spec.risk_summary,
                arguments_summary=arguments_summary,
            )

        duration_ms = int((perf_counter() - started_at) * 1000)
        result, output_truncated = _truncate_output(
            result,
            max_chars=spec.max_output_chars,
        )
        return ToolResult(
            call_id=call_id,
            name=tool_call.name,
            ok=True,
            result=result,
            provider=spec.provider,
            permission_level=spec.permission_level,
            requires_approval=spec.requires_approval,
            duration_ms=duration_ms,
            risk_summary=spec.risk_summary,
            arguments_summary=arguments_summary,
            output_truncated=output_truncated,
        )


def _accepts_context(tool: Callable[..., Any]) -> bool:
    try:
        signature = inspect.signature(tool)
    except (TypeError, ValueError):
        return False
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == "context":
            return True
    return False


def _default_risk_summary(
    *,
    permission_level: str,
    requires_approval: bool,
    provider: str,
) -> str:
    if requires_approval or permission_level != "read_only":
        return (
            f"{provider} tool requests {permission_level} permission and must be "
            "reviewed before execution."
        )
    return f"{provider} read-only tool; expected to inspect data without side effects."


def _validate_arguments(arguments: dict[str, Any], schema: dict[str, Any]) -> str | None:
    if schema.get("type", "object") != "object":
        return None
    required = schema.get("required", [])
    if isinstance(required, list):
        for name in required:
            if name not in arguments:
                return f"missing required tool argument: {name}"
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return None
    for name, value in arguments.items():
        property_schema = properties.get(name)
        if not isinstance(property_schema, dict):
            continue
        expected_type = property_schema.get("type")
        if expected_type and not _matches_json_schema_type(value, str(expected_type)):
            return (
                f"invalid type for tool argument {name}: expected "
                f"{expected_type}, got {type(value).__name__}"
            )
    return None


def _matches_json_schema_type(value: Any, expected_type: str) -> bool:
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "object":
        return isinstance(value, dict)
    return True


def _summarize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        name: _summarize_argument_value(name, value)
        for name, value in arguments.items()
    }


def summarize_tool_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    return _summarize_arguments(arguments)


def _summarize_argument_value(name: str, value: Any) -> Any:
    normalized_name = name.lower()
    if any(secret_name in normalized_name for secret_name in SENSITIVE_ARGUMENT_NAMES):
        return "<redacted>"
    if isinstance(value, str):
        return value if len(value) <= 200 else value[:200] + "...<truncated>"
    if isinstance(value, list):
        return [_summarize_argument_value(name, item) for item in value[:20]]
    if isinstance(value, dict):
        return {
            key: _summarize_argument_value(str(key), item)
            for key, item in list(value.items())[:20]
        }
    return value


def _truncate_output(result: Any, *, max_chars: int) -> tuple[Any, bool]:
    try:
        encoded = json.dumps(result, ensure_ascii=False)
    except TypeError:
        encoded = str(result)
    if len(encoded) <= max_chars:
        return result, False
    if isinstance(result, dict):
        metadata = {
            name: value
            for name, value in result.items()
            if value is None or isinstance(value, (bool, int, float))
        }
        for name in ("changed_files", "command"):
            value = result.get(name)
            if isinstance(value, list):
                candidate = json.dumps(value, ensure_ascii=False)
                if len(candidate) <= max_chars // 2:
                    metadata[name] = value
        metadata_size = len(json.dumps(metadata, ensure_ascii=False))
        preview_size = max(0, max_chars - metadata_size - 40)
        metadata["truncated_output_preview"] = encoded[:preview_size]
        return metadata, True
    return {"truncated_output_preview": encoded[:max_chars]}, True
