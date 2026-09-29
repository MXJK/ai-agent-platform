from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from threading import RLock
from uuid import uuid4

from .mailbox import Mailbox
from .models import AgentTeam, TeammateInfo, sanitize_team_name
from .shared_task import SharedTaskStore


class TeamError(RuntimeError):
    pass


class TeamManager:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = RLock()

    def create(self, *, name: str, lead_agent_id: str, description: str = "") -> AgentTeam:
        if not lead_agent_id:
            raise TeamError("lead_agent_id is required")
        base = sanitize_team_name(name)
        with self._lock:
            slug = base
            counter = 2
            while self._directory(slug).exists():
                slug = f"{base}-{counter}"
                counter += 1
            directory = self._directory(slug)
            directory.mkdir(parents=True, mode=0o700)
            team = AgentTeam(slug, lead_agent_id, description=description)
            self._save(team)
            self.tasks(slug).initialize()
            self.mailbox(slug)
            return team

    def get(self, name: str) -> AgentTeam | None:
        path = self._config(name)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TeamError(f"team config is unreadable: {name}") from exc
        if not isinstance(value, dict):
            raise TeamError(f"team config is malformed: {name}")
        return AgentTeam.from_dict(value)

    def list(self) -> list[AgentTeam]:
        teams: list[AgentTeam] = []
        for directory in sorted(self.root.iterdir()):
            if not directory.is_dir() or directory.is_symlink():
                continue
            team = self.get(directory.name)
            if team is not None:
                teams.append(team)
        return teams

    def add_member(self, team_name: str, member: TeammateInfo) -> AgentTeam:
        with self._lock:
            team = self._require(team_name)
            if team.member(member.name) or team.member(member.agent_id):
                raise TeamError(f"teammate already exists: {member.name}")
            team.members.append(member)
            self._save(team)
            return team

    def update_member(self, team_name: str, name_or_id: str, **changes: str) -> TeammateInfo:
        with self._lock:
            team = self._require(team_name)
            member = team.member(name_or_id)
            if member is None:
                raise TeamError(f"teammate not found: {name_or_id}")
            for key, value in changes.items():
                if hasattr(member, key):
                    setattr(member, key, value)
            self._save(team)
            return member

    def delete(self, team_name: str) -> None:
        with self._lock:
            team = self._require(team_name)
            active = [
                member.name
                for member in team.members
                if member.status
                not in {"idle", "completed", "cancelled", "failed", "blocked"}
            ]
            if active:
                raise TeamError("cannot delete a team with active members: " + ", ".join(active))
            directory = self._directory(team_name)
            if directory.is_symlink() or directory.parent != self.root:
                raise TeamError("team directory is outside the configured root")
            shutil.rmtree(directory)

    def mailbox(self, team_name: str) -> Mailbox:
        self._require(team_name)
        return Mailbox(self._directory(team_name) / "inboxes")

    def tasks(self, team_name: str) -> SharedTaskStore:
        directory = self._directory(team_name)
        if not directory.exists():
            raise TeamError(f"team not found: {team_name}")
        return SharedTaskStore(directory / "tasks.json")

    def resolve_recipient(self, team: AgentTeam, name_or_id: str) -> str | None:
        if name_or_id in {"lead", team.lead_agent_id}:
            return team.lead_agent_id
        member = team.member(name_or_id)
        return member.agent_id if member is not None else None

    def unique_member_name(self, team: AgentTeam, base: str) -> str:
        candidate = sanitize_team_name(base or "worker")
        existing = {member.name for member in team.members}
        if candidate not in existing:
            return candidate
        index = 2
        while f"{candidate}-{index}" in existing:
            index += 1
        return f"{candidate}-{index}"

    def _require(self, name: str) -> AgentTeam:
        team = self.get(name)
        if team is None:
            raise TeamError(f"team not found: {name}")
        return team

    def _directory(self, name: str) -> Path:
        safe = sanitize_team_name(name)
        if safe != name:
            raise TeamError(f"invalid team name: {name}")
        return self.root / safe

    def _config(self, name: str) -> Path:
        return self._directory(name) / "config.json"

    def _save(self, team: AgentTeam) -> None:
        path = self._config(team.name)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(team.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
