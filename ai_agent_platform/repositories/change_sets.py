from __future__ import annotations

from threading import Lock
from typing import Protocol

from ai_agent_platform.domain import ChangeSetRecord


class ChangeSetRepository(Protocol):
    def create(self, record: ChangeSetRecord) -> ChangeSetRecord:
        ...

    def get(self, change_set_id: str) -> ChangeSetRecord | None:
        ...

    def get_by_run(self, run_id: str) -> ChangeSetRecord | None:
        ...

    def compare_and_set(
        self,
        record: ChangeSetRecord,
        *,
        expected_status: str,
    ) -> ChangeSetRecord | None:
        ...


class InMemoryChangeSetRepository:
    def __init__(self) -> None:
        self._records: dict[str, ChangeSetRecord] = {}
        self._run_ids: dict[str, str] = {}
        self._lock = Lock()

    def create(self, record: ChangeSetRecord) -> ChangeSetRecord:
        with self._lock:
            existing_id = self._run_ids.get(record.run_id)
            if existing_id is not None:
                return self._records[existing_id]
            if record.id in self._records:
                return self._records[record.id]
            self._records[record.id] = record
            self._run_ids[record.run_id] = record.id
            return record

    def get(self, change_set_id: str) -> ChangeSetRecord | None:
        with self._lock:
            return self._records.get(change_set_id)

    def get_by_run(self, run_id: str) -> ChangeSetRecord | None:
        with self._lock:
            change_set_id = self._run_ids.get(run_id)
            return self._records.get(change_set_id) if change_set_id else None

    def compare_and_set(
        self,
        record: ChangeSetRecord,
        *,
        expected_status: str,
    ) -> ChangeSetRecord | None:
        with self._lock:
            current = self._records.get(record.id)
            if current is None or current.status != expected_status:
                return None
            self._records[record.id] = record
            self._run_ids[record.run_id] = record.id
            return record


class PostgresChangeSetRepository:
    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url
        _require_psycopg()

    def create(self, record: ChangeSetRecord) -> ChangeSetRecord:
        Jsonb = _require_jsonb()
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO agent_change_sets (
                    id, run_id, conversation_id, workspace_id, workspace_root,
                    workspace_revision, created_by, apply_mode, base_git_head,
                    baseline_file_hashes, changed_files, patch, patch_sha256,
                    validation_status, validation_summary, status, created_at,
                    updated_at, applied_by, applied_at, error, branch_name,
                    worktree_path
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (run_id) DO NOTHING
                RETURNING id
                """,
                _record_values(record, Jsonb=Jsonb),
            ).fetchone()
        if row is not None:
            return record
        existing = self.get_by_run(record.run_id)
        if existing is None:
            raise RuntimeError("change set insert lost without an existing record")
        return existing

    def get(self, change_set_id: str) -> ChangeSetRecord | None:
        return self._get("id = %s", (change_set_id,))

    def get_by_run(self, run_id: str) -> ChangeSetRecord | None:
        return self._get("run_id = %s", (run_id,))

    def compare_and_set(
        self,
        record: ChangeSetRecord,
        *,
        expected_status: str,
    ) -> ChangeSetRecord | None:
        Jsonb = _require_jsonb()
        with self._connect() as conn:
            row = conn.execute(
                """
                UPDATE agent_change_sets
                SET workspace_root = %s,
                    workspace_revision = %s,
                    apply_mode = %s,
                    baseline_file_hashes = %s,
                    changed_files = %s,
                    patch = %s,
                    patch_sha256 = %s,
                    validation_status = %s,
                    validation_summary = %s,
                    status = %s,
                    updated_at = %s,
                    applied_by = %s,
                    applied_at = %s,
                    error = %s,
                    branch_name = %s,
                    worktree_path = %s
                WHERE id = %s AND status = %s
                RETURNING id
                """,
                (
                    record.workspace_root,
                    record.workspace_revision,
                    record.apply_mode,
                    Jsonb(record.baseline_file_hashes),
                    Jsonb(record.changed_files),
                    record.patch,
                    record.patch_sha256,
                    record.validation_status,
                    Jsonb(record.validation_summary),
                    record.status,
                    record.updated_at,
                    record.applied_by,
                    record.applied_at,
                    record.error,
                    record.branch_name,
                    record.worktree_path,
                    record.id,
                    expected_status,
                ),
            ).fetchone()
        return record if row is not None else None

    def _get(
        self,
        where_clause: str,
        params: tuple[object, ...],
    ) -> ChangeSetRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM agent_change_sets WHERE {where_clause}",
                params,
            ).fetchone()
        return _record_from_row(row) if row is not None else None

    def _connect(self):
        psycopg = _require_psycopg()
        return psycopg.connect(self._database_url)


_SELECT_COLUMNS = """
    id, run_id, conversation_id, workspace_id, workspace_root,
    workspace_revision, created_by, apply_mode, base_git_head,
    baseline_file_hashes, changed_files, patch, patch_sha256,
    validation_status, validation_summary, status, created_at, updated_at,
    applied_by, applied_at, error, branch_name, worktree_path
"""


def _record_values(record: ChangeSetRecord, *, Jsonb) -> tuple[object, ...]:
    return (
        record.id,
        record.run_id,
        record.conversation_id,
        record.workspace_id,
        record.workspace_root,
        record.workspace_revision,
        record.created_by,
        record.apply_mode,
        record.base_git_head,
        Jsonb(record.baseline_file_hashes),
        Jsonb(record.changed_files),
        record.patch,
        record.patch_sha256,
        record.validation_status,
        Jsonb(record.validation_summary),
        record.status,
        record.created_at,
        record.updated_at,
        record.applied_by,
        record.applied_at,
        record.error,
        record.branch_name,
        record.worktree_path,
    )


def _record_from_row(row: tuple[object, ...]) -> ChangeSetRecord:
    return ChangeSetRecord(
        id=str(row[0]),
        run_id=str(row[1]),
        conversation_id=str(row[2]),
        workspace_id=str(row[3]),
        workspace_root=str(row[4]),
        workspace_revision=int(row[5]),
        created_by=str(row[6]),
        apply_mode=str(row[7]),
        base_git_head=str(row[8]) if row[8] is not None else None,
        baseline_file_hashes=dict(row[9] or {}),
        changed_files=[str(item) for item in (row[10] or [])],
        patch=str(row[11]),
        patch_sha256=str(row[12]),
        validation_status=str(row[13]),
        validation_summary=dict(row[14] or {}),
        status=str(row[15]),
        created_at=row[16],  # type: ignore[arg-type]
        updated_at=row[17],  # type: ignore[arg-type]
        applied_by=str(row[18]) if row[18] is not None else None,
        applied_at=row[19],  # type: ignore[arg-type]
        error=str(row[20]) if row[20] is not None else None,
        branch_name=str(row[21]) if row[21] is not None else None,
        worktree_path=str(row[22]) if row[22] is not None else None,
    )


def _require_psycopg():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "psycopg is required for PostgreSQL ChangeSet storage"
        ) from exc
    return psycopg


def _require_jsonb():
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise RuntimeError(
            "psycopg JSON support is required for PostgreSQL ChangeSet storage"
        ) from exc
    return Jsonb
