from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ai_agent_platform.domain import WorkspaceRecord
from ai_agent_platform.schemas.session import (
    TokenBudgetStatusResponse,
    TokenUsageOperationResponse,
)


class WorkspaceUpsertRequest(BaseModel):
    root_path: str = Field(min_length=1, max_length=2000)


class WorkspaceResponse(BaseModel):
    id: str
    root_path: str
    status: str = "ready"
    role: str | None = None
    can_update: bool = True
    revision: int
    created_at: datetime
    updated_at: datetime
    available: bool = True

    @classmethod
    def from_domain(
        cls,
        workspace: WorkspaceRecord,
        *,
        status: str = "ready",
        role: str | None = None,
        can_update: bool = True,
        available: bool = True,
    ) -> "WorkspaceResponse":
        return cls(
            id=workspace.id,
            root_path=workspace.root_path,
            status=status,
            role=role,
            can_update=can_update,
            revision=workspace.revision,
            created_at=workspace.created_at,
            updated_at=workspace.updated_at,
            available=available,
        )


class WorkspacesResponse(BaseModel):
    workspaces: list[WorkspaceResponse]


class WorkspaceDirectoryResponse(BaseModel):
    name: str
    path: str


class WorkspaceDirectoryBrowseResponse(BaseModel):
    current_path: str | None
    parent_path: str | None
    directories: list[WorkspaceDirectoryResponse]


class WorkspaceTokenUsageResponse(BaseModel):
    workspace_id: str
    input_tokens: int
    output_tokens: int
    thoughts_tokens: int
    total_tokens: int
    record_count: int
    conversation_count: int
    operations: list[TokenUsageOperationResponse]
    budget: TokenBudgetStatusResponse | None
