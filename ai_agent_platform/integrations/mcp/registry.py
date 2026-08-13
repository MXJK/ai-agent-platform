from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
from threading import RLock
import tempfile
from typing import Any, Mapping

from ai_agent_platform.integrations.mcp.config import (
    MCPServerConfig,
    load_mcp_server_configs,
)
from ai_agent_platform.integrations.mcp.manager import (
    MCPConnectionManager,
    MCPServerState,
    ManagedMCPClient,
)
from ai_agent_platform.integrations.mcp.provider import MCPToolProvider
from ai_agent_platform.integrations.tools import ToolRegistry
from ai_agent_platform.model_registry.secrets import SecretStore


class MCPRegistryError(RuntimeError):
    pass


class MCPRegistryNotFoundError(MCPRegistryError):
    pass


class MCPRegistryUnavailableError(MCPRegistryError):
    pass


class MCPRegistryService:
    """Persist local-admin MCP registrations and synchronize the live runtime."""

    def __init__(
        self,
        *,
        config_path: str | Path | None,
        secret_store: SecretStore,
        tool_registry: ToolRegistry,
        connection_manager: MCPConnectionManager | None,
        tool_allowlist: tuple[str, ...] | None = None,
    ) -> None:
        self._config_path = Path(config_path).expanduser() if config_path else None
        self._secret_store = secret_store
        self._tool_registry = tool_registry
        self._manager = connection_manager
        self._allowed_tool_names = (
            frozenset(tool_allowlist) if tool_allowlist is not None else None
        )
        self._lock = RLock()

    @property
    def runtime_enabled(self) -> bool:
        return self._manager is not None

    @property
    def config_writable(self) -> bool:
        return self._config_path is not None

    def registry_view(self) -> dict[str, Any]:
        with self._lock:
            configs = self._load_configs()
            return {
                "runtime_enabled": self.runtime_enabled,
                "config_writable": self.config_writable,
                "servers": [self._server_view(config) for config in configs],
            }

    def get_server(self, name: str) -> dict[str, Any]:
        with self._lock:
            return self._server_view(self._config(name))

    def upsert_server(
        self,
        *,
        name: str,
        transport: str,
        command: str | None,
        args: list[str],
        env: dict[str, str],
        env_secret_values: Mapping[str, str],
        remove_env_secrets: list[str],
        url: str | None,
        headers: dict[str, str],
        header_secret_values: Mapping[str, str],
        remove_header_secrets: list[str],
        allowed_hosts: list[str],
        allow_insecure_http: bool,
        allow_private_network: bool,
        legacy_compatibility: bool,
        required: bool,
        enabled: bool,
        connect_timeout_seconds: float,
        request_timeout_seconds: float,
        max_retries: int,
        retry_backoff_seconds: float,
        circuit_failure_threshold: int,
        circuit_reset_seconds: float,
        tool_cache_ttl_seconds: float,
    ) -> dict[str, Any]:
        with self._lock:
            self._require_config_path()
            configs = {config.name: config for config in self._load_configs()}
            existing = configs.get(name)
            env_refs = dict(existing.env_refs) if existing is not None else {}
            header_refs = dict(existing.header_refs) if existing is not None else {}

            for key in remove_env_secrets:
                env_refs.pop(key, None)
            for key in remove_header_secrets:
                header_refs.pop(key, None)
            env_refs.update(
                {key: _secret_ref(name, "env", key) for key in env_secret_values}
            )
            header_refs.update(
                {key: _secret_ref(name, "header", key) for key in header_secret_values}
            )

            config = MCPServerConfig(
                name=name,
                transport=transport,
                command=command,
                args=args,
                env=env,
                env_refs=env_refs,
                url=url,
                headers=headers,
                header_refs=header_refs,
                allowed_hosts=tuple(allowed_hosts),
                allow_insecure_http=allow_insecure_http,
                allow_private_network=allow_private_network,
                legacy_compatibility=legacy_compatibility,
                required=required,
                enabled=enabled,
                connect_timeout_seconds=connect_timeout_seconds,
                request_timeout_seconds=request_timeout_seconds,
                max_retries=max_retries,
                retry_backoff_seconds=retry_backoff_seconds,
                circuit_failure_threshold=circuit_failure_threshold,
                circuit_reset_seconds=circuit_reset_seconds,
                tool_cache_ttl_seconds=tool_cache_ttl_seconds,
            )

            secret_updates = {
                **{env_refs[key]: value for key, value in env_secret_values.items()},
                **{
                    header_refs[key]: value
                    for key, value in header_secret_values.items()
                },
            }
            previous_secret_values = {
                ref: self._secret_store.get(ref) for ref in secret_updates
            }
            try:
                for ref, value in secret_updates.items():
                    self._secret_store.set(ref, value)
                configs[name] = config
                self._write_configs(configs.values())
            except BaseException:
                _restore_secrets(
                    self._secret_store,
                    previous_secret_values,
                )
                raise

            self._delete_removed_secrets(existing, config)
            self._apply_runtime(config)
            return self._server_view(config)

    def set_enabled(self, name: str, *, enabled: bool) -> dict[str, Any]:
        with self._lock:
            configs = {config.name: config for config in self._load_configs()}
            try:
                existing = configs[name]
            except KeyError as exc:
                raise MCPRegistryNotFoundError("MCP server not found") from exc
            config = replace(existing, enabled=enabled)
            configs[name] = config
            self._write_configs(configs.values())
            self._apply_runtime(config)
            return self._server_view(config)

    def refresh_server(self, name: str) -> dict[str, Any]:
        with self._lock:
            config = self._config(name)
            if not config.enabled:
                raise MCPRegistryUnavailableError("MCP server is disabled")
            if self._manager is None:
                raise MCPRegistryUnavailableError(
                    "MCP runtime is disabled; enable MCP and restart the process"
                )
            if self._manager.status_for(name) is None:
                self._manager.upsert_server(config)
            else:
                self._manager.refresh(name)
            self._sync_provider(config)
            return self._server_view(config)

    def delete_server(self, name: str) -> None:
        with self._lock:
            configs = {config.name: config for config in self._load_configs()}
            try:
                existing = configs.pop(name)
            except KeyError as exc:
                raise MCPRegistryNotFoundError("MCP server not found") from exc
            self._write_configs(configs.values())
            self._tool_registry.remove_provider(_provider_name(name))
            if self._manager is not None:
                self._manager.remove_server(name)
            for ref in (*existing.env_refs.values(), *existing.header_refs.values()):
                if _is_managed_secret_ref(ref):
                    self._secret_store.delete(ref)

    def _apply_runtime(self, config: MCPServerConfig) -> None:
        self._tool_registry.remove_provider(_provider_name(config.name))
        if self._manager is None:
            return
        if not config.enabled:
            self._manager.remove_server(config.name)
            return
        status = self._manager.upsert_server(config)
        if status is not None and status.state == MCPServerState.READY:
            self._sync_provider(config)

    def _sync_provider(self, config: MCPServerConfig) -> None:
        self._tool_registry.remove_provider(_provider_name(config.name))
        if self._manager is None:
            return
        status = self._manager.status_for(config.name)
        if status is None or status.state != MCPServerState.READY:
            return
        provider = MCPToolProvider(
            server_name=config.name,
            client=ManagedMCPClient(self._manager, config.name),
        )
        provider.register(
            self._tool_registry,
            allowed_names=self._allowed_tool_names,
        )

    def _server_view(self, config: MCPServerConfig) -> dict[str, Any]:
        status = self._manager.status_for(config.name) if self._manager else None
        if not config.enabled:
            state = "disabled"
        elif self._manager is None:
            state = "restart_required"
        elif status is None:
            state = "unavailable"
        else:
            state = status.state.value
        discovered_tools = (
            [tool.name for tool in self._manager.server_tools(config.name)]
            if self._manager is not None
            else []
        )
        provider = _provider_name(config.name)
        registered_tools = sorted(
            spec.name
            for spec in self._tool_registry.list_specs()
            if spec.provider == provider
        )
        return {
            "name": config.name,
            "transport": config.transport,
            "command": config.command,
            "args": list(config.args),
            "env": dict(config.env),
            "env_secret_names": sorted(config.env_refs),
            "url": config.url,
            "headers": dict(config.headers),
            "header_secret_names": sorted(config.header_refs),
            "allowed_hosts": list(config.allowed_hosts),
            "allow_insecure_http": config.allow_insecure_http,
            "allow_private_network": config.allow_private_network,
            "legacy_compatibility": config.legacy_compatibility,
            "required": config.required,
            "enabled": config.enabled,
            "connect_timeout_seconds": config.connect_timeout_seconds,
            "request_timeout_seconds": config.request_timeout_seconds,
            "max_retries": config.max_retries,
            "retry_backoff_seconds": config.retry_backoff_seconds,
            "circuit_failure_threshold": config.circuit_failure_threshold,
            "circuit_reset_seconds": config.circuit_reset_seconds,
            "tool_cache_ttl_seconds": config.tool_cache_ttl_seconds,
            "endpoint": config.endpoint_label,
            "state": state,
            "protocol_version": status.protocol_version if status else None,
            "last_error_code": status.last_error_code if status else None,
            "retry_count": status.retry_count if status else 0,
            "cache_hit": status.cache_hit if status else False,
            "discovered_tools": discovered_tools,
            "registered_tools": registered_tools,
        }

    def _config(self, name: str) -> MCPServerConfig:
        match = next(
            (config for config in self._load_configs() if config.name == name),
            None,
        )
        if match is None:
            raise MCPRegistryNotFoundError("MCP server not found")
        return match

    def _load_configs(self) -> list[MCPServerConfig]:
        if self._config_path is None or not self._config_path.exists():
            return []
        return load_mcp_server_configs(
            self._config_path,
            include_disabled=True,
        )

    def _write_configs(self, configs: Any) -> None:
        path = self._require_config_path()
        if path.is_symlink():
            raise MCPRegistryError("MCP config path must not be a symbolic link")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = {
            "mcp_servers": {
                config.name: _config_payload(config)
                for config in sorted(configs, key=lambda item: item.name)
            }
        }
        fd, temp_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

    def _require_config_path(self) -> Path:
        if self._config_path is None:
            raise MCPRegistryUnavailableError(
                "MCP_CONFIG_PATH is required for frontend registration"
            )
        return self._config_path

    def _delete_removed_secrets(
        self,
        existing: MCPServerConfig | None,
        current: MCPServerConfig,
    ) -> None:
        if existing is None:
            return
        current_refs = set(current.env_refs.values()) | set(current.header_refs.values())
        previous_refs = set(existing.env_refs.values()) | set(existing.header_refs.values())
        for ref in previous_refs.difference(current_refs):
            if _is_managed_secret_ref(ref):
                self._secret_store.delete(ref)


def _provider_name(server_name: str) -> str:
    return f"mcp:{server_name}"


def _secret_ref(server_name: str, kind: str, name: str) -> str:
    return f"keyring:mcp-server:{server_name}:{kind}:{name.lower()}"


def _is_managed_secret_ref(ref: str) -> bool:
    return ref.startswith("keyring:mcp-server:")


def _restore_secrets(
    secret_store: SecretStore,
    previous_values: Mapping[str, str | None],
) -> None:
    for ref, previous in previous_values.items():
        if previous is None:
            secret_store.delete(ref)
        else:
            secret_store.set(ref, previous)


def _config_payload(config: MCPServerConfig) -> dict[str, Any]:
    return {
        "transport": config.transport,
        "command": config.command,
        "args": list(config.args),
        "env": dict(config.env),
        "env_refs": dict(config.env_refs),
        "url": config.url,
        "headers": dict(config.headers),
        "header_refs": dict(config.header_refs),
        "allowed_hosts": list(config.allowed_hosts),
        "allow_insecure_http": config.allow_insecure_http,
        "allow_private_network": config.allow_private_network,
        "legacy_compatibility": config.legacy_compatibility,
        "required": config.required,
        "enabled": config.enabled,
        "connect_timeout_seconds": config.connect_timeout_seconds,
        "request_timeout_seconds": config.request_timeout_seconds,
        "max_retries": config.max_retries,
        "retry_backoff_seconds": config.retry_backoff_seconds,
        "circuit_failure_threshold": config.circuit_failure_threshold,
        "circuit_reset_seconds": config.circuit_reset_seconds,
        "tool_cache_ttl_seconds": config.tool_cache_ttl_seconds,
    }
