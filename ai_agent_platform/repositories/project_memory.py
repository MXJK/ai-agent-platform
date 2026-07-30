"""In-memory and PostgreSQL repositories for project memory."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import re
from threading import RLock
from typing import Any
from uuid import uuid4

from ai_agent_platform.project_memory.models import (
    MemoryAuditEvent,
    MemoryEvidence,
    MemoryExtractionJob,
    MemoryIndexEvent,
    MemorySettings,
    ProjectMemory,
    WorkspaceMember,
)
from ai_agent_platform.repositories.postgres import (
    PostgresDependencyError,
    _require_jsonb,
    _require_psycopg,
)


class InMemoryProjectMemoryRepository:
    """Thread-safe local repository with the same lifecycle contract as PostgreSQL."""

    def __init__(self) -> None:
        self._members: dict[tuple[str, str], WorkspaceMember] = {}
        self._settings: dict[str, MemorySettings] = {}
        self._memories: dict[str, ProjectMemory] = {}
        self._jobs: dict[str, MemoryExtractionJob] = {}
        self._job_sources: dict[tuple[str, str, str], str] = {}
        self._index_events: dict[str, MemoryIndexEvent] = {}
        self._audit_events: list[MemoryAuditEvent] = []
        self._lock = RLock()

    def ensure_member(
        self, *, workspace_id: str, user_id: str, role: str
    ) -> WorkspaceMember:
        with self._lock:
            key = (workspace_id, user_id)
            existing = self._members.get(key)
            if existing is not None:
                return existing
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

    def get_member(self, *, workspace_id: str, user_id: str) -> WorkspaceMember | None:
        with self._lock:
            return self._members.get((workspace_id, user_id))

    def record_audit_event(self, event: MemoryAuditEvent) -> None:
        with self._lock:
            self._audit_events.append(event)

    def get_settings(
        self, *, workspace_id: str, default_mode: str
    ) -> MemorySettings:
        with self._lock:
            existing = self._settings.get(workspace_id)
            if existing is not None:
                return existing
            return MemorySettings(
                workspace_id=workspace_id,
                mode=default_mode,
                updated_by="system",
                updated_at=_now(),
            )

    def update_settings(
        self, *, workspace_id: str, mode: str, updated_by: str
    ) -> MemorySettings:
        with self._lock:
            settings = MemorySettings(
                workspace_id=workspace_id,
                mode=mode,
                updated_by=updated_by,
                updated_at=_now(),
            )
            self._settings[workspace_id] = settings
            return settings

    def create_memory(
        self,
        memory: ProjectMemory,
        *,
        evidence: list[MemoryEvidence],
        audit: MemoryAuditEvent,
    ) -> ProjectMemory:
        with self._lock:
            stored = replace(memory, evidence=list(evidence))
            self._memories[memory.id] = stored
            self._audit_events.append(audit)
            self._enqueue_event(memory.id, "upsert", memory.version)
            return stored

    def get_memory(self, memory_id: str) -> ProjectMemory | None:
        with self._lock:
            return self._memories.get(memory_id)

    def find_current_by_key(
        self,
        *,
        workspace_id: str,
        workspace_revision: int,
        canonical_key: str,
    ) -> ProjectMemory | None:
        with self._lock:
            matches = [
                item
                for item in self._memories.values()
                if item.workspace_id == workspace_id
                and item.workspace_revision == workspace_revision
                and item.canonical_key == canonical_key
                and item.status in {"active", "candidate"}
            ]
        matches.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        return matches[0] if matches else None

    def find_active_by_key(
        self,
        *,
        workspace_id: str,
        workspace_revision: int,
        canonical_key: str,
        exclude_memory_id: str | None = None,
    ) -> ProjectMemory | None:
        with self._lock:
            matches = [
                item
                for item in self._memories.values()
                if item.workspace_id == workspace_id
                and item.workspace_revision == workspace_revision
                and item.canonical_key == canonical_key
                and item.status == "active"
                and item.id != exclude_memory_id
            ]
        matches.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        return matches[0] if matches else None

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
        with self._lock:
            items = [
                item
                for item in self._memories.values()
                if item.workspace_id == workspace_id
                and (
                    workspace_revision is None
                    or item.workspace_revision == workspace_revision
                )
                and (status is None or item.status == status)
                and (kind is None or item.kind == kind)
            ]
        items.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        return items[offset : offset + limit]

    def update_memory(
        self,
        memory: ProjectMemory,
        *,
        expected_version: int,
        evidence: list[MemoryEvidence],
        audit: MemoryAuditEvent,
    ) -> ProjectMemory | None:
        with self._lock:
            current = self._memories.get(memory.id)
            if current is None or current.version != expected_version:
                return None
            merged = list(current.evidence)
            known = {item.id for item in merged}
            merged.extend(item for item in evidence if item.id not in known)
            stored = replace(memory, evidence=merged)
            self._memories[memory.id] = stored
            self._audit_events.append(audit)
            self._enqueue_event(memory.id, "upsert", memory.version)
            return stored

    def delete_memory(
        self,
        *,
        memory_id: str,
        expected_workspace_id: str,
        audit: MemoryAuditEvent,
    ) -> bool:
        with self._lock:
            current = self._memories.get(memory_id)
            if current is None or current.workspace_id != expected_workspace_id:
                return False
            del self._memories[memory_id]
            self._audit_events.append(audit)
            self._enqueue_event(memory_id, "delete", current.version + 1)
            return True

    def search_lexical(
        self,
        *,
        workspace_id: str,
        workspace_revision: int,
        query: str,
        limit: int,
    ) -> list[tuple[str, float]]:
        query_tokens = _tokens(query)
        now = _now()
        scored: list[tuple[str, float]] = []
        with self._lock:
            memories = list(self._memories.values())
        for memory in memories:
            if (
                memory.workspace_id != workspace_id
                or memory.workspace_revision != workspace_revision
                or memory.status != "active"
                or (memory.expires_at is not None and memory.expires_at <= now)
            ):
                continue
            memory_tokens = _tokens(
                f"{memory.title} {memory.kind} {memory.content}"
            )
            overlap = len(query_tokens & memory_tokens)
            if overlap:
                scored.append((memory.id, overlap / max(len(query_tokens), 1)))
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:limit]

    def record_access(self, memory_ids: list[str]) -> None:
        with self._lock:
            for memory_id in memory_ids:
                memory = self._memories.get(memory_id)
                if memory is not None:
                    self._memories[memory_id] = replace(
                        memory,
                        last_accessed_at=_now(),
                        access_count=memory.access_count + 1,
                    )

    def create_extraction_job(
        self, job: MemoryExtractionJob
    ) -> MemoryExtractionJob | None:
        with self._lock:
            key = (job.workspace_id, job.source_type, job.source_id)
            if key in self._job_sources:
                return None
            self._jobs[job.id] = job
            self._job_sources[key] = job.id
            return job

    def get_extraction_job(
        self,
        *,
        workspace_id: str,
        source_type: str,
        source_id: str,
    ) -> MemoryExtractionJob | None:
        with self._lock:
            job_id = self._job_sources.get(
                (workspace_id, source_type, source_id)
            )
            return self._jobs.get(job_id) if job_id is not None else None

    def update_extraction_job(
        self, job: MemoryExtractionJob
    ) -> MemoryExtractionJob:
        with self._lock:
            self._jobs[job.id] = job
            return job

    def list_extraction_jobs(
        self, *, workspace_id: str, limit: int
    ) -> list[MemoryExtractionJob]:
        with self._lock:
            jobs = [
                job for job in self._jobs.values() if job.workspace_id == workspace_id
            ]
        jobs.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return jobs[:limit]

    def list_index_events(self, *, limit: int) -> list[MemoryIndexEvent]:
        with self._lock:
            events = [
                event
                for event in self._index_events.values()
                if event.status in {"pending", "failed"}
            ]
        events.sort(key=lambda item: (item.created_at, item.id))
        return events[:limit]

    def mark_index_event(
        self, *, event_id: str, status: str, error: str | None
    ) -> None:
        with self._lock:
            event = self._index_events[event_id]
            self._index_events[event_id] = replace(
                event,
                status=status,
                attempts=event.attempts + 1,
                error=error,
                updated_at=_now(),
            )

    def enqueue_index_event(
        self,
        *,
        memory_id: str,
        operation: str,
        memory_version: int,
    ) -> None:
        with self._lock:
            self._enqueue_event(memory_id, operation, memory_version)

    def count_pending_index_events(self) -> int:
        with self._lock:
            return sum(
                event.status in {"pending", "failed"} and event.attempts < 5
                for event in self._index_events.values()
            )

    def enqueue_reindex(
        self, *, workspace_id: str, workspace_revision: int
    ) -> int:
        with self._lock:
            memories = [
                item
                for item in self._memories.values()
                if item.workspace_id == workspace_id
                and item.workspace_revision == workspace_revision
                and item.status == "active"
            ]
            for memory in memories:
                self._enqueue_event(memory.id, "upsert", memory.version)
            return len(memories)

    def _enqueue_event(
        self, memory_id: str, operation: str, memory_version: int
    ) -> None:
        now = _now()
        event = MemoryIndexEvent(
            id=f"midx_{uuid4().hex[:16]}",
            memory_id=memory_id,
            operation=operation,
            memory_version=memory_version,
            status="pending",
            attempts=0,
            error=None,
            created_at=now,
            updated_at=now,
        )
        self._index_events[event.id] = event


class PostgresProjectMemoryRepository:
    """PostgreSQL source of truth for project memory and index outbox."""

    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url
        _require_psycopg()

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
                    updated_at = workspace_members.updated_at
                RETURNING workspace_id, user_id, role, created_at, updated_at
                """,
                (workspace_id, user_id, role),
            ).fetchone()
        return _member_from_row(row)

    def get_member(self, *, workspace_id: str, user_id: str) -> WorkspaceMember | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT workspace_id, user_id, role, created_at, updated_at
                FROM workspace_members
                WHERE workspace_id = %s AND user_id = %s
                """,
                (workspace_id, user_id),
            ).fetchone()
        return _member_from_row(row) if row is not None else None

    def record_audit_event(self, event: MemoryAuditEvent) -> None:
        with self._connect() as conn:
            self._insert_audit(conn, event)

    def get_settings(
        self, *, workspace_id: str, default_mode: str
    ) -> MemorySettings:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT workspace_id, mode, updated_by, updated_at
                FROM workspace_memory_settings
                WHERE workspace_id = %s
                """,
                (workspace_id,),
            ).fetchone()
        if row is not None:
            return _settings_from_row(row)
        return MemorySettings(workspace_id, default_mode, "system", _now())

    def update_settings(
        self, *, workspace_id: str, mode: str, updated_by: str
    ) -> MemorySettings:
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO workspace_memory_settings (
                    workspace_id, mode, updated_by, updated_at
                )
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (workspace_id) DO UPDATE SET
                    mode = EXCLUDED.mode,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
                RETURNING workspace_id, mode, updated_by, updated_at
                """,
                (workspace_id, mode, updated_by),
            ).fetchone()
        return _settings_from_row(row)

    def create_memory(
        self,
        memory: ProjectMemory,
        *,
        evidence: list[MemoryEvidence],
        audit: MemoryAuditEvent,
    ) -> ProjectMemory:
        with self._connect() as conn:
            self._insert_memory(conn, memory)
            self._insert_evidence(conn, evidence)
            self._insert_audit(conn, audit)
            self._insert_outbox(conn, memory.id, "upsert", memory.version)
        return replace(memory, evidence=list(evidence))

    def get_memory(self, memory_id: str) -> ProjectMemory | None:
        with self._connect() as conn:
            row = conn.execute(
                _MEMORY_SELECT + " WHERE memories.id = %s",
                (memory_id,),
            ).fetchone()
            if row is None:
                return None
            evidence_rows = conn.execute(
                _EVIDENCE_SELECT
                + " WHERE memory_id = %s ORDER BY created_at ASC, id ASC",
                (memory_id,),
            ).fetchall()
        return _memory_from_row(row, [_evidence_from_row(item) for item in evidence_rows])

    def find_current_by_key(
        self,
        *,
        workspace_id: str,
        workspace_revision: int,
        canonical_key: str,
    ) -> ProjectMemory | None:
        with self._connect() as conn:
            row = conn.execute(
                _MEMORY_SELECT
                + """
                WHERE memories.workspace_id = %s
                  AND memories.workspace_revision = %s
                  AND memories.canonical_key = %s
                  AND memories.status IN ('active', 'candidate')
                ORDER BY memories.updated_at DESC, memories.id DESC
                LIMIT 1
                """,
                (workspace_id, workspace_revision, canonical_key),
            ).fetchone()
        return _memory_from_row(row, []) if row is not None else None

    def find_active_by_key(
        self,
        *,
        workspace_id: str,
        workspace_revision: int,
        canonical_key: str,
        exclude_memory_id: str | None = None,
    ) -> ProjectMemory | None:
        clauses = [
            "memories.workspace_id = %s",
            "memories.workspace_revision = %s",
            "memories.canonical_key = %s",
            "memories.status = 'active'",
        ]
        params: list[Any] = [
            workspace_id,
            workspace_revision,
            canonical_key,
        ]
        if exclude_memory_id is not None:
            clauses.append("memories.id <> %s")
            params.append(exclude_memory_id)
        with self._connect() as conn:
            row = conn.execute(
                _MEMORY_SELECT
                + " WHERE "
                + " AND ".join(clauses)
                + " ORDER BY memories.updated_at DESC, memories.id DESC LIMIT 1",
                tuple(params),
            ).fetchone()
        return _memory_from_row(row, []) if row is not None else None

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
        clauses = ["memories.workspace_id = %s"]
        params: list[Any] = [workspace_id]
        if workspace_revision is not None:
            clauses.append("memories.workspace_revision = %s")
            params.append(workspace_revision)
        if status is not None:
            clauses.append("memories.status = %s")
            params.append(status)
        if kind is not None:
            clauses.append("memories.kind = %s")
            params.append(kind)
        params.extend([limit, offset])
        with self._connect() as conn:
            rows = conn.execute(
                _MEMORY_SELECT
                + " WHERE "
                + " AND ".join(clauses)
                + """
                ORDER BY memories.updated_at DESC, memories.id DESC
                LIMIT %s OFFSET %s
                """,
                tuple(params),
            ).fetchall()
        return [_memory_from_row(row, []) for row in rows]

    def update_memory(
        self,
        memory: ProjectMemory,
        *,
        expected_version: int,
        evidence: list[MemoryEvidence],
        audit: MemoryAuditEvent,
    ) -> ProjectMemory | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                UPDATE project_memories
                SET
                    kind = %s,
                    title = %s,
                    content = %s,
                    canonical_key = %s,
                    search_text = %s,
                    status = %s,
                    confidence = %s,
                    importance = %s,
                    version = %s,
                    supersedes_id = %s,
                    expires_at = %s,
                    last_confirmed_at = %s,
                    updated_at = %s,
                    conflict = %s
                WHERE id = %s AND version = %s
                RETURNING id
                """,
                (
                    memory.kind,
                    memory.title,
                    memory.content,
                    memory.canonical_key,
                    _search_text(memory),
                    memory.status,
                    memory.confidence,
                    memory.importance,
                    memory.version,
                    memory.supersedes_id,
                    memory.expires_at,
                    memory.last_confirmed_at,
                    memory.updated_at,
                    memory.conflict,
                    memory.id,
                    expected_version,
                ),
            ).fetchone()
            if row is None:
                return None
            self._insert_evidence(conn, evidence)
            self._insert_audit(conn, audit)
            self._insert_outbox(conn, memory.id, "upsert", memory.version)
        return self.get_memory(memory.id)

    def delete_memory(
        self,
        *,
        memory_id: str,
        expected_workspace_id: str,
        audit: MemoryAuditEvent,
    ) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                DELETE FROM project_memories
                WHERE id = %s AND workspace_id = %s
                RETURNING version
                """,
                (memory_id, expected_workspace_id),
            ).fetchone()
            if row is None:
                return False
            self._insert_audit(conn, audit)
            self._insert_outbox(conn, memory_id, "delete", int(row[0]) + 1)
        return True

    def search_lexical(
        self,
        *,
        workspace_id: str,
        workspace_revision: int,
        query: str,
        limit: int,
    ) -> list[tuple[str, float]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                WITH lexical_query AS (
                    SELECT websearch_to_tsquery('simple', %s) AS value
                )
                SELECT memories.id,
                       ts_rank_cd(memories.search_vector, lexical_query.value)
                FROM project_memories AS memories
                CROSS JOIN lexical_query
                WHERE memories.workspace_id = %s
                  AND memories.workspace_revision = %s
                  AND memories.status = 'active'
                  AND (
                      memories.expires_at IS NULL
                      OR memories.expires_at > NOW()
                  )
                  AND memories.search_vector @@ lexical_query.value
                ORDER BY 2 DESC, memories.id ASC
                LIMIT %s
                """,
                (query, workspace_id, workspace_revision, limit),
            ).fetchall()
        return [(str(row[0]), float(row[1])) for row in rows]

    def record_access(self, memory_ids: list[str]) -> None:
        if not memory_ids:
            return
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE project_memories
                SET
                    last_accessed_at = NOW(),
                    access_count = access_count + 1
                WHERE id = ANY(%s)
                """,
                (memory_ids,),
            )

    def create_extraction_job(
        self, job: MemoryExtractionJob
    ) -> MemoryExtractionJob | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO memory_extraction_jobs (
                    id, workspace_id, workspace_revision, source_type, source_id,
                    status, attempts, candidate_count, active_count, error,
                    input_tokens, output_tokens, created_at, updated_at, completed_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
                ON CONFLICT (workspace_id, source_type, source_id) DO NOTHING
                RETURNING
                    id, workspace_id, workspace_revision, source_type, source_id,
                    status, attempts, candidate_count, active_count, error,
                    input_tokens, output_tokens, created_at, updated_at, completed_at
                """,
                _job_values(job),
            ).fetchone()
        return _job_from_row(row) if row is not None else None

    def get_extraction_job(
        self,
        *,
        workspace_id: str,
        source_type: str,
        source_id: str,
    ) -> MemoryExtractionJob | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    id, workspace_id, workspace_revision, source_type, source_id,
                    status, attempts, candidate_count, active_count, error,
                    input_tokens, output_tokens, created_at, updated_at, completed_at
                FROM memory_extraction_jobs
                WHERE workspace_id = %s
                  AND source_type = %s
                  AND source_id = %s
                """,
                (workspace_id, source_type, source_id),
            ).fetchone()
        return _job_from_row(row) if row is not None else None

    def update_extraction_job(
        self, job: MemoryExtractionJob
    ) -> MemoryExtractionJob:
        with self._connect() as conn:
            row = conn.execute(
                """
                UPDATE memory_extraction_jobs
                SET status = %s, attempts = %s,
                    candidate_count = %s, active_count = %s,
                    error = %s, input_tokens = %s, output_tokens = %s,
                    updated_at = %s, completed_at = %s
                WHERE id = %s
                RETURNING
                    id, workspace_id, workspace_revision, source_type, source_id,
                    status, attempts, candidate_count, active_count, error,
                    input_tokens, output_tokens, created_at, updated_at, completed_at
                """,
                (
                    job.status,
                    job.attempts,
                    job.candidate_count,
                    job.active_count,
                    job.error,
                    job.input_tokens,
                    job.output_tokens,
                    job.updated_at,
                    job.completed_at,
                    job.id,
                ),
            ).fetchone()
        if row is None:
            raise KeyError(job.id)
        return _job_from_row(row)

    def list_extraction_jobs(
        self, *, workspace_id: str, limit: int
    ) -> list[MemoryExtractionJob]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    id, workspace_id, workspace_revision, source_type, source_id,
                    status, attempts, candidate_count, active_count, error,
                    input_tokens, output_tokens, created_at, updated_at, completed_at
                FROM memory_extraction_jobs
                WHERE workspace_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (workspace_id, limit),
            ).fetchall()
        return [_job_from_row(row) for row in rows]

    def list_index_events(self, *, limit: int) -> list[MemoryIndexEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, memory_id, operation, memory_version, status,
                       attempts, error, created_at, updated_at
                FROM memory_index_outbox
                WHERE status IN ('pending', 'failed')
                ORDER BY created_at ASC, id ASC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [_index_event_from_row(row) for row in rows]

    def mark_index_event(
        self, *, event_id: str, status: str, error: str | None
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE memory_index_outbox
                SET status = %s, attempts = attempts + 1, error = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (status, error, event_id),
            )

    def enqueue_index_event(
        self,
        *,
        memory_id: str,
        operation: str,
        memory_version: int,
    ) -> None:
        with self._connect() as conn:
            self._insert_outbox(
                conn,
                memory_id,
                operation,
                memory_version,
            )

    def count_pending_index_events(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*)
                FROM memory_index_outbox
                WHERE status IN ('pending', 'failed') AND attempts < 5
                """
            ).fetchone()
        return int(row[0])

    def enqueue_reindex(
        self, *, workspace_id: str, workspace_revision: int
    ) -> int:
        with self._connect() as conn:
            rows = conn.execute(
                """
                INSERT INTO memory_index_outbox (
                    id, memory_id, operation, memory_version, status,
                    attempts, created_at, updated_at
                )
                SELECT
                    'midx_' || md5(random()::text || clock_timestamp()::text || id),
                    id, 'upsert', version, 'pending', 0, NOW(), NOW()
                FROM project_memories
                WHERE workspace_id = %s
                  AND workspace_revision = %s
                  AND status = 'active'
                RETURNING id
                """,
                (workspace_id, workspace_revision),
            ).fetchall()
        return len(rows)

    def _insert_memory(self, conn, memory: ProjectMemory) -> None:
        conn.execute(
            """
            INSERT INTO project_memories (
                id, workspace_id, workspace_revision, kind, title, content,
                canonical_key, search_text, status, confidence, importance,
                version, created_by, supersedes_id, expires_at,
                last_confirmed_at, last_accessed_at, access_count, conflict,
                created_at, updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                memory.id,
                memory.workspace_id,
                memory.workspace_revision,
                memory.kind,
                memory.title,
                memory.content,
                memory.canonical_key,
                _search_text(memory),
                memory.status,
                memory.confidence,
                memory.importance,
                memory.version,
                memory.created_by,
                memory.supersedes_id,
                memory.expires_at,
                memory.last_confirmed_at,
                memory.last_accessed_at,
                memory.access_count,
                memory.conflict,
                memory.created_at,
                memory.updated_at,
            ),
        )

    def _insert_evidence(self, conn, evidence: list[MemoryEvidence]) -> None:
        for item in evidence:
            conn.execute(
                """
                INSERT INTO project_memory_evidence (
                    id, memory_id, source_kind, source_id, path,
                    start_line, end_line, content_hash, excerpt, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    item.id,
                    item.memory_id,
                    item.source_kind,
                    item.source_id,
                    item.path,
                    item.start_line,
                    item.end_line,
                    item.content_hash,
                    item.excerpt,
                    item.created_at,
                ),
            )

    def _insert_audit(self, conn, audit: MemoryAuditEvent) -> None:
        Jsonb = _require_jsonb()
        conn.execute(
            """
            INSERT INTO memory_audit_events (
                id, workspace_id, memory_id, action,
                actor_user_id, metadata, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                audit.id,
                audit.workspace_id,
                audit.memory_id,
                audit.action,
                audit.actor_user_id,
                Jsonb(audit.metadata),
                audit.created_at,
            ),
        )

    def _insert_outbox(
        self, conn, memory_id: str, operation: str, memory_version: int
    ) -> None:
        conn.execute(
            """
            INSERT INTO memory_index_outbox (
                id, memory_id, operation, memory_version, status,
                attempts, error, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, 'pending', 0, NULL, NOW(), NOW())
            """,
            (
                f"midx_{uuid4().hex[:16]}",
                memory_id,
                operation,
                memory_version,
            ),
        )

    def _connect(self):
        psycopg = _require_psycopg()
        return psycopg.connect(self._database_url)


_MEMORY_SELECT = """
SELECT
    memories.id, memories.workspace_id, memories.workspace_revision,
    memories.kind, memories.title, memories.content, memories.canonical_key,
    memories.status, memories.confidence, memories.importance,
    memories.version, memories.created_by, memories.created_at,
    memories.updated_at, memories.supersedes_id, memories.expires_at,
    memories.last_confirmed_at, memories.last_accessed_at,
    memories.access_count, memories.conflict
FROM project_memories AS memories
"""
_EVIDENCE_SELECT = """
SELECT id, memory_id, source_kind, source_id, path, start_line, end_line,
       content_hash, excerpt, created_at
FROM project_memory_evidence
"""


def _member_from_row(row: tuple[Any, ...]) -> WorkspaceMember:
    return WorkspaceMember(str(row[0]), str(row[1]), str(row[2]), row[3], row[4])


def _settings_from_row(row: tuple[Any, ...]) -> MemorySettings:
    return MemorySettings(str(row[0]), str(row[1]), str(row[2]), row[3])


def _memory_from_row(
    row: tuple[Any, ...], evidence: list[MemoryEvidence]
) -> ProjectMemory:
    return ProjectMemory(
        id=str(row[0]),
        workspace_id=str(row[1]),
        workspace_revision=int(row[2]),
        kind=str(row[3]),
        title=str(row[4]),
        content=str(row[5]),
        canonical_key=str(row[6]),
        status=str(row[7]),
        confidence=float(row[8]),
        importance=int(row[9]),
        version=int(row[10]),
        created_by=str(row[11]),
        created_at=row[12],
        updated_at=row[13],
        supersedes_id=str(row[14]) if row[14] is not None else None,
        expires_at=row[15],
        last_confirmed_at=row[16],
        last_accessed_at=row[17],
        access_count=int(row[18]),
        evidence=evidence,
        conflict=bool(row[19]),
    )


def _evidence_from_row(row: tuple[Any, ...]) -> MemoryEvidence:
    return MemoryEvidence(
        id=str(row[0]),
        memory_id=str(row[1]),
        source_kind=str(row[2]),
        source_id=str(row[3]),
        path=str(row[4]) if row[4] is not None else None,
        start_line=int(row[5]) if row[5] is not None else None,
        end_line=int(row[6]) if row[6] is not None else None,
        content_hash=str(row[7]) if row[7] is not None else None,
        excerpt=str(row[8]) if row[8] is not None else None,
        created_at=row[9],
    )


def _job_from_row(row: tuple[Any, ...]) -> MemoryExtractionJob:
    return MemoryExtractionJob(
        id=str(row[0]),
        workspace_id=str(row[1]),
        workspace_revision=int(row[2]),
        source_type=str(row[3]),
        source_id=str(row[4]),
        status=str(row[5]),
        attempts=int(row[6]),
        candidate_count=int(row[7]),
        active_count=int(row[8]),
        error=str(row[9]) if row[9] is not None else None,
        input_tokens=int(row[10]),
        output_tokens=int(row[11]),
        created_at=row[12],
        updated_at=row[13],
        completed_at=row[14],
    )


def _job_values(job: MemoryExtractionJob) -> tuple[object, ...]:
    return (
        job.id,
        job.workspace_id,
        job.workspace_revision,
        job.source_type,
        job.source_id,
        job.status,
        job.attempts,
        job.candidate_count,
        job.active_count,
        job.error,
        job.input_tokens,
        job.output_tokens,
        job.created_at,
        job.updated_at,
        job.completed_at,
    )


def _index_event_from_row(row: tuple[Any, ...]) -> MemoryIndexEvent:
    return MemoryIndexEvent(
        id=str(row[0]),
        memory_id=str(row[1]),
        operation=str(row[2]),
        memory_version=int(row[3]),
        status=str(row[4]),
        attempts=int(row[5]),
        error=str(row[6]) if row[6] is not None else None,
        created_at=row[7],
        updated_at=row[8],
    )


def _search_text(memory: ProjectMemory) -> str:
    return f"{memory.title} {memory.kind} {memory.content}"


def _tokens(text: str) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(r"[\w\u4e00-\u9fff]+", text)
        if token.strip()
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "InMemoryProjectMemoryRepository",
    "PostgresProjectMemoryRepository",
    "PostgresDependencyError",
]
