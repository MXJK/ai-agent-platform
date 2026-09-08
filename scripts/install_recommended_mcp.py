#!/usr/bin/env python3
"""Register the project's reviewed MCP server defaults with a running Cogent API."""

from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def recommended_servers(
    *, allow_container_dns_proxy: bool = False
) -> dict[str, dict[str, Any]]:
    """Return idempotent registry payloads without embedding any credentials."""

    return {
        "github": {
            "transport": "streamable_http",
            "url": "https://api.githubcopilot.com/mcp/",
            "headers": {
                "X-MCP-Toolsets": "repos,pull_requests,issues,actions",
                "X-MCP-Readonly": "true",
                "X-MCP-Lockdown": "true",
            },
            "allowed_hosts": ["api.githubcopilot.com"],
            "allow_private_network": allow_container_dns_proxy,
            "enabled": False,
            "request_timeout_seconds": 30,
        },
        "context7": {
            "transport": "streamable_http",
            "url": "https://mcp.context7.com/mcp",
            "allowed_hosts": ["mcp.context7.com"],
            "allow_private_network": allow_container_dns_proxy,
            "enabled": True,
            "request_timeout_seconds": 30,
        },
        "playwright": {
            "transport": "streamable_http",
            "url": "http://mcp-playwright:8931/mcp",
            "allowed_hosts": ["mcp-playwright"],
            "allow_insecure_http": True,
            "allow_private_network": True,
            "enabled": True,
            "connect_timeout_seconds": 30,
            "request_timeout_seconds": 120,
        },
        "postgres": {
            "transport": "legacy_sse",
            "url": "http://mcp-postgres:8000/sse",
            "allowed_hosts": ["mcp-postgres"],
            "allow_insecure_http": True,
            "allow_private_network": True,
            "legacy_compatibility": True,
            "enabled": True,
            "connect_timeout_seconds": 30,
            "request_timeout_seconds": 60,
        },
        "qdrant": {
            "transport": "streamable_http",
            "url": "http://mcp-qdrant:8000/mcp/",
            "allowed_hosts": ["mcp-qdrant"],
            "allow_insecure_http": True,
            "allow_private_network": True,
            "enabled": False,
            "connect_timeout_seconds": 30,
            "request_timeout_seconds": 60,
        },
    }


def register_servers(
    api_url: str, *, allow_container_dns_proxy: bool = False
) -> list[dict[str, Any]]:
    base_url = api_url.rstrip("/")
    results: list[dict[str, Any]] = []
    for name, payload in recommended_servers(
        allow_container_dns_proxy=allow_container_dns_proxy
    ).items():
        request = Request(
            f"{base_url}/mcp/servers/{name}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        try:
            with urlopen(request, timeout=150) as response:  # noqa: S310
                results.append(json.load(response))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"failed to register {name}: HTTP {exc.code}: {detail}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(
                f"failed to reach Cogent API while registering {name}: {exc}"
            ) from exc
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Register the five reviewed MCP defaults with a running Cogent API."
    )
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:8000/api/v1",
        help="Cogent API base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--allow-container-dns-proxy",
        action="store_true",
        help=(
            "allow the two exact official remote hosts through a container runtime's "
            "synthetic private DNS range (for example OrbStack 198.18.0.0/15)"
        ),
    )
    args = parser.parse_args()

    for server in register_servers(
        args.api_url,
        allow_container_dns_proxy=args.allow_container_dns_proxy,
    ):
        tools = len(server.get("discovered_tools", []))
        print(f"{server['name']}: state={server['state']} tools={tools}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
