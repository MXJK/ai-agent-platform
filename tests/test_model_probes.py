from __future__ import annotations

from threading import Event, Thread
import unittest

from fastapi.testclient import TestClient

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations import ModelCapabilities, ModelConfig, ModelRouter
from ai_agent_platform.main import create_app
from ai_agent_platform.model_registry import (
    InMemoryModelRegistryRepository,
    InMemorySecretStore,
    ModelConnectionTestError,
    ModelRegistryService,
)


def _fake_model() -> ModelConfig:
    return ModelConfig(
        provider="fake",
        model="probe-model",
        context_window_tokens=8_192,
        capabilities=ModelCapabilities(),
        latency_ms=700,
    )


def _service(test_connection) -> ModelRegistryService:
    model = _fake_model()
    service = ModelRegistryService(
        InMemoryModelRegistryRepository(),
        InMemorySecretStore(),
        initial_models=[model],
    )
    service.bind_runtime(
        router=ModelRouter([model]),
        catalog_changed=lambda _models: None,
        test_connection=test_connection,
    )
    return service


class ModelProbeServiceTests(unittest.TestCase):
    def test_model_probe_records_separate_fixed_prompt_telemetry(self) -> None:
        calls: list[tuple[str, str]] = []

        def test_connection(provider: str, model: str) -> dict:
            calls.append((provider, model))
            return {
                "provider": provider,
                "model": model,
                "status": "available",
                "elapsed_ms": 321,
            }

        service = _service(test_connection)
        model = service.list_models()[0]

        result = service.test_model_connection(model["id"])
        refreshed = service.list_models()[0]

        self.assertEqual(calls, [("fake", "probe-model")])
        self.assertEqual(result["elapsed_ms"], 321)
        self.assertIsNotNone(result["checked_at"])
        self.assertEqual(refreshed["telemetry"]["sample_count"], 0)
        self.assertEqual(refreshed["telemetry"]["probe"]["sample_count"], 1)
        self.assertEqual(refreshed["telemetry"]["probe"]["latency_p50_ms"], 321)
        self.assertEqual(
            refreshed["routing_metadata"]["latency_source"],
            "backend_prior",
        )
        self.assertEqual(refreshed["status"], "available")

    def test_disabled_model_probe_returns_clear_error_and_records_failure(self) -> None:
        service = _service(lambda _provider, _model: {})
        model = service.list_models()[0]
        service.update_model(model["id"], enabled=False)

        with self.assertRaisesRegex(ModelConnectionTestError, "model is disabled"):
            service.test_model_connection(model["id"])

        probe = service.list_models()[0]["telemetry"]["probe"]
        self.assertEqual(probe["sample_count"], 1)
        self.assertEqual(probe["failure_count"], 1)
        self.assertEqual(probe["last_error"], "model is disabled")

    def test_upstream_probe_failure_is_persisted_without_live_sample(self) -> None:
        def fail(_provider: str, _model: str) -> dict:
            raise RuntimeError("provider timed out")

        service = _service(fail)
        model_id = service.list_models()[0]["id"]

        with self.assertRaisesRegex(ModelConnectionTestError, "provider timed out"):
            service.test_model_connection(model_id)

        telemetry = service.list_models()[0]["telemetry"]
        self.assertEqual(telemetry["sample_count"], 0)
        self.assertEqual(telemetry["probe"]["failure_count"], 1)
        self.assertEqual(telemetry["probe"]["last_error"], "provider timed out")

    def test_missing_provider_credential_is_reported_and_recorded(self) -> None:
        model = ModelConfig(
            provider="openai",
            model="credential-test-model",
            context_window_tokens=8_192,
            capabilities=ModelCapabilities(),
            latency_ms=700,
        )
        service = ModelRegistryService(
            InMemoryModelRegistryRepository(),
            InMemorySecretStore(),
            initial_models=[model],
        )
        service.bind_runtime(
            router=ModelRouter([model]),
            catalog_changed=lambda _models: None,
            test_connection=lambda _provider, _model: {},
        )
        model_id = service.list_models()[0]["id"]

        with self.assertRaisesRegex(ModelConnectionTestError, "API key"):
            service.test_model_connection(model_id)

        probe = service.list_models()[0]["telemetry"]["probe"]
        self.assertEqual(probe["failure_count"], 1)
        self.assertEqual(probe["last_error"], "API key is not configured")

    def test_concurrent_probe_for_same_model_is_rejected(self) -> None:
        entered = Event()
        release = Event()

        def test_connection(provider: str, model: str) -> dict:
            entered.set()
            release.wait(timeout=2)
            return {
                "provider": provider,
                "model": model,
                "status": "available",
                "elapsed_ms": 20,
            }

        service = _service(test_connection)
        model_id = service.list_models()[0]["id"]
        worker = Thread(target=service.test_model_connection, args=(model_id,))
        worker.start()
        self.assertTrue(entered.wait(timeout=1))
        try:
            with self.assertRaisesRegex(
                ModelConnectionTestError,
                "already running",
            ):
                service.test_model_connection(model_id)
        finally:
            release.set()
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())

    def test_periodic_probe_skips_model_with_recent_live_traffic(self) -> None:
        calls: list[str] = []

        def test_connection(provider: str, model: str) -> dict:
            calls.append(model)
            return {
                "provider": provider,
                "model": model,
                "status": "available",
                "elapsed_ms": 10,
            }

        service = _service(test_connection)
        service.record_success(
            "fake",
            "probe-model",
            total_latency_ms=450,
            ttft_ms=80,
        )

        outcomes = service.run_due_model_probes(stale_after_seconds=60)

        self.assertEqual(outcomes, [])
        self.assertEqual(calls, [])
        model = service.list_models()[0]
        self.assertEqual(model["telemetry"]["sample_count"], 1)
        self.assertEqual(model["telemetry"]["probe"]["sample_count"], 0)

    def test_periodic_probe_thread_waits_and_stops_cleanly(self) -> None:
        service = _service(lambda _provider, _model: {})

        service.start_periodic_probes(interval_seconds=60)
        self.assertTrue(service.periodic_probes_running)
        service.close()

        self.assertFalse(service.periodic_probes_running)


class ModelProbeApiTests(unittest.TestCase):
    def test_model_level_probe_endpoint_does_not_change_live_routing_sample(self) -> None:
        settings = Settings(
            llm_provider="fake",
            llm_model="probe-api-model",
            embedding_provider="local",
            model_secret_backend="memory",
        )
        with TestClient(
            create_app(settings=settings),
            client=("127.0.0.1", 50000),
        ) as client:
            model = client.get("/api/v1/model-registry").json()["models"][0]
            response = client.post(
                f"/api/v1/model-registry/models/{model['id']}/test"
            )
            refreshed = client.get("/api/v1/model-registry").json()["models"][0]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["model"], "probe-api-model")
        self.assertIn("checked_at", response.json())
        self.assertEqual(refreshed["telemetry"]["sample_count"], 0)
        self.assertEqual(refreshed["telemetry"]["probe"]["sample_count"], 1)
        self.assertEqual(
            refreshed["routing_metadata"]["latency_source"],
            "backend_prior",
        )


if __name__ == "__main__":
    unittest.main()
