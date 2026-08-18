"""SQLite source of truth for governed workspace project memory."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import re
import sqlite3
from uuid import uuid4

from ai_agent_platform.local_state import LocalStateDatabase
from ai_agent_platform.project_memory.models import (
    MemoryAuditEvent,
    MemoryEvidence,
    MemoryExtractionJob,
    MemoryIndexEvent,
    MemorySettings,
    ProjectMemory,
    WorkspaceMember,
)


class SQLiteProjectMemoryRepository:
    def __init__(self, *, database: LocalStateDatabase) -> None:
        self.database = database

    def ensure_member(self, *, workspace_id: str, user_id: str, role: str) -> WorkspaceMember:
        with self.database.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT * FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
                (workspace_id, user_id),
            ).fetchone()
            if row is None:
                now = _now()
                conn.execute(
                    "INSERT INTO workspace_members VALUES (?, ?, ?, ?, ?)",
                    (workspace_id, user_id, role, _iso(now), _iso(now)),
                )
                return WorkspaceMember(workspace_id, user_id, role, now, now)
        return _member_from_row(row)

    def get_member(self, *, workspace_id: str, user_id: str) -> WorkspaceMember | None:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
                (workspace_id, user_id),
            ).fetchone()
        return _member_from_row(row) if row is not None else None

    def record_audit_event(self, event: MemoryAuditEvent) -> None:
        with self.database.transaction(immediate=True) as conn:
            self._insert_audit(conn, event)

    def get_settings(self, *, workspace_id: str, default_mode: str) -> MemorySettings:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM workspace_memory_settings WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
        if row is None:
            return MemorySettings(workspace_id, default_mode, "system", _now())
        return MemorySettings(
            workspace_id=str(row["workspace_id"]),
            mode=str(row["mode"]),
            updated_by=str(row["updated_by"]),
            updated_at=_dt(row["updated_at"]),
        )

    def update_settings(self, *, workspace_id: str, mode: str, updated_by: str) -> MemorySettings:
        settings = MemorySettings(workspace_id, mode, updated_by, _now())
        with self.database.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO workspace_memory_settings VALUES (?, ?, ?, ?) "
                "ON CONFLICT(workspace_id) DO UPDATE SET mode=excluded.mode, "
                "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                (workspace_id, mode, updated_by, _iso(settings.updated_at)),
            )
        return settings

    def create_memory(
        self,
        memory: ProjectMemory,
        *,
        evidence: list[MemoryEvidence],
        audit: MemoryAuditEvent,
    ) -> ProjectMemory:
        with self.database.transaction(immediate=True) as conn:
            self._insert_memory(conn, memory)
            self._insert_evidence(conn, evidence)
            self._insert_audit(conn, audit)
            self._enqueue(conn, memory.id, "upsert", memory.version)
            self._upsert_fts(conn, memory)
        return replace(memory, evidence=list(evidence))

    def get_memory(self, memory_id: str) -> ProjectMemory | None:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM project_memories WHERE id = ?", (memory_id,)
            ).fetchone()
            if row is None:
                return None
            evidence = conn.execute(
                "SELECT * FROM project_memory_evidence WHERE memory_id = ? ORDER BY created_at, id",
                (memory_id,),
            ).fetchall()
        return _memory_from_row(row, [_evidence_from_row(item) for item in evidence])

    def find_current_by_key(
        self,
        *,
        workspace_id: str,
        workspace_revision: int,
        canonical_key: str,
    ) -> ProjectMemory | None:
        return self._find_key(
            workspace_id=workspace_id,
            workspace_revision=workspace_revision,
            canonical_key=canonical_key,
            statuses=("active", "candidate"),
        )

    def find_active_by_key(
        self,
        *,
        workspace_id: str,
        workspace_revision: int,
        canonical_key: str,
        exclude_memory_id: str | None = None,
    ) -> ProjectMemory | None:
        return self._find_key(
            workspace_id=workspace_id,
            workspace_revision=workspace_revision,
            canonical_key=canonical_key,
            statuses=("active",),
            exclude_memory_id=exclude_memory_id,
        )

    def _find_key(
        self,
        *,
        workspace_id: str,
        workspace_revision: int,
        canonical_key: str,
        statuses: tuple[str, ...],
        exclude_memory_id: str | None = None,
    ) -> ProjectMemory | None:
        placeholders = ",".join("?" for _ in statuses)
        params: list[object] = [workspace_id, workspace_revision, canonical_key, *statuses]
        exclusion = ""
        if exclude_memory_id is not None:
            exclusion = " AND id <> ?"
            params.append(exclude_memory_id)
        with self.database.connect() as conn:
            row = conn.execute(
                f"SELECT id FROM project_memories WHERE workspace_id = ? "
                f"AND workspace_revision = ? AND canonical_key = ? "
                f"AND status IN ({placeholders}){exclusion} "
                "ORDER BY updated_at DESC, id DESC LIMIT 1",
                params,
            ).fetchone()
        return self.get_memory(str(row[0])) if row is not None else None

    def list_memories(
        self,
        *,
        workspace_id: str,
        workspace_revision: int | None,
        status: str | None,
        kind: str | None,
        limit: int,
        offset: int,
    ) -> list[ProjectMemory]:
        clauses = ["workspace_id = ?"]
        params: list[object] = [workspace_id]
        if workspace_revision is not None:
            clauses.append("workspace_revision = ?")
            params.append(workspace_revision)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        params.extend((limit, offset))
        with self.database.connect() as conn:
            rows = conn.execute(
                f"SELECT id FROM project_memories WHERE {' AND '.join(clauses)} "
                "ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [item for row in rows if (item := self.get_memory(str(row[0]))) is not None]

    def update_memory(
        self,
        memory: ProjectMemory,
        *,
        expected_version: int,
        evidence: list[MemoryEvidence],
        audit: MemoryAuditEvent,
    ) -> ProjectMemory | None:
        with self.database.transaction(immediate=True) as conn:
            current = conn.execute(
                "SELECT version FROM project_memories WHERE id = ?", (memory.id,)
            ).fetchone()
            if current is None or int(current[0]) != expected_version:
                return None
            conn.execute(
                """
                UPDATE project_memories SET
                    workspace_id=?, workspace_revision=?, kind=?, title=?, content=?,
                    canonical_key=?, status=?, confidence=?, importance=?, version=?,
                    created_by=?, supersedes_id=?, expires_at=?, last_confirmed_at=?,
                    last_accessed_at=?, access_count=?, conflict=?, updated_at=?
                WHERE id=?
                """,
                _memory_update_values(memory),
            )
            self._insert_evidence(conn, evidence)
            self._insert_audit(conn, audit)
            self._enqueue(conn, memory.id, "upsert", memory.version)
            self._upsert_fts(conn, memory)
        return self.get_memory(memory.id)

    def delete_memory(
        self,
        *,
        memory_id: str,
        expected_workspace_id: str,
        audit: MemoryAuditEvent,
    ) -> bool:
        with self.database.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT version FROM project_memories WHERE id = ? AND workspace_id = ?",
                (memory_id, expected_workspace_id),
            ).fetchone()
            if row is None:
                return False
            self._insert_audit(conn, audit)
            conn.execute("DELETE FROM project_memories WHERE id = ?", (memory_id,))
            if self.database.fts5_available:
                conn.execute("DELETE FROM project_memories_fts WHERE memory_id = ?", (memory_id,))
            self._enqueue(conn, memory_id, "delete", int(row[0]) + 1)
        return True

    def search_lexical(
        self,
        *,
        workspace_id: str,
        workspace_revision: int,
        query: str,
        limit: int,
    ) -> list[tuple[str, float]]:
        now = _iso(_now())
        if self.database.fts5_available:
            match = _fts_query(query)
            if not match:
                return []
            with self.database.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT p.id, bm25(project_memories_fts) AS rank
                    FROM project_memories_fts f
                    JOIN project_memories p ON p.id = f.memory_id
                    WHERE project_memories_fts MATCH ?
                      AND p.workspace_id = ? AND p.workspace_revision = ?
                      AND p.status = 'active'
                      AND (p.expires_at IS NULL OR p.expires_at > ?)
                    ORDER BY rank ASC, p.id ASC LIMIT ?
                    """,
                    (match, workspace_id, workspace_revision, now, limit),
                ).fetchall()
            return [(str(row[0]), 1.0 / (1.0 + abs(float(row[1])))) for row in rows]
        tokens = _tokens(query)
        if not tokens:
            return []
        with self.database.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, title, kind, content FROM project_memories
                WHERE workspace_id = ? AND workspace_revision = ? AND status = 'active'
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (workspace_id, workspace_revision, now),
            ).fetchall()
        scored = []
        for row in rows:
            overlap = len(tokens & _tokens(f"{row[1]} {row[2]} {row[3]}"))
            if overlap:
                scored.append((str(row[0]), overlap / len(tokens)))
        return sorted(scored, key=lambda item: (-item[1], item[0]))[:limit]

    def record_access(self, memory_ids: list[str]) -> None:
        if not memory_ids:
            return
        now = _iso(_now())
        with self.database.transaction(immediate=True) as conn:
            conn.executemany(
                "UPDATE project_memories SET last_accessed_at = ?, access_count = access_count + 1 WHERE id = ?",
                [(now, memory_id) for memory_id in memory_ids],
            )

    def create_extraction_job(self, job: MemoryExtractionJob) -> MemoryExtractionJob | None:
        with self.database.transaction(immediate=True) as conn:
            try:
                conn.execute(
                    "INSERT INTO memory_extraction_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    _job_values(job),
                )
            except sqlite3.IntegrityError:
                return None
        return job

    def get_extraction_job(
        self, *, workspace_id: str, source_type: str, source_id: str
    ) -> MemoryExtractionJob | None:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_extraction_jobs WHERE workspace_id = ? AND source_type = ? AND source_id = ?",
                (workspace_id, source_type, source_id),
            ).fetchone()
        return _job_from_row(row) if row is not None else None

    def update_extraction_job(self, job: MemoryExtractionJob) -> MemoryExtractionJob:
        with self.database.transaction(immediate=True) as conn:
            conn.execute(
                """
                UPDATE memory_extraction_jobs SET status=?, attempts=?, candidate_count=?,
                    active_count=?, error=?, input_tokens=?, output_tokens=?,
                    updated_at=?, completed_at=? WHERE id=?
                """,
                (
                    job.status, job.attempts, job.candidate_count, job.active_count,
                    job.error, job.input_tokens, job.output_tokens, _iso(job.updated_at),
                    _iso(job.completed_at), job.id,
                ),
            )
        return job

    def list_extraction_jobs(self, *, workspace_id: str, limit: int) -> list[MemoryExtractionJob]:
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_extraction_jobs WHERE workspace_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (workspace_id, limit),
            ).fetchall()
        return [_job_from_row(row) for row in rows]

    def list_index_events(self, *, limit: int) -> list[MemoryIndexEvent]:
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_index_outbox WHERE status IN ('pending', 'failed') "
                "AND attempts < 5 ORDER BY created_at, id LIMIT ?",
                (limit,),
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def mark_index_event(self, *, event_id: str, status: str, error: str | None) -> None:
        with self.database.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE memory_index_outbox SET status=?, attempts=attempts+1, error=?, updated_at=? WHERE id=?",
                (status, error, _iso(_now()), event_id),
            )

    def enqueue_index_event(
        self, *, memory_id: str, operation: str, memory_version: int
    ) -> None:
        with self.database.transaction(immediate=True) as conn:
            self._enqueue(conn, memory_id, operation, memory_version)

    def count_pending_index_events(self) -> int:
        with self.database.connect() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM memory_index_outbox WHERE status IN ('pending', 'failed') AND attempts < 5"
                ).fetchone()[0]
            )

    def enqueue_reindex(self, *, workspace_id: str, workspace_revision: int) -> int:
        with self.database.transaction(immediate=True) as conn:
            rows = conn.execute(
                "SELECT id, version FROM project_memories WHERE workspace_id = ? "
                "AND workspace_revision = ? AND status = 'active'",
                (workspace_id, workspace_revision),
            ).fetchall()
            for row in rows:
                self._enqueue(conn, str(row[0]), "upsert", int(row[1]))
        return len(rows)

    def _insert_memory(self, conn: sqlite3.Connection, memory: ProjectMemory) -> None:
        conn.execute(
            "INSERT INTO project_memories VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            _memory_values(memory),
        )

    def _insert_evidence(self, conn: sqlite3.Connection, evidence: list[MemoryEvidence]) -> None:
        conn.executemany(
            "INSERT OR IGNORE INTO project_memory_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    item.id, item.memory_id, item.source_kind, item.source_id, item.path,
                    item.start_line, item.end_line, item.content_hash, item.excerpt,
                    _iso(item.created_at),
                )
                for item in evidence
            ],
        )

    def _insert_audit(self, conn: sqlite3.Connection, audit: MemoryAuditEvent) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO memory_audit_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                audit.id, audit.workspace_id, audit.memory_id, audit.action,
                audit.actor_user_id, json.dumps(audit.metadata, ensure_ascii=False),
                _iso(audit.created_at),
            ),
        )

    def _enqueue(
        self, conn: sqlite3.Connection, memory_id: str, operation: str, memory_version: int
    ) -> None:
        now = _now()
        conn.execute(
            "INSERT INTO memory_index_outbox VALUES (?, ?, ?, ?, 'pending', 0, NULL, ?, ?)",
            (f"midx_{uuid4().hex[:16]}", memory_id, operation, memory_version, _iso(now), _iso(now)),
        )

    def _upsert_fts(self, conn: sqlite3.Connection, memory: ProjectMemory) -> None:
        if not self.database.fts5_available:
            return
        conn.execute("DELETE FROM project_memories_fts WHERE memory_id = ?", (memory.id,))
        conn.execute(
            "INSERT INTO project_memories_fts(memory_id, title, kind, content) VALUES (?, ?, ?, ?)",
            (memory.id, memory.title, memory.kind, memory.content),
        )


def _memory_values(memory: ProjectMemory) -> tuple[object, ...]:
    return (
        memory.id, memory.workspace_id, memory.workspace_revision, memory.kind,
        memory.title, memory.content, memory.canonical_key, memory.status,
        memory.confidence, memory.importance, memory.version, memory.created_by,
        memory.supersedes_id, _iso(memory.expires_at), _iso(memory.last_confirmed_at),
        _iso(memory.last_accessed_at), memory.access_count, int(memory.conflict),
        _iso(memory.created_at), _iso(memory.updated_at),
    )


def _memory_update_values(memory: ProjectMemory) -> tuple[object, ...]:
    values = _memory_values(memory)
    return (*values[1:12], *values[12:18], values[19], memory.id)


def _memory_from_row(row: sqlite3.Row, evidence: list[MemoryEvidence]) -> ProjectMemory:
    return ProjectMemory(
        id=str(row["id"]), workspace_id=str(row["workspace_id"]),
        workspace_revision=int(row["workspace_revision"]), kind=str(row["kind"]),
        title=str(row["title"]), content=str(row["content"]),
        canonical_key=str(row["canonical_key"]), status=str(row["status"]),
        confidence=float(row["confidence"]), importance=int(row["importance"]),
        version=int(row["version"]), created_by=str(row["created_by"]),
        supersedes_id=row["supersedes_id"], expires_at=_dt(row["expires_at"]),
        last_confirmed_at=_dt(row["last_confirmed_at"]),
        last_accessed_at=_dt(row["last_accessed_at"]), access_count=int(row["access_count"]),
        evidence=evidence, conflict=bool(row["conflict"]),
        created_at=_dt(row["created_at"]), updated_at=_dt(row["updated_at"]),
    )


def _evidence_from_row(row: sqlite3.Row) -> MemoryEvidence:
    return MemoryEvidence(
        id=str(row["id"]), memory_id=str(row["memory_id"]),
        source_kind=str(row["source_kind"]), source_id=str(row["source_id"]),
        path=row["path"], start_line=row["start_line"], end_line=row["end_line"],
        content_hash=row["content_hash"], excerpt=row["excerpt"], created_at=_dt(row["created_at"]),
    )


def _member_from_row(row: sqlite3.Row) -> WorkspaceMember:
    return WorkspaceMember(
        workspace_id=str(row["workspace_id"]), user_id=str(row["user_id"]),
        role=str(row["role"]), created_at=_dt(row["created_at"]), updated_at=_dt(row["updated_at"]),
    )


def _job_values(job: MemoryExtractionJob) -> tuple[object, ...]:
    return (
        job.id, job.workspace_id, job.workspace_revision, job.source_type, job.source_id,
        job.status, job.attempts, job.candidate_count, job.active_count, job.error,
        job.input_tokens, job.output_tokens, _iso(job.created_at), _iso(job.updated_at),
        _iso(job.completed_at),
    )


def _job_from_row(row: sqlite3.Row) -> MemoryExtractionJob:
    return MemoryExtractionJob(
        id=str(row["id"]), workspace_id=str(row["workspace_id"]),
        workspace_revision=int(row["workspace_revision"]), source_type=str(row["source_type"]),
        source_id=str(row["source_id"]), status=str(row["status"]), attempts=int(row["attempts"]),
        candidate_count=int(row["candidate_count"]), active_count=int(row["active_count"]),
        error=row["error"], input_tokens=int(row["input_tokens"]), output_tokens=int(row["output_tokens"]),
        created_at=_dt(row["created_at"]), updated_at=_dt(row["updated_at"]),
        completed_at=_dt(row["completed_at"]),
    )


def _event_from_row(row: sqlite3.Row) -> MemoryIndexEvent:
    return MemoryIndexEvent(
        id=str(row["id"]), memory_id=str(row["memory_id"]), operation=str(row["operation"]),
        memory_version=int(row["memory_version"]), status=str(row["status"]),
        attempts=int(row["attempts"]), error=row["error"],
        created_at=_dt(row["created_at"]), updated_at=_dt(row["updated_at"]),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


def _dt(value: object | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value)).astimezone(timezone.utc)


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]", value.casefold()))


def _fts_query(value: str) -> str:
    return " OR ".join(f'"{token}"' for token in sorted(_tokens(value))[:24])


__all__ = ["SQLiteProjectMemoryRepository"]
