"""SQLite repository for user-scoped L3 memories and profile snapshots."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import sqlite3

from ai_agent_platform.local_state import LocalStateDatabase
from ai_agent_platform.memory.models import (
    UserMemory,
    UserMemoryEvidence,
    UserMemorySettings,
    UserMemoryScene,
    UserProfileSnapshot,
)


class SQLiteUserMemoryRepository:
    def __init__(self, *, database: LocalStateDatabase) -> None:
        self.database = database

    def get_settings(self, *, user_id: str, default_mode: str) -> UserMemorySettings:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_memory_settings WHERE user_id = ?", (user_id,)
            ).fetchone()
        if row is None:
            return UserMemorySettings(user_id=user_id, mode=default_mode, updated_at=_now())
        return UserMemorySettings(
            user_id=str(row["user_id"]),
            mode=str(row["mode"]),
            updated_at=_dt(row["updated_at"]),
        )

    def update_settings(self, *, user_id: str, mode: str) -> UserMemorySettings:
        settings = UserMemorySettings(user_id=user_id, mode=mode, updated_at=_now())
        with self.database.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO user_memory_settings VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET mode=excluded.mode, updated_at=excluded.updated_at",
                (user_id, mode, _iso(settings.updated_at)),
            )
        return settings

    def create(self, memory: UserMemory, evidence: list[UserMemoryEvidence]) -> UserMemory:
        with self.database.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO user_memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _memory_values(memory),
            )
            self._insert_evidence(conn, evidence)
        return replace(memory, evidence=list(evidence))

    def get(self, memory_id: str) -> UserMemory | None:
        with self.database.connect() as conn:
            row = conn.execute("SELECT * FROM user_memories WHERE id = ?", (memory_id,)).fetchone()
            if row is None:
                return None
            evidence = conn.execute(
                "SELECT * FROM user_memory_evidence WHERE memory_id = ? ORDER BY created_at, id",
                (memory_id,),
            ).fetchall()
        return _memory_from_row(row, [_evidence_from_row(item) for item in evidence])

    def list(
        self,
        *,
        user_id: str,
        status: str | None = None,
        kind: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[UserMemory]:
        clauses = ["user_id = ?"]
        params: list[object] = [user_id]
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        params.extend((limit, offset))
        with self.database.connect() as conn:
            rows = conn.execute(
                f"SELECT id FROM user_memories WHERE {' AND '.join(clauses)} "
                "ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [item for row in rows if (item := self.get(str(row[0]))) is not None]

    def find_current(self, *, user_id: str, canonical_key: str) -> UserMemory | None:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT id FROM user_memories WHERE user_id = ? AND canonical_key = ? "
                "AND status IN ('active', 'candidate') ORDER BY updated_at DESC, id DESC LIMIT 1",
                (user_id, canonical_key),
            ).fetchone()
        return self.get(str(row[0])) if row is not None else None

    def update(
        self,
        memory: UserMemory,
        *,
        expected_version: int,
        evidence: list[UserMemoryEvidence] = (),
    ) -> UserMemory | None:
        with self.database.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT version FROM user_memories WHERE id = ?", (memory.id,)
            ).fetchone()
            if row is None or int(row[0]) != expected_version:
                return None
            conn.execute(
                """
                UPDATE user_memories SET user_id=?, kind=?, title=?, content=?, canonical_key=?,
                    status=?, confidence=?, importance=?, version=?, created_by=?, supersedes_id=?,
                    last_confirmed_at=?, updated_at=? WHERE id=?
                """,
                (
                    memory.user_id, memory.kind, memory.title, memory.content,
                    memory.canonical_key, memory.status, memory.confidence,
                    memory.importance, memory.version, memory.created_by,
                    memory.supersedes_id, _iso(memory.last_confirmed_at),
                    _iso(memory.updated_at), memory.id,
                ),
            )
            self._insert_evidence(conn, list(evidence))
        return self.get(memory.id)

    def delete(self, *, memory_id: str, user_id: str) -> bool:
        with self.database.transaction(immediate=True) as conn:
            cursor = conn.execute(
                "DELETE FROM user_memories WHERE id = ? AND user_id = ?",
                (memory_id, user_id),
            )
        return cursor.rowcount > 0

    def get_snapshot(self, user_id: str) -> UserProfileSnapshot | None:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_profile_snapshots WHERE user_id = ?", (user_id,)
            ).fetchone()
        if row is None:
            return None
        return UserProfileSnapshot(
            user_id=str(row["user_id"]),
            version=int(row["version"]),
            content=str(row["content"]),
            source_memory_ids=list(json.loads(str(row["source_memory_ids_json"]))),
            updated_at=_dt(row["updated_at"]),
        )

    def get_scene(self, *, user_id: str, workspace_id: str) -> UserMemoryScene | None:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM user_memory_scenes WHERE user_id = ? AND workspace_id = ?",
                (user_id, workspace_id),
            ).fetchone()
        return _scene_from_row(row) if row is not None else None

    def list_scenes(self, *, user_id: str) -> list[UserMemoryScene]:
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM user_memory_scenes WHERE user_id = ? "
                "ORDER BY updated_at DESC, id DESC",
                (user_id,),
            ).fetchall()
        return [_scene_from_row(row) for row in rows]

    def save_scene(self, scene: UserMemoryScene) -> UserMemoryScene:
        with self.database.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO user_memory_scenes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, workspace_id) DO UPDATE SET "
                "id=excluded.id, title=excluded.title, content=excluded.content, "
                "source_memory_ids_json=excluded.source_memory_ids_json, "
                "version=excluded.version, updated_at=excluded.updated_at",
                (
                    scene.id, scene.user_id, scene.workspace_id, scene.title,
                    scene.content, json.dumps(scene.source_memory_ids), scene.version,
                    _iso(scene.created_at), _iso(scene.updated_at),
                ),
            )
        return scene

    def delete_scene(self, *, user_id: str, workspace_id: str) -> bool:
        with self.database.transaction(immediate=True) as conn:
            cursor = conn.execute(
                "DELETE FROM user_memory_scenes WHERE user_id = ? AND workspace_id = ?",
                (user_id, workspace_id),
            )
        return cursor.rowcount > 0

    def save_snapshot(self, snapshot: UserProfileSnapshot) -> UserProfileSnapshot:
        with self.database.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO user_profile_snapshots VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET version=excluded.version, "
                "content=excluded.content, source_memory_ids_json=excluded.source_memory_ids_json, "
                "updated_at=excluded.updated_at",
                (
                    snapshot.user_id, snapshot.version, snapshot.content,
                    json.dumps(snapshot.source_memory_ids), _iso(snapshot.updated_at),
                ),
            )
        return snapshot

    def _insert_evidence(
        self, conn: sqlite3.Connection, evidence: list[UserMemoryEvidence]
    ) -> None:
        conn.executemany(
            "INSERT OR IGNORE INTO user_memory_evidence VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    item.id, item.memory_id, item.source_kind, item.source_id,
                    item.excerpt, _iso(item.created_at),
                )
                for item in evidence
            ],
        )


def _memory_values(memory: UserMemory) -> tuple[object, ...]:
    return (
        memory.id, memory.user_id, memory.kind, memory.title, memory.content,
        memory.canonical_key, memory.status, memory.confidence, memory.importance,
        memory.version, memory.created_by, memory.supersedes_id,
        _iso(memory.last_confirmed_at), _iso(memory.created_at), _iso(memory.updated_at),
    )


def _memory_from_row(row: sqlite3.Row, evidence: list[UserMemoryEvidence]) -> UserMemory:
    return UserMemory(
        id=str(row["id"]), user_id=str(row["user_id"]), kind=str(row["kind"]),
        title=str(row["title"]), content=str(row["content"]),
        canonical_key=str(row["canonical_key"]), status=str(row["status"]),
        confidence=float(row["confidence"]), importance=int(row["importance"]),
        version=int(row["version"]), created_by=str(row["created_by"]),
        supersedes_id=row["supersedes_id"], last_confirmed_at=_dt(row["last_confirmed_at"]),
        created_at=_dt(row["created_at"]), updated_at=_dt(row["updated_at"]), evidence=evidence,
    )


def _evidence_from_row(row: sqlite3.Row) -> UserMemoryEvidence:
    return UserMemoryEvidence(
        id=str(row["id"]), memory_id=str(row["memory_id"]),
        source_kind=str(row["source_kind"]), source_id=str(row["source_id"]),
        excerpt=row["excerpt"], created_at=_dt(row["created_at"]),
    )


def _scene_from_row(row: sqlite3.Row) -> UserMemoryScene:
    return UserMemoryScene(
        id=str(row["id"]), user_id=str(row["user_id"]),
        workspace_id=str(row["workspace_id"]), title=str(row["title"]),
        content=str(row["content"]),
        source_memory_ids=list(json.loads(str(row["source_memory_ids_json"]))),
        version=int(row["version"]), created_at=_dt(row["created_at"]),
        updated_at=_dt(row["updated_at"]),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


def _dt(value: object | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value)).astimezone(timezone.utc)


__all__ = ["SQLiteUserMemoryRepository"]
