from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from ai_agent_platform.domain import SessionSummary


class SessionSummaryResponse(BaseModel):
    session_id: str
    message_count: int
    last_message: Optional[str]

    @classmethod
    def from_domain(cls, summary: SessionSummary) -> "SessionSummaryResponse":
        return cls(
            session_id=summary.session_id,
            message_count=summary.message_count,
            last_message=summary.last_message,
        )
