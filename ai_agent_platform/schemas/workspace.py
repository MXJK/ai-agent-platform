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
    revision: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, workspace: WorkspaceRecord) -> "WorkspaceResponse":
        return cls(
            id=workspace.id,
            root_path=workspace.root_path,
            revision=workspace.revision,
            created_at=workspace.created_at,
            updated_at=workspace.updated_at,
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
