from __future__ import annotations

import unittest

from ai_agent_platform.integrations import ModelCapabilities, ModelConfig
from ai_agent_platform.model_registry import (
    DiscoveredModel,
    InMemoryModelRegistryRepository,
    InMemorySecretStore,
    ModelDiscoveryError,
    ModelRegistryService,
    ProviderModelDiscovery,
)


class _StubDiscovery:
    def __init__(self, models: tuple[DiscoveredModel, ...]) -> None:
        self.models = models
        self.calls: list[tuple[str, str]] = []

    def discover(self, provider: str, api_key: str) -> tuple[DiscoveredModel, ...]:
        self.calls.append((provider, api_key))
        return self.models


def _fake_model() -> ModelConfig:
    return ModelConfig(
        provider="fake",
        model="demo-stream-model",
        context_window_tokens=128_000,
        capabilities=ModelCapabilities(),
        latency_ms=10,
    )


class ProviderModelDiscoveryTests(unittest.TestCase):
    def test_openai_discovery_filters_non_chat_models(self) -> None:
        calls = []

        def get_json(url, headers, params):
            calls.append((url, headers, params))
            return {
                "data": [
                    {"id": "gpt-5-mini"},
                    {"id": "gpt-image-1"},
                    {"id": "text-embedding-3-small"},
                    {"id": "whisper-1"},
                ]
            }

        models = ProviderModelDiscovery(json_getter=get_json).discover(
            "openai", "sk-secret"
        )

        self.assertEqual([item.model for item in models], ["gpt-5-mini"])
        self.assertEqual(calls[0][0], "https://api.openai.com/v1/models")
        self.assertEqual(calls[0][1]["Authorization"], "Bearer sk-secret")

    def test_deepseek_anthropic_and_google_catalogs_are_normalized(self) -> None:
        payloads = {
            "api.deepseek.com": {
                "data": [{"id": "deepseek-chat"}, {"id": "other-model"}]
            },
            "api.anthropic.com": {
                "data": [
                    {
                        "id": "claude-sonnet-5",
                        "display_name": "Claude Sonnet 5",
                        "max_input_tokens": 200_000,
                        "capabilities": {
                            "structured_outputs": {"supported": True}
                        },
                    }
                ]
            },
            "generativelanguage.googleapis.com": {
                "models": [
                    {
                        "name": "models/gemini-3.5-flash",
                        "displayName": "Gemini 3.5 Flash",
                        "inputTokenLimit": 1_000_000,
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/gemini-embedding-001",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            },
        }

        def get_json(url, headers, params):
            return next(value for host, value in payloads.items() if host in url)

        discovery = ProviderModelDiscovery(json_getter=get_json)

        deepseek = discovery.discover("deepseek", "secret")
        anthropic = discovery.discover("anthropic", "secret")
        google = discovery.discover("google", "secret")

        self.assertEqual([item.model for item in deepseek], ["deepseek-chat"])
        self.assertEqual(anthropic[0].display_name, "Claude Sonnet 5")
        self.assertEqual(anthropic[0].context_window_tokens, 200_000)
        self.assertTrue(anthropic[0].structured_output)
        self.assertEqual([item.model for item in google], ["gemini-3.5-flash"])
        self.assertEqual(google[0].context_window_tokens, 1_000_000)

    def test_domestic_provider_catalogs_are_normalized(self) -> None:
        payloads = {
            "open.bigmodel.cn": {
                "data": [
                    {"id": "glm-4.6"},
                    {"id": "glm-4.5-air"},
                    {"id": "cogview-4"},
                    {"id": "embedding-3"},
                ]
            },
            "api.minimaxi.com": {
                "data": [
                    {"id": "MiniMax-M2"},
                    {"id": "abab6.5s-chat"},
                    {"id": "speech-02-hd"},
                ]
            },
            "ark.cn-beijing.volces.com": {
                "data": [
                    {"id": "Doubao-Seed-Evolving"},
                    {"id": "doubao-seed-2.1-turbo"},
                    {"id": "doubao-seed-2.0-lite"},
                    {"id": "doubao-seed-1-6-250615"},
                    {"id": "doubao-embedding"},
                ]
            },
        }
        calls = []

        def get_json(url, headers, params):
            calls.append((url, headers))
            return next(value for host, value in payloads.items() if host in url)

        discovery = ProviderModelDiscovery(json_getter=get_json)

        glm = discovery.discover("glm", "secret")
        minimax = discovery.discover("minimax", "secret")
        doubao = discovery.discover("doubao", "secret")

        self.assertEqual([item.model for item in glm], ["glm-4.5-air", "glm-4.6"])
        self.assertEqual(glm[1].display_name, "GLM 4.6")
        self.assertEqual([item.model for item in minimax], ["abab6.5s-chat", "MiniMax-M2"])
        self.assertEqual(minimax[1].display_name, "MiniMax M2")
        self.assertEqual(
            [item.model for item in doubao],
            [
                "doubao-seed-2.0-lite",
                "doubao-seed-2.1-turbo",
                "doubao-seed-evolving",
            ],
        )
        self.assertEqual(doubao[0].display_name, "Doubao-Seed-2.0-lite")
        self.assertEqual(doubao[0].context_window_tokens, 256_000)
        self.assertEqual(doubao[0].max_output_tokens, 128_000)
        self.assertEqual(doubao[2].context_window_tokens, 1_024_000)
        self.assertEqual(doubao[2].max_output_tokens, 256_000)
        self.assertTrue(all(item.tool_calling for item in doubao))
        self.assertTrue(all(item.structured_output for item in doubao))
        for url, headers in calls:
            self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertTrue(
            all(
                url.endswith("/models")
                for url, _ in calls
            )
        )

    def test_unsupported_provider_error_does_not_echo_api_key(self) -> None:
        with self.assertRaises(ModelDiscoveryError) as raised:
            ProviderModelDiscovery().discover("unknown", "never-echo-this")

        self.assertNotIn("never-echo-this", str(raised.exception))


class ModelDiscoveryRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.discovery = _StubDiscovery(
            (
                DiscoveredModel(
                    model="gpt-5-mini",
                    display_name="GPT-5 Mini",
                    context_window_tokens=400_000,
                    tool_calling=True,
                    structured_output=True,
                ),
            )
        )
        self.service = ModelRegistryService(
            InMemoryModelRegistryRepository(),
            InMemorySecretStore(),
            initial_models=[_fake_model()],
            model_discovery=self.discovery,
        )
        self.service.upsert_connection(
            provider="openai",
            display_name="OpenAI",
            api_key="sk-secret",
            enabled=True,
        )

    def test_discovery_and_registration_use_backend_owned_metadata(self) -> None:
        catalog = self.service.discover_models("openai")
        created = self.service.register_model(
            provider="openai",
            model="gpt-5-mini",
        )
        refreshed = self.service.discover_models("openai")

        self.assertEqual(self.discovery.calls[0], ("openai", "sk-secret"))
        self.assertEqual(catalog["models"][0]["display_name"], "GPT-5 Mini")
        self.assertFalse(catalog["models"][0]["already_registered"])
        self.assertEqual(created["display_name"], "GPT-5 Mini")
        self.assertEqual(created["context_window_tokens"], 400_000)
        self.assertGreater(created["input_cost_per_million"], 0)
        self.assertEqual(
            created["routing_metadata"]["latency_source"], "backend_prior"
        )
        self.assertTrue(refreshed["models"][0]["already_registered"])

    def test_observed_latency_replaces_cold_start_prior_in_live_router(self) -> None:
        model = self.service.register_model(
            provider="openai",
            model="gpt-5-mini",
        )
        cold_start = next(
            item
            for item in self.service.model_configs()
            if item.model == "gpt-5-mini"
        )

        self.service.record_success(
            "openai", "gpt-5-mini", total_latency_ms=420, ttft_ms=80
        )
        self.service.record_success(
            "openai", "gpt-5-mini", total_latency_ms=680, ttft_ms=100
        )

        observed = next(
            item
            for item in self.service.model_configs()
            if item.model == "gpt-5-mini"
        )
        view = next(
            item for item in self.service.list_models() if item["id"] == model["id"]
        )
        self.assertNotEqual(cold_start.latency_ms, 420)
        self.assertEqual(observed.latency_ms, 420)
        self.assertEqual(view["routing_metadata"]["routing_latency_ms"], 420)
        self.assertEqual(view["routing_metadata"]["latency_source"], "observed_p50")

    def test_disabled_connection_is_not_discovered(self) -> None:
        self.service.upsert_connection(
            provider="openai",
            display_name="OpenAI",
            api_key=None,
            enabled=False,
        )

        with self.assertRaisesRegex(ValueError, "disabled"):
            self.service.discover_models("openai")

        self.assertEqual(self.discovery.calls, [])

    def test_missing_credential_is_not_discovered(self) -> None:
        discovery = _StubDiscovery(self.discovery.models)
        service = ModelRegistryService(
            InMemoryModelRegistryRepository(),
            InMemorySecretStore(),
            initial_models=[_fake_model()],
            model_discovery=discovery,
        )
        service.upsert_connection(
            provider="openai",
            display_name="OpenAI",
            api_key=None,
            enabled=True,
        )

        with self.assertRaisesRegex(ValueError, "API key"):
            service.discover_models("openai")

        self.assertEqual(discovery.calls, [])


if __name__ == "__main__":
    unittest.main()
