from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator

from .tools.base import (
    StreamEnd, StreamEvent, TextDelta, ThinkingComplete, ThinkingDelta, ToolCallComplete,
)


@dataclass
class CollectedResponse:
    text: str = ''
    tool_calls: list[ToolCallComplete] = field(default_factory=list)
    thinking_blocks: list[ThinkingComplete] = field(default_factory=list)
    stop_reason: str = ''
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    thinking_tokens: int = 0


class StreamCollector:
    def __init__(self) -> None:
        self.response = CollectedResponse()

    async def consume(self, stream: AsyncIterator[StreamEvent]) -> AsyncIterator[StreamEvent]:
        async for event in stream:
            if isinstance(event, TextDelta):
                self.response.text += event.text
                yield event
            elif isinstance(event, ThinkingDelta):
                if event.displayable:
                    yield event
            elif isinstance(event, ThinkingComplete):
                self.response.thinking_blocks.append(event)
                if event.displayable:
                    yield ThinkingDelta(text=event.thinking, displayable=True)
            elif isinstance(event, ToolCallComplete):
                self.response.tool_calls.append(event)
                yield event
            elif isinstance(event, StreamEnd):
                for name in ('stop_reason', 'input_tokens', 'output_tokens', 'cache_read', 'cache_creation', 'thinking_tokens'):
                    setattr(self.response, name, getattr(event, name))
