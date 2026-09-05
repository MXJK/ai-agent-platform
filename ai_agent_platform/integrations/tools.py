from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import replace
import hashlib
import inspect
import json
from threading import Event, Lock, RLock, Thread
import time
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from ai_agent_platform.integrations.permissions import (
    PermissionDecision,
    PermissionRequest,
    PermissionResolver,
    ToolApproval,
    ToolExecutionContext,
    ToolUseContext,
)


SENSITIVE_ARGUMENT_NAMES = {
    "api_key",
    "authorization",
    "password",
    "secret",
    "token",
}
DEFAULT_TOOL_MAX_OUTPUT_CHARS = 8000
DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0
DEFAULT_TOOL_CONTEXT_PARAMETER = "context"
# jsonschema renders the rejected instance inside its own message, so only
# validators whose message is built from schema-side names may be reused.
SCHEMA_SAFE_MESSAGE_VALIDATORS = frozenset({"required", "additionalProperties"})


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


class ToolTimeoutError(ToolExecutionError):
    """The tool missed its deadline; its worker thread was abandoned.

    A Python callable cannot be preempted, so the abandoned worker keeps
    running and may still apply side effects after this error is reported.
    """

    def __init__(self, message: str, *, worker: Thread | None = None) -> None:
        super().__init__(message, code="tool_timeout", retryable=True)
        self.worker = worker


@dataclass(frozen=True)
class AbandonedToolCall:
    """A timed-out call whose worker thread may still be applying changes."""

    run_id: str
    call_id: str
    name: str
    worker: Thread

    @property
    def running(self) -> bool:
        return self.worker.is_alive()


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
    context_parameter: str = DEFAULT_TOOL_CONTEXT_PARAMETER
    risk_summary: str = "Read-only tool with no expected side effects."
    max_output_chars: int = DEFAULT_TOOL_MAX_OUTPUT_CHARS
    timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS
    max_retries: int = 0
    idempotent: bool = True
    permission_source: str = "local_policy"
    defer_loading: bool = False
    native_type: str = ""


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
    permission_decision: dict[str, str] | None = None

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
            "permission_decision": self.permission_decision,
        }
        if self.ok:
            payload["result"] = self.result
        else:
            payload["error"] = self.error
            payload["error_code"] = self.error_code or "tool_execution_error"
        return payload


class ToolRegistry:
    """Registry for local and future MCP-backed agent tools."""

    def __init__(
        self,
        permission_resolver: PermissionResolver | None = None,
    ) -> None:
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
        self._abandoned_calls: list[AbandonedToolCall] = []
        self._abandoned_lock = Lock()
        self._registry_lock = RLock()
        self._permission_resolver = permission_resolver

    def attach_permission_resolver(self, resolver: PermissionResolver) -> None:
        with self._registry_lock:
            if (
                self._permission_resolver is not None
                and self._permission_resolver is not resolver
            ):
                raise ValueError("ToolRegistry already has a different PermissionResolver")
            self._permission_resolver = resolver

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
        context_parameter: str = DEFAULT_TOOL_CONTEXT_PARAMETER,
        risk_summary: str | None = None,
        max_output_chars: int = DEFAULT_TOOL_MAX_OUTPUT_CHARS,
        timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
        max_retries: int = 0,
        idempotent: bool | None = None,
        permission_source: str = "local_policy",
    ) -> None:
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
        resolved_accepts_context = (
            _accepts_context(tool) if accepts_context is None else accepts_context
        )
        if resolved_accepts_context and context_parameter in _schema_property_names(
            resolved_input_schema
        ):
            raise ValueError(
                f"tool {name} declares an argument named {context_parameter!r} that "
                "collides with the injected execution context parameter"
            )
        spec = ToolSpec(
            name=name,
            description=description or name,
            input_schema=resolved_input_schema,
            output_schema=resolved_output_schema,
            provider=provider,
            permission_level=permission_level,
            requires_approval=requires_approval,
            accepts_context=resolved_accepts_context,
            context_parameter=context_parameter,
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
            permission_source=permission_source,
        )
        with self._registry_lock:
            if name in self._tools:
                raise ValueError(f"tool already registered: {name}")
            self._tools[name] = tool
            self._specs[name] = spec

    def list_specs(
        self,
        context: ToolUseContext | None = None,
    ) -> list[ToolSpec]:
        with self._registry_lock:
            specs = list(self._specs.values())
            resolver = self._permission_resolver
        if context is None or resolver is None:
            return specs
        return [
            spec
            for spec in specs
            if resolver.resolve(
                PermissionRequest.from_spec(spec),
                context.for_display(spec.name),
                phase="display",
            ).effect
            != "deny"
        ]

    def get_spec(self, name: str) -> ToolSpec | None:
        with self._registry_lock:
            return self._specs.get(name)

    def remove_provider(self, provider: str) -> tuple[str, ...]:
        """Remove dynamically registered tools owned by one provider."""

        with self._registry_lock:
            names = tuple(
                sorted(
                    name
                    for name, spec in self._specs.items()
                    if spec.provider == provider
                )
            )
            for name in names:
                self._tools.pop(name, None)
                self._specs.pop(name, None)
        return names

    def resolve_permission(
        self,
        tool_call: ToolCall,
        context: ToolUseContext,
        *,
        phase: str = "execute",
    ) -> PermissionDecision:
        with self._registry_lock:
            spec = self._specs.get(tool_call.name)
            resolver = self._permission_resolver
        if spec is None:
            return PermissionDecision(
                effect="deny",
                matched_rule="process.unknown_tool",
                reason="The requested tool is not registered in this process.",
                risk_summary="Unknown tool with unbounded behavior.",
            )
        if resolver is None:
            needs_approval = bool(
                spec.requires_approval
                or spec.permission_level != "read_only"
                or context.approval_policy == "always"
            )
            if needs_approval and context.approval_policy == "never":
                effect = "deny"
                rule = "approval_policy.never"
            elif needs_approval and context.approval_policy == "auto_approve":
                effect = "allow"
                rule = "approval_policy.auto_approve"
            elif needs_approval:
                effect = "ask"
                rule = "tool_spec.approval_required"
            else:
                effect = "allow"
                rule = "tool_spec.read_only_allow"
            return PermissionDecision(
                effect=effect,
                matched_rule=rule,
                reason=(
                    "ToolSpec requires approval before execution."
                    if effect == "ask"
                    else (
                        "The effective approval policy denies this operation."
                        if effect == "deny"
                        else (
                            "The effective approval policy auto-approves this "
                            "operation."
                            if rule == "approval_policy.auto_approve"
                            else "ToolSpec allows this read-only operation."
                        )
                    )
                ),
                risk_summary=spec.risk_summary,
            )
        bound = context.bind(
            call_id=tool_call.call_id,
            tool_name=tool_call.name,
            arguments=tool_call.arguments,
        )
        return resolver.resolve(
            PermissionRequest.from_spec(spec),
            bound,
            phase=phase,  # type: ignore[arg-type]
        )

    def issue_approval(
        self,
        tool_call: ToolCall,
        context: ToolUseContext,
        *,
        approved_by: str,
    ) -> ToolApproval:
        with self._registry_lock:
            spec = self._specs.get(tool_call.name)
            resolver = self._permission_resolver
        if spec is None:
            raise PermissionError("The requested tool is not registered")
        bound = context.bind(
            call_id=tool_call.call_id,
            tool_name=tool_call.name,
            arguments=tool_call.arguments,
        )
        if resolver is None:
            decision = self.resolve_permission(
                tool_call,
                context,
                phase="plan",
            )
            if decision.effect == "deny":
                raise PermissionError(decision.reason)
            if not all(
                (
                    bound.run_id,
                    bound.call_id,
                    bound.tool_name,
                    bound.arguments_hash,
                    approved_by,
                )
            ):
                raise PermissionError("approval binding is incomplete")
            return ToolApproval(
                run_id=str(bound.run_id),
                call_id=str(bound.call_id),
                tool_name=str(bound.tool_name),
                arguments_hash=str(bound.arguments_hash),
                approved_by=approved_by,
            )
        return resolver.issue_approval(
            PermissionRequest.from_spec(spec),
            bound,
            approved_by=approved_by,
        )

    def select(self, allowed_names: tuple[str, ...]) -> "ToolRegistryView":
        """Return an immutable Run-scoped selection without changing this registry."""
        selected = tuple(dict.fromkeys(allowed_names))
        with self._registry_lock:
            unknown = set(selected).difference(self._tools)
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"configured tool selection contains unknown tools: {names}")
        return ToolRegistryView(self, selected)

    def restrict_to(self, allowed_names: tuple[str, ...]) -> None:
        """Irreversibly apply a process-owned capability upper bound."""
        allowed = set(allowed_names)
        with self._registry_lock:
            unknown = allowed.difference(self._tools)
            if unknown:
                names = ", ".join(sorted(unknown))
                raise ValueError(
                    f"configured tool selection contains unknown tools: {names}"
                )
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
        with self._registry_lock:
            resolver = self._permission_resolver
            tool = self._tools.get(tool_call.name)
        if resolver is not None:
            raise PermissionError(
                "direct tool calls are disabled when PermissionResolver is active"
            )
        if tool is None:
            raise ValueError(f"unknown tool: {tool_call.name}")
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
        with self._registry_lock:
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
            tool = self._tools.get(tool_call.name)
            resolver = self._permission_resolver
        arguments_summary = _summarize_arguments(tool_call.arguments)
        fingerprint = _tool_call_fingerprint(tool_call)
        cache_key = _idempotency_key(context, call_id)
        if tool is None:
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

        permission_decision: PermissionDecision | None = None
        if resolver is not None:
            if context is None:
                permission_decision = PermissionDecision(
                    effect="deny",
                    matched_rule="context.required",
                    reason="Tool execution requires a ToolUseContext.",
                    risk_summary=spec.risk_summary,
                )
            else:
                permission_decision = self.resolve_permission(
                    tool_call,
                    context,
                    phase="execute",
                )
            if permission_decision.effect != "allow":
                return ToolResult(
                    call_id=call_id,
                    name=tool_call.name,
                    ok=False,
                    error=permission_decision.reason,
                    provider=spec.provider,
                    permission_level=spec.permission_level,
                    requires_approval=spec.requires_approval,
                    duration_ms=int((perf_counter() - started_at) * 1000),
                    risk_summary=permission_decision.risk_summary,
                    arguments_summary=arguments_summary,
                    error_code=(
                        "permission_approval_required"
                        if permission_decision.effect == "ask"
                        else "permission_denied"
                    ),
                    permission_decision=permission_decision.to_dict(),
                )

        cached = self._cached_result(cache_key, fingerprint)
        if cached is not None:
            return cached

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

        blocking = self._blocking_abandoned_call(context, spec)
        if blocking is not None:
            return ToolResult(
                call_id=call_id,
                name=tool_call.name,
                ok=False,
                error=(
                    f"tool call {blocking.call_id} ({blocking.name}) timed out and is "
                    "still running; another side-effecting call in this run would "
                    "race it"
                ),
                provider=spec.provider,
                permission_level=spec.permission_level,
                requires_approval=spec.requires_approval,
                duration_ms=int((perf_counter() - started_at) * 1000),
                risk_summary=spec.risk_summary,
                arguments_summary=arguments_summary,
                error_code="tool_timeout_in_flight",
            )

        attempts = 0
        result: Any = None
        execution_context = (
            context.bind(
                call_id=call_id,
                tool_name=tool_call.name,
                arguments=tool_call.arguments,
            )
            if context is not None
            else None
        )
        while attempts <= spec.max_retries:
            attempts += 1
            try:
                result = _call_with_timeout(
                    tool,
                    arguments=tool_call.arguments,
                    context=execution_context,
                    accepts_context=spec.accepts_context,
                    context_parameter=spec.context_parameter,
                    timeout_seconds=spec.timeout_seconds,
                    name=tool_call.name,
                )
                break
            except Exception as exc:
                if isinstance(exc, ToolTimeoutError):
                    self._record_abandoned_call(
                        context,
                        call_id=call_id,
                        name=tool_call.name,
                        worker=exc.worker,
                        idempotent=spec.idempotent,
                    )
                should_retry = (
                    spec.idempotent
                    and bool(getattr(exc, "retryable", False))
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
                    error=str(exc),
                    provider=spec.provider,
                    permission_level=spec.permission_level,
                    requires_approval=spec.requires_approval,
                    duration_ms=duration_ms,
                    risk_summary=spec.risk_summary,
                    arguments_summary=arguments_summary,
                    error_code=str(getattr(exc, "code", "tool_execution_error")),
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
            permission_decision=(
                permission_decision.to_dict()
                if permission_decision is not None
                else None
            ),
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

    def abandoned_calls(self) -> tuple[AbandonedToolCall, ...]:
        """Return timed-out calls whose worker thread is still running."""

        with self._abandoned_lock:
            self._abandoned_calls = [
                item for item in self._abandoned_calls if item.running
            ]
            return tuple(self._abandoned_calls)

    def _record_abandoned_call(
        self,
        context: ToolExecutionContext | None,
        *,
        call_id: str,
        name: str,
        worker: Thread | None,
        idempotent: bool,
    ) -> None:
        # Only side-effecting calls are tracked: an abandoned read cannot
        # corrupt the workspace, and tracking it would grow without bound.
        if idempotent or worker is None or not worker.is_alive():
            return
        run_id = str(context.run_id or "") if context is not None else ""
        if not run_id:
            return
        with self._abandoned_lock:
            self._abandoned_calls = [
                item for item in self._abandoned_calls if item.running
            ]
            self._abandoned_calls.append(
                AbandonedToolCall(
                    run_id=run_id,
                    call_id=call_id,
                    name=name,
                    worker=worker,
                )
            )

    def _blocking_abandoned_call(
        self,
        context: ToolExecutionContext | None,
        spec: ToolSpec,
    ) -> AbandonedToolCall | None:
        if spec.idempotent or context is None or not context.run_id:
            return None
        run_id = str(context.run_id)
        for item in self.abandoned_calls():
            if item.run_id == run_id:
                return item
        return None

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

    def list_specs(
        self,
        context: ToolUseContext | None = None,
    ) -> list[ToolSpec]:
        return [
            spec
            for spec in self._source.list_specs(context=context)
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
            decision = PermissionDecision(
                effect="deny",
                matched_rule="project.tool_selection",
                reason="The project tool selection excludes this operation.",
                risk_summary="Tool is outside the frozen Run selection.",
            )
            spec = self._source.get_spec(tool_call.name)
            return ToolResult(
                call_id=tool_call.call_id,
                name=tool_call.name,
                ok=False,
                error=decision.reason,
                error_code="permission_denied",
                provider=spec.provider if spec is not None else "unknown",
                permission_level=(
                    spec.permission_level if spec is not None else "unknown"
                ),
                requires_approval=(
                    spec.requires_approval if spec is not None else False
                ),
                risk_summary=decision.risk_summary,
                arguments_summary=_summarize_arguments(tool_call.arguments),
                permission_decision=decision.to_dict(),
            )
        return self._source.execute(tool_call, context=context)

    def resolve_permission(
        self,
        tool_call: ToolCall,
        context: ToolUseContext,
        *,
        phase: str = "execute",
    ) -> PermissionDecision:
        if tool_call.name not in self._allowed_names:
            return PermissionDecision(
                effect="deny",
                matched_rule="project.tool_selection",
                reason="The project tool selection excludes this operation.",
                risk_summary="Tool is outside the frozen Run selection.",
            )
        return self._source.resolve_permission(
            tool_call,
            context,
            phase=phase,
        )

    def issue_approval(
        self,
        tool_call: ToolCall,
        context: ToolUseContext,
        *,
        approved_by: str,
    ) -> ToolApproval:
        if tool_call.name not in self._allowed_names:
            raise PermissionError("The project tool selection excludes this operation")
        return self._source.issue_approval(
            tool_call,
            context,
            approved_by=approved_by,
        )

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
        return f"invalid {label} at {path}: {_validation_detail(exc)}"
    return None


def _validation_detail(exc: ValidationError) -> str:
    """Describe a schema failure without echoing the rejected value.

    Tool arguments and outputs carry credentials and file contents, and this
    text is replayed to the model and persisted with the Run.
    """

    validator = str(exc.validator or "schema")
    if validator in SCHEMA_SAFE_MESSAGE_VALIDATORS:
        return exc.message
    try:
        expected = json.dumps(exc.validator_value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        expected = str(exc.validator_value)
    return (
        f"failed {validator!r} constraint ({expected}); "
        f"received {_describe_instance(exc.instance)}"
    )


def _describe_instance(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return f"{type(value).__name__} value"
    if isinstance(value, str):
        return f"string of length {len(value)}"
    if isinstance(value, (list, tuple)):
        return f"array of {len(value)} item(s)"
    if isinstance(value, dict):
        return f"object with {len(value)} key(s)"
    return type(value).__name__


def _schema_property_names(schema: dict[str, Any]) -> frozenset[str]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return frozenset()
    return frozenset(str(name) for name in properties)


def _call_with_timeout(
    tool: Callable[..., Any],
    *,
    arguments: dict[str, Any],
    context: ToolExecutionContext | None,
    accepts_context: bool,
    timeout_seconds: float,
    context_parameter: str = DEFAULT_TOOL_CONTEXT_PARAMETER,
    name: str = "",
) -> Any:
    call_arguments = dict(arguments)
    if context is not None and accepts_context:
        if context_parameter in call_arguments:
            raise ToolExecutionError(
                f"tool argument {context_parameter!r} collides with the injected "
                "execution context parameter",
                code="tool_argument_conflict",
            )
        call_arguments[context_parameter] = context
    completed = Event()
    outcome: dict[str, Any] = {}

    def invoke() -> None:
        try:
            outcome["value"] = tool(**call_arguments)
        except BaseException as exc:  # re-raised on the calling thread
            outcome["error"] = exc
        finally:
            completed.set()

    # A daemon thread, not a pooled worker: a wedged tool must never block
    # interpreter shutdown, and an abandoned worker must never be reused.
    worker = Thread(
        target=invoke,
        name=f"agent-tool-{name or 'call'}",
        daemon=True,
    )
    worker.start()
    if not completed.wait(timeout_seconds):
        raise ToolTimeoutError(
            f"tool execution timed out after {timeout_seconds:g}s; the call may "
            "still be running and its side effects may still land",
            worker=worker,
        )
    error = outcome.get("error")
    if error is not None:
        raise error
    return outcome.get("value")


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
