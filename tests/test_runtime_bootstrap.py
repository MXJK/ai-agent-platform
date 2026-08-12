from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_agent_platform.core import Settings
from ai_agent_platform.main import create_app
from ai_agent_platform.runtime import (
    ApplicationFactory,
    RuntimeContainer,
    build_runtime,
)
from ai_agent_platform.workers import runtime as worker_runtime


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


class _RecordingFactory(ApplicationFactory):
    def __init__(self) -> None:
        self.roles: list[str] = []
        self.queues: list[_FakeTaskQueue] = []
        self.local_settings = Settings(
            model_secret_backend="memory",
            rag_reranker_provider="none",
        )

    def build_runtime(self, settings: Settings, *, role="api", **kwargs):
        self.roles.append(role)
        return super().build_runtime(settings, role=role, **kwargs)

    def create_task_queue(self, settings, *, role, metrics):
        queue = _FakeTaskQueue([], name=f"{role}_queue")
        self.queues.append(queue)
        return queue

    def create_session_repository(self, settings):
        return super().create_session_repository(self.local_settings)

    def create_agent_run_store(self, settings):
        return super().create_agent_run_store(self.local_settings)

    def create_change_set_store(self, settings):
        return super().create_change_set_store(self.local_settings)

    def create_document_store(self, settings):
        return super().create_document_store(self.local_settings)

    def create_knowledge_base_store(self, settings):
        return super().create_knowledge_base_store(self.local_settings)

    def create_workspace_store(self, settings):
        return super().create_workspace_store(self.local_settings)

    def create_project_memory_service(self, settings, **kwargs):
        return super().create_project_memory_service(
            self.local_settings,
            **kwargs,
        )

    def create_rag_service(self, settings, **kwargs):
        return super().create_rag_service(self.local_settings, **kwargs)

    def create_langgraph_checkpointer(self, settings):
        return None, None


class RuntimeBootstrapTests(unittest.TestCase):
    def settings(self, **overrides: object) -> Settings:
        values = {
            "task_queue_backend": "in_process",
            "model_secret_backend": "memory",
            "rag_reranker_provider": "none",
        }
        values.update(overrides)
        return Settings(**values)

    def distributed_settings(self) -> Settings:
        return self.settings(
            task_queue_backend="celery",
            session_repository="postgres",
            agent_run_store="postgres",
            change_set_store="postgres",
            document_store="postgres",
            workspace_store="postgres",
            langgraph_checkpointer="postgres",
            rag_vector_store="qdrant",
        )

    def test_api_and_worker_use_the_same_application_factory_graph(self) -> None:
        factory = _RecordingFactory()
        settings = self.distributed_settings()
        app = create_app(settings=settings, application_factory=factory)
        worker = worker_runtime._create_worker_services(
            settings=settings,
            application_factory=factory,
        )
        try:
            api = app.state.runtime
            self.assertIs(app.state.startup_timeline, api.startup_timeline)
            self.assertIs(app.state.resolved_config, api.resolved_config)
            self.assertEqual(app.state.config_snapshot, api.config_snapshot)
            self.assertEqual(factory.roles, ["api", "worker"])
            for field_name in (
                "metrics",
                "directory_picker",
                "task_queue",
                "session_repository",
                "agent_run_store",
                "change_set_store",
                "document_store",
                "knowledge_base_store",
                "workspace_store",
                "usage_ledger",
                "llm_client",
                "model_registry",
                "game_agent_runtime",
                "workspace_service",
                "project_memory_service",
                "change_set_service",
                "rag_service",
                "knowledge_base_service",
                "mcp_providers",
                "tool_registry",
                "skill_service",
                "skill_catalog",
                "command_registry",
                "checkpointer",
                "coding_agent_runtime",
                "session_service",
                "agent_run_service",
            ):
                self.assertIs(
                    type(getattr(api, field_name)),
                    type(getattr(worker, field_name)),
                    field_name,
                )
            self.assertIs(api.agent_run_service._runtime, api.coding_agent_runtime)
            self.assertIs(
                worker.agent_run_service._runtime,
                worker.coding_agent_runtime,
            )
            expected_timeline = [
                "config_loaded",
                "stores_ready",
                "mcp_ready",
                "tools_ready",
                "skills_ready",
                "agent_ready",
            ]
            self.assertEqual(
                [item.name for item in api.startup_timeline],
                expected_timeline,
            )
            self.assertEqual(
                [item.name for item in worker.startup_timeline],
                expected_timeline,
            )
        finally:
            app.state.runtime.close()
            worker.close()

    def test_build_runtime_accepts_future_cli_role_without_adding_a_cli(self) -> None:
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

            def create_langgraph_checkpointer(self, settings):
                raise RuntimeError("checkpointer setup failed")

        with self.assertRaisesRegex(RuntimeError, "checkpointer setup failed"):
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
            settings=self.settings(task_queue_backend="in_process"),
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

    def test_worker_services_remain_a_process_local_singleton(self) -> None:
        events: list[str] = []
        container = RuntimeContainer(settings=self.settings(), role="worker")
        container.register_cleanup("worker", lambda: events.append("closed"))
        worker_runtime.close_worker_services()
        try:
            with patch.object(
                worker_runtime,
                "_create_worker_services",
                return_value=container,
            ) as create_services:
                first = worker_runtime.get_worker_services()
                second = worker_runtime.get_worker_services()
                self.assertIs(first, second)
                create_services.assert_called_once_with()
                worker_runtime.close_worker_services()
                worker_runtime.close_worker_services()
        finally:
            worker_runtime.close_worker_services()

        self.assertEqual(events, ["closed"])


if __name__ == "__main__":
    unittest.main()
