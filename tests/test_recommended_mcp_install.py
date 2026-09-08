from scripts.install_recommended_mcp import recommended_servers


def test_recommended_mcp_registry_defaults_are_bounded() -> None:
    servers = recommended_servers()

    assert set(servers) == {"github", "context7", "playwright", "postgres", "qdrant"}

    github = servers["github"]
    assert github["enabled"] is False
    assert github["headers"] == {
        "X-MCP-Toolsets": "repos,pull_requests,issues,actions",
        "X-MCP-Readonly": "true",
        "X-MCP-Lockdown": "true",
    }
    assert github["allowed_hosts"] == ["api.githubcopilot.com"]
    assert github["allow_private_network"] is False

    assert servers["context7"]["enabled"] is True
    assert servers["context7"]["allowed_hosts"] == ["mcp.context7.com"]
    assert servers["context7"]["allow_private_network"] is False

    for name in ("playwright", "postgres", "qdrant"):
        server = servers[name]
        assert server["allow_insecure_http"] is True
        assert server["allow_private_network"] is True
        assert server["allowed_hosts"] == [f"mcp-{name}"]

    assert servers["postgres"]["transport"] == "legacy_sse"
    assert servers["postgres"]["legacy_compatibility"] is True
    assert servers["qdrant"]["enabled"] is False
    assert servers["qdrant"]["url"].endswith("/mcp/")

    proxied = recommended_servers(allow_container_dns_proxy=True)
    assert proxied["github"]["allow_private_network"] is True
    assert proxied["context7"]["allow_private_network"] is True
