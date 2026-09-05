from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Literal
ToolCategory = Literal['read', 'write', 'command']

@dataclass(frozen=True)
class Tool:
    tool_name: str
    category: ToolCategory
    description: str = ''

    @property
    def name(self) -> str:
        return self.tool_name
MAX_OUTPUT_CHARS = 50000
TOOL_SEARCH_TOOL_NAME = 'ToolSearch'
MCP_CALL_TOOL_NAME = 'mcp_call'

@dataclass
class TextDelta:
    text: str

@dataclass
class ToolCallStart:
    tool_name: str
    tool_id: str

@dataclass
class ToolCallDelta:
    text: str

@dataclass
class ToolCallComplete:
    tool_id: str
    tool_name: str
    arguments: dict[str, Any]

@dataclass
class ThinkingDelta:
    text: str
    displayable: bool = False

@dataclass
class ThinkingComplete:
    thinking: str
    signature: str = ''
    provider: str = ''
    displayable: bool = False

@dataclass
class StreamEnd:
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    thinking_tokens: int = 0
    decision: Any = None

StreamEvent = TextDelta | ThinkingDelta | ThinkingComplete | ToolCallStart | ToolCallDelta | ToolCallComplete | StreamEnd
