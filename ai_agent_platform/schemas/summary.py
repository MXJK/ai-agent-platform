from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from ai_agent_platform.domain import SessionSummary


class SessionSummaryResponse(BaseModel):
    session_id: str
    message_count: int
    last_message: Optional[str]
    compressed_summary: Optional[str] = None
    summarized_message_count: int = 0
    summary_version: int = 0
    summary_updated_at: Optional[datetime] = None

    @classmethod
    def from_domain(cls, summary: SessionSummary) -> "SessionSummaryResponse":
        return cls(
            session_id=summary.session_id,
            message_count=summary.message_count,
            last_message=summary.last_message,
            compressed_summary=summary.compressed_summary,
            summarized_message_count=summary.summarized_message_count,
            summary_version=summary.summary_version,
            summary_updated_at=summary.summary_updated_at,
        )
