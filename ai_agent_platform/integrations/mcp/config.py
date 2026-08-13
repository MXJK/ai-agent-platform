from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlsplit


CURRENT_STDIO = "stdio"
STREAMABLE_HTTP = "streamable_http"
LEGACY_STDIO_2025_06_18 = "stdio_2025_06_18"
LEGACY_SSE = "legacy_sse"
SUPPORTED_TRANSPORTS = frozenset(
    {CURRENT_STDIO, STREAMABLE_HTTP, LEGACY_STDIO_2025_06_18, LEGACY_SSE}
)
_SERVER_FIELDS = frozenset(
    {
        "name",
        "transport",
        "command",
        "args",
        "env",
        "env_refs",
        "url",
        "headers",
        "header_refs",
        "allowed_hosts",
        "allow_insecure_http",
        "allow_private_network",
        "legacy_compatibility",
        "required",
        "enabled",
        "connect_timeout_seconds",
        "request_timeout_seconds",
        "max_retries",
        "retry_backoff_seconds",
        "circuit_failure_threshold",
        "circuit_reset_seconds",
        "tool_cache_ttl_seconds",
    }
)

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SERVER_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_SENSITIVE_NAMES = ("AUTH", "COOKIE", "CREDENTIAL", "KEY", "PASSWORD", "SECRET", "TOKEN")
_DANGEROUS_ENV_PREFIXES = (
    "BASH_ENV",
    "ENV",
    "LD_",
    "DYLD_",
    "NODE_OPTIONS",
    "PERL5OPT",
    "PYTHONHOME",
    "PYTHONPATH",
    "RUBYOPT",
)
_RESERVED_HEADERS = frozenset(
    {
        "accept",
        "connection",
        "content-length",
        "content-type",
        "host",
        "mcp-method",
        "mcp-name",
        "mcp-protocol-version",
        "mcp-session-id",
        "origin",
        "transfer-encoding",
    }
)
_BLOCKED_INTERNAL_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.azure.internal",
        "instance-data.ec2.internal",
    }
)


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    transport: str = CURRENT_STDIO
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict, repr=False)
    env_refs: dict[str, str] = field(default_factory=dict, repr=False)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    header_refs: dict[str, str] = field(default_factory=dict, repr=False)
    allowed_hosts: tuple[str, ...] = ()
    allow_insecure_http: bool = False
    allow_private_network: bool = False
    legacy_compatibility: bool = False
    required: bool = False
    enabled: bool = True
    connect_timeout_seconds: float = 10.0
    request_timeout_seconds: float = 10.0
    max_retries: int = 1
    retry_backoff_seconds: float = 0.1
    circuit_failure_threshold: int = 3
    circuit_reset_seconds: float = 30.0
    tool_cache_ttl_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not _SERVER_NAME.fullmatch(self.name):
            raise ValueError("MCP server name must use safe ASCII identifier characters")
        if self.transport not in SUPPORTED_TRANSPORTS:
            raise ValueError(f"unsupported MCP transport: {self.transport}")
        for field_name, value in (
            ("allow_insecure_http", self.allow_insecure_http),
            ("allow_private_network", self.allow_private_network),
            ("legacy_compatibility", self.legacy_compatibility),
            ("required", self.required),
            ("enabled", self.enabled),
        ):
            if not isinstance(value, bool):
                raise ValueError(f"MCP server {self.name} {field_name} must be a boolean")
        for field_name, value in (
            ("connect_timeout_seconds", self.connect_timeout_seconds),
            ("request_timeout_seconds", self.request_timeout_seconds),
            ("circuit_reset_seconds", self.circuit_reset_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"MCP server {self.name} {field_name} must be numeric")
            if not math.isfinite(float(value)) or value <= 0:
                raise ValueError(f"MCP server {self.name} {field_name} must be positive")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value < 0
            for value in (self.retry_backoff_seconds, self.tool_cache_ttl_seconds)
        ):
            raise ValueError(f"MCP server {self.name} retry/cache values must not be negative")
        if (
            isinstance(self.max_retries, bool)
            or not isinstance(self.max_retries, int)
            or isinstance(self.circuit_failure_threshold, bool)
            or not isinstance(self.circuit_failure_threshold, int)
            or self.max_retries < 0
            or self.circuit_failure_threshold <= 0
        ):
            raise ValueError(f"MCP server {self.name} retry/circuit counts are invalid")
        if self.transport in {CURRENT_STDIO, LEGACY_STDIO_2025_06_18}:
            if not self.command or "\x00" in self.command:
                raise ValueError(f"MCP server {self.name} stdio command is required")
            _validate_scalar(self.name, self.command, kind="stdio command")
            if self.url is not None:
                raise ValueError(f"MCP server {self.name} stdio transport cannot set url")
        else:
            if self.command is not None or self.args:
                raise ValueError(f"MCP server {self.name} HTTP transport cannot set command/args")
            validate_http_target(self)
        if self.transport in {LEGACY_STDIO_2025_06_18, LEGACY_SSE} and not self.legacy_compatibility:
            raise ValueError(
                f"MCP server {self.name} legacy transport requires legacy_compatibility=true"
            )
        _validate_args(self.name, self.args)
        _validate_environment(self.name, self.env, self.env_refs)
        _validate_headers(self.name, self.headers, self.header_refs)

    @property
    def endpoint_label(self) -> str:
        if self.url:
            parsed = urlsplit(self.url)
            port = f":{parsed.port}" if parsed.port else ""
            return f"{parsed.scheme}://{parsed.hostname}{port}"
        return f"stdio:{Path(self.command or '').name}"


def load_mcp_server_configs(
    path: Path | str,
    *,
    default_request_timeout_seconds: float | None = None,
    include_disabled: bool = False,
) -> list[MCPServerConfig]:
    config_path = Path(path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if isinstance(raw, Mapping):
        servers = raw.get("mcp_servers", raw)
    elif isinstance(raw, list):
        servers = raw
    else:
        raise ValueError("MCP config root must be an object or list")
    if isinstance(servers, dict):
        payloads = [(str(name), payload) for name, payload in servers.items()]
    elif isinstance(servers, list):
        payloads = []
        for index, value in enumerate(servers):
            payload = _mapping(value, label=f"MCP server entry {index}")
            name = payload.get("name")
            if not isinstance(name, str):
                raise ValueError(f"MCP server entry {index} name must be a string")
            payloads.append((name, payload))
    else:
        raise ValueError("MCP config must contain an object or list of server configs")
    parsed_configs = [
        _server_config_from_dict(
            name,
            payload,
            default_request_timeout_seconds=default_request_timeout_seconds,
        )
        for name, payload in payloads
    ]
    configs = (
        parsed_configs
        if include_disabled
        else [config for config in parsed_configs if config.enabled]
    )
    names = [config.name for config in configs]
    if len(names) != len(set(names)):
        raise ValueError("MCP server names must be unique")
    return sorted(configs, key=lambda item: item.name)


def _server_config_from_dict(
    name: str,
    raw: Mapping[str, Any],
    *,
    default_request_timeout_seconds: float | None = None,
) -> MCPServerConfig:
    payload = _mapping(raw, label=f"MCP server {name}")
    unknown = sorted(set(payload).difference(_SERVER_FIELDS))
    if unknown:
        raise ValueError(f"MCP server {name} has unknown fields: {', '.join(unknown)}")
    args = _string_list(payload.get("args", []), label=f"MCP server {name} args")
    allowed_hosts = tuple(
        item.lower()
        for item in _string_list(
            payload.get("allowed_hosts", []),
            label=f"MCP server {name} allowed_hosts",
        )
    )
    return MCPServerConfig(
        name=name,
        transport=str(payload.get("transport", CURRENT_STDIO)),
        command=_optional_string(payload.get("command"), label="command"),
        args=args,
        env=_string_map(payload.get("env", {}), label=f"MCP server {name} env"),
        env_refs=_string_map(payload.get("env_refs", {}), label=f"MCP server {name} env_refs"),
        url=_optional_string(payload.get("url"), label="url"),
        headers=_string_map(payload.get("headers", {}), label=f"MCP server {name} headers"),
        header_refs=_string_map(
            payload.get("header_refs", {}),
            label=f"MCP server {name} header_refs",
        ),
        allowed_hosts=allowed_hosts,
        allow_insecure_http=_boolean(
            payload.get("allow_insecure_http", False),
            label=f"MCP server {name} allow_insecure_http",
        ),
        allow_private_network=_boolean(
            payload.get("allow_private_network", False),
            label=f"MCP server {name} allow_private_network",
        ),
        legacy_compatibility=_boolean(
            payload.get("legacy_compatibility", False),
            label=f"MCP server {name} legacy_compatibility",
        ),
        required=_boolean(
            payload.get("required", False),
            label=f"MCP server {name} required",
        ),
        enabled=_boolean(
            payload.get("enabled", True),
            label=f"MCP server {name} enabled",
        ),
        connect_timeout_seconds=_number(
            payload.get("connect_timeout_seconds", 10.0),
            label=f"MCP server {name} connect_timeout_seconds",
        ),
        request_timeout_seconds=_number(
            payload.get(
                "request_timeout_seconds",
                default_request_timeout_seconds
                if default_request_timeout_seconds is not None
                else 10.0,
            ),
            label=f"MCP server {name} request_timeout_seconds",
        ),
        max_retries=_integer(
            payload.get("max_retries", 1),
            label=f"MCP server {name} max_retries",
        ),
        retry_backoff_seconds=_number(
            payload.get("retry_backoff_seconds", 0.1),
            label=f"MCP server {name} retry_backoff_seconds",
        ),
        circuit_failure_threshold=_integer(
            payload.get("circuit_failure_threshold", 3),
            label=f"MCP server {name} circuit_failure_threshold",
        ),
        circuit_reset_seconds=_number(
            payload.get("circuit_reset_seconds", 30.0),
            label=f"MCP server {name} circuit_reset_seconds",
        ),
        tool_cache_ttl_seconds=_number(
            payload.get("tool_cache_ttl_seconds", 30.0),
            label=f"MCP server {name} tool_cache_ttl_seconds",
        ),
    )


def validate_http_target(config: MCPServerConfig) -> None:
    if not config.url:
        raise ValueError(f"MCP server {config.name} HTTP url is required")
    parsed = urlsplit(config.url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError(f"MCP server {config.name} HTTP url must be absolute http(s)")
    if parsed.scheme != "https" and not config.allow_insecure_http:
        raise ValueError(f"MCP server {config.name} HTTP url must use https")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            f"MCP server {config.name} HTTP url cannot contain credentials, query, or fragment"
        )
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"MCP server {config.name} HTTP port is invalid") from exc
    host = parsed.hostname.lower().rstrip(".")
    if not config.allowed_hosts or host not in {item.rstrip(".") for item in config.allowed_hosts}:
        raise ValueError(f"MCP server {config.name} HTTP host is not allowlisted")
    if host in _BLOCKED_INTERNAL_HOSTS:
        raise ValueError(f"MCP server {config.name} metadata endpoints are forbidden")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if (host == "localhost" or host.endswith((".localhost", ".local", ".internal"))) and not config.allow_private_network:
            raise ValueError(f"MCP server {config.name} private HTTP host is forbidden")
    else:
        if not address.is_global and not config.allow_private_network:
            raise ValueError(f"MCP server {config.name} private HTTP address is forbidden")


def _validate_args(name: str, args: list[str]) -> None:
    if any("\x00" in item for item in args):
        raise ValueError(f"MCP server {name} args cannot contain NUL bytes")


def _validate_environment(name: str, env: Mapping[str, str], refs: Mapping[str, str]) -> None:
    overlap = set(env).intersection(refs)
    if overlap:
        raise ValueError(f"MCP server {name} env and env_refs overlap")
    for env_name, value in env.items():
        _validate_env_name(name, env_name)
        if _is_sensitive_name(env_name):
            raise ValueError(
                f"MCP server {name} sensitive environment values must use env_refs"
            )
        _validate_scalar(name, value, kind="environment value")
    for env_name, secret_ref in refs.items():
        _validate_env_name(name, env_name)
        if not secret_ref.strip() or "\r" in secret_ref or "\n" in secret_ref:
            raise ValueError(f"MCP server {name} env secret reference is invalid")


def _validate_env_name(server: str, name: str) -> None:
    upper = name.upper()
    if not _ENV_NAME.fullmatch(name) or any(upper.startswith(item) for item in _DANGEROUS_ENV_PREFIXES):
        raise ValueError(f"MCP server {server} environment name is forbidden: {name}")


def _validate_headers(name: str, headers: Mapping[str, str], refs: Mapping[str, str]) -> None:
    lower_literals = {item.lower() for item in headers}
    lower_refs = {item.lower() for item in refs}
    if len(lower_literals) != len(headers) or len(lower_refs) != len(refs):
        raise ValueError(f"MCP server {name} has duplicate case-insensitive headers")
    if lower_literals.intersection(lower_refs):
        raise ValueError(f"MCP server {name} headers and header_refs overlap")
    for header, value in headers.items():
        _validate_header_name(name, header)
        if _is_sensitive_name(header):
            raise ValueError(f"MCP server {name} credential headers must use header_refs")
        _validate_scalar(name, value, kind="header value")
    for header, secret_ref in refs.items():
        _validate_header_name(name, header)
        if not secret_ref.strip() or "\r" in secret_ref or "\n" in secret_ref:
            raise ValueError(f"MCP server {name} header secret reference is invalid")


def _validate_header_name(server: str, header: str) -> None:
    if not _HEADER_NAME.fullmatch(header) or header.lower() in _RESERVED_HEADERS:
        raise ValueError(f"MCP server {server} header name is forbidden: {header}")


def _validate_scalar(server: str, value: str, *, kind: str) -> None:
    if "\x00" in value or "\r" in value or "\n" in value:
        raise ValueError(f"MCP server {server} {kind} contains control characters")


def _is_sensitive_name(name: str) -> bool:
    upper = name.upper().replace("-", "_")
    return any(token in upper for token in _SENSITIVE_NAMES)


def _mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _string_map(value: Any, *, label: str) -> dict[str, str]:
    mapping = _mapping(value, label=label)
    if not all(isinstance(key, str) and isinstance(item, str) for key, item in mapping.items()):
        raise ValueError(f"{label} must be a string map")
    return dict(mapping)


def _string_list(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a list of strings")
    return list(value)


def _optional_string(value: Any, *, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"MCP server {label} must be a string")
    return value


def _boolean(value: Any, *, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{label} must be finite")
    return converted


def _integer(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value
