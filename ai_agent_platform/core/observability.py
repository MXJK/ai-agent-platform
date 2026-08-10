"""Logging configuration and HTTP request correlation middleware."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import re
from time import perf_counter
from typing import Any, Iterator
from uuid import uuid4

from ai_agent_platform.core.metrics import MetricsRegistry


_LOG_CONTEXT: ContextVar[dict[str, str]] = ContextVar("log_context", default={})
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SENSITIVE_FIELD_PARTS = {
    "api_key",
    "authorization",
    "backend_url",
    "connection_string",
    "database_url",
    "dsn",
    "password",
    "redis_url",
    "secret",
    "token",
}
_STANDARD_LOG_ATTRIBUTES = set(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
}


@contextmanager
def log_context(**fields: object) -> Iterator[None]:
    current = dict(_LOG_CONTEXT.get())
    current.update(
        {name: str(value) for name, value in fields.items() if value is not None}
    )
    token = _LOG_CONTEXT.set(current)
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for name, value in _LOG_CONTEXT.get().items():
            if not hasattr(record, name):
                setattr(record, name, value)
        return True


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name, value in record.__dict__.items():
            if name.startswith("_") or name in _STANDARD_LOG_ATTRIBUTES:
                continue
            if _is_json_value(value):
                payload[name] = _redact_log_value(name, value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(*, level: str, log_format: str) -> None:
    """Configure only the project logger so host applications keep control."""

    project_logger = logging.getLogger("ai_agent_platform")
    project_logger.setLevel(level.upper())
    project_logger.propagate = False

    handler = next(
        (
            item
            for item in project_logger.handlers
            if getattr(item, "_ai_agent_platform_handler", False)
        ),
        None,
    )
    if handler is None:
        handler = logging.StreamHandler()
        handler._ai_agent_platform_handler = True  # type: ignore[attr-defined]
        handler.addFilter(ContextFilter())
        project_logger.addHandler(handler)

    handler.setLevel(level.upper())
    if log_format == "json":
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s"
            )
        )


class RequestObservabilityMiddleware:
    """Attach request IDs and measure the complete HTTP response stream."""

    def __init__(self, app: Any, *, metrics: MetricsRegistry) -> None:
        self.app = app
        self.metrics = metrics
        self.logger = logging.getLogger("ai_agent_platform.http")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id(scope)
        method = str(scope.get("method", "UNKNOWN"))
        path = str(scope.get("path", ""))
        started_at = perf_counter()
        status_code = 500
        completed = False

        def record_request(*, failed: bool) -> None:
            nonlocal completed
            if completed:
                return
            completed = True
            duration_ms = int((perf_counter() - started_at) * 1000)
            self.metrics.increment("http_requests_total")
            self.metrics.increment(f"http_responses_{status_code // 100}xx_total")
            if failed:
                self.metrics.increment("http_request_failures_total")
            self.metrics.observe_ms("http_request_duration_ms", duration_ms)
            self.logger.info(
                "http request completed",
                extra={
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
            )

        async def send_with_request_id(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            elif message["type"] == "http.response.body" and not message.get(
                "more_body", False
            ):
                record_request(failed=status_code >= 500)
            await send(message)

        with log_context(request_id=request_id):
            try:
                await self.app(scope, receive, send_with_request_id)
            except Exception:
                record_request(failed=True)
                self.logger.exception(
                    "http request failed",
                    extra={
                        "request_id": request_id,
                        "method": method,
                        "path": path,
                        "status_code": status_code,
                    },
                )
                raise


def _request_id(scope: dict[str, Any]) -> str:
    for raw_name, raw_value in scope.get("headers", []):
        if raw_name.lower() != b"x-request-id":
            continue
        value = raw_value.decode("ascii", errors="ignore")
        if _REQUEST_ID_PATTERN.fullmatch(value):
            return value
        break
    return f"req_{uuid4().hex[:16]}"


def _is_json_value(value: object) -> bool:
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return False
    return True


def _is_sensitive_field(name: str) -> bool:
    normalized = name.lower()
    return any(part in normalized for part in _SENSITIVE_FIELD_PARTS)


def _redact_log_value(name: str, value: object) -> object:
    if _is_sensitive_field(name):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(item_name): _redact_log_value(str(item_name), item_value)
            for item_name, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_log_value(name, item) for item in value]
    return value
