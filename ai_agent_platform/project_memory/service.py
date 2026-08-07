"""Application service for project-memory governance, retrieval, and extraction."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
from time import perf_counter
from typing import Any, Callable, Protocol
from uuid import uuid4

from ai_agent_platform.core import MetricsRegistry
from ai_agent_platform.integrations.rag import EmbeddingProvider
from ai_agent_platform.project_memory.extractor import MemoryCandidate, MemoryExtractor
from ai_agent_platform.project_memory.models import (
    MEMORY_KINDS,
    MEMORY_MODES,
    MEMORY_STATUSES,
    ROLE_RANK,
    MemoryAuditEvent,
    MemoryEvidence,
    MemoryExtractionJob,
    MemorySettings,
    ProjectMemory,
    ProjectMemoryRepository,
    RetrievedMemory,
)
from ai_agent_platform.project_memory.vector import (
    MemoryVectorStore,
    embed_memory,
)
from ai_agent_platform.usage_ledger import model_usage_scope


class WorkspaceProvider(Protocol):
    def get(self, workspace_id: str) -> Any:
        ...


class MemoryNotFoundError(KeyError):
    pass


class MemoryAccessDeniedError(PermissionError):
    pass


class MemoryConflictError(RuntimeError):
    pass


class MemoryValidationError(ValueError):
    pass


class ProjectMemoryService:
    def __init__(
        self,
        *,
        repository: ProjectMemoryRepository,
        workspace_service: WorkspaceProvider,
        embedding_provider: EmbeddingProvider,
        vector_store: MemoryVectorStore,
        extractor: MemoryExtractor,
        enabled: bool,
        default_mode: str,
        candidate_threshold: float,
        auto_threshold: float,
        recall_limit: int,
        result_limit: int,
        max_context_chars: int,
        relevance_weight: float = 0.65,
        recency_weight: float = 0.20,
        importance_weight: float = 0.15,
        recency_half_life_days: float = 180.0,
        metrics: MetricsRegistry | None = None,
    ) -> None:
        self._repository = repository
        self._workspace_service = workspace_service
        self._embedding_provider = embedding_provider
        self._vector_store = vector_store
        self._extractor = extractor
        self._enabled = enabled
        self._default_mode = default_mode
        self._candidate_threshold = candidate_threshold
        self._auto_threshold = auto_threshold
        self._recall_limit = recall_limit
        self._result_limit = result_limit
        self._max_context_chars = max_context_chars
        self._relevance_weight = relevance_weight
        self._recency_weight = recency_weight
        self._importance_weight = importance_weight
        self._recency_half_life_days = recency_half_life_days
        self._metrics = metrics or MetricsRegistry()
        self._index_outbox_submitter: Callable[[str], None] | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_index_outbox_submitter(
        self, submitter: Callable[[str], None] | None
    ) -> None:
        """Connect the service to an asynchronous outbox consumer."""
        self._index_outbox_submitter = submitter

    def ensure_workspace_admin(
        self, *, workspace_id: str, actor_user_id: str
    ) -> None:
        self._repository.ensure_member(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            role="admin",
        )

    def authorize(
        self,
        *,
        workspace_id: str,
        actor_user_id: str,
        required_role: str = "viewer",
    ) -> None:
        if required_role not in ROLE_RANK:
            raise MemoryValidationError(
                f"unsupported workspace role: {required_role}"
            )
        self._require_role(workspace_id, actor_user_id, required_role)

    def role_for(self, *, workspace_id: str, actor_user_id: str) -> str | None:
        member = self._repository.get_member(
            workspace_id=workspace_id,
            user_id=actor_user_id,
        )
        return member.role if member is not None else None

    def get_settings(
        self, *, workspace_id: str, actor_user_id: str
    ) -> MemorySettings:
        self._workspace_service.get(workspace_id)
        self._require_role(workspace_id, actor_user_id, "viewer")
        return self._repository.get_settings(
            workspace_id=workspace_id,
            default_mode=self._default_mode,
        )

    def update_settings(
        self, *, workspace_id: str, actor_user_id: str, mode: str
    ) -> MemorySettings:
        self._workspace_service.get(workspace_id)
        self._require_role(workspace_id, actor_user_id, "admin")
        if mode not in MEMORY_MODES:
            raise MemoryValidationError(f"unsupported memory mode: {mode}")
        return self._repository.update_settings(
            workspace_id=workspace_id,
            mode=mode,
            updated_by=actor_user_id,
        )

    def create_manual(
        self,
        *,
        workspace_id: str,
        actor_user_id: str,
        kind: str,
        title: str,
        content: str,
        importance: int,
        expires_at: datetime | None = None,
    ) -> ProjectMemory:
        workspace = self._workspace_service.get(workspace_id)
        self._require_role(workspace_id, actor_user_id, "editor")
        kind, title, content, importance = _validate_memory_input(
            kind=kind,
            title=title,
            content=content,
            importance=importance,
        )
        try:
            _reject_sensitive_or_instructional(content)
        except MemoryValidationError as exc:
            self._record_blocked_candidate(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                source_type="manual",
                source_id=actor_user_id,
                reason=str(exc),
            )
            raise
        now = _now()
        memory = ProjectMemory(
            id=f"mem_{uuid4().hex[:16]}",
            workspace_id=workspace.id,
            workspace_revision=workspace.revision,
            kind=kind,
            title=title,
            content=content,
            canonical_key=_canonical_key(kind, title, ""),
            status="active",
            confidence=1.0,
            importance=importance,
            version=1,
            created_by=actor_user_id,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
            last_confirmed_at=now,
        )
        evidence = [
            _evidence(
                memory.id,
                source_kind="manual",
                source_id=actor_user_id,
                excerpt=content,
            )
        ]
        stored = self._store_with_conflict_policy(
            memory,
            evidence=evidence,
            actor_user_id=actor_user_id,
            high_authority=True,
        )
        self._schedule_index_outbox(f"{stored.id}:{stored.version}")
        return stored

    def get_memory(
        self,
        *,
        workspace_id: str,
        memory_id: str,
        actor_user_id: str,
    ) -> ProjectMemory:
        self._require_role(workspace_id, actor_user_id, "viewer")
        memory = self._repository.get_memory(memory_id)
        if memory is None or memory.workspace_id != workspace_id:
            raise MemoryNotFoundError(memory_id)
        return memory

    def list_memories(
        self,
        *,
        workspace_id: str,
        actor_user_id: str,
        status: str | None = None,
        kind: str | None = None,
        include_previous_revisions: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ProjectMemory]:
        workspace = self._workspace_service.get(workspace_id)
        self._require_role(workspace_id, actor_user_id, "viewer")
        if status is not None and status not in MEMORY_STATUSES:
            raise MemoryValidationError(f"unsupported memory status: {status}")
        if kind is not None and kind not in MEMORY_KINDS:
            raise MemoryValidationError(f"unsupported memory kind: {kind}")
        return self._repository.list_memories(
            workspace_id=workspace_id,
            workspace_revision=(
                None if include_previous_revisions else workspace.revision
            ),
            status=status,
            kind=kind,
            limit=max(1, min(limit, 200)),
            offset=max(0, offset),
        )

    def update_memory(
        self,
        *,
        workspace_id: str,
        memory_id: str,
        actor_user_id: str,
        expected_version: int,
        kind: str,
        title: str,
        content: str,
        importance: int,
        expires_at: datetime | None,
    ) -> ProjectMemory:
        workspace = self._workspace_service.get(workspace_id)
        self._require_role(workspace_id, actor_user_id, "editor")
        current = self.get_memory(
            workspace_id=workspace_id,
            memory_id=memory_id,
            actor_user_id=actor_user_id,
        )
        if current.workspace_revision != workspace.revision:
            raise MemoryConflictError("memory belongs to a previous workspace revision")
        kind, title, content, importance = _validate_memory_input(
            kind=kind,
            title=title,
            content=content,
            importance=importance,
        )
        _reject_sensitive_or_instructional(content)
        if current.version != expected_version:
            raise MemoryConflictError("memory version changed")
        now = _now()
        canonical_key = _canonical_key(kind, title, "")
        competing = self._repository.find_active_by_key(
            workspace_id=workspace_id,
            workspace_revision=workspace.revision,
            canonical_key=canonical_key,
            exclude_memory_id=current.id,
        )
        if current.status == "active" and competing is not None:
            raise MemoryConflictError(
                "another active memory already uses this canonical key"
            )
        updated = replace(
            current,
            kind=kind,
            title=title,
            content=content,
            canonical_key=canonical_key,
            confidence=1.0,
            importance=importance,
            version=expected_version + 1,
            expires_at=expires_at,
            last_confirmed_at=now,
            updated_at=now,
            conflict=False,
        )
        stored = self._repository.update_memory(
            updated,
            expected_version=expected_version,
            evidence=[
                _evidence(
                    current.id,
                    source_kind="manual_edit",
                    source_id=actor_user_id,
                    excerpt=content,
                )
            ],
            audit=_audit(
                current,
                action="edited",
                actor_user_id=actor_user_id,
                metadata={"from_version": expected_version},
            ),
        )
        if stored is None:
            raise MemoryConflictError("memory version changed")
        self._schedule_index_outbox(f"{stored.id}:{stored.version}")
        return stored

    def confirm(
        self,
        *,
        workspace_id: str,
        memory_id: str,
        actor_user_id: str,
        expected_version: int,
    ) -> ProjectMemory:
        workspace = self._workspace_service.get(workspace_id)
        self._require_role(workspace_id, actor_user_id, "editor")
        current = self.get_memory(
            workspace_id=workspace_id,
            memory_id=memory_id,
            actor_user_id=actor_user_id,
        )
        if current.version != expected_version:
            raise MemoryConflictError("memory version changed")
        if current.workspace_revision != workspace.revision:
            self._require_role(workspace_id, actor_user_id, "admin")
            return self._copy_to_current_revision(
                current=current,
                workspace_revision=workspace.revision,
                actor_user_id=actor_user_id,
            )
        competing = self._repository.find_active_by_key(
            workspace_id=workspace_id,
            workspace_revision=workspace.revision,
            canonical_key=current.canonical_key,
            exclude_memory_id=current.id,
        )
        if competing is not None:
            self._supersede(competing, current.id, actor_user_id)
        now = _now()
        confirmed = replace(
            current,
            status="active",
            confidence=1.0,
            version=expected_version + 1,
            supersedes_id=(
                competing.id if competing is not None else current.supersedes_id
            ),
            last_confirmed_at=now,
            updated_at=now,
            conflict=False,
        )
        stored = self._repository.update_memory(
            confirmed,
            expected_version=expected_version,
            evidence=[],
            audit=_audit(
                current,
                action="confirmed",
                actor_user_id=actor_user_id,
                metadata={"from_status": current.status},
            ),
        )
        if stored is None:
            raise MemoryConflictError("memory version changed")
        self._schedule_index_outbox(f"{stored.id}:{stored.version}")
        return stored

    def _copy_to_current_revision(
        self,
        *,
        current: ProjectMemory,
        workspace_revision: int,
        actor_user_id: str,
    ) -> ProjectMemory:
        now = _now()
        copied = replace(
            current,
            id=f"mem_{uuid4().hex[:16]}",
            workspace_revision=workspace_revision,
            status="active",
            confidence=1.0,
            version=1,
            created_by=actor_user_id,
            supersedes_id=current.id,
            last_confirmed_at=now,
            last_accessed_at=None,
            access_count=0,
            conflict=False,
            evidence=[],
            created_at=now,
            updated_at=now,
        )
        evidence = [
            replace(
                item,
                id=f"mev_{uuid4().hex[:16]}",
                memory_id=copied.id,
                created_at=now,
            )
            for item in current.evidence
        ]
        evidence.append(
            _evidence(
                copied.id,
                source_kind="revision_reconfirm",
                source_id=current.id,
                excerpt=current.content,
            )
        )
        stored = self._store_with_conflict_policy(
            copied,
            evidence=evidence,
            actor_user_id=actor_user_id,
            high_authority=True,
        )
        self._schedule_index_outbox(f"{stored.id}:{stored.version}")
        return stored

    def reject(
        self,
        *,
        workspace_id: str,
        memory_id: str,
        actor_user_id: str,
        expected_version: int,
    ) -> ProjectMemory:
        self._require_role(workspace_id, actor_user_id, "editor")
        current = self.get_memory(
            workspace_id=workspace_id,
            memory_id=memory_id,
            actor_user_id=actor_user_id,
        )
        if current.version != expected_version:
            raise MemoryConflictError("memory version changed")
        rejected = replace(
            current,
            status="rejected",
            version=expected_version + 1,
            updated_at=_now(),
            conflict=False,
        )
        stored = self._repository.update_memory(
            rejected,
            expected_version=expected_version,
            evidence=[],
            audit=_audit(
                current,
                action="rejected",
                actor_user_id=actor_user_id,
                metadata={"from_status": current.status},
            ),
        )
        if stored is None:
            raise MemoryConflictError("memory version changed")
        self._schedule_index_outbox(f"{stored.id}:{stored.version}")
        return stored

    def forget(
        self, *, workspace_id: str, memory_id: str, actor_user_id: str
    ) -> None:
        self._require_role(workspace_id, actor_user_id, "admin")
        current = self.get_memory(
            workspace_id=workspace_id,
            memory_id=memory_id,
            actor_user_id=actor_user_id,
        )
        deleted = self._repository.delete_memory(
            memory_id=memory_id,
            expected_workspace_id=workspace_id,
            audit=_audit(
                current,
                action="deleted",
                actor_user_id=actor_user_id,
                metadata={"version": current.version},
            ),
        )
        if not deleted:
            raise MemoryNotFoundError(memory_id)
        self._schedule_index_outbox(f"{current.id}:delete:{current.version + 1}")

    def retrieve(
        self,
        *,
        workspace_id: str,
        actor_user_id: str,
        query: str,
    ) -> list[RetrievedMemory]:
        started = perf_counter()
        if not self._enabled or not query.strip():
            return []
        workspace = self._workspace_service.get(workspace_id)
        self._require_role(workspace_id, actor_user_id, "viewer")
        settings = self._repository.get_settings(
            workspace_id=workspace_id,
            default_mode=self._default_mode,
        )
        if settings.mode not in {"review", "auto"}:
            return []
        dense: list[tuple[str, float, int]] = []
        lexical: list[tuple[str, float]] = []
        try:
            with model_usage_scope(
                workspace_id=workspace_id,
                operation="embedding",
                resource_id=f"project_memory:{workspace_id}",
            ):
                query_embedding = self._embedding_provider.embed_texts(
                    [query], task_type="query"
                )[0]
            dense = self._vector_store.search(
                workspace_id=workspace_id,
                workspace_revision=workspace.revision,
                query_embedding=query_embedding,
                limit=self._recall_limit,
            )
        except Exception:
            self._metrics.increment("project_memory_dense_fallback_total")
        try:
            lexical = self._repository.search_lexical(
                workspace_id=workspace_id,
                workspace_revision=workspace.revision,
                query=query,
                limit=self._recall_limit,
            )
        except Exception:
            self._metrics.increment("project_memory_lexical_failures_total")

        fused = _rrf(dense, lexical)
        ranked: list[RetrievedMemory] = []
        ranking_time = _now()
        for memory_id, ranks in sorted(
            fused.items(),
            key=lambda item: (-item[1]["fusion"], item[0]),
        ):
            memory = self._repository.get_memory(memory_id)
            if not _eligible(memory, workspace_id, workspace.revision):
                continue
            assert memory is not None
            if not self._evidence_is_current(memory, workspace.root_path):
                self._mark_stale(memory)
                continue
            fusion_score = float(ranks["fusion"])
            dense_rank = ranks.get("dense_rank")
            if (
                dense_rank is not None
                and ranks.get("dense_version") != memory.version
            ):
                fusion_score -= float(ranks.get("dense_contribution", 0.0))
                dense_rank = None
            if fusion_score <= 0:
                continue
            relevance_score = _normalized_relevance(
                fusion_score,
                dense_available=bool(dense),
                lexical_available=bool(lexical),
            )
            recency_score = _recency_score(
                memory,
                ranking_time,
                half_life_days=self._recency_half_life_days,
            )
            importance_score = memory.importance / 5.0
            score = (
                self._relevance_weight * relevance_score
                + self._recency_weight * recency_score
                + self._importance_weight * importance_score
            )
            ranked.append(
                RetrievedMemory(
                    memory=memory,
                    score=score,
                    relevance_score=relevance_score,
                    recency_score=recency_score,
                    importance_score=importance_score,
                    dense_rank=dense_rank,
                    lexical_rank=ranks.get("lexical_rank"),
                    fusion_score=fusion_score,
                )
            )
        ranked.sort(key=lambda item: (-item.score, item.memory.id))
        selected: list[RetrievedMemory] = []
        used_chars = 0
        for item in ranked:
            if used_chars + len(item.memory.content) > self._max_context_chars:
                continue
            selected.append(item)
            used_chars += len(item.memory.content)
            if len(selected) >= self._result_limit:
                break
        self._repository.record_access([item.memory.id for item in selected])
        self._metrics.increment("project_memory_retrievals_total")
        self._metrics.increment("project_memory_hits_total", len(selected))
        self._metrics.observe_ms(
            "project_memory_retrieval_duration_ms",
            int((perf_counter() - started) * 1000),
        )
        return selected

    def extract_and_store(
        self,
        *,
        workspace_id: str,
        actor_user_id: str,
        source_type: str,
        source_id: str,
        user_message: str,
        assistant_message: str,
        verified: bool,
        source_evidence: list[dict[str, Any]] | None = None,
    ) -> MemoryExtractionJob | None:
        if not self._enabled:
            return None
        workspace = self._workspace_service.get(workspace_id)
        self._require_role(workspace_id, actor_user_id, "viewer")
        settings = self._repository.get_settings(
            workspace_id=workspace_id,
            default_mode=self._default_mode,
        )
        if settings.mode == "off":
            return None
        now = _now()
        existing_job = self._repository.get_extraction_job(
            workspace_id=workspace_id,
            source_type=source_type,
            source_id=source_id,
        )
        if existing_job is not None and existing_job.status != "failed":
            self._metrics.increment("project_memory_extraction_duplicates_total")
            return None
        if existing_job is None:
            job = MemoryExtractionJob(
                id=f"mjob_{uuid4().hex[:16]}",
                workspace_id=workspace_id,
                workspace_revision=workspace.revision,
                source_type=source_type,
                source_id=source_id,
                status="pending",
                attempts=0,
                candidate_count=0,
                active_count=0,
                error=None,
                input_tokens=0,
                output_tokens=0,
                created_at=now,
                updated_at=now,
            )
            created = self._repository.create_extraction_job(job)
            if created is None:
                self._metrics.increment(
                    "project_memory_extraction_duplicates_total"
                )
                return None
        else:
            job = existing_job
        extracting = replace(
            job,
            status="extracting",
            attempts=job.attempts + 1,
            error=None,
            completed_at=None,
            updated_at=_now(),
        )
        self._repository.update_extraction_job(extracting)
        try:
            result = self._extractor.extract(
                user_message=user_message,
                assistant_message=assistant_message,
                source_type=source_type,
                verified=verified,
            )
            stored: list[ProjectMemory] = []
            for candidate in result.candidates:
                try:
                    memory = self._ingest_candidate(
                        workspace_id=workspace_id,
                        workspace_revision=workspace.revision,
                        actor_user_id=actor_user_id,
                        candidate=candidate,
                        mode=settings.mode,
                        source_type=source_type,
                        source_id=source_id,
                        source_evidence=source_evidence or [],
                    )
                except MemoryValidationError as exc:
                    self._record_blocked_candidate(
                        workspace_id=workspace_id,
                        actor_user_id=actor_user_id,
                        source_type=source_type,
                        source_id=source_id,
                        reason=str(exc),
                    )
                    continue
                if memory is not None:
                    stored.append(memory)
            completed = replace(
                extracting,
                status="completed",
                candidate_count=len(stored),
                active_count=sum(item.status == "active" for item in stored),
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                updated_at=_now(),
                completed_at=_now(),
            )
            self._repository.update_extraction_job(completed)
            self._schedule_index_outbox(completed.id)
            self._metrics.increment("project_memory_extractions_completed_total")
            self._metrics.increment(
                "project_memory_candidates_total", len(stored)
            )
            self._metrics.increment(
                "project_memory_auto_active_total",
                completed.active_count,
            )
            self._metrics.increment(
                "project_memory_extraction_input_tokens_total",
                result.input_tokens,
            )
            self._metrics.increment(
                "project_memory_extraction_output_tokens_total",
                result.output_tokens,
            )
            return completed
        except Exception as exc:
            failed = replace(
                extracting,
                status="failed",
                error=str(exc)[:1000],
                updated_at=_now(),
                completed_at=_now(),
            )
            self._repository.update_extraction_job(failed)
            self._metrics.increment("project_memory_extractions_failed_total")
            raise

    def list_jobs(
        self, *, workspace_id: str, actor_user_id: str, limit: int
    ) -> list[MemoryExtractionJob]:
        self._require_role(workspace_id, actor_user_id, "viewer")
        return self._repository.list_extraction_jobs(
            workspace_id=workspace_id,
            limit=max(1, min(limit, 100)),
        )

    def reindex(self, *, workspace_id: str, actor_user_id: str) -> int:
        workspace = self._workspace_service.get(workspace_id)
        self._require_role(workspace_id, actor_user_id, "admin")
        active: list[ProjectMemory] = []
        offset = 0
        while True:
            page = self._repository.list_memories(
                workspace_id=workspace_id,
                workspace_revision=workspace.revision,
                status="active",
                kind=None,
                limit=200,
                offset=offset,
            )
            active.extend(page)
            if len(page) < 200:
                break
            offset += len(page)
        desired = {memory.id: memory for memory in active}
        try:
            indexed = self._vector_store.list_indexed(
                workspace_id=workspace_id
            )
        except Exception:
            indexed = {}
            self._metrics.increment(
                "project_memory_index_consistency_fallback_total"
            )
        repairs = 0
        for memory in desired.values():
            expected = (memory.workspace_revision, memory.version)
            if indexed.get(memory.id) != expected:
                self._repository.enqueue_index_event(
                    memory_id=memory.id,
                    operation="upsert",
                    memory_version=memory.version,
                )
                repairs += 1
        for memory_id, (_, version) in indexed.items():
            if memory_id not in desired:
                self._repository.enqueue_index_event(
                    memory_id=memory_id,
                    operation="delete",
                    memory_version=version + 1,
                )
                repairs += 1
        self._metrics.increment(
            "project_memory_index_consistency_repairs_total",
            repairs,
        )
        if repairs:
            self._schedule_index_outbox(
                f"reindex:{workspace_id}:{workspace.revision}:{repairs}"
            )
        return repairs

    def process_index_outbox(
        self, *, trigger_id: str | None = None, limit: int = 100
    ) -> int:
        del trigger_id
        completed = 0
        failed = 0
        for event in self._repository.list_index_events(limit=limit):
            if event.attempts >= 5:
                continue
            try:
                memory = self._repository.get_memory(event.memory_id)
                if (
                    event.operation == "delete"
                    or memory is None
                    or memory.status != "active"
                    or memory.version != event.memory_version
                ):
                    self._vector_store.delete(event.memory_id)
                else:
                    with model_usage_scope(
                        workspace_id=memory.workspace_id,
                        operation="embedding",
                        resource_id=memory.id,
                    ):
                        self._vector_store.upsert(
                            memory,
                            embed_memory(self._embedding_provider, memory),
                        )
                self._repository.mark_index_event(
                    event_id=event.id,
                    status="completed",
                    error=None,
                )
                self._metrics.increment("project_memory_index_events_completed_total")
                completed += 1
            except Exception as exc:
                self._repository.mark_index_event(
                    event_id=event.id,
                    status="failed",
                    error=str(exc)[:1000],
                )
                self._metrics.increment("project_memory_index_events_failed_total")
                failed += 1
        self._metrics.set_gauge(
            "project_memory_index_outbox_backlog",
            self._repository.count_pending_index_events(),
        )
        if failed:
            raise RuntimeError(
                f"{failed} project-memory index event(s) failed"
            )
        return completed

    def _schedule_index_outbox(self, trigger_id: str) -> None:
        submitter = self._index_outbox_submitter
        if submitter is None:
            self._metrics.set_gauge(
                "project_memory_index_outbox_backlog",
                self._repository.count_pending_index_events(),
            )
            return
        try:
            submitter(trigger_id)
        except Exception:
            self._metrics.increment(
                "project_memory_index_outbox_enqueue_failed_total"
            )
            self._metrics.set_gauge(
                "project_memory_index_outbox_backlog",
                self._repository.count_pending_index_events(),
            )

    def _ingest_candidate(
        self,
        *,
        workspace_id: str,
        workspace_revision: int,
        actor_user_id: str,
        candidate: MemoryCandidate,
        mode: str,
        source_type: str,
        source_id: str,
        source_evidence: list[dict[str, Any]],
    ) -> ProjectMemory | None:
        if candidate.confidence < self._candidate_threshold:
            return None
        _reject_sensitive_or_instructional(candidate.content)
        high_authority = candidate.authority in {
            "explicit_user",
            "user_statement",
            "verified_agent",
        }
        auto_active = (
            mode == "auto"
            and high_authority
            and candidate.confidence >= self._auto_threshold
        )
        if candidate.authority == "explicit_user":
            auto_active = True
        if (
            auto_active
            and candidate.authority == "verified_agent"
            and candidate.kind == "architecture_fact"
            and not any(
                item.get("path") and item.get("content_hash")
                for item in source_evidence
            )
        ):
            auto_active = False
        status = "active" if auto_active else "candidate"
        now = _now()
        memory = ProjectMemory(
            id=f"mem_{uuid4().hex[:16]}",
            workspace_id=workspace_id,
            workspace_revision=workspace_revision,
            kind=candidate.kind,
            title=candidate.title[:160],
            content=candidate.content[:2000],
            canonical_key=_canonical_key(
                candidate.kind,
                candidate.title,
                candidate.canonical_key,
            ),
            status=status,
            confidence=candidate.confidence,
            importance=candidate.importance,
            version=1,
            created_by=actor_user_id,
            created_at=now,
            updated_at=now,
            last_confirmed_at=now if status == "active" else None,
        )
        evidence = [
            _evidence(
                memory.id,
                source_kind=source_type,
                source_id=source_id,
                excerpt=candidate.content,
            )
        ]
        for item in source_evidence[:5]:
            evidence.append(
                _evidence(
                    memory.id,
                    source_kind=str(item.get("kind") or "source"),
                    source_id=str(item.get("source_id") or source_id),
                    path=_optional_text(item.get("path"), 2000),
                    start_line=_optional_int(item.get("start_line")),
                    end_line=_optional_int(item.get("end_line")),
                    content_hash=_optional_hash(item.get("content_hash")),
                    excerpt=_optional_text(item.get("excerpt"), 500),
                )
            )
        return self._store_with_conflict_policy(
            memory,
            evidence=evidence,
            actor_user_id=actor_user_id,
            high_authority=auto_active,
        )

    def _store_with_conflict_policy(
        self,
        memory: ProjectMemory,
        *,
        evidence: list[MemoryEvidence],
        actor_user_id: str,
        high_authority: bool,
    ) -> ProjectMemory:
        existing = self._repository.find_current_by_key(
            workspace_id=memory.workspace_id,
            workspace_revision=memory.workspace_revision,
            canonical_key=memory.canonical_key,
        )
        if existing is not None and _normalized(existing.content) == _normalized(
            memory.content
        ):
            now = _now()
            updated = replace(
                existing,
                status=(
                    "active"
                    if existing.status == "active" or memory.status == "active"
                    else existing.status
                ),
                confidence=max(existing.confidence, memory.confidence),
                importance=max(existing.importance, memory.importance),
                version=existing.version + 1,
                last_confirmed_at=(
                    now if memory.status == "active" else existing.last_confirmed_at
                ),
                updated_at=now,
            )
            stored = self._repository.update_memory(
                updated,
                expected_version=existing.version,
                evidence=[
                    replace(item, memory_id=existing.id) for item in evidence
                ],
                audit=_audit(
                    existing,
                    action="confirmed",
                    actor_user_id=actor_user_id,
                    metadata={"deduplicated": True},
                ),
            )
            if stored is None:
                raise MemoryConflictError("memory changed during deduplication")
            return stored
        if (
            existing is not None
            and existing.status == "active"
            and memory.status == "active"
            and high_authority
        ):
            self._supersede(existing, memory.id, actor_user_id)
            memory = replace(memory, supersedes_id=existing.id)
        elif existing is not None:
            memory = replace(memory, status="candidate", conflict=True)
            self._metrics.increment("project_memory_conflicts_total")
        return self._repository.create_memory(
            memory,
            evidence=evidence,
            audit=_audit(
                memory,
                action="conflict" if memory.conflict else "created",
                actor_user_id=actor_user_id,
                metadata={"status": memory.status, "conflict": memory.conflict},
            ),
        )

    def _supersede(
        self, memory: ProjectMemory, replacement_id: str, actor_user_id: str
    ) -> None:
        updated = replace(
            memory,
            status="superseded",
            version=memory.version + 1,
            updated_at=_now(),
        )
        stored = self._repository.update_memory(
            updated,
            expected_version=memory.version,
            evidence=[],
            audit=_audit(
                memory,
                action="superseded",
                actor_user_id=actor_user_id,
                metadata={"replacement_id": replacement_id},
            ),
        )
        if stored is None:
            raise MemoryConflictError("memory changed during supersession")

    def _mark_stale(self, memory: ProjectMemory) -> None:
        stale = replace(
            memory,
            status="stale",
            version=memory.version + 1,
            updated_at=_now(),
        )
        stored = self._repository.update_memory(
            stale,
            expected_version=memory.version,
            evidence=[],
            audit=_audit(
                memory,
                action="marked_stale",
                actor_user_id="system",
                metadata={"reason": "source_hash_mismatch"},
            ),
        )
        if stored is not None:
            self._schedule_index_outbox(f"{stored.id}:{stored.version}")
        self._metrics.increment("project_memory_stale_total")

    def _evidence_is_current(
        self, memory: ProjectMemory, workspace_root: str
    ) -> bool:
        file_evidence = [
            item
            for item in memory.evidence
            if item.path and item.content_hash
        ]
        if not file_evidence:
            return True
        return all(
            _validate_file_evidence(workspace_root, item)
            for item in file_evidence[:6]
        )

    def _require_role(
        self, workspace_id: str, actor_user_id: str, required: str
    ) -> None:
        member = self._repository.get_member(
            workspace_id=workspace_id,
            user_id=actor_user_id,
        )
        if member is None or ROLE_RANK.get(member.role, 0) < ROLE_RANK[required]:
            raise MemoryAccessDeniedError(
                f"{required} access is required for workspace {workspace_id}"
            )

    def _record_blocked_candidate(
        self,
        *,
        workspace_id: str,
        actor_user_id: str,
        source_type: str,
        source_id: str,
        reason: str,
    ) -> None:
        self._repository.record_audit_event(
            MemoryAuditEvent(
                id=f"maud_{uuid4().hex[:16]}",
                workspace_id=workspace_id,
                memory_id=f"blocked_{uuid4().hex[:16]}",
                action="security_rejected",
                actor_user_id=actor_user_id,
                metadata={
                    "source_type": source_type[:64],
                    "source_id": source_id[:256],
                    "reason": reason[:200],
                },
                created_at=_now(),
            )
        )
        self._metrics.increment("project_memory_security_rejections_total")


def _rrf(
    dense: list[tuple[str, float, int]],
    lexical: list[tuple[str, float]],
    *,
    rrf_k: int = 60,
) -> dict[str, dict[str, Any]]:
    fused: dict[str, dict[str, Any]] = {}
    for rank, (memory_id, _, version) in enumerate(dense, start=1):
        item = fused.setdefault(memory_id, {"fusion": 0.0})
        item["dense_rank"] = rank
        item["dense_version"] = version
        item["dense_contribution"] = 0.65 / (rrf_k + rank)
        item["fusion"] += item["dense_contribution"]
    for rank, (memory_id, _) in enumerate(lexical, start=1):
        item = fused.setdefault(memory_id, {"fusion": 0.0})
        item["lexical_rank"] = rank
        item["fusion"] += 0.35 / (rrf_k + rank)
    return fused


def _eligible(
    memory: ProjectMemory | None,
    workspace_id: str,
    workspace_revision: int,
) -> bool:
    if memory is None:
        return False
    return (
        memory.workspace_id == workspace_id
        and memory.workspace_revision == workspace_revision
        and memory.status == "active"
        and (memory.expires_at is None or memory.expires_at > _now())
    )


def _recency_score(
    memory: ProjectMemory,
    now: datetime,
    *,
    half_life_days: float,
) -> float:
    """Exponential 0-1 recency score without deleting older memories."""
    confirmed_at = memory.last_confirmed_at or memory.updated_at
    age_days = max(0.0, (now - confirmed_at).total_seconds() / 86_400)
    return 2.0 ** (-age_days / half_life_days)


def _normalized_relevance(
    fusion_score: float,
    *,
    dense_available: bool,
    lexical_available: bool,
    rrf_k: int = 60,
) -> float:
    max_fusion = 0.0
    if dense_available:
        max_fusion += 0.65 / (rrf_k + 1)
    if lexical_available:
        max_fusion += 0.35 / (rrf_k + 1)
    if max_fusion <= 0:
        return 0.0
    return max(0.0, min(1.0, fusion_score / max_fusion))


def _validate_memory_input(
    *, kind: str, title: str, content: str, importance: int
) -> tuple[str, str, str, int]:
    if kind not in MEMORY_KINDS:
        raise MemoryValidationError(f"unsupported memory kind: {kind}")
    title = " ".join(title.split())
    content = " ".join(content.split())
    if not title or len(title) > 160:
        raise MemoryValidationError("memory title must be 1-160 characters")
    if not content or len(content) > 2000:
        raise MemoryValidationError("memory content must be 1-2000 characters")
    if importance < 1 or importance > 5:
        raise MemoryValidationError("memory importance must be between 1 and 5")
    return kind, title, content, importance


_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_-]?key|token|password|secret)\b\s*[:=]\s*[^\s,;]{6,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![A-Z0-9_])[A-Z][A-Z0-9_]{2,}\s*=\s*"
        r"(?:\"[^\"]+\"|'[^']+'|[^\s,;]+)"
    ),
    re.compile(r"\b(?:postgres(?:ql)?|mysql|redis)://[^/\s:]+:[^@\s]+@"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE),
    re.compile(r"\b(?:sk|ghp|github_pat)_[A-Za-z0-9_-]{12,}\b"),
)
_INJECTION_PATTERNS = (
    re.compile(r"ignore (?:all |the )?(?:previous|system) instructions", re.I),
    re.compile(r"忽略(?:以上|之前|系统)指令"),
    re.compile(r"reveal (?:the )?system prompt", re.I),
)


def _reject_sensitive_or_instructional(content: str) -> None:
    if any(pattern.search(content) for pattern in _SECRET_PATTERNS):
        raise MemoryValidationError("memory contains a credential-like value")
    if any(pattern.search(content) for pattern in _INJECTION_PATTERNS):
        raise MemoryValidationError("memory contains prompt-injection instructions")


def _canonical_key(kind: str, title: str, proposed: str) -> str:
    normalized = _normalized(proposed or title)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"{kind}:{digest}"


def _normalized(value: str) -> str:
    return re.sub(r"\W+", " ", value.casefold()).strip()


def _evidence(
    memory_id: str,
    *,
    source_kind: str,
    source_id: str,
    path: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    content_hash: str | None = None,
    excerpt: str | None = None,
) -> MemoryEvidence:
    return MemoryEvidence(
        id=f"mev_{uuid4().hex[:16]}",
        memory_id=memory_id,
        source_kind=source_kind[:64],
        source_id=source_id[:256],
        path=path,
        start_line=start_line,
        end_line=end_line,
        content_hash=content_hash,
        excerpt=excerpt[:500] if excerpt else None,
        created_at=_now(),
    )


def _audit(
    memory: ProjectMemory,
    *,
    action: str,
    actor_user_id: str,
    metadata: dict[str, object],
) -> MemoryAuditEvent:
    return MemoryAuditEvent(
        id=f"maud_{uuid4().hex[:16]}",
        workspace_id=memory.workspace_id,
        memory_id=memory.id,
        action=action,
        actor_user_id=actor_user_id,
        metadata=metadata,
        created_at=_now(),
    )


def _validate_file_evidence(workspace_root: str, evidence: MemoryEvidence) -> bool:
    if not evidence.path or not evidence.content_hash:
        return True
    root = Path(workspace_root).resolve()
    try:
        path = (root / evidence.path).resolve()
        if root != path and root not in path.parents:
            return False
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError):
        return False
    if hashlib.sha256(raw).hexdigest() == evidence.content_hash:
        return True
    lines = text.splitlines()
    start = max((evidence.start_line or 1) - 1, 0)
    end = evidence.end_line or len(lines)
    selected = "\n".join(lines[start:end])
    return hashlib.sha256(selected.encode("utf-8")).hexdigest() == evidence.content_hash


def _optional_text(value: object, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_hash(value: object) -> str | None:
    text = _optional_text(value, 64)
    return text if text and re.fullmatch(r"[0-9a-f]{64}", text) else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "MemoryAccessDeniedError",
    "MemoryConflictError",
    "MemoryNotFoundError",
    "MemoryValidationError",
    "ProjectMemoryService",
]
