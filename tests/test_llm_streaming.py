from __future__ import annotations

import sys
from threading import Event
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from ai_agent_platform.api.routes.chat import sse_heartbeat, stream_with_heartbeat
from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.llm import (
    LLMClient,
    LLMProviderError,
    LLMStreamEvent,
    collect_llm_usage,
)


class _ValueObject:
    def __init__(self, **values: object) -> None:
        self.__dict__.update(values)


class _Part:
    @classmethod
    def from_text(cls, *, text: str) -> _ValueObject:
        return _ValueObject(text=text)


class _Content(_ValueObject):
    pass


class _ThinkingLevel:
    MINIMAL = "MINIMAL"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class _FakeModels:
    def __init__(self, owner: "_FakeClient") -> None:
        self.owner = owner

    def generate_content_stream(self, **kwargs: object):
        self.owner.generate_kwargs = kwargs
        if self.owner.error is not None:
            raise self.owner.error
        return iter(self.owner.chunks)


class _FakeClient:
    chunks: list[object] = []
    error: BaseException | None = None
    instances: list["_FakeClient"] = []

    def __init__(self, **kwargs: object) -> None:
        self.client_kwargs = kwargs
        self.generate_kwargs: dict[str, object] = {}
        self.closed = False
        self.models = _FakeModels(self)
        self.instances.append(self)

    def close(self) -> None:
        self.closed = True


def _fake_google_modules() -> dict[str, ModuleType]:
    types_module = ModuleType("google.genai.types")
    types_module.Content = _Content
    types_module.Part = _Part
    types_module.ThinkingLevel = _ThinkingLevel
    types_module.ThinkingConfig = _ValueObject
    types_module.GenerateContentConfig = _ValueObject
    types_module.HttpOptions = _ValueObject

    genai_module = ModuleType("google.genai")
    genai_module.Client = _FakeClient
    genai_module.types = types_module

    google_module = ModuleType("google")
    google_module.genai = genai_module
    return {
        "google": google_module,
        "google.genai": genai_module,
        "google.genai.types": types_module,
    }


def _chunk(
    *,
    text: str = "",
    finish_reason: str | None = None,
    input_tokens: int = 12,
    output_tokens: int = 7,
    thoughts_tokens: int = 5,
) -> object:
    usage = SimpleNamespace(
        prompt_token_count=input_tokens,
        candidates_token_count=output_tokens,
        thoughts_token_count=thoughts_tokens,
    )
    candidates = (
        [SimpleNamespace(finish_reason=finish_reason)]
        if finish_reason is not None
        else []
    )
    return SimpleNamespace(text=text, usage_metadata=usage, candidates=candidates)


class GoogleStreamingTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeClient.instances = []
        _FakeClient.error = None

    def client(self) -> LLMClient:
        return LLMClient(
            Settings(
                llm_provider="google",
                llm_model="gemini-3.5-flash",
                google_api_key="test-key",
                llm_max_output_tokens=4096,
                llm_thinking_level="low",
                llm_timeout_seconds=7.5,
            )
        )

    def test_google_stream_applies_thinking_level_timeout_and_usage(self) -> None:
        _FakeClient.chunks = [
            _chunk(text="hello"),
            _chunk(finish_reason="STOP"),
        ]

        with patch.dict(sys.modules, _fake_google_modules()):
            events = list(
                self.client().stream_chat(
                    [{"role": "user", "content": "hello"}],
                    thinking_level="medium",
                )
            )

        self.assertEqual(
            [event.type for event in events],
            ["route", "delta", "usage", "done"],
        )
        self.assertEqual(events[0].provider, "google")
        usage = events[2].usage
        self.assertIsNotNone(usage)
        assert usage is not None
        self.assertEqual(usage.thoughts_tokens, 5)
        self.assertEqual(usage.total_tokens, 24)

        fake_client = _FakeClient.instances[0]
        self.assertEqual(fake_client.client_kwargs["http_options"].timeout, 7500)
        config = fake_client.generate_kwargs["config"]
        self.assertEqual(config.max_output_tokens, 4096)
        self.assertEqual(config.thinking_config.thinking_level, "MEDIUM")
        self.assertTrue(fake_client.closed)

    def test_google_max_tokens_raises_explicit_finish_error_after_usage(self) -> None:
        _FakeClient.chunks = [
            _chunk(text="partial"),
            _chunk(finish_reason="MAX_TOKENS", output_tokens=900, thoughts_tokens=1100),
        ]

        with patch.dict(sys.modules, _fake_google_modules()):
            iterator = iter(
                self.client().stream_chat(
                    [{"role": "user", "content": "long answer"}]
                )
            )
            route_event = next(iterator)
            self.assertEqual(route_event.type, "route")
            self.assertEqual(route_event.model, "gemini-3.5-flash")
            self.assertEqual(next(iterator).type, "delta")
            usage_event = next(iterator)
            self.assertEqual(usage_event.type, "usage")
            with self.assertRaises(LLMProviderError) as raised:
                next(iterator)

        self.assertEqual(raised.exception.code, "max_output_tokens")
        self.assertEqual(raised.exception.finish_reason, "MAX_TOKENS")
        self.assertFalse(raised.exception.retryable)

    def test_google_timeout_is_normalized_and_retryable(self) -> None:
        _FakeClient.error = httpx.ReadTimeout("read timed out")

        with patch.dict(sys.modules, _fake_google_modules()):
            iterator = self.client()._stream_google(
                [{"role": "user", "content": "hello"}],
                "gemini-3.5-flash",
            )
            with self.assertRaises(LLMProviderError) as raised:
                next(iter(iterator))

        self.assertEqual(raised.exception.code, "llm_timeout")
        self.assertTrue(raised.exception.retryable)
        self.assertTrue(_FakeClient.instances[0].closed)

    def test_complete_reports_and_collects_provider_usage(self) -> None:
        client = LLMClient(
            Settings(llm_provider="fake", llm_model="fake-agent-model")
        )

        with collect_llm_usage() as usage:
            first = client.complete("first prompt")
            second = client.complete("second prompt")

        self.assertIsNotNone(first.usage)
        self.assertIsNotNone(second.usage)
        assert first.usage is not None
        assert second.usage is not None
        self.assertEqual(
            usage.input_tokens,
            first.usage.input_tokens + second.usage.input_tokens,
        )
        self.assertEqual(
            usage.output_tokens,
            first.usage.output_tokens + second.usage.output_tokens,
        )
        self.assertEqual(
            usage.total_tokens,
            usage.input_tokens + usage.output_tokens + usage.thoughts_tokens,
        )


class HeartbeatTests(unittest.TestCase):
    def test_idle_stream_emits_sse_comment_heartbeat(self) -> None:
        release = Event()

        def delayed_events():
            release.wait(timeout=0.2)
            yield LLMStreamEvent(type="done")

        iterator = stream_with_heartbeat(
            delayed_events(),
            heartbeat_seconds=0.01,
        )
        self.assertIsNone(next(iterator))
        self.assertEqual(sse_heartbeat(), ": heartbeat\n\n")
        release.set()
        self.assertEqual(next(iterator).type, "done")


if __name__ == "__main__":
    unittest.main()
