from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations import (
    LLMClient,
    ModelCapabilities,
    ModelConfig,
    ModelRouter,
    RoutingRequirements,
)
from ai_agent_platform.integrations.llm import _parse_deepseek_event
from ai_agent_platform.integrations.tools import ToolSpec
from ai_agent_platform.main import create_app
from ai_agent_platform.model_registry import (
    DiscoveredModel,
    InMemoryModelRegistryRepository,
    InMemorySecretStore,
    ModelDiscoveryError,
    ModelRegistryService,
    ModelSelection,
    PostgresModelRegistryRepository,
)


def _fake_model() -> ModelConfig:
    return ModelConfig(
        provider="fake",
        model="demo-stream-model",
        context_window_tokens=128_000,
        capabilities=ModelCapabilities(
            tool_calling=True,
            structured_output=True,
        ),
        quality_score=0.5,
        latency_ms=10,
    )


def _registered_payload(provider: str = "openai") -> dict:
    return {
        "provider": provider,
        "model": "test-model",
        "display_name": "Test Model",
        "context_window_tokens": 128_000,
        "tool_calling": True,
        "structured_output": True,
        "input_cost_per_million": 0.25,
        "output_cost_per_million": 1.0,
        "quality_score": 0.8,
        "configured_latency_ms": 500,
        "enabled": True,
        "auto_eligible": True,
    }


class ModelRegistryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.secrets = InMemorySecretStore()
        self.service = ModelRegistryService(
            InMemoryModelRegistryRepository(),
            self.secrets,
            initial_models=[_fake_model()],
        )

    def test_provider_credential_is_global_write_only_and_model_is_dynamic(self) -> None:
        connection = self.service.upsert_connection(
            provider="openai",
            display_name="OpenAI",
            api_key="sk-test-secret",
            enabled=True,
        )
        created = self.service.create_model(**_registered_payload())

        self.assertTrue(connection["credential_configured"])
        self.assertNotIn("api_key", connection)
        self.assertEqual(
            self.service.credential_for_provider("openai"),
            "sk-test-secret",
        )
        self.assertTrue(
            any(
                item.provider == "openai" and item.model == "test-model"
                for item in self.service.model_configs()
            )
        )
        self.assertEqual(created["status"], "unknown")

    def test_all_real_provider_credentials_share_the_global_registry(self) -> None:
        for provider in ("openai", "deepseek", "anthropic", "google"):
            with self.subTest(provider=provider):
                connection = self.service.upsert_connection(
                    provider=provider,
                    display_name=provider.title(),
                    api_key=f"{provider}-secret",
                    enabled=True,
                )
                self.assertTrue(connection["credential_configured"])
                self.assertNotIn("api_key", connection)

        configured = {
            item["provider"]
            for item in self.service.list_connections()
            if item["credential_configured"]
        }
        self.assertTrue(
            {"openai", "deepseek", "anthropic", "google"}.issubset(configured)
        )

    def test_manual_preference_fallback_and_agent_snapshot_are_stable(self) -> None:
        self.service.upsert_connection(
            provider="openai",
            display_name="OpenAI",
            api_key="sk-test-secret",
            enabled=True,
        )
        model = self.service.create_model(**_registered_payload())
        self.service.set_preference(
            session_id="session-1",
            mode="manual",
            routing_policy="smart",
            preferred_model_id=model["id"],
            fallback_enabled=True,
        )
        snapshot = self.service.snapshot_run_selection(
            "run-1",
            "session-1",
            self.service.selection_for_session("session-1"),
        )
        self.service.set_preference(
            session_id="session-1",
            mode="auto",
            routing_policy="cost",
            preferred_model_id=None,
            fallback_enabled=True,
        )

        self.assertEqual(snapshot.mode, "manual")
        self.assertEqual(snapshot.preferred_provider, "openai")
        self.assertEqual(snapshot.preferred_model, "test-model")
        self.assertTrue(snapshot.fallback_enabled)
        self.assertEqual(
            self.service.selection_for_run("run-1", "session-1").mode,
            "manual",
        )

    def test_deleted_preferred_model_heals_session_to_auto(self) -> None:
        self.service.upsert_connection(
            provider="openai",
            display_name="OpenAI",
            api_key="sk-test-secret",
            enabled=True,
        )
        model = self.service.create_model(**_registered_payload())
        self.service.set_preference(
            session_id="session-1",
            mode="manual",
            routing_policy="smart",
            preferred_model_id=model["id"],
            fallback_enabled=True,
        )

        self.service.delete_model(model["id"])

        self.assertEqual(self.service.get_preference("session-1").mode, "auto")

        explicit = ModelSelection(
            mode="manual",
            routing_policy="quality",
            preferred_provider="google",
            preferred_model="gemini-3-pro",
            thinking_level="high",
            fallback_enabled=False,
        )
        self.service.snapshot_run_selection(
            "run-explicit",
            "session-1",
            explicit,
        )
        restored = self.service.selection_for_run("run-explicit", "session-1")
        self.assertEqual(restored.preferred_provider, "google")
        self.assertEqual(restored.preferred_model, "gemini-3-pro")
        self.assertEqual(restored.thinking_level, "high")

    def test_passive_telemetry_updates_latency_and_status(self) -> None:
        self.service.upsert_connection(
            provider="openai",
            display_name="OpenAI",
            api_key="sk-test-secret",
            enabled=True,
        )
        model = self.service.create_model(**_registered_payload())
        self.service.record_success(
            "openai",
            "test-model",
            total_latency_ms=420,
            ttft_ms=90,
        )
        self.service.record_success(
            "openai",
            "test-model",
            total_latency_ms=580,
            ttft_ms=110,
        )

        view = next(
            item for item in self.service.list_models() if item["id"] == model["id"]
        )
        self.assertEqual(view["status"], "available")
        self.assertEqual(view["telemetry"]["sample_count"], 2)
        self.assertEqual(view["telemetry"]["total_latency_p50_ms"], 420)
        self.assertEqual(view["telemetry"]["ttft_p95_ms"], 110)


class ModelRegistryApiTests(unittest.TestCase):
    @patch(
        "ai_agent_platform.model_registry.discovery.ProviderModelDiscovery.discover",
        side_effect=ModelDiscoveryError("provider rate-limited model discovery"),
    )
    def test_provider_discovery_returns_sanitized_upstream_error(self, discover) -> None:
        settings = Settings(
            llm_provider="fake",
            llm_model="demo-stream-model",
            embedding_provider="local",
            model_secret_backend="memory",
        )
        with TestClient(create_app(settings=settings)) as client:
            client.put(
                "/api/v1/model-registry/connections/openai",
                json={
                    "display_name": "OpenAI",
                    "api_key": "sk-never-return-this",
                    "enabled": True,
                },
            )
            response = client.get(
                "/api/v1/model-registry/connections/openai/available-models"
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json()["detail"], "provider rate-limited model discovery"
        )
        self.assertNotIn("sk-never-return-this", response.text)
        discover.assert_called_once()

    @patch(
        "ai_agent_platform.model_registry.discovery.ProviderModelDiscovery.discover",
        return_value=(
            DiscoveredModel(
                model="gpt-5-mini",
                display_name="GPT-5 Mini",
                context_window_tokens=400_000,
                tool_calling=True,
                structured_output=True,
            ),
        ),
    )
    def test_provider_discovery_supports_simplified_registration(self, discover) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = Settings(
                llm_provider="fake",
                llm_model="demo-stream-model",
                embedding_provider="local",
                model_secret_backend="memory",
                workspace_allowed_roots=(str(Path(temp_dir).resolve()),),
            )
            with TestClient(create_app(settings=settings)) as client:
                client.put(
                    "/api/v1/model-registry/connections/openai",
                    json={
                        "display_name": "OpenAI",
                        "api_key": "sk-never-return-this",
                        "enabled": True,
                    },
                )
                catalog = client.get(
                    "/api/v1/model-registry/connections/openai/available-models"
                )
                created = client.post(
                    "/api/v1/model-registry/models",
                    json={
                        "provider": "openai",
                        "model": "gpt-5-mini",
                        "enabled": True,
                        "auto_eligible": True,
                    },
                )

        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(catalog.json()["models"][0]["model"], "gpt-5-mini")
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["display_name"], "GPT-5 Mini")
        self.assertEqual(created.json()["context_window_tokens"], 400_000)
        discover.assert_called_once_with("openai", "sk-never-return-this")

    def test_frontend_registration_and_session_preference_are_persisted(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = Settings(
                llm_provider="fake",
                llm_model="demo-stream-model",
                embedding_provider="local",
                model_secret_backend="memory",
                workspace_allowed_roots=(str(Path(temp_dir).resolve()),),
            )
            with TestClient(create_app(settings=settings)) as client:
                session_id = client.post(
                    "/api/v1/sessions",
                    json={"user_id": "local"},
                ).json()["id"]
                saved = client.put(
                    "/api/v1/model-registry/connections/openai",
                    json={
                        "display_name": "OpenAI",
                        "api_key": "sk-never-return-this",
                        "enabled": True,
                    },
                )
                model = client.post(
                    "/api/v1/model-registry/models",
                    json=_registered_payload(),
                ).json()
                preference = client.put(
                    f"/api/v1/sessions/{session_id}/model-preference",
                    json={
                        "mode": "manual",
                        "routing_policy": "smart",
                        "preferred_model_id": model["id"],
                        "fallback_enabled": True,
                    },
                )

                registry = client.get("/api/v1/model-registry").json()
                session = client.get(f"/api/v1/sessions/{session_id}").json()
                frontend = client.get("/").text
                frontend_js = client.get("/static/app.js").text
                frontend_css = client.get("/static/styles.css").text

        self.assertEqual(saved.status_code, 200)
        self.assertNotIn("api_key", saved.json())
        self.assertNotIn("sk-never-return-this", str(registry))
        self.assertEqual(preference.status_code, 200)
        self.assertEqual(preference.json()["preferred_model_id"], model["id"])
        self.assertTrue(preference.json()["fallback_enabled"])
        self.assertEqual(session["provider"], "openai")
        self.assertEqual(session["model"], "test-model")
        self.assertIn('id="auto-model-toggle"', frontend)
        self.assertIn('id="model-picker-trigger"', frontend)
        self.assertIn('id="model-picker-menu"', frontend)
        self.assertIn('data-view-panel="models"', frontend)
        self.assertIn('id="discovered-model-select"', frontend)
        self.assertIn('id="manual-model-id-input"', frontend)
        self.assertNotIn('id="registered-model-quality-input"', frontend)
        self.assertNotIn('id="registered-model-latency-input"', frontend)
        self.assertIn("available-models", frontend_js)
        self.assertIn("if (milliseconds <= 1000)", frontend_js)
        self.assertIn("if (milliseconds <= 3000)", frontend_js)
        self.assertIn("manual-model-mode", frontend_js)
        self.assertIn(".latency-dot.fast", frontend_css)
        self.assertIn(".latency-dot.moderate", frontend_css)
        self.assertIn(".latency-dot.slow", frontend_css)
        self.assertIn(".model-choice-control.manual", frontend_css)
        self.assertIn("max-width: calc(100vw - 32px)", frontend_css)

    def test_chat_preserves_session_manual_fallback_switch(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = Settings(
                llm_provider="fake",
                llm_model="demo-stream-model",
                embedding_provider="local",
                model_secret_backend="memory",
                workspace_allowed_roots=(str(Path(temp_dir).resolve()),),
            )
            with TestClient(create_app(settings=settings)) as client:
                session_id = client.post(
                    "/api/v1/sessions",
                    json={"user_id": "local"},
                ).json()["id"]
                registry = client.get("/api/v1/model-registry").json()
                fake_model = next(
                    item for item in registry["models"] if item["provider"] == "fake"
                )
                client.put(
                    f"/api/v1/sessions/{session_id}/model-preference",
                    json={
                        "mode": "manual",
                        "routing_policy": "smart",
                        "preferred_model_id": fake_model["id"],
                        "fallback_enabled": False,
                    },
                )

                response = client.post(
                    "/api/v1/chat/stream",
                    json={
                        "conversation_id": session_id,
                        "message": "hello",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertIn('"fallback_enabled": false', response.text)

    def test_registry_writes_are_rejected_outside_local_auth_mode(self) -> None:
        settings = Settings(
            llm_provider="fake",
            llm_model="demo-stream-model",
            embedding_provider="local",
            model_secret_backend="memory",
            auth_mode="trusted_header",
            gateway_trust_secret="test-gateway-secret",
        )
        with TestClient(create_app(settings=settings)) as client:
            response = client.put(
                "/api/v1/model-registry/connections/openai",
                json={
                    "display_name": "OpenAI",
                    "api_key": "must-not-be-stored",
                    "enabled": True,
                },
            )

        self.assertEqual(response.status_code, 403)


class PostgresModelRegistryTests(unittest.TestCase):
    def test_agent_run_selection_snapshot_round_trips_all_routing_fields(self) -> None:
        selected = ModelSelection(
            mode="manual",
            routing_policy="quality",
            preferred_model_id="mdl_google",
            preferred_provider="google",
            preferred_model="gemini-3-pro",
            thinking_level="high",
            fallback_enabled=False,
        )
        row = (
            selected.mode,
            selected.routing_policy,
            selected.preferred_model_id,
            selected.preferred_provider,
            selected.preferred_model,
            selected.thinking_level,
            selected.fallback_enabled,
        )
        connection = _FakeConnection([None, row])
        with patch(
            "ai_agent_platform.model_registry.repository._require_psycopg",
            return_value=object(),
        ):
            repository = PostgresModelRegistryRepository(
                database_url="postgresql://test"
            )
            repository._connect = lambda: connection
            repository.upsert_run_selection("run-1", "session-1", selected)
            restored = repository.get_run_selection("run-1")

        self.assertEqual(restored, selected)
        insert_sql, insert_params = connection.calls[0]
        self.assertIn("preferred_provider", insert_sql)
        self.assertIn("thinking_level", insert_sql)
        self.assertEqual(insert_params[5:8], ("google", "gemini-3-pro", "high"))


class DeepSeekProtocolTests(unittest.TestCase):
    def test_openai_compatible_stream_parser_preserves_usage_and_text(self) -> None:
        events = list(
            _parse_deepseek_event(
                "",
                {
                    "choices": [{"delta": {"content": "你好"}}],
                    "usage": {
                        "prompt_tokens": 12,
                        "completion_tokens": 3,
                        "total_tokens": 15,
                    },
                },
            )
        )

        self.assertEqual([item.type for item in events], ["usage", "delta"])
        self.assertEqual(events[0].usage.input_tokens, 12)
        self.assertEqual(events[0].usage.output_tokens, 3)
        self.assertEqual(events[1].text, "你好")

    def test_native_function_call_uses_chat_completions_contract(self) -> None:
        client = LLMClient(
            Settings(
                llm_provider="fake",
                llm_model="demo-stream-model",
                embedding_provider="local",
                deepseek_api_key="deepseek-secret",
            )
        )
        captured = {}

        def fake_post(url, *, headers, payload):
            captured.update(url=url, headers=headers, payload=payload)
            return {
                "model": "deepseek-chat",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path":"README.md"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 5,
                    "total_tokens": 25,
                },
            }

        client._post_json = fake_post
        tool = ToolSpec(
            name="read_file",
            description="Read a repository file",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
            output_schema={"type": "object"},
            provider="local",
        )

        decision = client._decide_deepseek_tools(
            [{"role": "user", "content": "Read the README"}],
            [tool],
            {"read_file": "read_file"},
            "deepseek-chat",
            max_output_tokens=512,
        )

        self.assertEqual(
            captured["url"],
            "https://api.deepseek.com/chat/completions",
        )
        self.assertEqual(captured["payload"]["tool_choice"], "auto")
        self.assertEqual(decision.tool_calls[0].name, "read_file")
        self.assertEqual(decision.tool_calls[0].arguments, {"path": "README.md"})
        self.assertEqual(decision.tool_calls[0].source, "deepseek_native")
        self.assertEqual(decision.usage.total_tokens, 25)


class SmartRoutingTests(unittest.TestCase):
    def test_task_difficulty_changes_quality_cost_tradeoff(self) -> None:
        premium = ModelConfig(
            provider="premium",
            model="reasoning",
            context_window_tokens=128_000,
            capabilities=ModelCapabilities(),
            input_cost_per_million=20,
            output_cost_per_million=20,
            quality_score=1.0,
            latency_ms=2000,
        )
        economical = ModelConfig(
            provider="economical",
            model="fast",
            context_window_tokens=128_000,
            capabilities=ModelCapabilities(),
            input_cost_per_million=0.1,
            output_cost_per_million=0.1,
            quality_score=0.4,
            latency_ms=100,
        )
        router = ModelRouter([premium, economical], default_policy="smart")

        easy = router.route(
            RoutingRequirements(task_complexity="low"),
            policy="smart",
        )
        hard = router.route(
            RoutingRequirements(task_complexity="high"),
            policy="smart",
        )

        self.assertEqual(easy.candidates[0].model, "fast")
        self.assertEqual(hard.candidates[0].model, "reasoning")
        self.assertIn("task_complexity=high", hard.trace.selection_reason or "")


class _FakeCursor:
    def __init__(self, result):
        self._result = result

    def fetchone(self):
        return self._result


class _FakeConnection:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        result = self._results.pop(0) if self._results else None
        return _FakeCursor(result)


if __name__ == "__main__":
    unittest.main()
