from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ai_agent_platform.domain import ChangeSetRecord


class ChangeSetApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_set_id: str = Field(min_length=1, max_length=64)
    patch_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ChangeSetRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_set_id: str = Field(min_length=1, max_length=64)


class ChangeSetResponse(BaseModel):
    id: str
    run_id: str
    conversation_id: str
    workspace_id: str
    workspace_revision: int
    created_by: str
    apply_mode: str
    base_git_head: str | None
    baseline_file_hashes: dict[str, str | None]
    changed_files: list[str]
    patch: str
    patch_sha256: str
    validation_status: str
    validation_summary: dict[str, Any]
    status: str
    created_at: datetime
    updated_at: datetime
    applied_by: str | None
    applied_at: datetime | None
    error: str | None
    branch_name: str | None
    worktree_path: str | None

    @classmethod
    def from_domain(cls, record: ChangeSetRecord) -> "ChangeSetResponse":
        return cls(
            id=record.id,
            run_id=record.run_id,
            conversation_id=record.conversation_id,
            workspace_id=record.workspace_id,
            workspace_revision=record.workspace_revision,
            created_by=record.created_by,
            apply_mode=record.apply_mode,
            base_git_head=record.base_git_head,
            baseline_file_hashes=record.baseline_file_hashes,
            changed_files=record.changed_files,
            patch=record.patch,
            patch_sha256=record.patch_sha256,
            validation_status=record.validation_status,
            validation_summary=record.validation_summary,
            status=record.status,
            created_at=record.created_at,
            updated_at=record.updated_at,
            applied_by=record.applied_by,
            applied_at=record.applied_at,
            error=record.error,
            branch_name=record.branch_name,
            worktree_path=record.worktree_path,
        )
