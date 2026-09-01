"""Accumulate native SSE tool turns without buffering their visible text.

Only public text is forwarded. Tool arguments and signed reasoning blocks stay
in the provider transcript and are validated by the existing decision parsers.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Iterable

from ai_agent_platform.integrations.llm import (
    CHAT_COMPLETIONS_PROVIDERS,
    LLMProviderError,
    LLMStreamEvent,
    _anthropic_usage_from_mapping,
    _chat_usage_from_mapping,
    _could_start_tool_protocol,
    _json_arguments,
    _starts_tool_protocol,
    _usage_from_mapping,
)


class NativeStreamAccumulator:
    def __init__(self, provider: str, on_delta: Callable[[str], None]) -> None:
        self.provider = provider
        self.on_delta = on_delta
        self.body: dict[str, Any] = {}
        self.blocks: dict[int, dict[str, Any]] = {}
        self.arguments: dict[int, str] = {}
        self.finished = False
        self.deepseek_text_pending = ""
        self.deepseek_protocol_detected = False

    def parse(
        self,
        event_name: str,
        payload: dict[str, Any],
    ) -> Iterable[LLMStreamEvent]:
        if not isinstance(payload, dict):
            raise LLMProviderError(
                "invalid native stream event",
                code="llm_invalid_response",
            )
        kind = str(payload.get("type") or event_name)
        if kind in {"error", "response.failed"} or payload.get("error"):
            raise LLMProviderError(
                "provider reported an error in the native tool stream",
                code="llm_stream_error",
                retryable=True,
            )
        if self.provider == "openai":
            self._openai(kind, payload)
        elif self.provider == "anthropic":
            self._anthropic(kind, payload)
        elif self.provider in CHAT_COMPLETIONS_PROVIDERS:
            self._chat_completions(payload)
        # The HTTP SSE transport owns framing; this accumulator owns completion.
        return ()

    def _text(self, text: Any) -> None:
        if isinstance(text, str) and text:
            self.on_delta(text)

    def _deepseek_text(self, text: str) -> None:
        """Publish ordinary text while withholding split tool-protocol prefixes."""

        if self.deepseek_protocol_detected or not text:
            return
        self.deepseek_text_pending += text
        while self.deepseek_text_pending:
            marker = self.deepseek_text_pending.find("<")
            if marker < 0:
                self._text(self.deepseek_text_pending)
                self.deepseek_text_pending = ""
                return
            if marker > 0:
                self._text(self.deepseek_text_pending[:marker])
                self.deepseek_text_pending = self.deepseek_text_pending[marker:]
                continue
            if _starts_tool_protocol(self.deepseek_text_pending):
                self.deepseek_protocol_detected = True
                self.deepseek_text_pending = ""
                return
            if _could_start_tool_protocol(self.deepseek_text_pending):
                return
            self._text("<")
            self.deepseek_text_pending = self.deepseek_text_pending[1:]

    def _finish_deepseek_text(self) -> None:
        if self.provider != "deepseek" or self.deepseek_protocol_detected:
            self.deepseek_text_pending = ""
            return
        if _could_start_tool_protocol(self.deepseek_text_pending):
            self.deepseek_protocol_detected = True
            self.deepseek_text_pending = ""
            return
        self._text(self.deepseek_text_pending)
        self.deepseek_text_pending = ""

    def _openai(self, kind: str, payload: dict[str, Any]) -> None:
        if kind in {"response.output_text.delta", "response.refusal.delta"}:
            self._text(payload.get("delta"))
        elif kind in {"response.completed", "response.incomplete"}:
            response = payload.get("response")
            if not isinstance(response, dict) or not isinstance(
                response.get("output"), list
            ):
                raise LLMProviderError(
                    "missing terminal response",
                    code="llm_invalid_response",
                )
            # The terminal response includes complete tool args and reasoning items.
            self.body = response
            self.finished = True

    def _anthropic(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "message_start":
            self.body = deepcopy(payload.get("message") or {})
        elif kind == "content_block_start":
            index = int(payload["index"])
            block = deepcopy(payload["content_block"])
            self.blocks[index] = block
            if block.get("type") == "text":
                self._text(block.get("text"))
        elif kind == "content_block_delta":
            index = int(payload["index"])
            block = self.blocks[index]
            delta = payload.get("delta") or {}
            delta_type = delta.get("type")
            if delta_type == "input_json_delta":
                self.arguments[index] = self.arguments.get(index, "") + delta.get(
                    "partial_json", ""
                )
            elif delta_type in {"text_delta", "thinking_delta", "signature_delta"}:
                field = {
                    "text_delta": "text",
                    "thinking_delta": "thinking",
                    "signature_delta": "signature",
                }[delta_type]
                block[field] = block.get(field, "") + delta.get(field, "")
                if delta_type == "text_delta":
                    self._text(delta.get("text"))
            elif delta_type == "citations_delta":
                block.setdefault("citations", []).append(delta.get("citation"))
        elif kind == "message_delta":
            self.body.update(payload.get("delta") or {})
            self.body.setdefault("usage", {}).update(payload.get("usage") or {})
        elif kind == "message_stop":
            self.finished = bool(self.body.get("stop_reason"))

    def _chat_completions(self, payload: dict[str, Any]) -> None:
        if payload.get("model"):
            self.body["model"] = payload["model"]
        if payload.get("usage") is not None:
            self.body["usage"] = payload["usage"]
        for choice in payload.get("choices") or []:
            if choice.get("index", 0) != 0:
                continue
            message = self.body.setdefault(
                "message",
                {"role": "assistant", "content": ""},
            )
            delta = choice.get("delta") or {}
            for field in ("content", "reasoning_content"):
                text = delta.get(field)
                if isinstance(text, str):
                    message[field] = message.get(field, "") + text
                    if field == "content":
                        if self.provider == "deepseek":
                            self._deepseek_text(text)
                        else:
                            self._text(text)
            self._reasoning_details(message, delta.get("reasoning_details"))
            for call in delta.get("tool_calls") or []:
                index = int(call["index"])
                target = self.blocks.setdefault(
                    index,
                    {
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    },
                )
                if call.get("id"):
                    target["id"] = call["id"]
                for key, value in (call.get("function") or {}).items():
                    if isinstance(value, str):
                        target["function"][key] = (
                            target["function"].get(key, "") + value
                        )
            if choice.get("finish_reason"):
                self.body["finish_reason"] = choice["finish_reason"]
                self.finished = True
                self._finish_deepseek_text()

    @staticmethod
    def _reasoning_details(message: dict[str, Any], value: Any) -> None:
        """Merge MiniMax split-reasoning deltas without publishing them."""

        if not isinstance(value, list):
            return
        details = message.setdefault("reasoning_details", [])
        if not isinstance(details, list):
            details = []
            message["reasoning_details"] = details
        for position, raw in enumerate(value):
            if not isinstance(raw, dict):
                continue
            raw_index = raw.get("index", position)
            target = next(
                (
                    item
                    for item in details
                    if isinstance(item, dict)
                    and (
                        item.get("index") == raw_index
                        or (
                            raw.get("id")
                            and item.get("id") == raw.get("id")
                        )
                    )
                ),
                None,
            )
            if target is None:
                target = {}
                details.append(target)
            for key, item_value in raw.items():
                if key == "text" and isinstance(item_value, str):
                    current = str(target.get("text") or "")
                    target["text"] = (
                        item_value
                        if item_value.startswith(current)
                        else current + item_value
                    )
                else:
                    target[key] = deepcopy(item_value)

    def result(self) -> dict[str, Any]:
        if not self.finished:
            usage = (
                _chat_usage_from_mapping(
                    self.body.get("usage"),
                    provider=self.provider,
                )
                if self.provider in CHAT_COMPLETIONS_PROVIDERS
                else (
                    _anthropic_usage_from_mapping(self.body.get("usage"))
                    if self.provider == "anthropic"
                    else _usage_from_mapping(self.body.get("usage"))
                )
            )
            raise LLMProviderError(
                "native tool stream ended before its terminal event",
                code="llm_stream_incomplete",
                retryable=True,
                usage=usage,
            )
        self._finish_deepseek_text()
        if self.provider == "anthropic":
            for index, arguments in self.arguments.items():
                self.blocks[index]["input"] = _json_arguments(
                    arguments,
                    finish_reason=self.body.get("stop_reason"),
                    usage=_anthropic_usage_from_mapping(self.body.get("usage")),
                )
            self.body["content"] = [
                self.blocks[index] for index in sorted(self.blocks)
            ]
        elif self.provider in CHAT_COMPLETIONS_PROVIDERS:
            message = self.body.pop("message", {})
            if self.blocks:
                message["tool_calls"] = [
                    self.blocks[index] for index in sorted(self.blocks)
                ]
            self.body["choices"] = [
                {
                    "message": message,
                    "finish_reason": self.body.pop("finish_reason"),
                }
            ]
        return self.body
