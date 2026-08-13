from dataclasses import dataclass
import json
import unittest

from ai_agent_platform.domain import ToolSelectionContext
from ai_agent_platform.integrations import (
    SandboxCapabilities,
    ToolCall,
    ToolCatalog,
    ToolCatalogEntry,
    ToolCatalogError,
    ToolNameConflictError,
    ToolPoolBuildError,
    ToolPoolBuilder,
    ToolPoolRestoreError,
    ToolRegistry,
    ToolSpec,
    ToolUseContext,
)


def _context(
    *,
    role: str = "admin",
    process_denied: tuple[str, ...] = (),
    project_denied: tuple[str, ...] = (),
) -> ToolUseContext:
    return ToolUseContext(
        conversation_id="session_1",
        workspace_id="workspace_1",
        workspace_root="/tmp/workspace_1",
        authorized_workspace_root="/tmp/workspace_1",
        run_id="run_1",
        actor_user_id="alice",
        workspace_role=role,
        process_denied_tools=process_denied,
        project_denied_tools=project_denied,
    )


def _register(
    registry: ToolRegistry,
    name: str,
    *,
    provider: str = "local",
    permission_level: str = "read_only",
    description: str | None = None,
    input_schema: dict | None = None,
) -> ToolSpec:
    registry.register(
        name,
        lambda **_: {"name": name},
        provider=provider,
        permission_level=permission_level,
        requires_approval=permission_level != "read_only",
        description=description or name,
        input_schema=input_schema or {"type": "object"},
    )
    spec = registry.get_spec(name)
    assert spec is not None
    return spec


@dataclass(frozen=True)
class _Skill:
    name: str
    required_tools: tuple[str, ...]
    agents: tuple[str, ...] = ("coding",)
    modes: tuple[str, ...] = ("default",)

    @property
    def qualified_name(self) -> str:
        return f"project:{self.name}"

    def applies_to(self, *, agent: str, mode: str) -> bool:
        return agent in self.agents and mode in self.modes


class ToolCatalogTests(unittest.TestCase):
    def test_namespaces_stable_sort_and_deep_copy(self) -> None:
        registry = ToolRegistry()
        _register(registry, "repo.zeta")
        original = _register(
            registry,
            "repo.alpha",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        )

        catalog = ToolCatalog.from_registry(registry)

        self.assertEqual(
            [entry.name for entry in catalog.entries],
            ["repo.alpha", "repo.zeta"],
        )
        self.assertTrue(catalog.catalog_hash.startswith("sha256:"))
        leaked = catalog.entries[0].spec.input_schema
        leaked["properties"] = {}
        self.assertEqual(
            catalog.get("repo.alpha").spec.input_schema,
            original.input_schema,
        )
        with self.assertRaisesRegex(AttributeError, "immutable"):
            catalog._version = "changed"  # type: ignore[misc]

    def test_mcp_namespace_conflicts_fail_instead_of_overwriting(self) -> None:
        first = ToolCatalogEntry(
            spec=ToolSpec(
                name="mcp.Demo.echo",
                description="first",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                provider="mcp:Demo",
            ),
            namespace="mcp",
            source="mcp",
        )
        second = ToolCatalogEntry(
            spec=ToolSpec(
                name="mcp.demo.echo",
                description="second",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                provider="mcp:demo",
            ),
            namespace="mcp",
            source="mcp",
        )

        with self.assertRaisesRegex(ToolNameConflictError, "conflict"):
            ToolCatalog((first, second))

        with self.assertRaisesRegex(ToolCatalogError, "reserved mcp"):
            ToolCatalog.from_sources(
                local_tools=(
                    ToolSpec(
                        name="mcp.demo.local_claim",
                        description="bad namespace claim",
                        input_schema={"type": "object"},
                        output_schema={"type": "object"},
                        provider="local",
                    ),
                )
            )


class ToolPoolBuilderTests(unittest.TestCase):
    def test_agent_mode_role_model_and_sandbox_filtering(self) -> None:
        registry = ToolRegistry()
        coding_read = _register(registry, "repo.read")
        coding_write = _register(
            registry,
            "sandbox.write",
            provider="sandbox:local",
            permission_level="write_safe",
        )
        business_read = _register(registry, "crm.lookup")
        catalog = ToolCatalog(
            (
                ToolCatalogEntry(
                    spec=coding_read,
                    namespace="repo",
                    source="local",
                    agent_types=("coding",),
                    run_modes=("default",),
                ),
                ToolCatalogEntry(
                    spec=coding_write,
                    namespace="sandbox",
                    source="local",
                    agent_types=("coding",),
                    run_modes=("default",),
                    required_sandbox_capabilities=("available", "writable"),
                ),
                ToolCatalogEntry(
                    spec=business_read,
                    namespace="crm",
                    source="local",
                    agent_types=("business",),
                    run_modes=("review",),
                ),
            )
        )
        builder = ToolPoolBuilder(registry)

        viewer_pool = builder.build(
            catalog=catalog,
            agent_type="coding",
            run_mode="default",
            model_capabilities={"tool_calling": True},
            tool_use_context=_context(role="viewer"),
            sandbox_capabilities=SandboxCapabilities(available=True),
        )
        self.assertEqual(viewer_pool.allowed_names, ("repo.read",))
        self.assertEqual(
            {item.name: item.reason for item in viewer_pool.exclusions},
            {
                "crm.lookup": "agent_type",
                "sandbox.write": "workspace_role",
            },
        )

        wrong_mode = builder.build(
            catalog=catalog,
            agent_type="business",
            run_mode="default",
            model_capabilities={"tool_calling": True},
            tool_use_context=_context(role="viewer"),
        )
        self.assertEqual(wrong_mode.allowed_names, ())
        self.assertEqual(
            {item.name: item.reason for item in wrong_mode.exclusions}[
                "crm.lookup"
            ],
            "run_mode",
        )
        business_review = builder.build(
            catalog=catalog,
            agent_type="business",
            run_mode="review",
            model_capabilities={"tool_calling": True},
            tool_use_context=_context(role="viewer"),
        )
        self.assertEqual(business_review.allowed_names, ("crm.lookup",))

        no_sandbox = builder.build(
            catalog=catalog,
            agent_type="coding",
            run_mode="default",
            model_capabilities={"tool_calling": True},
            tool_use_context=_context(),
            sandbox_capabilities=SandboxCapabilities(available=False),
        )
        self.assertEqual(no_sandbox.allowed_names, ("repo.read",))
        self.assertEqual(
            {item.name: item.reason for item in no_sandbox.exclusions}[
                "sandbox.write"
            ],
            "sandbox_capability",
        )

        no_model_tools = builder.build(
            catalog=catalog,
            agent_type="coding",
            run_mode="default",
            model_capabilities={"tool_calling": False},
            tool_use_context=_context(),
            sandbox_capabilities=SandboxCapabilities(available=False),
        )
        self.assertEqual(no_model_tools.allowed_names, ())

    def test_deny_rules_and_skill_dependencies_are_fail_closed(self) -> None:
        registry = ToolRegistry()
        _register(registry, "repo.read")
        _register(registry, "mcp.demo.search", provider="mcp:demo")
        builder = ToolPoolBuilder(registry)

        pool = builder.build(
            skills=(
                _Skill(
                    name="review",
                    required_tools=("repo.read", "mcp.demo.search"),
                ),
            ),
            agent_type="coding",
            run_mode="default",
            tool_use_context=_context(project_denied=("repo.read",)),
            deny_rules=("mcp.*",),
        )

        self.assertEqual(pool.allowed_names, ())
        self.assertEqual(
            pool.missing_skill_dependencies[0].missing_tools,
            ("mcp.demo.search", "repo.read"),
        )
        with self.assertRaisesRegex(
            ToolPoolBuildError,
            "Skill tool dependencies",
        ):
            builder.build(
                skills=(
                    _Skill(name="review", required_tools=("missing.tool",)),
                ),
                tool_use_context=_context(),
                strict_skill_dependencies=True,
            )

    def test_effective_pool_is_model_visible_boundary(self) -> None:
        registry = ToolRegistry()
        _register(registry, "repo.allowed")
        _register(registry, "repo.hidden")
        pool = ToolPoolBuilder(registry).build(
            tool_use_context=_context(),
            requested_names=("repo.allowed",),
        )

        self.assertEqual(
            [spec.name for spec in pool.list_specs()],
            ["repo.allowed"],
        )
        denied = pool.execute(ToolCall(name="repo.hidden", arguments={}))
        self.assertFalse(denied.ok)
        self.assertEqual(denied.error_code, "permission_denied")
        with self.assertRaisesRegex(AttributeError, "immutable"):
            pool._catalog_hash = "changed"  # type: ignore[misc]


class ToolPoolSnapshotTests(unittest.TestCase):
    def test_snapshot_is_redacted_and_restores_original_set_after_addition(self) -> None:
        registry = ToolRegistry()
        _register(
            registry,
            "mcp.demo.lookup",
            provider="mcp:demo",
            input_schema={
                "type": "object",
                "properties": {
                    "api_key": {
                        "type": "string",
                        "default": "top-secret-token",
                    }
                },
            },
        )
        builder = ToolPoolBuilder(registry)
        pool = builder.build(tool_use_context=_context())
        snapshot = ToolSelectionContext(
            enabled_tools=pool.allowed_names,
            source="test",
            version=pool.pool_version,
            catalog_version=pool.catalog_version,
            catalog_hash=pool.catalog_hash,
            catalog_summary=pool.catalog_summary,
            pool_hash=pool.pool_hash,
            normalized_summary=pool.normalized_summary,
        )
        persisted = json.dumps(snapshot.__dict__)
        self.assertNotIn("top-secret-token", persisted)

        _register(registry, "repo.new_after_snapshot")
        restored = builder.restore(snapshot)
        self.assertEqual(restored.allowed_names, ("mcp.demo.lookup",))

    def test_restore_fails_when_snapshotted_mcp_tool_disappears_or_changes(self) -> None:
        registry = ToolRegistry()
        _register(registry, "mcp.demo.lookup", provider="mcp:demo")
        builder = ToolPoolBuilder(registry)
        pool = builder.build(tool_use_context=_context())
        snapshot = ToolSelectionContext(
            enabled_tools=pool.allowed_names,
            source="test",
            version=pool.pool_version,
            catalog_version=pool.catalog_version,
            catalog_hash=pool.catalog_hash,
            catalog_summary=pool.catalog_summary,
            pool_hash=pool.pool_hash,
            normalized_summary=pool.normalized_summary,
        )

        registry.remove_provider("mcp:demo")
        with self.assertRaisesRegex(ToolPoolRestoreError, "unavailable"):
            builder.restore(snapshot)

        _register(
            registry,
            "mcp.demo.lookup",
            provider="mcp:demo",
            description="changed contract",
        )
        with self.assertRaisesRegex(ToolPoolRestoreError, "changed"):
            builder.restore(snapshot)


if __name__ == "__main__":
    unittest.main()
