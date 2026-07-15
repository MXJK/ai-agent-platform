from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


def load_mcp_server_configs(path: Path | str) -> list[MCPServerConfig]:
    config_path = Path(path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    servers = raw.get("mcp_servers", raw)

    if isinstance(servers, dict):
        return [
            _server_config_from_dict(name, payload)
            for name, payload in servers.items()
            if payload.get("enabled", True)
        ]
    if isinstance(servers, list):
        return [
            _server_config_from_dict(str(payload["name"]), payload)
            for payload in servers
            if payload.get("enabled", True)
        ]
    raise ValueError("MCP config must contain an object or list of server configs")


def _server_config_from_dict(name: str, payload: dict[str, Any]) -> MCPServerConfig:
    args = payload.get("args", [])
    env = payload.get("env", {})
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise ValueError(f"MCP server {name} args must be a list of strings")
    if not isinstance(env, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in env.items()
    ):
        raise ValueError(f"MCP server {name} env must be a string map")
    return MCPServerConfig(
        name=name,
        transport=str(payload.get("transport", "stdio")),
        command=payload.get("command"),
        args=args,
        env=env,
        enabled=bool(payload.get("enabled", True)),
    )
