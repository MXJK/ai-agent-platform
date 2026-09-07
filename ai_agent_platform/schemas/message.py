from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from ai_agent_platform.domain import Message


MessageRole = Literal["system", "user", "assistant", "tool"]


class AddMessageRequest(BaseModel):
    role: MessageRole
    content: str = Field(min_length=1, max_length=4000)


class MessageResponse(BaseModel):
    id: str
    session_id: str
    role: MessageRole
    content: str
    created_at: datetime
    source_run_id: Optional[str] = None

    @classmethod
    def from_domain(cls, message: Message) -> "MessageResponse":
        return cls(
            id=message.id,
            session_id=message.session_id,
            role=message.role,  # type: ignore[arg-type]
            content=message.content,
            created_at=message.created_at,
            source_run_id=message.source_run_id,
        )


class MessagesResponse(BaseModel):
    messages: list[MessageResponse]
