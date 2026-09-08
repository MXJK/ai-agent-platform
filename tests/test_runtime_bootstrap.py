from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_agent_platform.core import Settings
from ai_agent_platform.main import create_app
from ai_agent_platform.model_registry import (
    InMemoryModelRegistryRepository,
    InMemorySecretStore,
    ModelRegistryService,
)
from ai_agent_platform.runtime import (
    ApplicationFactory,
    RuntimeContainer,
    build_runtime,
)


class _FakeTaskQueue:
    def __init__(self, events: list[str], name: str = "task_queue") -> None:
        self._events = events
        self._name = name
        self.close_calls = 0

    def submit(self, *_args: object, **_kwargs: object) -> None:
        return None

    def close(self) -> None:
        self.close_calls += 1
        self._events.append(self._name)


class _FakeProvider:
    def __init__(self, name: str, events: list[str]) -> None:
        self.server_name = name
        self._events = events

    def close(self) -> None:
        self._events.append(self.server_name)


class _FakeToolRegistry:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def close(self) -> None:
        self._events.append("tool_registry")


class RuntimeBootstrapTests(unittest.TestCase):
    def settings(self, **overrides: object) -> Settings:
        values = {
            "model_secret_backend": "memory",
            "rag_reranker_provider": "none",
        }
        values.update(overrides)
        return Settings(**values)

    def test_postgres_model_registry_does_not_bootstrap_static_models(self) -> None:
        factory = ApplicationFactory()
        settings = self.settings(
            model_registry_store="postgres",
            llm_provider="google",
            llm_model="configured-but-not-registered",
        )
        llm_client = factory.create_llm_client(settings)

        with patch(
            "ai_agent_platform.runtime.PostgresModelRegistryRepository",
            return_value=InMemoryModelRegistryRepository(),
        ):
            registry = factory.create_model_registry(
                settings,
                llm_client,
                secret_store=InMemorySecretStore(),
            )

        self.assertEqual(registry.list_connections(), [])
        self.assertEqual(registry.list_models(), [])
        self.assertEqual(llm_client.model_router.models, ())

    def test_memory_model_registry_keeps_ephemeral_bootstrap_model(self) -> None:
        factory = ApplicationFactory()
        settings = self.settings(
            model_registry_store="memory",
            llm_provider="fake",
            llm_model="ephemeral-test-model",
        )
        llm_client = factory.create_llm_client(settings)

        registry = factory.create_model_registry(
            settings,
            llm_client,
            secret_store=InMemorySecretStore(),
        )

        self.assertEqual(
            [(item["provider"], item["model"]) for item in registry.list_models()],
            [("fake", "ephemeral-test-model")],
        )

    def test_skill_service_does_not_resurrect_deleted_legacy_user_skills(self) -> None:
        with TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            current_root = home / ".cogent" / "skills"
            legacy_root = home / ".ai-agent-platform" / "skills"
            _write_skill(current_root, "current")
            _write_skill(legacy_root, "deleted")
            settings = self.settings(skills_directory_path=str(current_root))

            with patch("ai_agent_platform.runtime.Path.home", return_value=home):
                service = ApplicationFactory().create_skill_service(
                    settings,
                    tool_registry=SimpleNamespace(list_specs=lambda: ()),
                )

            catalog = service.discover(enabled=True)
            self.assertIsNotNone(catalog.get_skill("current"))
            self.assertIsNone(catalog.get_skill("deleted"))

    def test_periodic_model_probes_start_only_for_api_role(self) -> None:
        factory = ApplicationFactory()
        settings = self.settings(model_probe_interval_seconds=60)

        with patch.object(
            ModelRegistryService,
            "start_periodic_probes",
        ) as start_probes:
            api = factory.build_runtime(settings, role="api")
            cli = factory.build_runtime(settings, role="cli")
            try:
                start_probes.assert_called_once_with(interval_seconds=60)
            finally:
                api.close()
                cli.close()

    def test_build_runtime_supports_the_cli_adapter_role(self) -> None:
        runtime = build_runtime(self.settings(), role="cli")
        try:
            self.assertEqual(runtime.role, "cli")
            self.assertEqual(runtime.execution_context_factory.entrypoint_type, "cli")
            self.assertEqual(
                [item.name for item in runtime.startup_timeline],
                [
                    "config_loaded",
                    "stores_ready",
                    "mcp_ready",
                    "tools_ready",
                    "skills_ready",
                    "agent_ready",
                ],
            )
        finally:
            runtime.close()

    def test_runtime_configuration_rejects_non_atomic_query_store_pair(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "session_repository and agent_run_store must use the same backend",
        ):
            self.settings(
                session_repository="memory",
                agent_run_store="postgres",
            )

    def test_runtime_only_applies_process_tool_cap_to_global_registry(self) -> None:
        registry = ApplicationFactory().create_tool_registry(
            self.settings(enabled_tools=("file_symbol_locator",)),
            mcp_providers=[],
        )
        try:
            names = [spec.name for spec in registry.list_specs()]
            self.assertIn("file_symbol_locator", names)
            self.assertIn("code_explainer", names)
        finally:
            registry.close()

        capped = ApplicationFactory().create_tool_registry(
            self.settings(tool_allowlist=("file_symbol_locator",)),
            mcp_providers=[],
        )
        try:
            self.assertEqual(
                [spec.name for spec in capped.list_specs()],
                ["file_symbol_locator"],
            )
        finally:
            capped.close()

    def test_runtime_container_closes_resources_once_in_reverse_order(self) -> None:
        events: list[str] = []
        container = RuntimeContainer(settings=Settings(), role="api")
        for name in ("first", "second", "third"):
            container.register_cleanup(name, lambda name=name: events.append(name))

        self.assertEqual(container.close(), [])
        self.assertEqual(container.close(), [])

        self.assertTrue(container.closed)
        self.assertEqual(events, ["third", "second", "first"])

    def test_partial_startup_failure_rolls_back_created_resources(self) -> None:
        events: list[str] = []

        class FailingFactory(ApplicationFactory):
            def create_task_queue(self, settings, *, role, metrics):
                return _FakeTaskQueue(events)

            def create_mcp_providers(self, settings):
                return [
                    _FakeProvider("mcp_one", events),
                    _FakeProvider("mcp_two", events),
                ]

            def create_tool_registry(self, settings, *, mcp_providers):
                return _FakeToolRegistry(events)

            def create_cogent_runtime(self, settings, **kwargs):
                raise RuntimeError("Cogent setup failed")

        with self.assertRaisesRegex(RuntimeError, "Cogent setup failed"):
            build_runtime(
                self.settings(),
                role="api",
                factory=FailingFactory(),
            )

        self.assertEqual(
            events,
            ["tool_registry", "mcp_two", "mcp_one", "task_queue"],
        )

    def test_create_app_preserves_existing_test_substitute_injection(self) -> None:
        llm_client = SimpleNamespace()
        rag_service = SimpleNamespace()
        coding_runtime = SimpleNamespace()
        directory_picker = SimpleNamespace()

        app = create_app(
            settings=self.settings(),
            llm_client=llm_client,
            rag_service=rag_service,
            coding_agent_runtime=coding_runtime,
            directory_picker=directory_picker,
        )
        try:
            runtime = app.state.runtime
            self.assertIs(runtime.llm_client, llm_client)
            self.assertIs(runtime.rag_service, rag_service)
            self.assertIs(runtime.coding_agent_runtime, coding_runtime)
            self.assertIs(runtime.directory_picker, directory_picker)
        finally:
            app.state.runtime.close()

def _write_skill(root: Path, name: str) -> None:
    document = root / name / "SKILL.md"
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {name} description\n"
        "---\n"
        "Follow these declarative instructions.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
