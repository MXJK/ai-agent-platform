from __future__ import annotations

from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, replace
import hashlib
import json
import re
import time
from typing import Any, Iterable, Iterator, Literal
from uuid import uuid4

import httpx

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.tools import ToolCall, ToolSpec
from ai_agent_platform.token_counting import estimate_text_tokens
from ai_agent_platform.usage_ledger import (
    TokenBudgetExceededError,
    current_model_usage_context,
)


LLMEventType = Literal["delta", "usage", "done"]


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int
    output_tokens: int
    thoughts_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.thoughts_tokens


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    usage: LLMUsage | None = None


@dataclass(frozen=True)
class LLMRequestPlan:
    requested_provider: str
    requested_model: str
    provider: str
    model: str
    input_tokens: int
    max_output_tokens: int
    input_count_method: str
    budget_decision: str = "allowed"
    budget_reason: str | None = None
    usage_context: Any = None


@dataclass(frozen=True)
class LLMToolDecision:
    text: str
    tool_calls: list[ToolCall]
    model: str
    provider: str
    stop_reason: str
    usage: LLMUsage | None = None
    provider_items: list[dict[str, Any]] | None = None


@dataclass
class LLMUsageAccumulator:
    input_tokens: int = 0
    output_tokens: int = 0
    thoughts_tokens: int = 0

    def add(self, usage: LLMUsage) -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.thoughts_tokens += usage.thoughts_tokens

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.thoughts_tokens


_LLM_USAGE_ACCUMULATOR: ContextVar[LLMUsageAccumulator | None] = ContextVar(
    "llm_usage_accumulator",
    default=None,
)


@contextmanager
def collect_llm_usage() -> Iterator[LLMUsageAccumulator]:
    accumulator = LLMUsageAccumulator()
    token = _LLM_USAGE_ACCUMULATOR.set(accumulator)
    try:
        yield accumulator
    finally:
        _LLM_USAGE_ACCUMULATOR.reset(token)


@dataclass(frozen=True)
class LLMStreamEvent:
    type: LLMEventType
    text: str = ""
    usage: LLMUsage | None = None


class LLMProviderError(Exception):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        code: str = "llm_provider_error",
        finish_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.code = code
        self.finish_reason = finish_reason


class LLMClient:
    """Streams text from Google, OpenAI, Anthropic, or a local fake provider."""

    def __init__(self, settings: Settings, usage_ledger=None) -> None:
        self._settings = settings
        self._usage_ledger = usage_ledger

    def set_usage_ledger(self, usage_ledger) -> None:
        self._usage_ledger = usage_ledger

    @property
    def native_tool_calling_enabled(self) -> bool:
        return self._settings.llm_provider != "fake"

    def decide_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> LLMToolDecision:
        requested_provider = provider or self._settings.llm_provider
        requested_model = model or self._settings.llm_model
        aliases = _tool_aliases(tools)
        plan = self._prepare_tool_request(
            messages,
            tools,
            aliases,
            provider=requested_provider,
            model=requested_model,
        )
        last_error: LLMProviderError | None = None
        for attempt in range(self._settings.llm_max_retries + 1):
            try:
                if plan.provider == "openai":
                    decision = self._decide_openai_tools(
                        messages,
                        tools,
                        aliases,
                        plan.model,
                        max_output_tokens=plan.max_output_tokens,
                    )
                elif plan.provider == "anthropic":
                    decision = self._decide_anthropic_tools(
                        messages,
                        tools,
                        aliases,
                        plan.model,
                        max_output_tokens=plan.max_output_tokens,
                    )
                elif plan.provider == "google":
                    decision = self._decide_google_tools(
                        messages,
                        tools,
                        aliases,
                        plan.model,
                        max_output_tokens=plan.max_output_tokens,
                    )
                elif plan.provider == "fake":
                    decision = self._decide_fake_tools(
                        messages,
                        plan.model,
                    )
                else:
                    raise LLMProviderError(
                        f"unsupported llm provider: {plan.provider}"
                    )
                usage = decision.usage or LLMUsage(
                    input_tokens=plan.input_tokens,
                    output_tokens=0,
                )
                if usage.input_tokens <= 0 or plan.provider == "fake":
                    usage = replace(usage, input_tokens=plan.input_tokens)
                    decision = replace(decision, usage=usage)
                self._record_request_usage(plan, usage)
                accumulator = _LLM_USAGE_ACCUMULATOR.get()
                if accumulator is not None:
                    accumulator.add(usage)
                return decision
            except LLMProviderError as exc:
                last_error = exc
                if not exc.retryable or attempt >= self._settings.llm_max_retries:
                    raise
                time.sleep(min(0.2 * (2**attempt), 2.0))
        assert last_error is not None
        raise last_error

    def stream_chat(
        self,
        messages: list[dict[str, str]],
        *,
        provider: str | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
        request_plan: LLMRequestPlan | None = None,
    ) -> Iterable[LLMStreamEvent]:
        plan = request_plan or self.prepare_chat_request(
            messages,
            provider=provider,
            model=model,
        )
        stream_factory = self._stream_factory(
            plan.provider,
            plan.model,
            thinking_level,
            plan.max_output_tokens,
        )

        last_error: LLMProviderError | None = None
        latest_usage: LLMUsage | None = None
        usage_recorded = False
        for attempt in range(self._settings.llm_max_retries + 1):
            emitted = False
            try:
                for event in stream_factory(messages):
                    emitted = True
                    if event.type == "usage" and event.usage is not None:
                        latest_usage = LLMUsage(
                            input_tokens=(
                                event.usage.input_tokens
                                if event.usage.input_tokens > 0
                                else plan.input_tokens
                            ),
                            output_tokens=event.usage.output_tokens,
                            thoughts_tokens=event.usage.thoughts_tokens,
                        )
                        event = replace(event, usage=latest_usage)
                    if event.type == "done" and not usage_recorded:
                        latest_usage = latest_usage or LLMUsage(
                            input_tokens=plan.input_tokens,
                            output_tokens=0,
                        )
                        self._record_request_usage(plan, latest_usage)
                        accumulator = _LLM_USAGE_ACCUMULATOR.get()
                        if accumulator is not None:
                            accumulator.add(latest_usage)
                        usage_recorded = True
                    yield event
                if not usage_recorded:
                    latest_usage = LLMUsage(
                        input_tokens=(
                            latest_usage.input_tokens
                            if latest_usage is not None
                            else plan.input_tokens
                        ),
                        output_tokens=(
                            latest_usage.output_tokens
                            if latest_usage is not None
                            else 0
                        ),
                        thoughts_tokens=(
                            latest_usage.thoughts_tokens
                            if latest_usage is not None
                            else 0
                        ),
                    )
                    self._record_request_usage(plan, latest_usage)
                    accumulator = _LLM_USAGE_ACCUMULATOR.get()
                    if accumulator is not None:
                        accumulator.add(latest_usage)
                    usage_recorded = True
                return
            except LLMProviderError as exc:
                last_error = exc
                if emitted or not exc.retryable or attempt >= self._settings.llm_max_retries:
                    if emitted and not usage_recorded:
                        partial_usage = latest_usage or LLMUsage(
                            input_tokens=plan.input_tokens,
                            output_tokens=0,
                        )
                        self._record_request_usage(plan, partial_usage)
                        accumulator = _LLM_USAGE_ACCUMULATOR.get()
                        if accumulator is not None:
                            accumulator.add(partial_usage)
                    raise
                time.sleep(min(0.2 * (2**attempt), 2.0))

        if last_error is not None:
            raise last_error

    def prepare_chat_request(
        self,
        messages: list[dict[str, str]],
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> LLMRequestPlan:
        requested_provider = provider or self._settings.llm_provider
        requested_model = model or self._settings.llm_model
        self._require_model_allowed(requested_provider, requested_model)
        input_tokens, count_method = self._count_input_tokens(
            messages,
            provider=requested_provider,
            model=requested_model,
        )
        plan = LLMRequestPlan(
            requested_provider=requested_provider,
            requested_model=requested_model,
            provider=requested_provider,
            model=requested_model,
            input_tokens=input_tokens,
            max_output_tokens=self._settings.llm_max_output_tokens,
            input_count_method=count_method,
            usage_context=current_model_usage_context(),
        )
        if self._usage_ledger is None:
            return plan
        try:
            authorization = self._usage_ledger.authorize(
                requested_provider=requested_provider,
                requested_model=requested_model,
                input_tokens=input_tokens,
                max_output_tokens=self._settings.llm_max_output_tokens,
                input_count_method=count_method,
            )
        except TokenBudgetExceededError as exc:
            raise LLMProviderError(
                str(exc),
                code="token_budget_exceeded",
            ) from exc
        self._require_model_allowed(authorization.provider, authorization.model)
        if authorization.budget_decision == "downgraded":
            input_tokens, count_method = self._count_input_tokens(
                messages,
                provider=authorization.provider,
                model=authorization.model,
            )
        return LLMRequestPlan(
            requested_provider=requested_provider,
            requested_model=requested_model,
            provider=authorization.provider,
            model=authorization.model,
            input_tokens=input_tokens,
            max_output_tokens=authorization.max_output_tokens,
            input_count_method=count_method,
            budget_decision=authorization.budget_decision,
            budget_reason=authorization.budget_reason,
            usage_context=current_model_usage_context(),
        )

    def complete(self, prompt: str) -> LLMResponse:
        text_parts: list[str] = []
        latest_usage: LLMUsage | None = None
        messages = [{"role": "user", "content": prompt}]
        plan = self.prepare_chat_request(messages)
        for event in self.stream_chat(messages, request_plan=plan):
            if event.type == "delta":
                text_parts.append(event.text)
            elif event.type == "usage" and event.usage is not None:
                latest_usage = event.usage
        return LLMResponse(
            text="".join(text_parts),
            model=plan.model,
            usage=latest_usage,
        )

    def _decide_fake_tools(
        self,
        messages: list[dict[str, Any]],
        model: str,
    ) -> LLMToolDecision:
        text = "fake model completed the native tool turn"
        usage = LLMUsage(
            input_tokens=_count_fake_text_tokens(
                _join_any_message_text(messages)
            ),
            output_tokens=_count_fake_text_tokens(text),
        )
        return LLMToolDecision(
            text=text,
            tool_calls=[],
            model=model,
            provider="fake",
            stop_reason="end_turn",
            usage=usage,
            provider_items=[
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }
            ],
        )

    def _decide_openai_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        aliases: dict[str, str],
        model: str,
        *,
        max_output_tokens: int,
    ) -> LLMToolDecision:
        if not self._settings.openai_api_key:
            raise LLMProviderError("OPENAI_API_KEY is not configured")
        reverse_aliases = {registry_name: alias for alias, registry_name in aliases.items()}
        payload: dict[str, Any] = {
            "model": model,
            "input": _openai_tool_input(messages, reverse_aliases),
            "tools": [
                {
                    "type": "function",
                    "name": reverse_aliases[spec.name],
                    "description": spec.description,
                    "parameters": spec.input_schema,
                }
                for spec in tools
            ],
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            "max_output_tokens": max_output_tokens,
        }
        body = self._post_json(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {self._settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
        )
        output = body.get("output", [])
        output = output if isinstance(output, list) else []
        calls: list[ToolCall] = []
        text_parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "function_call":
                calls.append(
                    ToolCall(
                        call_id=str(item.get("call_id") or f"tool_{uuid4().hex[:12]}"),
                        name=aliases.get(str(item.get("name") or ""), str(item.get("name") or "")),
                        arguments=_json_arguments(item.get("arguments")),
                        source="openai_native",
                    )
                )
            if item.get("type") == "message":
                for block in item.get("content", []):
                    if isinstance(block, dict) and block.get("type") in {
                        "output_text",
                        "refusal",
                    }:
                        text_parts.append(str(block.get("text") or block.get("refusal") or ""))
        usage = _usage_from_mapping(body.get("usage"))
        return LLMToolDecision(
            text="".join(text_parts).strip(),
            tool_calls=calls,
            model=str(body.get("model") or model),
            provider="openai",
            stop_reason="tool_use" if calls else str(body.get("status") or "completed"),
            usage=usage,
            provider_items=[dict(item) for item in output if isinstance(item, dict)],
        )

    def _decide_anthropic_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        aliases: dict[str, str],
        model: str,
        *,
        max_output_tokens: int,
    ) -> LLMToolDecision:
        if not self._settings.anthropic_api_key:
            raise LLMProviderError("ANTHROPIC_API_KEY is not configured")
        reverse_aliases = {registry_name: alias for alias, registry_name in aliases.items()}
        system = [
            str(message.get("content") or "")
            for message in messages
            if message.get("role") == "system"
        ]
        payload: dict[str, Any] = {
            "model": model,
            "messages": _anthropic_tool_messages(messages, reverse_aliases),
            "tools": [
                {
                    "name": reverse_aliases[spec.name],
                    "description": spec.description,
                    "input_schema": spec.input_schema,
                }
                for spec in tools
            ],
            "tool_choice": {"type": "auto"},
            "max_tokens": max_output_tokens,
        }
        if system:
            payload["system"] = "\n\n".join(system)
        body = self._post_json(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self._settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            payload=payload,
        )
        content = body.get("content", [])
        content = content if isinstance(content, list) else []
        calls: list[ToolCall] = []
        text_parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                calls.append(
                    ToolCall(
                        call_id=str(block.get("id") or f"tool_{uuid4().hex[:12]}"),
                        name=aliases.get(str(block.get("name") or ""), str(block.get("name") or "")),
                        arguments=dict(block.get("input") or {}),
                        source="anthropic_native",
                    )
                )
            elif block.get("type") == "text":
                text_parts.append(str(block.get("text") or ""))
        return LLMToolDecision(
            text="".join(text_parts).strip(),
            tool_calls=calls,
            model=str(body.get("model") or model),
            provider="anthropic",
            stop_reason=str(body.get("stop_reason") or ("tool_use" if calls else "end_turn")),
            usage=_usage_from_mapping(body.get("usage")),
            provider_items=[dict(block) for block in content if isinstance(block, dict)],
        )

    def _decide_google_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        aliases: dict[str, str],
        model: str,
        *,
        max_output_tokens: int,
    ) -> LLMToolDecision:
        if not self._settings.google_api_key:
            raise LLMProviderError("GOOGLE_API_KEY is not configured")
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise LLMProviderError(
                "google-genai is not installed; run pip install google-genai"
            ) from exc

        reverse_aliases = {registry_name: alias for alias, registry_name in aliases.items()}
        declarations = [
            types.FunctionDeclaration(
                name=reverse_aliases[spec.name],
                description=spec.description,
                parameters_json_schema=spec.input_schema,
                response_json_schema=spec.output_schema,
            )
            for spec in tools
        ]
        config_kwargs: dict[str, Any] = {
            "max_output_tokens": max_output_tokens,
            "tools": [types.Tool(function_declarations=declarations)],
        }
        system_instruction = _google_system_instruction_any(messages)
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        client = genai.Client(
            api_key=self._settings.google_api_key,
            http_options=types.HttpOptions(
                timeout=max(1, int(self._settings.llm_timeout_seconds * 1000))
            ),
        )
        try:
            response = client.models.generate_content(
                model=model,
                contents=_google_tool_contents(messages, types, reverse_aliases),
                config=types.GenerateContentConfig(**config_kwargs),
            )
        except Exception as exc:
            if _is_timeout_exception(exc):
                raise LLMProviderError(
                    "llm provider request timed out",
                    retryable=True,
                    code="llm_timeout",
                ) from exc
            raise LLMProviderError(str(exc), retryable=True) from exc
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                with suppress(Exception):
                    close()

        candidates = getattr(response, "candidates", None) or []
        candidate = candidates[0] if candidates else None
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        calls: list[ToolCall] = []
        text_parts: list[str] = []
        for part in parts:
            function_call = getattr(part, "function_call", None)
            if function_call is not None and getattr(function_call, "name", None):
                alias = str(function_call.name)
                calls.append(
                    ToolCall(
                        call_id=str(
                            getattr(function_call, "id", None)
                            or f"tool_{uuid4().hex[:12]}"
                        ),
                        name=aliases.get(alias, alias),
                        arguments=dict(getattr(function_call, "args", None) or {}),
                        source="google_native",
                    )
                )
            text = getattr(part, "text", None)
            if isinstance(text, str) and text:
                text_parts.append(text)
        provider_items: list[dict[str, Any]] = []
        if content is not None:
            dump = getattr(content, "model_dump", None)
            if callable(dump):
                provider_items.append(dump(mode="json", by_alias=False))
        finish_reason = _google_candidate_finish_reason(candidate)
        return LLMToolDecision(
            text="".join(text_parts).strip(),
            tool_calls=calls,
            model=model,
            provider="google",
            stop_reason="tool_use" if calls else (finish_reason or "STOP"),
            usage=_google_usage(getattr(response, "usage_metadata", None)),
            provider_items=provider_items,
        )

    def _prepare_tool_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        aliases: dict[str, str],
        *,
        provider: str,
        model: str,
    ) -> LLMRequestPlan:
        self._require_model_allowed(provider, model)
        input_tokens, count_method = self._count_tool_input_tokens(
            messages,
            tools,
            aliases,
            provider=provider,
            model=model,
        )
        if self._usage_ledger is None:
            return LLMRequestPlan(
                requested_provider=provider,
                requested_model=model,
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                max_output_tokens=self._settings.llm_max_output_tokens,
                input_count_method=count_method,
                usage_context=current_model_usage_context(),
            )
        try:
            authorization = self._usage_ledger.authorize(
                requested_provider=provider,
                requested_model=model,
                input_tokens=input_tokens,
                max_output_tokens=self._settings.llm_max_output_tokens,
                input_count_method=count_method,
            )
        except TokenBudgetExceededError as exc:
            raise LLMProviderError(
                str(exc),
                code="token_budget_exceeded",
            ) from exc
        self._require_model_allowed(authorization.provider, authorization.model)
        if authorization.budget_decision == "downgraded":
            input_tokens, count_method = self._count_tool_input_tokens(
                messages,
                tools,
                aliases,
                provider=authorization.provider,
                model=authorization.model,
            )
        return LLMRequestPlan(
            requested_provider=provider,
            requested_model=model,
            provider=authorization.provider,
            model=authorization.model,
            input_tokens=input_tokens,
            max_output_tokens=authorization.max_output_tokens,
            input_count_method=count_method,
            budget_decision=authorization.budget_decision,
            budget_reason=authorization.budget_reason,
            usage_context=current_model_usage_context(),
        )

    def _count_tool_input_tokens(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        aliases: dict[str, str],
        *,
        provider: str,
        model: str,
    ) -> tuple[int, str]:
        reverse_aliases = {
            registry_name: alias for alias, registry_name in aliases.items()
        }
        if provider == "fake":
            serialized = _join_any_message_text(messages) + json.dumps(
                [spec.input_schema for spec in tools],
                ensure_ascii=False,
                sort_keys=True,
            )
            return _count_fake_text_tokens(serialized), "fake_lexical_tokenizer"
        if provider == "openai":
            if not self._settings.openai_api_key:
                raise LLMProviderError("OPENAI_API_KEY is not configured")
            payload = {
                "model": model,
                "input": _openai_tool_input(messages, reverse_aliases),
                "tools": [
                    {
                        "type": "function",
                        "name": reverse_aliases[spec.name],
                        "description": spec.description,
                        "parameters": spec.input_schema,
                    }
                    for spec in tools
                ],
            }
            body = self._post_json(
                "https://api.openai.com/v1/responses/input_tokens",
                headers={
                    "Authorization": f"Bearer {self._settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                payload=payload,
            )
            count = body.get("input_tokens")
            if not isinstance(count, int):
                raise LLMProviderError(
                    "OpenAI token count response is missing input_tokens",
                    code="token_count_failed",
                )
            return max(0, count), "openai_responses_input_tokens"
        if provider == "anthropic":
            if not self._settings.anthropic_api_key:
                raise LLMProviderError("ANTHROPIC_API_KEY is not configured")
            system = [
                str(message.get("content") or "")
                for message in messages
                if message.get("role") == "system"
            ]
            payload: dict[str, Any] = {
                "model": model,
                "messages": _anthropic_tool_messages(
                    messages,
                    reverse_aliases,
                ),
                "tools": [
                    {
                        "name": reverse_aliases[spec.name],
                        "description": spec.description,
                        "input_schema": spec.input_schema,
                    }
                    for spec in tools
                ],
            }
            if system:
                payload["system"] = "\n\n".join(system)
            body = self._post_json(
                "https://api.anthropic.com/v1/messages/count_tokens",
                headers={
                    "x-api-key": self._settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                payload=payload,
            )
            count = body.get("input_tokens")
            if not isinstance(count, int):
                raise LLMProviderError(
                    "Anthropic token count response is missing input_tokens",
                    code="token_count_failed",
                )
            return max(0, count), "anthropic_messages_count_tokens"
        if provider == "google":
            if not self._settings.google_api_key:
                raise LLMProviderError("GOOGLE_API_KEY is not configured")
            try:
                from google import genai
                from google.genai import types
            except ImportError as exc:
                raise LLMProviderError(
                    "google-genai is not installed; run pip install google-genai"
                ) from exc
            declarations = [
                types.FunctionDeclaration(
                    name=reverse_aliases[spec.name],
                    description=spec.description,
                    parameters_json_schema=spec.input_schema,
                    response_json_schema=spec.output_schema,
                )
                for spec in tools
            ]
            config_kwargs: dict[str, Any] = {
                "tools": [types.Tool(function_declarations=declarations)]
            }
            system_instruction = _google_system_instruction_any(messages)
            if system_instruction:
                config_kwargs["system_instruction"] = system_instruction
            client = genai.Client(
                api_key=self._settings.google_api_key,
                http_options=types.HttpOptions(
                    timeout=max(
                        1,
                        int(self._settings.llm_timeout_seconds * 1000),
                    )
                ),
            )
            try:
                response = client.models.count_tokens(
                    model=model,
                    contents=_google_tool_contents(
                        messages,
                        types,
                        reverse_aliases,
                    ),
                    config=types.CountTokensConfig(**config_kwargs),
                )
            except Exception as exc:
                raise LLMProviderError(
                    f"Gemini token count failed: {exc}",
                    retryable=True,
                    code="token_count_failed",
                ) from exc
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    with suppress(Exception):
                        close()
            count = getattr(response, "total_tokens", None)
            if not isinstance(count, int):
                raise LLMProviderError(
                    "Gemini token count response is missing total_tokens",
                    code="token_count_failed",
                )
            return max(0, count), "gemini_models_count_tokens"
        raise LLMProviderError(
            f"unsupported llm provider: {provider}",
            code="llm_provider_not_allowed",
        )

    def _require_model_allowed(self, provider: str, model: str) -> None:
        if not self._settings.is_model_allowed(provider, model):
            code = (
                "llm_provider_not_allowed"
                if provider
                not in (
                    set(self._settings.model_provider_allowlist)
                    or {
                        self._settings.llm_provider,
                        self._settings.embedding_provider,
                        self._settings.token_budget_fallback_provider,
                    }
                )
                else "llm_model_not_allowed"
            )
            raise LLMProviderError(
                f"model selection is not allowlisted: {provider}:{model}",
                code=code,
            )

    def _count_input_tokens(
        self,
        messages: list[dict[str, str]],
        *,
        provider: str,
        model: str,
    ) -> tuple[int, str]:
        if provider == "fake":
            return _count_fake_message_tokens(messages), "fake_lexical_tokenizer"
        if provider == "openai":
            if not self._settings.openai_api_key:
                raise LLMProviderError("OPENAI_API_KEY is not configured")
            body = self._post_json(
                "https://api.openai.com/v1/responses/input_tokens",
                headers={
                    "Authorization": f"Bearer {self._settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                payload={"model": model, "input": messages},
            )
            count = body.get("input_tokens")
            if not isinstance(count, int):
                raise LLMProviderError(
                    "OpenAI token count response is missing input_tokens",
                    code="token_count_failed",
                )
            return max(0, count), "openai_responses_input_tokens"
        if provider == "anthropic":
            if not self._settings.anthropic_api_key:
                raise LLMProviderError("ANTHROPIC_API_KEY is not configured")
            system_messages = [
                message["content"]
                for message in messages
                if message["role"] == "system"
            ]
            payload: dict[str, Any] = {
                "model": model,
                "messages": [
                    message
                    for message in messages
                    if message["role"] in {"user", "assistant"}
                ],
            }
            if system_messages:
                payload["system"] = "\n\n".join(system_messages)
            body = self._post_json(
                "https://api.anthropic.com/v1/messages/count_tokens",
                headers={
                    "x-api-key": self._settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                payload=payload,
            )
            count = body.get("input_tokens")
            if not isinstance(count, int):
                raise LLMProviderError(
                    "Anthropic token count response is missing input_tokens",
                    code="token_count_failed",
                )
            return max(0, count), "anthropic_messages_count_tokens"
        if provider == "google":
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
            config = (
                types.CountTokensConfig(system_instruction=system_instruction)
                if system_instruction
                else None
            )
            client = genai.Client(
                api_key=self._settings.google_api_key,
                http_options=types.HttpOptions(
                    timeout=max(
                        1,
                        int(self._settings.llm_timeout_seconds * 1000),
                    )
                ),
            )
            try:
                response = client.models.count_tokens(
                    model=model,
                    contents=_google_contents(messages, types),
                    config=config,
                )
            except Exception as exc:
                raise LLMProviderError(
                    f"Gemini token count failed: {exc}",
                    retryable=True,
                    code="token_count_failed",
                ) from exc
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    with suppress(Exception):
                        close()
            count = getattr(response, "total_tokens", None)
            if not isinstance(count, int):
                raise LLMProviderError(
                    "Gemini token count response is missing total_tokens",
                    code="token_count_failed",
                )
            return max(0, count), "gemini_models_count_tokens"
        raise LLMProviderError(
            f"unsupported llm provider: {provider}",
            code="llm_provider_not_allowed",
        )

    def _record_request_usage(
        self,
        plan: LLMRequestPlan,
        usage: LLMUsage,
    ) -> None:
        if self._usage_ledger is None:
            return
        self._usage_ledger.record(
            provider=plan.provider,
            model=plan.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            thoughts_tokens=usage.thoughts_tokens,
            requested_provider=plan.requested_provider,
            requested_model=plan.requested_model,
            input_count_method=plan.input_count_method,
            budget_decision=plan.budget_decision,
            context=plan.usage_context,
        )

    def _post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self._settings.llm_timeout_seconds)
            ) as client:
                response = client.post(url, headers=headers, json=payload)
                if response.status_code >= 400:
                    retryable = response.status_code in {408, 409, 429} or (
                        response.status_code >= 500
                    )
                    raise LLMProviderError(
                        f"llm provider returned HTTP {response.status_code}",
                        retryable=retryable,
                        code="llm_http_error",
                    )
                body = response.json()
        except httpx.TimeoutException as exc:
            raise LLMProviderError(
                "llm provider request timed out",
                retryable=True,
                code="llm_timeout",
            ) from exc
        except httpx.TransportError as exc:
            raise LLMProviderError(
                "llm provider network request failed",
                retryable=True,
                code="llm_transport_error",
            ) from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMProviderError(
                "llm provider returned invalid JSON",
                code="llm_invalid_json",
            ) from exc
        if not isinstance(body, dict):
            raise LLMProviderError(
                "llm provider returned an invalid response object",
                code="llm_invalid_response",
            )
        return body

    def _stream_factory(
        self,
        provider: str,
        model: str,
        thinking_level: str | None,
        max_output_tokens: int,
    ):
        if provider == "fake":
            return lambda messages: self._stream_fake(messages, model)
        if provider == "openai":
            return lambda messages: self._stream_openai(
                messages,
                model,
                max_output_tokens=max_output_tokens,
            )
        if provider == "anthropic":
            return lambda messages: self._stream_anthropic(
                messages,
                model,
                max_output_tokens=max_output_tokens,
            )
        if provider == "google":
            return lambda messages: self._stream_google(
                messages,
                model,
                thinking_level=thinking_level,
                max_output_tokens=max_output_tokens,
            )
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
                input_tokens=_count_fake_message_tokens(messages),
                output_tokens=_count_fake_text_tokens(answer),
            ),
        )
        yield LLMStreamEvent(type="done")

    def _stream_openai(
        self,
        messages: list[dict[str, str]],
        model: str,
        *,
        max_output_tokens: int,
    ) -> Iterable[LLMStreamEvent]:
        if not self._settings.openai_api_key:
            raise LLMProviderError("OPENAI_API_KEY is not configured")

        payload = {
            "model": model,
            "input": messages,
            "max_output_tokens": max_output_tokens,
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
        self,
        messages: list[dict[str, str]],
        model: str,
        *,
        max_output_tokens: int,
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
            "max_tokens": max_output_tokens,
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
        self,
        messages: list[dict[str, str]],
        model: str,
        *,
        thinking_level: str | None = None,
        max_output_tokens: int,
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
            "max_output_tokens": max_output_tokens
        }
        selected_thinking_level = thinking_level
        if selected_thinking_level is None and model.startswith("gemini-3"):
            selected_thinking_level = self._settings.llm_thinking_level
        if selected_thinking_level is not None:
            if not model.startswith("gemini-3"):
                raise LLMProviderError(
                    "thinking_level is only supported for Gemini 3 models",
                    code="unsupported_thinking_level",
                )
            try:
                thinking_level_value = getattr(
                    types.ThinkingLevel,
                    selected_thinking_level.upper(),
                )
            except (AttributeError, TypeError) as exc:
                raise LLMProviderError(
                    "google-genai >=2.14.0 is required for Gemini thinking_level",
                    code="google_sdk_upgrade_required",
                ) from exc
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_level=thinking_level_value
            )
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction

        client = genai.Client(
            api_key=self._settings.google_api_key,
            http_options=types.HttpOptions(
                timeout=max(1, int(self._settings.llm_timeout_seconds * 1000))
            ),
        )
        usage: LLMUsage | None = None
        finish_reason: str | None = None
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
                chunk_finish_reason = _google_finish_reason(chunk)
                if chunk_finish_reason is not None:
                    finish_reason = chunk_finish_reason
        except Exception as exc:
            if _is_timeout_exception(exc):
                raise LLMProviderError(
                    "llm provider request timed out",
                    retryable=True,
                    code="llm_timeout",
                ) from exc
            raise LLMProviderError(str(exc), retryable=True) from exc
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                with suppress(Exception):
                    close()

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
        if finish_reason not in {None, "STOP"}:
            if finish_reason == "MAX_TOKENS":
                raise LLMProviderError(
                    "Gemini reached the configured output token limit",
                    code="max_output_tokens",
                    finish_reason=finish_reason,
                )
            raise LLMProviderError(
                f"Gemini stopped with finish reason {finish_reason}",
                code="llm_finish_reason",
                finish_reason=finish_reason,
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
            usage = _usage_from_mapping(response.get("usage"))
            if usage is not None:
                yield LLMStreamEvent(type="usage", usage=usage)
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
        thoughts_tokens=_int_attr(usage_metadata, "thoughts_token_count"),
    )


def _google_finish_reason(chunk: object) -> str | None:
    for candidate in getattr(chunk, "candidates", None) or []:
        reason = getattr(candidate, "finish_reason", None)
        if reason is None:
            continue
        value = getattr(reason, "value", reason)
        normalized = str(value).strip().upper()
        if normalized and normalized not in {"NONE", "FINISH_REASON_UNSPECIFIED"}:
            return normalized.removeprefix("FINISHREASON.")
    return None


def _is_timeout_exception(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (TimeoutError, httpx.TimeoutException)):
            return True
        if "timed out" in str(current).lower() or "timeout" in str(current).lower():
            return True
        current = current.__cause__ or current.__context__
    return False


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
    return max(1, estimate_text_tokens(text))


def _count_fake_message_tokens(messages: list[dict[str, str]]) -> int:
    total = 2
    for message in messages:
        total += 4
        for value in (message.get("role", ""), message.get("content", "")):
            total += _count_fake_text_tokens(value)
    return total


def _count_fake_text_tokens(value: str) -> int:
    total = 0
    ascii_buffer: list[str] = []
    for character in value:
        if ord(character) < 128 and character.isalnum():
            ascii_buffer.append(character)
            continue
        if ascii_buffer:
            total += 1
            ascii_buffer = []
        if not character.isspace():
            total += 1
    if ascii_buffer:
        total += 1
    return total


def _join_message_text(messages: list[dict[str, str]]) -> str:
    return "\n".join(message["content"] for message in messages)


def _join_any_message_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif content is not None:
            parts.append(json.dumps(content, ensure_ascii=False, default=str))
    return "\n".join(parts)


def _tool_aliases(tools: list[ToolSpec]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for spec in tools:
        base = re.sub(r"[^A-Za-z0-9_-]+", "_", spec.name).strip("_")
        if not base:
            base = "tool"
        if not base[0].isalpha() and base[0] != "_":
            base = f"tool_{base}"
        digest = hashlib.sha256(spec.name.encode("utf-8")).hexdigest()[:8]
        alias = base[:64]
        if alias in aliases and aliases[alias] != spec.name:
            alias = f"{base[:55]}_{digest}"
        if len(alias) > 64:
            alias = f"{alias[:55]}_{digest}"
        aliases[alias] = spec.name
    return aliases


def _json_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise LLMProviderError(
            "model returned malformed tool arguments",
            code="invalid_tool_arguments",
        ) from exc
    if not isinstance(parsed, dict):
        raise LLMProviderError(
            "model returned non-object tool arguments",
            code="invalid_tool_arguments",
        )
    return parsed


def _usage_from_mapping(value: Any) -> LLMUsage | None:
    if not isinstance(value, dict):
        return None
    output_details = value.get("output_tokens_details")
    output_details = output_details if isinstance(output_details, dict) else {}
    output_tokens = int(value.get("output_tokens") or 0)
    thoughts_tokens = int(output_details.get("reasoning_tokens") or 0)
    return LLMUsage(
        input_tokens=int(value.get("input_tokens") or 0),
        output_tokens=max(0, output_tokens - thoughts_tokens),
        thoughts_tokens=thoughts_tokens,
    )


def _openai_tool_input(
    messages: list[dict[str, Any]],
    reverse_aliases: dict[str, str],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "")
        if role == "assistant" and message.get("provider") == "openai":
            provider_items = message.get("provider_items")
            if isinstance(provider_items, list):
                items.extend(
                    dict(item) for item in provider_items if isinstance(item, dict)
                )
                continue
        if role in {"system", "user", "assistant"}:
            content = message.get("content")
            if isinstance(content, str) and content:
                items.append({"role": role, "content": content})
            for call in message.get("tool_calls", []):
                if not isinstance(call, dict):
                    continue
                registry_name = str(call.get("name") or "")
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(call.get("call_id") or ""),
                        "name": reverse_aliases.get(registry_name, registry_name),
                        "arguments": json.dumps(
                            call.get("arguments") or {},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                )
            continue
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("call_id") or ""),
                    "output": json.dumps(
                        message.get("content"),
                        ensure_ascii=False,
                        default=str,
                    ),
                }
            )
    return items


def _anthropic_tool_messages(
    messages: list[dict[str, Any]],
    reverse_aliases: dict[str, str],
) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []

    def flush_tool_results() -> None:
        if pending_tool_results:
            converted.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for message in messages:
        role = str(message.get("role") or "")
        if role == "system":
            continue
        if role == "tool":
            pending_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": str(message.get("call_id") or ""),
                    "content": json.dumps(
                        message.get("content"),
                        ensure_ascii=False,
                        default=str,
                    ),
                    "is_error": bool(message.get("is_error")),
                }
            )
            continue
        flush_tool_results()
        if role == "assistant" and message.get("provider") == "anthropic":
            provider_items = message.get("provider_items")
            if isinstance(provider_items, list):
                converted.append(
                    {
                        "role": "assistant",
                        "content": [
                            dict(item)
                            for item in provider_items
                            if isinstance(item, dict)
                        ],
                    }
                )
                continue
        if role not in {"user", "assistant"}:
            continue
        blocks: list[dict[str, Any]] = []
        content = message.get("content")
        if isinstance(content, str) and content:
            blocks.append({"type": "text", "text": content})
        for call in message.get("tool_calls", []):
            if not isinstance(call, dict):
                continue
            registry_name = str(call.get("name") or "")
            blocks.append(
                {
                    "type": "tool_use",
                    "id": str(call.get("call_id") or ""),
                    "name": reverse_aliases.get(registry_name, registry_name),
                    "input": dict(call.get("arguments") or {}),
                }
            )
        if blocks:
            converted.append({"role": role, "content": blocks})
    flush_tool_results()
    return converted


def _google_tool_contents(
    messages: list[dict[str, Any]],
    types: Any,
    reverse_aliases: dict[str, str],
) -> list[Any]:
    contents: list[Any] = []
    for message in messages:
        role = str(message.get("role") or "")
        if role == "system":
            continue
        if role == "assistant" and message.get("provider") == "google":
            provider_items = message.get("provider_items")
            if isinstance(provider_items, list) and provider_items:
                contents.extend(
                    types.Content(**item)
                    for item in provider_items
                    if isinstance(item, dict)
                )
                continue
        if role == "tool":
            content = message.get("content")
            response = content if isinstance(content, dict) else {"result": content}
            registry_name = str(message.get("name") or "")
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                id=str(message.get("call_id") or ""),
                                name=reverse_aliases.get(registry_name, registry_name),
                                response=response,
                            )
                        )
                    ],
                )
            )
            continue
        if role not in {"user", "assistant"}:
            continue
        parts: list[Any] = []
        content = message.get("content")
        if isinstance(content, str) and content:
            parts.append(types.Part.from_text(text=content))
        for call in message.get("tool_calls", []):
            if not isinstance(call, dict):
                continue
            registry_name = str(call.get("name") or "")
            parts.append(
                types.Part(
                    function_call=types.FunctionCall(
                        id=str(call.get("call_id") or ""),
                        name=reverse_aliases.get(registry_name, registry_name),
                        args=dict(call.get("arguments") or {}),
                    )
                )
            )
        if parts:
            contents.append(
                types.Content(
                    role="model" if role == "assistant" else "user",
                    parts=parts,
                )
            )
    return contents


def _google_system_instruction_any(
    messages: list[dict[str, Any]],
) -> str | None:
    system = [
        str(message.get("content") or "")
        for message in messages
        if message.get("role") == "system"
    ]
    return "\n\n".join(system) if system else None


def _google_candidate_finish_reason(candidate: Any) -> str | None:
    if candidate is None:
        return None
    reason = getattr(candidate, "finish_reason", None)
    if reason is None:
        return None
    value = getattr(reason, "value", reason)
    normalized = str(value).strip().upper()
    return normalized.removeprefix("FINISHREASON.") if normalized else None
