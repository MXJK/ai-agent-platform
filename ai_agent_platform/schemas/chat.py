from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


LLMProviderName = Literal["fake", "openai", "anthropic"]


class ChatStreamRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=8000)
    provider: Optional[LLMProviderName] = None
    model: Optional[str] = Field(default=None, min_length=1, max_length=128)
