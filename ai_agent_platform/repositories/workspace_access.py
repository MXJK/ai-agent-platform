"""Workspace membership repositories independent of retired DB memory."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import sqlite3
from threading import RLock

from ai_agent_platform.domain import WORKSPACE_ROLE_RANK, WorkspaceMember
from ai_agent_platform.local_state import LocalStateDatabase
from ai_agent_platform.repositories.postgres import _require_psycopg


class InMemoryWorkspaceAccessRepository:
    def __init__(self) -> None:
        self._members: dict[tuple[str, str], WorkspaceMember] = {}
        self._lock = RLock()

    def delete_workspace_access(self, *, workspace_id: str) -> None:
        with self._lock:
            self._members = {
                key: member
                for key, member in self._members.items()
                if member.workspace_id != workspace_id
            }

    def ensure_member(
        self, *, workspace_id: str, user_id: str, role: str
    ) -> WorkspaceMember:
        with self._lock:
            key = (workspace_id, user_id)
            existing = self._members.get(key)
            if existing is not None:
                if WORKSPACE_ROLE_RANK[existing.role] >= WORKSPACE_ROLE_RANK[role]:
                    return existing
                promoted = replace(existing, role=role, updated_at=_now())
                self._members[key] = promoted
                return promoted
            now = _now()
            member = WorkspaceMember(
                workspace_id=workspace_id,
                user_id=user_id,
                role=role,
                created_at=now,
                updated_at=now,
            )
            self._members[key] = member
            return member

    def get_member(
        self, *, workspace_id: str, user_id: str
    ) -> WorkspaceMember | None:
        with self._lock:
            return self._members.get((workspace_id, user_id))


class PostgresWorkspaceAccessRepository:
    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url
        _require_psycopg()

    def delete_workspace_access(self, *, workspace_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM workspace_members WHERE workspace_id = %s",
                (workspace_id,),
            )

    def ensure_member(
        self, *, workspace_id: str, user_id: str, role: str
    ) -> WorkspaceMember:
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO workspace_members (
                    workspace_id, user_id, role, created_at, updated_at
                )
                VALUES (%s, %s, %s, NOW(), NOW())
                ON CONFLICT (workspace_id, user_id) DO UPDATE SET
                    role = CASE
                        WHEN CASE workspace_members.role
                            WHEN 'admin' THEN 3
                            WHEN 'editor' THEN 2
                            ELSE 1
                        END < CASE EXCLUDED.role
                            WHEN 'admin' THEN 3
                            WHEN 'editor' THEN 2
                            ELSE 1
                        END
                        THEN EXCLUDED.role
                        ELSE workspace_members.role
                    END,
                    updated_at = CASE
                        WHEN CASE workspace_members.role
                            WHEN 'admin' THEN 3
                            WHEN 'editor' THEN 2
                            ELSE 1
                        END < CASE EXCLUDED.role
                            WHEN 'admin' THEN 3
                            WHEN 'editor' THEN 2
                            ELSE 1
                        END
                        THEN NOW()
                        ELSE workspace_members.updated_at
                    END
                RETURNING workspace_id, user_id, role, created_at, updated_at
                """,
                (workspace_id, user_id, role),
            ).fetchone()
        return _postgres_member(row)

    def get_member(
        self, *, workspace_id: str, user_id: str
    ) -> WorkspaceMember | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT workspace_id, user_id, role, created_at, updated_at
                FROM workspace_members
                WHERE workspace_id = %s AND user_id = %s
                """,
                (workspace_id, user_id),
            ).fetchone()
        return _postgres_member(row) if row is not None else None

    def _connect(self):
        psycopg = _require_psycopg()
        return psycopg.connect(self._database_url)


class SQLiteWorkspaceAccessRepository:
    def __init__(self, *, database: LocalStateDatabase) -> None:
        self.database = database

    def delete_workspace_access(self, *, workspace_id: str) -> None:
        with self.database.transaction(immediate=True) as conn:
            conn.execute(
                "DELETE FROM workspace_members WHERE workspace_id = ?",
                (workspace_id,),
            )

    def ensure_member(
        self, *, workspace_id: str, user_id: str, role: str
    ) -> WorkspaceMember:
        with self.database.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT * FROM workspace_members "
                "WHERE workspace_id = ? AND user_id = ?",
                (workspace_id, user_id),
            ).fetchone()
            now = _now()
            if row is None:
                conn.execute(
                    "INSERT INTO workspace_members VALUES (?, ?, ?, ?, ?)",
                    (workspace_id, user_id, role, _iso(now), _iso(now)),
                )
                return WorkspaceMember(workspace_id, user_id, role, now, now)
            if WORKSPACE_ROLE_RANK[str(row["role"])] < WORKSPACE_ROLE_RANK[role]:
                conn.execute(
                    "UPDATE workspace_members SET role = ?, updated_at = ? "
                    "WHERE workspace_id = ? AND user_id = ?",
                    (role, _iso(now), workspace_id, user_id),
                )
                return WorkspaceMember(
                    workspace_id,
                    user_id,
                    role,
                    _dt(row["created_at"]),
                    now,
                )
        return _sqlite_member(row)

    def get_member(
        self, *, workspace_id: str, user_id: str
    ) -> WorkspaceMember | None:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM workspace_members "
                "WHERE workspace_id = ? AND user_id = ?",
                (workspace_id, user_id),
            ).fetchone()
        return _sqlite_member(row) if row is not None else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _postgres_member(row: tuple[object, ...]) -> WorkspaceMember:
    return WorkspaceMember(
        workspace_id=str(row[0]),
        user_id=str(row[1]),
        role=str(row[2]),
        created_at=row[3],  # type: ignore[arg-type]
        updated_at=row[4],  # type: ignore[arg-type]
    )


def _sqlite_member(row: sqlite3.Row) -> WorkspaceMember:
    return WorkspaceMember(
        workspace_id=str(row["workspace_id"]),
        user_id=str(row["user_id"]),
        role=str(row["role"]),
        created_at=_dt(row["created_at"]),
        updated_at=_dt(row["updated_at"]),
    )


__all__ = [
    "InMemoryWorkspaceAccessRepository",
    "PostgresWorkspaceAccessRepository",
    "SQLiteWorkspaceAccessRepository",
]
