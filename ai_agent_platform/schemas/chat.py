from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


LLMProviderName = Literal["fake", "openai", "deepseek", "anthropic", "google"]
LLMThinkingLevel = Literal["minimal", "low", "medium", "high"]
LLMRoutingPolicy = Literal["smart", "quality", "cost", "latency"]


class ChatStreamRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=8000)
    provider: Optional[LLMProviderName] = None
    model: Optional[str] = Field(default=None, min_length=1, max_length=128)
    thinking_level: Optional[LLMThinkingLevel] = None
    routing_policy: Optional[LLMRoutingPolicy] = None
    requires_structured_output: bool = False
    min_context_tokens: Optional[int] = Field(default=None, ge=1)
    workspace_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
