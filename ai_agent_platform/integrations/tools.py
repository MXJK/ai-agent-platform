from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from dataclasses import replace
import hashlib
import inspect
import json
from threading import Lock
import time
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError


SENSITIVE_ARGUMENT_NAMES = {
    "api_key",
    "authorization",
    "password",
    "secret",
    "token",
}
DEFAULT_TOOL_MAX_OUTPUT_CHARS = 8000
DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0


class ToolExecutionError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "tool_execution_error",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str = field(default_factory=lambda: f"tool_{uuid4().hex[:12]}")
    source: str = "planner"

    def __post_init__(self) -> None:
        if not self.call_id:
            object.__setattr__(self, "call_id", f"tool_{uuid4().hex[:12]}")


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
    timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS
    max_retries: int = 0
    idempotent: bool = True


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
    error_code: str | None = None
    attempts: int = 1
    cached: bool = False

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
            "attempts": self.attempts,
            "cached": self.cached,
        }
        if self.ok:
            payload["result"] = self.result
        else:
            payload["error"] = self.error
            payload["error_code"] = self.error_code or "tool_execution_error"
        return payload


@dataclass(frozen=True)
class ToolExecutionContext:
    conversation_id: str
    workspace_id: str
    workspace_root: str
    run_id: str | None = None


class ToolRegistry:
    """Registry for local and future MCP-backed agent tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Any]] = {}
        self._specs: dict[str, ToolSpec] = {}
        self._context_cleanup_callbacks: list[
            Callable[[ToolExecutionContext], Any]
        ] = []
        self._close_callbacks: list[Callable[[], Any]] = []
        self._context_exporters: dict[
            str, Callable[[ToolExecutionContext], Any]
        ] = {}
        self._idempotency_results: dict[tuple[str, str], tuple[str, ToolResult]] = {}
        self._idempotency_guards: dict[tuple[str, str], Lock] = {}
        self._idempotency_lock = Lock()

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
        timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
        max_retries: int = 0,
        idempotent: bool | None = None,
    ) -> None:
        if name in self._tools:
            raise ValueError(f"tool already registered: {name}")
        if max_output_chars <= 0:
            raise ValueError("max_output_chars must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_retries < 0:
            raise ValueError("max_retries must be greater than or equal to 0")
        resolved_input_schema = input_schema or {"type": "object"}
        resolved_output_schema = output_schema or {"type": "object"}
        _check_schema(resolved_input_schema, name=name, kind="input")
        _check_schema(resolved_output_schema, name=name, kind="output")
        resolved_idempotent = (
            permission_level == "read_only"
            if idempotent is None
            else idempotent
        )
        self._tools[name] = tool
        self._specs[name] = ToolSpec(
            name=name,
            description=description or name,
            input_schema=resolved_input_schema,
            output_schema=resolved_output_schema,
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
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            idempotent=resolved_idempotent,
        )

    def list_specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def get_spec(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def select(self, allowed_names: tuple[str, ...]) -> "ToolRegistryView":
        """Return an immutable Run-scoped selection without changing this registry."""
        selected = tuple(dict.fromkeys(allowed_names))
        unknown = set(selected).difference(self._tools)
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"configured tool selection contains unknown tools: {names}")
        return ToolRegistryView(self, selected)

    def restrict_to(self, allowed_names: tuple[str, ...]) -> None:
        """Irreversibly apply a process-owned capability upper bound."""
        allowed = set(allowed_names)
        unknown = allowed.difference(self._tools)
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"configured tool selection contains unknown tools: {names}")
        self._tools = {
            name: tool for name, tool in self._tools.items() if name in allowed
        }
        self._specs = {
            name: spec for name, spec in self._specs.items() if name in allowed
        }

    def register_context_cleanup(
        self,
        callback: Callable[[ToolExecutionContext], Any],
    ) -> None:
        self._context_cleanup_callbacks.append(callback)

    def register_close(self, callback: Callable[[], Any]) -> None:
        self._close_callbacks.append(callback)

    def register_context_exporter(
        self,
        name: str,
        callback: Callable[[ToolExecutionContext], Any],
    ) -> None:
        if not name or name in self._context_exporters:
            raise ValueError(f"context exporter already registered: {name}")
        self._context_exporters[name] = callback

    def export_context(self, name: str, context: ToolExecutionContext) -> Any:
        try:
            exporter = self._context_exporters[name]
        except KeyError as exc:
            raise ValueError(f"unknown context exporter: {name}") from exc
        return exporter(context)

    def cleanup_context(self, context: ToolExecutionContext) -> list[str]:
        errors: list[str] = []
        for callback in reversed(self._context_cleanup_callbacks):
            try:
                callback(context)
            except Exception as exc:
                errors.append(str(exc))
        return errors

    def close(self) -> list[str]:
        errors: list[str] = []
        for callback in reversed(self._close_callbacks):
            try:
                callback()
            except Exception as exc:
                errors.append(str(exc))
        return errors

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
        cache_key = _idempotency_key(context, tool_call.call_id)
        if cache_key is None:
            return self._execute_serialized(tool_call, context)
        with self._idempotency_lock:
            guard = self._idempotency_guards.setdefault(cache_key, Lock())
        with guard:
            return self._execute_serialized(tool_call, context)

    def _execute_serialized(
        self,
        tool_call: ToolCall,
        context: ToolExecutionContext | None,
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
        fingerprint = _tool_call_fingerprint(tool_call)
        cache_key = _idempotency_key(context, call_id)
        cached = self._cached_result(cache_key, fingerprint)
        if cached is not None:
            return cached
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
                error_code="unknown_tool",
            )

        validation_error = _validate_instance(
            tool_call.arguments,
            spec.input_schema,
            label="tool arguments",
        )
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
                error_code="invalid_tool_arguments",
            )

        attempts = 0
        result: Any = None
        while attempts <= spec.max_retries:
            attempts += 1
            try:
                result = _call_with_timeout(
                    tool,
                    arguments=tool_call.arguments,
                    context=context,
                    accepts_context=spec.accepts_context,
                    timeout_seconds=spec.timeout_seconds,
                )
                break
            except Exception as exc:
                retryable = bool(getattr(exc, "retryable", False))
                if isinstance(exc, FutureTimeoutError):
                    retryable = True
                should_retry = (
                    spec.idempotent
                    and retryable
                    and attempts <= spec.max_retries
                )
                if should_retry:
                    time.sleep(min(0.05 * (2 ** (attempts - 1)), 0.5))
                    continue
                duration_ms = int((perf_counter() - started_at) * 1000)
                failed = ToolResult(
                    call_id=call_id,
                    name=tool_call.name,
                    ok=False,
                    error=(
                        f"tool execution timed out after {spec.timeout_seconds:g}s"
                        if isinstance(exc, FutureTimeoutError)
                        else str(exc)
                    ),
                    provider=spec.provider,
                    permission_level=spec.permission_level,
                    requires_approval=spec.requires_approval,
                    duration_ms=duration_ms,
                    risk_summary=spec.risk_summary,
                    arguments_summary=arguments_summary,
                    error_code=(
                        "tool_timeout"
                        if isinstance(exc, FutureTimeoutError)
                        else str(getattr(exc, "code", "tool_execution_error"))
                    ),
                    attempts=attempts,
                )
                self._store_result(cache_key, fingerprint, failed)
                return failed

        output_validation_error = _validate_instance(
            result,
            spec.output_schema,
            label="tool output",
        )
        if output_validation_error is not None:
            duration_ms = int((perf_counter() - started_at) * 1000)
            failed = ToolResult(
                call_id=call_id,
                name=tool_call.name,
                ok=False,
                error=output_validation_error,
                provider=spec.provider,
                permission_level=spec.permission_level,
                requires_approval=spec.requires_approval,
                duration_ms=duration_ms,
                risk_summary=spec.risk_summary,
                arguments_summary=arguments_summary,
                error_code="invalid_tool_output",
                attempts=attempts,
            )
            self._store_result(cache_key, fingerprint, failed)
            return failed

        duration_ms = int((perf_counter() - started_at) * 1000)
        declared_output_truncated = bool(
            isinstance(result, dict) and result.get("output_truncated")
        )
        result, output_truncated = _truncate_output(
            result,
            max_chars=spec.max_output_chars,
        )
        completed = ToolResult(
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
            output_truncated=(
                declared_output_truncated or output_truncated
            ),
            attempts=attempts,
        )
        self._store_result(cache_key, fingerprint, completed)
        return completed

    def _cached_result(
        self,
        cache_key: tuple[str, str] | None,
        fingerprint: str,
    ) -> ToolResult | None:
        if cache_key is None:
            return None
        with self._idempotency_lock:
            cached = self._idempotency_results.get(cache_key)
        if cached is None:
            return None
        cached_fingerprint, result = cached
        if cached_fingerprint != fingerprint:
            return ToolResult(
                call_id=cache_key[1],
                name=result.name,
                ok=False,
                error="call_id was already used for different tool arguments",
                provider=result.provider,
                permission_level=result.permission_level,
                requires_approval=result.requires_approval,
                risk_summary=result.risk_summary,
                arguments_summary=result.arguments_summary,
                error_code="idempotency_conflict",
                cached=True,
            )
        return replace(result, cached=True)

    def _store_result(
        self,
        cache_key: tuple[str, str] | None,
        fingerprint: str,
        result: ToolResult,
    ) -> None:
        if cache_key is None:
            return
        with self._idempotency_lock:
            self._idempotency_results.setdefault(cache_key, (fingerprint, result))


class ToolRegistryView:
    """Read-only, non-owning view of a process ToolRegistry for one Run."""

    def __init__(self, source: ToolRegistry, allowed_names: tuple[str, ...]) -> None:
        self._source = source
        self._allowed_names = frozenset(allowed_names)

    @property
    def allowed_names(self) -> tuple[str, ...]:
        return tuple(
            spec.name
            for spec in self._source.list_specs()
            if spec.name in self._allowed_names
        )

    def list_specs(self) -> list[ToolSpec]:
        return [
            spec
            for spec in self._source.list_specs()
            if spec.name in self._allowed_names
        ]

    def get_spec(self, name: str) -> ToolSpec | None:
        if name not in self._allowed_names:
            return None
        return self._source.get_spec(name)

    def select(self, allowed_names: tuple[str, ...]) -> "ToolRegistryView":
        selected = tuple(dict.fromkeys(allowed_names))
        unknown = set(selected).difference(self._allowed_names)
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"configured tool selection contains unknown tools: {names}")
        return ToolRegistryView(self._source, selected)

    def call(self, tool_call: ToolCall) -> Any:
        if tool_call.name not in self._allowed_names:
            raise ValueError(f"unknown tool: {tool_call.name}")
        return self._source.call(tool_call)

    def execute(
        self,
        tool_call: ToolCall,
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        if tool_call.name not in self._allowed_names:
            return ToolResult(
                call_id=tool_call.call_id,
                name=tool_call.name,
                ok=False,
                error=f"unknown tool: {tool_call.name}",
                error_code="unknown_tool",
            )
        return self._source.execute(tool_call, context=context)

    def export_context(self, name: str, context: ToolExecutionContext) -> Any:
        return self._source.export_context(name, context)

    def cleanup_context(self, context: ToolExecutionContext) -> list[str]:
        return self._source.cleanup_context(context)


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


def _check_schema(schema: dict[str, Any], *, name: str, kind: str) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"invalid {kind} schema for tool {name}: {exc.message}") from exc


def _validate_instance(
    value: Any,
    schema: dict[str, Any],
    *,
    label: str,
) -> str | None:
    try:
        Draft202012Validator(schema).validate(value)
    except ValidationError as exc:
        path = "$"
        for item in exc.absolute_path:
            path += f"[{item}]" if isinstance(item, int) else f".{item}"
        return f"invalid {label} at {path}: {exc.message}"
    return None


def _call_with_timeout(
    tool: Callable[..., Any],
    *,
    arguments: dict[str, Any],
    context: ToolExecutionContext | None,
    accepts_context: bool,
    timeout_seconds: float,
) -> Any:
    def invoke() -> Any:
        if context is not None and accepts_context:
            return tool(context=context, **arguments)
        return tool(**arguments)

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-tool")
    future = executor.submit(invoke)
    try:
        return future.result(timeout=timeout_seconds)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _idempotency_key(
    context: ToolExecutionContext | None,
    call_id: str,
) -> tuple[str, str] | None:
    if context is None or not context.run_id:
        return None
    return context.run_id, call_id


def _tool_call_fingerprint(tool_call: ToolCall) -> str:
    payload = json.dumps(
        {
            "name": tool_call.name,
            "arguments": tool_call.arguments,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
