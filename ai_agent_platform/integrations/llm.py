from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Iterable, Literal

import httpx

from ai_agent_platform.core import Settings


LLMEventType = Literal["delta", "usage", "done"]


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class LLMStreamEvent:
    type: LLMEventType
    text: str = ""
    usage: LLMUsage | None = None


class LLMProviderError(Exception):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMClient:
    """Streams text from Google, OpenAI, Anthropic, or a local fake provider."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def stream_chat(
        self,
        messages: list[dict[str, str]],
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> Iterable[LLMStreamEvent]:
        selected_provider = provider or self._settings.llm_provider
        selected_model = model or self._settings.llm_model
        stream_factory = self._stream_factory(selected_provider, selected_model)

        last_error: LLMProviderError | None = None
        for attempt in range(self._settings.llm_max_retries + 1):
            emitted = False
            try:
                for event in stream_factory(messages):
                    emitted = True
                    yield event
                return
            except LLMProviderError as exc:
                last_error = exc
                if emitted or not exc.retryable or attempt >= self._settings.llm_max_retries:
                    raise
                time.sleep(min(0.2 * (2**attempt), 2.0))

        if last_error is not None:
            raise last_error

    def complete(self, prompt: str) -> LLMResponse:
        text_parts: list[str] = []
        messages = [{"role": "user", "content": prompt}]
        for event in self.stream_chat(messages):
            if event.type == "delta":
                text_parts.append(event.text)
        return LLMResponse(text="".join(text_parts), model=self._settings.llm_model)

    def _stream_factory(self, provider: str, model: str):
        if provider == "fake":
            return lambda messages: self._stream_fake(messages, model)
        if provider == "openai":
            return lambda messages: self._stream_openai(messages, model)
        if provider == "anthropic":
            return lambda messages: self._stream_anthropic(messages, model)
        if provider == "google":
            return lambda messages: self._stream_google(messages, model)
        raise LLMProviderError(f"unsupported llm provider: {provider}")

    def _stream_fake(
        self, messages: list[dict[str, str]], model: str
    ) -> Iterable[LLMStreamEvent]:
        user_text = messages[-1]["content"] if messages else ""
        answer = f"fake model reply to: {user_text}"
        for token in answer.split(" "):
            yield LLMStreamEvent(type="delta", text=f"{token} ")
        yield LLMStreamEvent(
            type="usage",
            usage=LLMUsage(
                input_tokens=_estimate_tokens(_join_message_text(messages)),
                output_tokens=_estimate_tokens(answer),
            ),
        )
        yield LLMStreamEvent(type="done")

    def _stream_openai(
        self, messages: list[dict[str, str]], model: str
    ) -> Iterable[LLMStreamEvent]:
        if not self._settings.openai_api_key:
            raise LLMProviderError("OPENAI_API_KEY is not configured")

        payload = {
            "model": model,
            "input": messages,
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {self._settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        yield from self._stream_http_sse(
            "https://api.openai.com/v1/responses",
            headers=headers,
            payload=payload,
            parser=_parse_openai_event,
        )

    def _stream_anthropic(
        self, messages: list[dict[str, str]], model: str
    ) -> Iterable[LLMStreamEvent]:
        if not self._settings.anthropic_api_key:
            raise LLMProviderError("ANTHROPIC_API_KEY is not configured")

        system_messages = [
            message["content"] for message in messages if message["role"] == "system"
        ]
        chat_messages = [
            message
            for message in messages
            if message["role"] in {"user", "assistant"}
        ]
        payload: dict[str, object] = {
            "model": model,
            "messages": chat_messages,
            "max_tokens": self._settings.llm_max_output_tokens,
            "stream": True,
        }
        if system_messages:
            payload["system"] = "\n\n".join(system_messages)

        headers = {
            "x-api-key": self._settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        yield from self._stream_http_sse(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            payload=payload,
            parser=_parse_anthropic_event,
        )

    def _stream_google(
        self, messages: list[dict[str, str]], model: str
    ) -> Iterable[LLMStreamEvent]:
        if not self._settings.google_api_key:
            raise LLMProviderError("GOOGLE_API_KEY is not configured")

        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise LLMProviderError(
                "google-genai is not installed; run pip install google-genai"
            ) from exc

        system_instruction = _google_system_instruction(messages)
        config_kwargs: dict[str, object] = {
            "max_output_tokens": self._settings.llm_max_output_tokens
        }
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction

        client = genai.Client(api_key=self._settings.google_api_key)
        usage: LLMUsage | None = None
        try:
            stream = client.models.generate_content_stream(
                model=model,
                contents=_google_contents(messages, types),
                config=types.GenerateContentConfig(**config_kwargs),
            )
            for chunk in stream:
                text = getattr(chunk, "text", "")
                if isinstance(text, str) and text:
                    yield LLMStreamEvent(type="delta", text=text)
                chunk_usage = _google_usage(getattr(chunk, "usage_metadata", None))
                if chunk_usage is not None:
                    usage = chunk_usage
        except Exception as exc:
            raise LLMProviderError(str(exc), retryable=True) from exc

        if usage is not None:
            yield LLMStreamEvent(type="usage", usage=usage)
        else:
            yield LLMStreamEvent(
                type="usage",
                usage=LLMUsage(
                    input_tokens=_estimate_tokens(_join_message_text(messages)),
                    output_tokens=0,
                ),
            )
        yield LLMStreamEvent(type="done")

    def _stream_http_sse(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, object],
        parser,
    ) -> Iterable[LLMStreamEvent]:
        timeout = httpx.Timeout(self._settings.llm_timeout_seconds)
        try:
            with httpx.Client(timeout=timeout) as client:
                with client.stream(
                    "POST", url, headers=headers, json=payload
                ) as response:
                    if response.status_code >= 400:
                        retryable = response.status_code in {408, 409, 429} or (
                            response.status_code >= 500
                        )
                        raise LLMProviderError(
                            f"llm provider returned HTTP {response.status_code}",
                            retryable=retryable,
                        )

                    event_name = ""
                    data_lines: list[str] = []
                    for raw_line in response.iter_lines():
                        line = raw_line.strip()
                        if not line:
                            yield from _parse_sse_event(
                                event_name=event_name,
                                data_lines=data_lines,
                                parser=parser,
                            )
                            event_name = ""
                            data_lines = []
                            continue
                        if line.startswith("event:"):
                            event_name = line.removeprefix("event:").strip()
                        elif line.startswith("data:"):
                            data_lines.append(line.removeprefix("data:").strip())
        except httpx.TimeoutException as exc:
            raise LLMProviderError("llm provider request timed out", retryable=True) from exc
        except httpx.TransportError as exc:
            raise LLMProviderError(
                "llm provider network request failed", retryable=True
            ) from exc


def _parse_sse_event(event_name: str, data_lines: list[str], parser):
    if not data_lines:
        return
    data = "\n".join(data_lines)
    if data == "[DONE]":
        yield LLMStreamEvent(type="done")
        return
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise LLMProviderError("llm provider returned malformed SSE JSON") from exc
    yield from parser(event_name, payload)


def _parse_openai_event(
    event_name: str, payload: dict[str, object]
) -> Iterable[LLMStreamEvent]:
    event_type = str(payload.get("type") or event_name)
    if event_type in {"response.output_text.delta", "response.refusal.delta"}:
        delta = payload.get("delta")
        if isinstance(delta, str) and delta:
            yield LLMStreamEvent(type="delta", text=delta)
    elif event_type == "response.completed":
        response = payload.get("response")
        if isinstance(response, dict):
            usage = response.get("usage")
            if isinstance(usage, dict):
                yield _usage_event(
                    usage.get("input_tokens"),
                    usage.get("output_tokens"),
                )
        yield LLMStreamEvent(type="done")
    elif event_type == "error":
        raise LLMProviderError(_error_message(payload), retryable=False)


def _parse_anthropic_event(
    event_name: str, payload: dict[str, object]
) -> Iterable[LLMStreamEvent]:
    event_type = str(payload.get("type") or event_name)
    if event_type == "message_start":
        message = payload.get("message")
        if isinstance(message, dict):
            usage = message.get("usage")
            if isinstance(usage, dict):
                yield _usage_event(
                    usage.get("input_tokens"),
                    usage.get("output_tokens"),
                )
    elif event_type == "content_block_delta":
        delta = payload.get("delta")
        if isinstance(delta, dict) and delta.get("type") == "text_delta":
            text = delta.get("text")
            if isinstance(text, str) and text:
                yield LLMStreamEvent(type="delta", text=text)
    elif event_type == "message_delta":
        usage = payload.get("usage")
        if isinstance(usage, dict):
            yield _usage_event(0, usage.get("output_tokens"))
    elif event_type == "message_stop":
        yield LLMStreamEvent(type="done")
    elif event_type == "error":
        raise LLMProviderError(_error_message(payload), retryable=True)


def _google_contents(messages: list[dict[str, str]], types: Any) -> list[Any]:
    contents = []
    for message in messages:
        role = message["role"]
        if role == "system":
            continue
        contents.append(
            types.Content(
                role=_google_role(role),
                parts=[types.Part.from_text(text=message["content"])],
            )
        )
    return contents


def _google_system_instruction(messages: list[dict[str, str]]) -> str | None:
    system_messages = [
        message["content"] for message in messages if message["role"] == "system"
    ]
    if not system_messages:
        return None
    return "\n\n".join(system_messages)


def _google_role(role: str) -> str:
    return "model" if role == "assistant" else "user"


def _google_usage(usage_metadata: object) -> LLMUsage | None:
    if usage_metadata is None:
        return None
    return LLMUsage(
        input_tokens=_int_attr(usage_metadata, "prompt_token_count"),
        output_tokens=_int_attr(usage_metadata, "candidates_token_count"),
    )


def _usage_event(input_tokens: object, output_tokens: object) -> LLMStreamEvent:
    return LLMStreamEvent(
        type="usage",
        usage=LLMUsage(
            input_tokens=input_tokens if isinstance(input_tokens, int) else 0,
            output_tokens=output_tokens if isinstance(output_tokens, int) else 0,
        ),
    )


def _int_attr(value: object, name: str) -> int:
    attr = getattr(value, name, 0)
    return attr if isinstance(attr, int) else 0


def _error_message(payload: dict[str, object]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return message
    return "llm provider returned an error event"


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _join_message_text(messages: list[dict[str, str]]) -> str:
    return "\n".join(message["content"] for message in messages)
