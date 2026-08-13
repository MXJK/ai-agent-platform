from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, SecretStr


MCPTransportName = Literal[
    "stdio",
    "streamable_http",
    "stdio_2025_06_18",
    "legacy_sse",
]


class MCPServerUpsertRequest(BaseModel):
    transport: MCPTransportName = "stdio"
    command: str | None = Field(default=None, max_length=4096)
    args: list[str] = Field(default_factory=list, max_length=64)
    env: dict[str, str] = Field(default_factory=dict, max_length=32)
    env_secrets: dict[str, SecretStr] = Field(default_factory=dict, max_length=16)
    remove_env_secrets: list[str] = Field(default_factory=list, max_length=16)
    url: str | None = Field(default=None, max_length=2048)
    headers: dict[str, str] = Field(default_factory=dict, max_length=32)
    header_secrets: dict[str, SecretStr] = Field(default_factory=dict, max_length=16)
    remove_header_secrets: list[str] = Field(default_factory=list, max_length=16)
    allowed_hosts: list[str] = Field(default_factory=list, max_length=16)
    allow_insecure_http: bool = False
    allow_private_network: bool = False
    legacy_compatibility: bool = False
    required: bool = False
    enabled: bool = True
    connect_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    request_timeout_seconds: float = Field(default=10.0, gt=0, le=900)
    max_retries: int = Field(default=1, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=0.1, ge=0, le=60)
    circuit_failure_threshold: int = Field(default=3, ge=1, le=100)
    circuit_reset_seconds: float = Field(default=30.0, gt=0, le=3600)
    tool_cache_ttl_seconds: float = Field(default=30.0, ge=0, le=86400)


class MCPServerEnabledRequest(BaseModel):
    enabled: bool


class MCPServerResponse(BaseModel):
    name: str
    transport: str
    command: str | None
    args: list[str]
    env: dict[str, str]
    env_secret_names: list[str]
    url: str | None
    headers: dict[str, str]
    header_secret_names: list[str]
    allowed_hosts: list[str]
    allow_insecure_http: bool
    allow_private_network: bool
    legacy_compatibility: bool
    required: bool
    enabled: bool
    connect_timeout_seconds: float
    request_timeout_seconds: float
    max_retries: int
    retry_backoff_seconds: float
    circuit_failure_threshold: int
    circuit_reset_seconds: float
    tool_cache_ttl_seconds: float
    endpoint: str
    state: str
    protocol_version: str | None
    last_error_code: str | None
    retry_count: int
    cache_hit: bool
    discovered_tools: list[str]
    registered_tools: list[str]


class MCPRegistryResponse(BaseModel):
    runtime_enabled: bool
    config_writable: bool
    servers: list[MCPServerResponse]
