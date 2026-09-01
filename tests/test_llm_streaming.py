from __future__ import annotations

import socket
import ssl
import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
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
    OPENAI_CHAT_COMPLETION_ENDPOINTS,
    _retry_after_seconds_from_headers,
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

    def count_tokens(self, **kwargs: object) -> object:
        self.owner.count_kwargs = kwargs
        return SimpleNamespace(total_tokens=12)


class _FakeClient:
    chunks: list[object] = []
    error: BaseException | None = None
    instances: list["_FakeClient"] = []

    def __init__(self, **kwargs: object) -> None:
        self.client_kwargs = kwargs
        self.count_kwargs: dict[str, object] = {}
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
    types_module.CountTokensConfig = _ValueObject
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
                llm_max_output_tokens=4096,
                llm_thinking_level="low",
                llm_timeout_seconds=7.5,
            ),
            credential_resolver=lambda provider: (
                "test-key" if provider == "google" else None
            ),
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

        fake_client = _FakeClient.instances[-1]
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
                max_output_tokens=4096,
            )
            with self.assertRaises(LLMProviderError) as raised:
                next(iter(iterator))

        self.assertEqual(raised.exception.code, "llm_read_timeout")
        self.assertTrue(raised.exception.retryable)
        self.assertTrue(_FakeClient.instances[0].closed)

    def test_google_wrapped_httpx_error_keeps_specific_classification(self) -> None:
        request = httpx.Request("POST", "https://provider.example/v1/messages")
        connect_error = httpx.ConnectError(
            "sensitive dns host",
            request=request,
        )
        connect_error.__cause__ = socket.gaierror(-2, "sensitive dns host")
        wrapped_error = RuntimeError("provider sdk request failed")
        wrapped_error.__cause__ = connect_error
        _FakeClient.error = wrapped_error

        with patch.dict(sys.modules, _fake_google_modules()):
            iterator = self.client()._stream_google(
                [{"role": "user", "content": "hello"}],
                "gemini-3.5-flash",
                max_output_tokens=4096,
            )
            with self.assertRaises(LLMProviderError) as raised:
                next(iter(iterator))

        self.assertEqual(raised.exception.code, "llm_dns_error")
        self.assertEqual(str(raised.exception), "llm provider DNS resolution failed")
        self.assertNotIn("sensitive", str(raised.exception))

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

    def test_complete_stream_reports_answer_deltas_before_returning(self) -> None:
        client = LLMClient(
            Settings(llm_provider="fake", llm_model="fake-agent-model")
        )
        deltas: list[str] = []

        response = client.complete_stream(
            "stream this answer",
            on_delta=deltas.append,
            delta_batch_chars=1,
        )

        self.assertTrue(deltas)
        self.assertEqual("".join(deltas), response.text)


class OpenAIStreamingTests(unittest.TestCase):
    def test_stream_payload_passes_configured_max_output_tokens(self) -> None:
        client = LLMClient(
            Settings(
                llm_provider="openai",
                llm_model="gpt-test",
                llm_max_output_tokens=777,
            ),
            credential_resolver=lambda provider: (
                "test-key" if provider == "openai" else None
            ),
        )
        with patch.object(
            client,
            "_stream_http_sse",
            return_value=iter([LLMStreamEvent(type="done")]),
        ) as stream:
            events = list(
                client._stream_openai(
                    [{"role": "user", "content": "hello"}],
                    "gpt-test",
                    max_output_tokens=777,
                )
            )

        self.assertEqual([event.type for event in events], ["done"])
        self.assertEqual(
            stream.call_args.kwargs["payload"]["max_output_tokens"],
            777,
        )

    def test_preflight_uses_openai_input_token_endpoint(self) -> None:
        client = LLMClient(
            Settings(
                llm_provider="openai",
                llm_model="gpt-test",
            ),
            credential_resolver=lambda provider: (
                "test-key" if provider == "openai" else None
            ),
        )
        messages = [
            {"role": "system", "content": "system policy"},
            {"role": "user", "content": "final prompt"},
        ]
        with patch.object(
            client,
            "_post_json",
            return_value={"input_tokens": 123},
        ) as post:
            plan = client.prepare_chat_request(messages)

        self.assertEqual(plan.input_tokens, 123)
        self.assertEqual(
            plan.input_count_method,
            "openai_responses_input_tokens",
        )
        self.assertTrue(post.call_args.args[0].endswith("/responses/input_tokens"))
        self.assertEqual(post.call_args.kwargs["payload"]["input"], messages)

    def test_request_override_must_be_registered_in_runtime_catalog(self) -> None:
        client = LLMClient(
            Settings(
                llm_provider="fake",
                llm_model="fake-primary",
            )
        )

        with self.assertRaises(LLMProviderError) as raised:
            client.prepare_chat_request(
                [{"role": "user", "content": "hello"}],
                provider="openai",
                model="gpt-not-approved",
            )

        self.assertEqual(raised.exception.code, "llm_provider_not_allowed")


class ChatCompletionsStreamingTests(unittest.TestCase):
    def _client(self, provider: str) -> LLMClient:
        return LLMClient(
            Settings(llm_provider=provider, llm_model="glm-4.6"),
            credential_resolver=lambda item: (
                "test-key" if item == provider else None
            ),
        )

    def test_glm_streams_against_bigmodel_endpoint(self) -> None:
        client = self._client("glm")
        with patch.object(
            client,
            "_stream_http_sse",
            return_value=iter([LLMStreamEvent(type="done")]),
        ) as stream:
            events = list(
                client._stream_chat_completions(
                    "glm",
                    [{"role": "user", "content": "你好"}],
                    "glm-4.6",
                    max_output_tokens=4_096,
                )
            )

        self.assertEqual([event.type for event in events], ["done"])
        self.assertEqual(
            stream.call_args.args[0],
            OPENAI_CHAT_COMPLETION_ENDPOINTS["glm"],
        )
        headers = stream.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        payload = stream.call_args.kwargs["payload"]
        self.assertEqual(payload["model"], "glm-4.6")
        self.assertEqual(payload["max_tokens"], 4_096)
        self.assertTrue(payload["stream"])
        # stream_options stays DeepSeek-specific until every domestic
        # provider is confirmed to accept it.
        self.assertNotIn("stream_options", payload)

    def test_minimax_stream_separates_private_reasoning(self) -> None:
        client = self._client("minimax")
        with patch.object(
            client,
            "_stream_http_sse",
            return_value=iter([LLMStreamEvent(type="done")]),
        ) as stream:
            list(
                client._stream_chat_completions(
                    "minimax",
                    [{"role": "user", "content": "你好"}],
                    "MiniMax-M2",
                    max_output_tokens=4_096,
                )
            )

        payload = stream.call_args.kwargs["payload"]
        self.assertIs(payload["reasoning_split"], True)

    def test_missing_credential_is_reported_before_any_request(self) -> None:
        client = LLMClient(
            Settings(llm_provider="minimax", llm_model="MiniMax-M2"),
            credential_resolver=lambda provider: None,
        )
        with patch.object(client, "_stream_http_sse") as stream:
            with self.assertRaises(LLMProviderError) as raised:
                list(
                    client._stream_chat_completions(
                        "minimax",
                        [{"role": "user", "content": "hi"}],
                        "MiniMax-M2",
                        max_output_tokens=1_024,
                    )
                )

        self.assertIn("minimax credential is not configured", str(raised.exception))
        stream.assert_not_called()


class RetryAfterTests(unittest.TestCase):
    @staticmethod
    def _with_cause(
        error: httpx.RequestError,
        cause: BaseException,
    ) -> httpx.RequestError:
        error.__cause__ = cause
        return error

    @staticmethod
    def _raising_client(error: httpx.RequestError):
        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            @staticmethod
            def post(url, *, headers, json):
                del url, headers, json
                raise error

            @staticmethod
            def stream(method, url, *, headers, json):
                del method, url, headers, json
                raise error

        return FakeClient()

    def test_httpx_failures_have_stable_safe_error_classifications(self) -> None:
        request = httpx.Request("POST", "https://provider.example/v1/messages")
        cases = [
            (
                httpx.ConnectTimeout("sensitive connect timeout", request=request),
                "llm_connect_timeout",
                "llm provider connection timed out",
                True,
            ),
            (
                httpx.ReadTimeout("sensitive read timeout", request=request),
                "llm_read_timeout",
                "llm provider response read timed out",
                True,
            ),
            (
                httpx.WriteTimeout("sensitive write timeout", request=request),
                "llm_write_timeout",
                "llm provider request write timed out",
                True,
            ),
            (
                httpx.PoolTimeout("sensitive pool timeout", request=request),
                "llm_pool_timeout",
                "llm provider connection pool wait timed out",
                True,
            ),
            (
                self._with_cause(
                    httpx.ConnectError("sensitive dns host", request=request),
                    socket.gaierror(-2, "sensitive dns host"),
                ),
                "llm_dns_error",
                "llm provider DNS resolution failed",
                True,
            ),
            (
                self._with_cause(
                    httpx.ConnectError("sensitive certificate", request=request),
                    ssl.SSLCertVerificationError(1, "sensitive certificate"),
                ),
                "llm_tls_certificate_error",
                "llm provider TLS certificate verification failed",
                False,
            ),
            (
                self._with_cause(
                    httpx.ConnectError("sensitive TLS", request=request),
                    ssl.SSLError(1, "sensitive TLS"),
                ),
                "llm_tls_error",
                "llm provider TLS connection failed",
                True,
            ),
            (
                httpx.ConnectError("sensitive address", request=request),
                "llm_connection_error",
                "llm provider connection failed",
                True,
            ),
            (
                httpx.ProxyError("sensitive proxy", request=request),
                "llm_proxy_error",
                "llm provider proxy connection failed",
                True,
            ),
            (
                httpx.ReadError("sensitive response", request=request),
                "llm_read_error",
                "llm provider response read failed",
                True,
            ),
            (
                httpx.WriteError("sensitive request", request=request),
                "llm_write_error",
                "llm provider request write failed",
                True,
            ),
            (
                httpx.CloseError("sensitive close", request=request),
                "llm_close_error",
                "llm provider connection close failed",
                True,
            ),
            (
                httpx.RemoteProtocolError("sensitive remote", request=request),
                "llm_remote_protocol_error",
                "llm provider remote protocol error",
                True,
            ),
            (
                httpx.LocalProtocolError("sensitive local", request=request),
                "llm_local_protocol_error",
                "llm provider local protocol error",
                False,
            ),
            (
                httpx.DecodingError("sensitive encoding", request=request),
                "llm_decoding_error",
                "llm provider response decoding failed",
                True,
            ),
            (
                httpx.TransportError("sensitive transport", request=request),
                "llm_transport_error",
                "llm provider transport failed",
                True,
            ),
        ]

        client = LLMClient(Settings())
        for transport_error, code, message, retryable in cases:
            with self.subTest(code=code):
                with (
                    patch(
                        "ai_agent_platform.integrations.llm.httpx.Client",
                        return_value=self._raising_client(transport_error),
                    ),
                    self.assertRaises(LLMProviderError) as raised,
                ):
                    client._post_json(
                        "https://provider.example/v1/messages",
                        headers={},
                        payload={"messages": []},
                    )

                self.assertEqual(raised.exception.code, code)
                self.assertEqual(str(raised.exception), message)
                self.assertEqual(raised.exception.retryable, retryable)
                self.assertNotIn("sensitive", str(raised.exception))

    def test_json_and_sse_requests_share_transport_classification(self) -> None:
        request = httpx.Request("POST", "https://provider.example/v1/messages")
        transport_error = httpx.RemoteProtocolError(
            "sensitive upstream disconnect",
            request=request,
        )
        client = LLMClient(Settings())

        with (
            patch(
                "ai_agent_platform.integrations.llm.httpx.Client",
                return_value=self._raising_client(transport_error),
            ),
            self.assertRaises(LLMProviderError) as json_error,
        ):
            client._post_json(
                "https://provider.example/v1/messages",
                headers={},
                payload={"messages": []},
            )

        with (
            patch(
                "ai_agent_platform.integrations.llm.httpx.Client",
                return_value=self._raising_client(transport_error),
            ),
            self.assertRaises(LLMProviderError) as stream_error,
        ):
            next(
                iter(
                    client._stream_http_sse(
                        "https://provider.example/v1/messages",
                        headers={},
                        payload={"messages": []},
                        parser=lambda *_args: (),
                    )
                )
            )

        self.assertEqual(json_error.exception.code, "llm_remote_protocol_error")
        self.assertEqual(stream_error.exception.code, json_error.exception.code)
        self.assertEqual(str(stream_error.exception), str(json_error.exception))

    def test_specific_network_retry_policy_falls_back_to_legacy_groups(self) -> None:
        client = LLMClient(
            Settings(
                llm_max_retries=9,
                llm_retry_policy_json=(
                    '{"llm_connection_error":1,"llm_transport_error":3,'
                    '"llm_timeout":2,"default":7}'
                ),
            )
        )

        self.assertEqual(
            client._retry_limit(
                LLMProviderError("connection", code="llm_connection_error")
            ),
            1,
        )
        self.assertEqual(
            client._retry_limit(LLMProviderError("dns", code="llm_dns_error")),
            3,
        )
        self.assertEqual(
            client._retry_limit(
                LLMProviderError("read timeout", code="llm_read_timeout")
            ),
            2,
        )
        self.assertEqual(
            client._retry_limit(LLMProviderError("other", code="other_error")),
            7,
        )

    def test_parses_delta_seconds_and_http_date(self) -> None:
        now = datetime(2026, 8, 25, 2, 0, tzinfo=timezone.utc)
        retry_at = now + timedelta(seconds=12)

        self.assertEqual(
            _retry_after_seconds_from_headers({"retry-after": "1.5"}),
            1.5,
        )
        self.assertEqual(
            _retry_after_seconds_from_headers(
                {"Retry-After": format_datetime(retry_at, usegmt=True)},
                now_seconds=now.timestamp(),
            ),
            12.0,
        )
        self.assertIsNone(
            _retry_after_seconds_from_headers({"retry-after": "invalid"})
        )
        self.assertIsNone(
            _retry_after_seconds_from_headers({"retry-after": "-1"})
        )

    def test_large_retry_number_stays_at_local_backoff_bound(self) -> None:
        client = LLMClient(
            Settings(
                llm_retry_backoff_max_seconds=2.0,
                llm_retry_jitter_seconds=0.0,
            )
        )

        delay, source = client._retry_delay(
            LLMProviderError(
                "transport failed",
                retryable=True,
                code="llm_transport_error",
            ),
            retry_number=10_000,
        )

        self.assertEqual(delay, 2.0)
        self.assertEqual(source, "exponential_backoff")

    def test_http_server_error_carries_retry_after_to_gateway_policy(self) -> None:
        class FakeResponse:
            status_code = 503
            headers = {"Retry-After": "2"}

            @staticmethod
            def json():
                return {"error": {"message": "temporarily unavailable"}}

        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            @staticmethod
            def post(url, *, headers, json):
                del url, headers, json
                return FakeResponse()

        client = LLMClient(Settings())
        with (
            patch(
                "ai_agent_platform.integrations.llm.httpx.Client",
                return_value=FakeClient(),
            ),
            self.assertRaises(LLMProviderError) as raised,
        ):
            client._post_json(
                "https://provider.example/v1/messages",
                headers={},
                payload={"messages": []},
            )

        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.code, "llm_server_error")
        self.assertEqual(raised.exception.retry_after_seconds, 2.0)

    def test_sse_rate_limit_carries_retry_after_to_gateway_policy(self) -> None:
        class FakeResponse:
            status_code = 429
            headers = {"retry-after": "3"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            @staticmethod
            def read():
                return b""

            @staticmethod
            def json():
                return {"error": {"message": "rate limited"}}

        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            @staticmethod
            def stream(method, url, *, headers, json):
                del method, url, headers, json
                return FakeResponse()

        client = LLMClient(Settings())
        with (
            patch(
                "ai_agent_platform.integrations.llm.httpx.Client",
                return_value=FakeClient(),
            ),
            self.assertRaises(LLMProviderError) as raised,
        ):
            next(
                iter(
                    client._stream_http_sse(
                        "https://provider.example/v1/messages",
                        headers={},
                        payload={"messages": []},
                        parser=lambda *_args: (),
                    )
                )
            )

        self.assertEqual(raised.exception.code, "rate_limit")
        self.assertEqual(raised.exception.retry_after_seconds, 3.0)


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
