from __future__ import annotations

from dataclasses import dataclass, field
import re
import time
from typing import Any


def sanitize_team_name(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]", "-", name.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return (slug or "team")[:64]


@dataclass
class TeammateInfo:
    name: str
    agent_id: str
    agent_type: str
    model: str
    worktree_name: str
    worktree_path: str
    child_run_id: str = ""
    status: str = "running"
    joined_at: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "agentId": self.agent_id,
            "name": self.name,
            "agentType": self.agent_type,
            "model": self.model,
            "joinedAt": self.joined_at,
            "worktreeName": self.worktree_name,
            "worktreePath": self.worktree_path,
            "childRunId": self.child_run_id,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TeammateInfo":
        return cls(
            name=str(value.get("name") or ""),
            agent_id=str(value.get("agentId") or ""),
            agent_type=str(value.get("agentType") or ""),
            model=str(value.get("model") or "inherit"),
            worktree_name=str(value.get("worktreeName") or ""),
            worktree_path=str(value.get("worktreePath") or ""),
            child_run_id=str(value.get("childRunId") or ""),
            status=str(value.get("status") or "idle"),
            joined_at=int(value.get("joinedAt") or 0),
        )


@dataclass
class AgentTeam:
    name: str
    lead_agent_id: str
    description: str = ""
    members: list[TeammateInfo] = field(default_factory=list)
    created_at: int = field(default_factory=lambda: int(time.time()))

    def member(self, name_or_id: str) -> TeammateInfo | None:
        return next(
            (
                member
                for member in self.members
                if member.name == name_or_id or member.agent_id == name_or_id
            ),
            None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "createdAt": self.created_at,
            "leadAgentId": self.lead_agent_id,
            "members": [member.to_dict() for member in self.members],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AgentTeam":
        return cls(
            name=str(value["name"]),
            lead_agent_id=str(value.get("leadAgentId") or ""),
            description=str(value.get("description") or ""),
            members=[
                TeammateInfo.from_dict(item)
                for item in value.get("members", [])
                if isinstance(item, dict)
            ],
            created_at=int(value.get("createdAt") or 0),
        )
