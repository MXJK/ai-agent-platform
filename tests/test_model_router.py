from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations import (
    LLMClient,
    LLMProviderError,
    LLMStreamEvent,
    LLMToolDecision,
    ModelCapabilities,
    ModelConfig,
    ModelRouter,
    ProviderHealthManager,
    RoutingRequirements,
)
from ai_agent_platform.main import create_app


def _model(
    provider: str,
    model: str,
    *,
    quality: float,
    cost: float,
    latency: int,
    context: int = 128000,
    tools: bool = True,
    structured: bool = True,
) -> ModelConfig:
    return ModelConfig(
        provider=provider,
        model=model,
        context_window_tokens=context,
        capabilities=ModelCapabilities(
            tool_calling=tools,
            structured_output=structured,
        ),
        input_cost_per_million=cost,
        output_cost_per_million=cost,
        quality_score=quality,
        latency_ms=latency,
    )


@dataclass
class _StreamScript:
    events: list[LLMStreamEvent]
    error_after: LLMProviderError | None = None


class ScriptedFakeProvider:
    def __init__(self, scripts: list[_StreamScript] | None = None) -> None:
        self.scripts = deque(scripts or [])
        self.stream_calls: list[str] = []
        self.tool_error: LLMProviderError | None = None

    def stream_chat(self, messages, *, model, thinking_level):
        self.stream_calls.append(model)
        script = self.scripts.popleft() if self.scripts else _success_script(model)
        for event in script.events:
            yield event
        if script.error_after is not None:
            raise script.error_after

    def decide_tools(self, messages, tools, *, model):
        if self.tool_error is not None:
            raise self.tool_error
        return LLMToolDecision(
            text=f"{model} selected no tools",
            tool_calls=[],
            model=model,
            provider="fake",
            stop_reason="end_turn",
        )


class RecordingUsageLedger:
    def __init__(self) -> None:
        self.authorizations: list[dict[str, object]] = []
        self.records: list[dict[str, object]] = []

    def authorize(self, **kwargs):
        self.authorizations.append(dict(kwargs))
        return SimpleNamespace(
            provider=kwargs["requested_provider"],
            model=kwargs["requested_model"],
            max_output_tokens=kwargs["max_output_tokens"],
            budget_decision="allowed",
            budget_reason=None,
        )

    def record(self, **kwargs):
        self.records.append(dict(kwargs))


def _success_script(model: str = "model") -> _StreamScript:
    return _StreamScript(
        events=[
            LLMStreamEvent(type="delta", text=f"reply from {model}"),
            LLMStreamEvent(type="done"),
        ]
    )


class ModelPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.models = [
            _model(
                "quality_provider",
                "quality-model",
                quality=0.95,
                cost=8.0,
                latency=900,
            ),
            _model(
                "cost_provider",
                "cost-model",
                quality=0.65,
                cost=0.2,
                latency=400,
            ),
            _model(
                "fast_provider",
                "fast-model",
                quality=0.75,
                cost=1.5,
                latency=80,
            ),
        ]
        self.router = ModelRouter(self.models)
        self.requirements = RoutingRequirements(
            tool_calling=True,
            structured_output=True,
            min_context_tokens=32000,
            estimated_input_tokens=4000,
            expected_output_tokens=1000,
        )

    def test_quality_cost_and_latency_policies_rank_deterministically(self) -> None:
        quality = self.router.route(self.requirements, policy="quality")
        cost = self.router.route(self.requirements, policy="cost")
        latency = self.router.route(self.requirements, policy="latency")

        self.assertEqual(quality.candidates[0].model, "quality-model")
        self.assertEqual(cost.candidates[0].model, "cost-model")
        self.assertEqual(latency.candidates[0].model, "fast-model")
        self.assertIn("quality_score=0.950", quality.trace.selection_reason or "")

    def test_capability_and_context_filters_are_explained_in_trace(self) -> None:
        router = ModelRouter(
            [
                _model(
                    "limited",
                    "limited-model",
                    quality=1.0,
                    cost=0.0,
                    latency=10,
                    context=8000,
                    tools=False,
                    structured=False,
                ),
                self.models[1],
            ]
        )

        plan = router.route(self.requirements)

        self.assertEqual(plan.candidates[0].model, "cost-model")
        limited = plan.trace.candidates[0]
        self.assertFalse(limited.eligible)
        self.assertEqual(
            limited.rejection_reasons,
            [
                "tool_calling_not_supported",
                "structured_output_not_supported",
                "context_window_too_small",
            ],
        )


class ModelFallbackAndCircuitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = [100.0]
        health = ProviderHealthManager(
            failure_threshold=2,
            recovery_timeout_seconds=10.0,
            error_window_size=4,
            error_rate_min_requests=2,
            error_rate_threshold=0.5,
            clock=lambda: self.now[0],
        )
        self.primary_model = _model(
            "primary_fake",
            "primary-model",
            quality=0.95,
            cost=2.0,
            latency=200,
        )
        self.backup_model = _model(
            "backup_fake",
            "backup-model",
            quality=0.7,
            cost=0.5,
            latency=300,
        )
        self.router = ModelRouter(
            [self.primary_model, self.backup_model],
            health=health,
        )

    def client(
        self,
        primary: ScriptedFakeProvider,
        backup: ScriptedFakeProvider,
    ) -> LLMClient:
        return LLMClient(
            Settings(
                llm_max_retries=0,
            ),
            model_router=self.router,
            provider_adapters={
                "primary_fake": primary,
                "backup_fake": backup,
            },
        )

    def test_rate_limit_before_first_delta_falls_back_across_provider(self) -> None:
        primary = ScriptedFakeProvider(
            [
                _StreamScript(
                    events=[],
                    error_after=LLMProviderError(
                        "rate limited",
                        retryable=True,
                        code="rate_limit",
                    ),
                )
            ]
        )
        backup = ScriptedFakeProvider([_success_script("backup-model")])

        client = self.client(primary, backup)
        with patch.object(
            client,
            "_count_input_tokens",
            wraps=client._count_input_tokens,
        ) as count_tokens:
            events = list(client.stream_chat(_messages()))

        self.assertEqual([event.type for event in events], ["route", "delta", "done"])
        route = events[0].route_trace or {}
        self.assertEqual(route["failures"][0]["code"], "rate_limit")
        self.assertEqual(
            route["final_model"],
            {"provider": "backup_fake", "model": "backup-model"},
        )
        self.assertEqual(primary.stream_calls, ["primary-model"])
        self.assertEqual(backup.stream_calls, ["backup-model"])
        self.assertEqual(
            [call.kwargs["provider"] for call in count_tokens.call_args_list],
            ["primary_fake", "backup_fake"],
        )

    def test_runtime_unavailable_catalog_model_is_filtered_before_provider_call(self) -> None:
        primary = ScriptedFakeProvider([_success_script("primary-model")])
        backup = ScriptedFakeProvider([_success_script("backup-model")])
        client = LLMClient(
            Settings(llm_max_retries=0),
            model_router=self.router,
            provider_adapters={
                "primary_fake": primary,
                "backup_fake": backup,
            },
            model_access_resolver=lambda provider, model: (
                provider == "backup_fake" and model == "backup-model"
            ),
        )

        events = list(client.stream_chat(_messages()))

        self.assertEqual(events[0].provider, "backup_fake")
        primary_trace = next(
            item
            for item in (events[0].route_trace or {})["candidates"]
            if item["provider"] == "primary_fake"
        )
        self.assertIn("model_unavailable", primary_trace["rejection_reasons"])
        self.assertEqual(primary.stream_calls, [])

    def test_fallback_authorizes_each_attempt_and_records_only_actual_usage(self) -> None:
        primary = ScriptedFakeProvider(
            [
                _StreamScript(
                    events=[],
                    error_after=LLMProviderError(
                        "rate limited",
                        retryable=True,
                        code="rate_limit",
                    ),
                )
            ]
        )
        backup = ScriptedFakeProvider([_success_script("backup-model")])
        ledger = RecordingUsageLedger()
        client = self.client(primary, backup)
        client.set_usage_ledger(ledger)

        list(client.stream_chat(_messages()))

        self.assertEqual(
            [item["requested_provider"] for item in ledger.authorizations],
            ["primary_fake", "backup_fake"],
        )
        self.assertEqual(len(ledger.records), 1)
        self.assertEqual(ledger.records[0]["provider"], "backup_fake")

    def test_partial_failure_records_usage_once_without_backup_replay(self) -> None:
        primary = ScriptedFakeProvider(
            [
                _StreamScript(
                    events=[LLMStreamEvent(type="delta", text="partial")],
                    error_after=LLMProviderError(
                        "stream disconnected",
                        retryable=True,
                        code="llm_transport_error",
                    ),
                )
            ]
        )
        backup = ScriptedFakeProvider([_success_script("backup-model")])
        ledger = RecordingUsageLedger()
        client = self.client(primary, backup)
        client.set_usage_ledger(ledger)

        with self.assertRaises(LLMProviderError):
            list(client.stream_chat(_messages()))

        self.assertEqual(len(ledger.records), 1)
        self.assertEqual(ledger.records[0]["provider"], "primary_fake")
        self.assertEqual(backup.stream_calls, [])

    def test_budget_downgrade_target_is_revalidated_for_capabilities(self) -> None:
        primary_model = _model(
            "google",
            "primary-model",
            quality=0.9,
            cost=2.0,
            latency=200,
            structured=True,
        )
        fallback_model = _model(
            "openai",
            "cheap-model",
            quality=0.2,
            cost=0.1,
            latency=500,
            structured=False,
        )
        primary = ScriptedFakeProvider()
        fallback = ScriptedFakeProvider()

        class AlwaysDowngradeLedger:
            def authorize(self, **kwargs):
                return SimpleNamespace(
                    provider="openai",
                    model="cheap-model",
                    max_output_tokens=kwargs["max_output_tokens"],
                    budget_decision="downgraded",
                    budget_reason="test budget exceeded",
                )

            def record(self, **kwargs):
                raise AssertionError("ineligible fallback must not be called")

        client = LLMClient(
            Settings(
                llm_provider="google",
                llm_model="primary-model",
                token_budget_fallback_provider="openai",
                token_budget_fallback_model="cheap-model",
            ),
            AlwaysDowngradeLedger(),
            model_router=ModelRouter([primary_model, fallback_model]),
            provider_adapters={"google": primary, "openai": fallback},
        )

        with self.assertRaises(LLMProviderError) as raised:
            client.prepare_chat_request(
                _messages(),
                structured_output=True,
            )

        self.assertEqual(raised.exception.code, "budget_fallback_ineligible")
        self.assertEqual(primary.stream_calls, [])
        self.assertEqual(fallback.stream_calls, [])

    def test_chat_sse_exposes_complete_route_trace(self) -> None:
        primary = ScriptedFakeProvider(
            [
                _StreamScript(
                    events=[],
                    error_after=LLMProviderError(
                        "rate limited",
                        retryable=True,
                        code="rate_limit",
                    ),
                )
            ]
        )
        backup = ScriptedFakeProvider([_success_script("backup-model")])
        settings = Settings(llm_max_retries=0)
        llm_client = self.client(primary, backup)

        with TestClient(
            create_app(settings=settings, llm_client=llm_client)
        ) as api:
            session_id = api.post(
                "/api/v1/sessions",
                json={"user_id": "router-test"},
            ).json()["id"]
            response = api.post(
                "/api/v1/chat/stream",
                json={
                    "conversation_id": session_id,
                    "message": "route this",
                    "routing_policy": "quality",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: route", response.text)
        self.assertIn('"selection_reason"', response.text)
        self.assertIn('"code": "rate_limit"', response.text)
        self.assertIn('"provider": "backup_fake"', response.text)

    def test_timeouts_open_circuit_then_half_open_success_recovers(self) -> None:
        timeout = lambda: LLMProviderError(
            "timed out",
            retryable=True,
            code="llm_timeout",
        )
        primary = ScriptedFakeProvider(
            [
                _StreamScript([], timeout()),
                _StreamScript([], timeout()),
                _success_script("primary-model"),
            ]
        )
        backup = ScriptedFakeProvider()
        client = self.client(primary, backup)

        first = list(client.stream_chat(_messages()))
        second = list(client.stream_chat(_messages()))
        third = list(client.stream_chat(_messages()))

        self.assertEqual(first[0].provider, "backup_fake")
        self.assertEqual(second[0].provider, "backup_fake")
        self.assertEqual(third[0].provider, "backup_fake")
        third_trace = third[0].route_trace or {}
        primary_trace = next(
            item
            for item in third_trace["candidates"]
            if item["provider"] == "primary_fake"
        )
        self.assertIn(
            "provider_circuit_open",
            primary_trace["rejection_reasons"],
        )
        self.assertEqual(
            self.router.health.snapshot("primary_fake").state,
            "open",
        )
        self.assertEqual(len(primary.stream_calls), 2)

        self.now[0] += 11.0
        recovered = list(client.stream_chat(_messages()))

        self.assertEqual(recovered[0].provider, "primary_fake")
        self.assertEqual(
            self.router.health.snapshot("primary_fake").state,
            "closed",
        )
        self.assertEqual(len(primary.stream_calls), 3)

    def test_failure_after_first_delta_does_not_replay_on_backup(self) -> None:
        primary = ScriptedFakeProvider(
            [
                _StreamScript(
                    events=[LLMStreamEvent(type="delta", text="partial")],
                    error_after=LLMProviderError(
                        "stream disconnected",
                        retryable=True,
                        code="llm_transport_error",
                    ),
                )
            ]
        )
        backup = ScriptedFakeProvider([_success_script("backup-model")])
        iterator = iter(self.client(primary, backup).stream_chat(_messages()))

        self.assertEqual(next(iterator).type, "route")
        self.assertEqual(next(iterator).text, "partial")
        with self.assertRaises(LLMProviderError) as raised:
            next(iterator)

        self.assertEqual(backup.stream_calls, [])
        route = raised.exception.route_trace or {}
        self.assertTrue(route["failures"][0]["after_stream_start"])
        self.assertEqual(
            route["final_model"],
            {"provider": "primary_fake", "model": "primary-model"},
        )


def _messages() -> list[dict[str, str]]:
    return [{"role": "user", "content": "hello"}]


if __name__ == "__main__":
    unittest.main()
