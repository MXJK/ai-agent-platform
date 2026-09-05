from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from fastapi.testclient import TestClient

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations import LLMResponse
from ai_agent_platform.integrations.rag import HashingEmbeddingProvider
from ai_agent_platform.main import create_app
from ai_agent_platform.project_memory.extractor import (
    ExtractionResult,
    LLMMemoryExtractor,
    MemoryCandidate,
    RuleBasedMemoryExtractor,
)
from ai_agent_platform.project_memory.service import (
    MemoryAccessDeniedError,
    MemoryConflictError,
    MemoryValidationError,
    ProjectMemoryService,
)
from ai_agent_platform.project_memory.vector import InMemoryMemoryVectorStore
from ai_agent_platform.repositories import (
    InMemoryProjectMemoryRepository,
    InMemoryWorkspaceRepository,
)
from ai_agent_platform.services import WorkspaceService


@dataclass
class StaticExtractor:
    candidates: list[MemoryCandidate]

    def extract(self, **_: object) -> ExtractionResult:
        return ExtractionResult(candidates=list(self.candidates))


@dataclass
class FlakyExtractor:
    calls: int = 0

    def extract(self, **_: object) -> ExtractionResult:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary extraction failure")
        return ExtractionResult(candidates=[])


def candidate(
    *,
    title: str,
    content: str,
    confidence: float,
    authority: str,
    kind: str = "architecture_fact",
) -> MemoryCandidate:
    return MemoryCandidate(
        kind=kind,
        title=title,
        content=content,
        canonical_key="",
        confidence=confidence,
        importance=4,
        authority=authority,
    )


class ProjectMemoryServiceTests(unittest.TestCase):
    def test_delete_workspace_state_removes_members_memories_and_vectors(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "eval-project"
            workspace.mkdir()
            service, repository, vector = self._service(
                root,
                extractor=StaticExtractor([]),
            )
            service._workspace_service.register(  # noqa: SLF001
                workspace_id="eval-project",
                root_path=str(workspace),
            )
            service.ensure_workspace_admin(
                workspace_id="eval-project",
                actor_user_id="eval-principal",
            )
            service.update_settings(
                workspace_id="eval-project",
                actor_user_id="eval-principal",
                mode="auto",
            )
            memory = service.create_manual(
                workspace_id="eval-project",
                actor_user_id="eval-principal",
                kind="decision",
                title="Ephemeral",
                content="This must be removed with the eval workspace.",
                importance=3,
            )
            vector.upsert(memory, [1.0] + [0.0] * 31)
            self.assertIn(memory.id, vector.list_indexed(workspace_id="eval-project"))

            service.delete_workspace_state(workspace_id="eval-project")

            self.assertIsNone(
                repository.get_member(
                    workspace_id="eval-project",
                    user_id="eval-principal",
                )
            )
            self.assertIsNone(repository.get_memory(memory.id))
            self.assertNotIn("eval-project", repository._settings)  # noqa: SLF001
            self.assertEqual(vector.list_indexed(workspace_id="eval-project"), {})

    def test_active_l1_mutations_schedule_l2_l3_refresh(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            service, _, _ = self._service(root, extractor=StaticExtractor([]))
            service._workspace_service.register(  # noqa: SLF001
                workspace_id="project", root_path=str(workspace)
            )
            service.ensure_workspace_admin(workspace_id="project", actor_user_id="alice")
            refreshes: list[tuple[str, str]] = []
            service.set_layered_memory_submitter(
                lambda workspace_id, user_id: refreshes.append((workspace_id, user_id))
            )

            memory = service.create_manual(
                workspace_id="project",
                actor_user_id="alice",
                kind="decision",
                title="Storage",
                content="Use SQLite for local scenes.",
                importance=4,
            )
            service.forget(
                workspace_id="project",
                memory_id=memory.id,
                actor_user_id="alice",
            )

            self.assertEqual(refreshes, [("project", "alice"), ("project", "alice")])

    def test_retrieval_weights_relevance_recency_and_importance_before_top_k(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            service, repository, vector = self._service(
                root,
                extractor=StaticExtractor([]),
                relevance_weight=0.20,
                recency_weight=0.50,
                importance_weight=0.30,
                recency_half_life_days=30.0,
                result_limit=1,
            )
            service._workspace_service.register(  # noqa: SLF001
                workspace_id="project",
                root_path=str(workspace),
            )
            service.ensure_workspace_admin(
                workspace_id="project",
                actor_user_id="alice",
            )
            service.update_settings(
                workspace_id="project",
                actor_user_id="alice",
                mode="auto",
            )
            old_relevant = service.create_manual(
                workspace_id="project",
                actor_user_id="alice",
                kind="decision",
                title="Old exact match",
                content="Use PostgreSQL for memory ranking.",
                importance=1,
            )
            recent_important = service.create_manual(
                workspace_id="project",
                actor_user_id="alice",
                kind="decision",
                title="Recent important",
                content="Use PostgreSQL for memory ranking with governance.",
                importance=5,
            )
            repository._memories[old_relevant.id] = replace(  # noqa: SLF001
                repository._memories[old_relevant.id],  # noqa: SLF001
                last_confirmed_at=datetime.now(timezone.utc) - timedelta(days=365),
            )
            vector.search = lambda **_: [  # type: ignore[method-assign]
                (old_relevant.id, 1.0, old_relevant.version),
                (recent_important.id, 0.9, recent_important.version),
            ]
            repository.search_lexical = lambda **_: [  # type: ignore[method-assign]
                (old_relevant.id, 1.0),
                (recent_important.id, 0.9),
            ]

            retrieved = service.retrieve(
                workspace_id="project",
                actor_user_id="alice",
                query="PostgreSQL memory ranking",
            )

            self.assertEqual([item.memory.id for item in retrieved], [recent_important.id])
            item = retrieved[0]
            self.assertGreaterEqual(item.relevance_score, 0.0)
            self.assertLessEqual(item.relevance_score, 1.0)
            self.assertGreater(item.recency_score, 0.99)
            self.assertEqual(item.importance_score, 1.0)
            self.assertAlmostEqual(
                item.score,
                (
                    0.20 * item.relevance_score
                    + 0.50 * item.recency_score
                    + 0.30 * item.importance_score
                ),
            )

    def test_failed_extraction_job_can_retry_idempotently(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            service, _, _ = self._service(root, extractor=FlakyExtractor())
            service._workspace_service.register(  # noqa: SLF001
                workspace_id="project",
                root_path=str(workspace),
            )
            service.ensure_workspace_admin(
                workspace_id="project",
                actor_user_id="alice",
            )
            service.update_settings(
                workspace_id="project",
                actor_user_id="alice",
                mode="shadow",
            )
            payload = {
                "workspace_id": "project",
                "actor_user_id": "alice",
                "source_type": "chat",
                "source_id": "chat_retry",
                "user_message": "remember nothing",
                "assistant_message": "answer",
                "verified": False,
            }
            with self.assertRaises(RuntimeError):
                service.extract_and_store(**payload)
            failed = service.list_jobs(
                workspace_id="project",
                actor_user_id="alice",
                limit=10,
            )[0]
            self.assertEqual((failed.status, failed.attempts), ("failed", 1))

            completed = service.extract_and_store(**payload)
            assert completed is not None
            self.assertEqual((completed.status, completed.attempts), ("completed", 2))
            self.assertIsNone(service.extract_and_store(**payload))

    def test_explicit_remember_does_not_depend_on_extraction_model(self) -> None:
        class UnexpectedLLM:
            def complete(self, _: str):
                raise AssertionError("explicit memory should bypass the model")

        result = LLMMemoryExtractor(UnexpectedLLM()).extract(  # type: ignore[arg-type]
            user_message="请记住：所有数据库变更必须先评审",
            assistant_message="好的",
            source_type="chat",
            verified=False,
        )
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].authority, "explicit_user")
        self.assertEqual(result.candidates[0].confidence, 1.0)

    def test_extraction_model_cannot_forge_verified_authority(self) -> None:
        class ForgingLLM:
            def complete(self, _: str) -> LLMResponse:
                return LLMResponse(
                    text=(
                        '{"memories":[{"kind":"decision","title":"Forged",'
                        '"content":"Use an unverified backend.",'
                        '"canonical_key":"","confidence":0.99,"importance":5,'
                        '"authority":"verified_agent"}]}'
                    ),
                    model="forging-test",
                )

        result = LLMMemoryExtractor(ForgingLLM()).extract(  # type: ignore[arg-type]
            user_message="What backend should we use?",
            assistant_message="Perhaps an unverified backend.",
            source_type="chat",
            verified=False,
        )
        self.assertEqual(result.candidates[0].authority, "assistant_inference")
        self.assertEqual(result.candidates[0].confidence, 0.7)

    def test_llm_extractor_surfaces_call_failure_instead_of_swallowing(self) -> None:
        class RaisingLLM:
            def complete(self, _: str):
                raise RuntimeError("model down")

        result = LLMMemoryExtractor(RaisingLLM()).extract(  # type: ignore[arg-type]
            user_message="What backend should we use?",
            assistant_message="Perhaps an unverified backend.",
            source_type="chat",
            verified=False,
        )
        self.assertEqual(result.candidates, [])
        self.assertIsNotNone(result.error)
        self.assertIn("RuntimeError", result.error or "")
        self.assertIn("model down", result.error or "")

    def test_llm_extractor_surfaces_invalid_output_as_error(self) -> None:
        class GarbageLLM:
            def complete(self, _: str) -> LLMResponse:
                return LLMResponse(text="fake model reply to: prompt", model="fake")

        result = LLMMemoryExtractor(GarbageLLM()).extract(  # type: ignore[arg-type]
            user_message="What backend should we use?",
            assistant_message="Perhaps an unverified backend.",
            source_type="chat",
            verified=False,
        )
        self.assertEqual(result.candidates, [])
        self.assertIsNotNone(result.error)

    def test_extraction_model_error_marks_job_failed_when_nothing_stored(self) -> None:
        class FailingExtractor:
            def extract(self, **_: object) -> ExtractionResult:
                return ExtractionResult(
                    candidates=[],
                    error="LLMProviderError: no eligible model",
                )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            service, _, _ = self._service(root, extractor=FailingExtractor())
            service._workspace_service.register(  # noqa: SLF001
                workspace_id="project",
                root_path=str(workspace),
            )
            service.ensure_workspace_admin(
                workspace_id="project",
                actor_user_id="alice",
            )
            service.update_settings(
                workspace_id="project",
                actor_user_id="alice",
                mode="auto",
            )
            job = service.extract_and_store(
                workspace_id="project",
                actor_user_id="alice",
                source_type="agent_run",
                source_id="run_error",
                user_message="Fix the build",
                assistant_message="Done.",
                verified=True,
            )
            assert job is not None
            self.assertEqual(job.status, "failed")
            self.assertIn("no eligible model", job.error or "")

    def test_extraction_model_error_keeps_deterministic_fallback(self) -> None:
        class FailingExtractor:
            def extract(self, **_: object) -> ExtractionResult:
                return ExtractionResult(
                    candidates=[
                        candidate(
                            title="请记住：先跑 pytest",
                            content="请记住：先跑 pytest",
                            confidence=1.0,
                            authority="explicit_user",
                        )
                    ],
                    error="LLMProviderError: no eligible model",
                )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            service, _, _ = self._service(root, extractor=FailingExtractor())
            service._workspace_service.register(  # noqa: SLF001
                workspace_id="project",
                root_path=str(workspace),
            )
            service.ensure_workspace_admin(
                workspace_id="project",
                actor_user_id="alice",
            )
            service.update_settings(
                workspace_id="project",
                actor_user_id="alice",
                mode="auto",
            )
            job = service.extract_and_store(
                workspace_id="project",
                actor_user_id="alice",
                source_type="chat",
                source_id="run_fallback",
                user_message="请记住：先跑 pytest",
                assistant_message="好的",
                verified=False,
            )
            assert job is not None
            self.assertEqual(job.status, "completed")
            self.assertEqual(job.candidate_count, 1)
            self.assertIn("no eligible model", job.error or "")

    def test_fallback_extraction_deduplicates_sources_before_evidence_limit(self) -> None:
        class RaisingLLM:
            def complete(self, prompt: str):
                raise ValueError("invalid model JSON")

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            service, repository, _ = self._service(
                root, extractor=LLMMemoryExtractor(RaisingLLM())
            )
            service._workspace_service.register(
                workspace_id="project", root_path=str(workspace)
            )
            service.ensure_workspace_admin(workspace_id="project", actor_user_id="alice")
            service.update_settings(
                workspace_id="project", actor_user_id="alice", mode="auto"
            )
            source = {
                "kind": "search_match",
                "source_id": "run_duplicates",
                "path": ".dockerignore",
                "start_line": 1,
                "end_line": 2,
                "content_hash": "a" * 64,
                "excerpt": "Build exclusions",
            }
            payload = dict(
                workspace_id="project",
                actor_user_id="alice",
                source_type="agent_run",
                source_id="run_duplicates",
                user_message="Inspect the build configuration",
                assistant_message="The Docker build excludes local cache files.",
                verified=True,
                source_evidence=[
                    source,
                    *[
                        dict(source, start_line=10, end_line=12, content_hash="b" * 64)
                        for _ in range(5)
                    ],
                    dict(source, path="Dockerfile"),
                    dict(source, source_id="run_other"),
                    dict(source, kind="file"),
                    {"kind": "validation_result", "source_id": "run_duplicates"},
                    dict(source, path="over-budget.py"),
                ],
            )
            job = service.extract_and_store(**payload)
            assert job is not None
            self.assertEqual(
                (job.status, job.candidate_count, job.active_count),
                ("completed", 1, 1),
            )
            self.assertIn("invalid model JSON", job.error or "")
            memories = service.list_memories(
                workspace_id="project", actor_user_id="alice", limit=10
            )
            self.assertEqual(len(memories), 1)
            memory = repository.get_memory(memories[0].id)
            assert memory is not None
            self.assertEqual(
                [
                    (item.source_kind, item.source_id, item.path)
                    for item in memory.evidence
                ],
                [
                    ("agent_run", "run_duplicates", None),
                    ("search_match", "run_duplicates", ".dockerignore"),
                    ("search_match", "run_duplicates", "Dockerfile"),
                    ("search_match", "run_other", ".dockerignore"),
                    ("file", "run_duplicates", ".dockerignore"),
                    ("validation_result", "run_duplicates", None),
                ],
            )
            first = memory.evidence[1]
            self.assertEqual(
                (first.start_line, first.end_line, first.content_hash),
                (1, 2, "a" * 64),
            )
            self.assertIsNone(service.extract_and_store(**payload))

    def test_dense_failure_falls_back_to_lexical_retrieval(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            service, _, vector = self._service(
                root,
                extractor=StaticExtractor([]),
            )
            service._workspace_service.register(  # noqa: SLF001
                workspace_id="project",
                root_path=str(workspace),
            )
            service.ensure_workspace_admin(
                workspace_id="project",
                actor_user_id="alice",
            )
            service.update_settings(
                workspace_id="project",
                actor_user_id="alice",
                mode="auto",
            )
            memory = service.create_manual(
                workspace_id="project",
                actor_user_id="alice",
                kind="decision",
                title="Lexical fallback",
                content="PostgreSQL lexical retrieval remains available.",
                importance=5,
            )

            def fail_dense(**_: object):
                raise ConnectionError("qdrant unavailable")

            vector.search = fail_dense  # type: ignore[method-assign]
            recalled = service.retrieve(
                workspace_id="project",
                actor_user_id="alice",
                query="PostgreSQL lexical retrieval",
            )
            self.assertEqual([item.memory.id for item in recalled], [memory.id])

    def test_stale_dense_version_is_not_used_for_retrieval(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            service, _, _ = self._service(
                root,
                extractor=StaticExtractor([]),
            )
            service._workspace_service.register(  # noqa: SLF001
                workspace_id="project",
                root_path=str(workspace),
            )
            service.ensure_workspace_admin(
                workspace_id="project",
                actor_user_id="alice",
            )
            service.update_settings(
                workspace_id="project",
                actor_user_id="alice",
                mode="auto",
            )
            original = service.create_manual(
                workspace_id="project",
                actor_user_id="alice",
                kind="architecture_fact",
                title="Cache backend",
                content="The cache backend uses Redis.",
                importance=4,
            )
            service.process_index_outbox()
            service.update_memory(
                workspace_id="project",
                memory_id=original.id,
                actor_user_id="alice",
                expected_version=original.version,
                kind=original.kind,
                title=original.title,
                content="The cache backend uses PostgreSQL.",
                importance=original.importance,
                expires_at=None,
            )

            self.assertEqual(
                service.retrieve(
                    workspace_id="project",
                    actor_user_id="alice",
                    query="Redis",
                ),
                [],
            )

    def test_manual_memory_is_scoped_by_workspace_revision(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            alpha_v1 = root / "alpha-v1"
            alpha_v2 = root / "alpha-v2"
            beta = root / "beta"
            for path in (alpha_v1, alpha_v2, beta):
                path.mkdir()
            service, repository, _ = self._service(
                root, extractor=StaticExtractor([])
            )
            service._workspace_service.register(  # noqa: SLF001
                workspace_id="alpha",
                root_path=str(alpha_v1),
            )
            service.ensure_workspace_admin(
                workspace_id="alpha",
                actor_user_id="alice",
            )
            service.update_settings(
                workspace_id="alpha",
                actor_user_id="alice",
                mode="auto",
            )
            memory = service.create_manual(
                workspace_id="alpha",
                actor_user_id="alice",
                kind="decision",
                title="Database source of truth",
                content="PostgreSQL is the authoritative project-memory store.",
                importance=5,
            )
            self.assertEqual(memory.status, "active")
            self.assertEqual(memory.confidence, 1.0)
            self.assertEqual(
                [item.memory.id for item in service.retrieve(
                    workspace_id="alpha",
                    actor_user_id="alice",
                    query="authoritative project memory database",
                )],
                [memory.id],
            )

            service._workspace_service.register(  # noqa: SLF001
                workspace_id="beta",
                root_path=str(beta),
            )
            service.ensure_workspace_admin(
                workspace_id="beta",
                actor_user_id="alice",
            )
            service.update_settings(
                workspace_id="beta",
                actor_user_id="alice",
                mode="auto",
            )
            self.assertEqual(
                service.retrieve(
                    workspace_id="beta",
                    actor_user_id="alice",
                    query="authoritative project memory database",
                ),
                [],
            )

            updated_workspace = service._workspace_service.register(  # noqa: SLF001
                workspace_id="alpha",
                root_path=str(alpha_v2),
            )
            self.assertEqual(updated_workspace.revision, 2)
            self.assertEqual(
                service.list_memories(
                    workspace_id="alpha",
                    actor_user_id="alice",
                ),
                [],
            )
            previous = service.list_memories(
                workspace_id="alpha",
                actor_user_id="alice",
                include_previous_revisions=True,
            )
            self.assertEqual([item.id for item in previous], [memory.id])
            repository.ensure_member(
                workspace_id="alpha",
                user_id="bob",
                role="editor",
            )
            with self.assertRaises(MemoryAccessDeniedError):
                service.confirm(
                    workspace_id="alpha",
                    memory_id=memory.id,
                    actor_user_id="bob",
                    expected_version=memory.version,
                )
            copied = service.confirm(
                workspace_id="alpha",
                memory_id=memory.id,
                actor_user_id="alice",
                expected_version=memory.version,
            )
            self.assertNotEqual(copied.id, memory.id)
            self.assertEqual(copied.workspace_revision, 2)
            self.assertEqual(copied.supersedes_id, memory.id)
            self.assertEqual(
                [item.memory.id for item in service.retrieve(
                    workspace_id="alpha",
                    actor_user_id="alice",
                    query="authoritative project memory database",
                )],
                [copied.id],
            )

    def test_roles_optimistic_lock_and_hard_forget(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            service, repository, vector = self._service(
                root,
                extractor=StaticExtractor([]),
            )
            service._workspace_service.register(  # noqa: SLF001
                workspace_id="project",
                root_path=str(workspace),
            )
            service.ensure_workspace_admin(
                workspace_id="project",
                actor_user_id="admin",
            )
            repository.ensure_member(
                workspace_id="project",
                user_id="viewer",
                role="viewer",
            )
            service.ensure_workspace_admin(
                workspace_id="project",
                actor_user_id="viewer",
            )
            self.assertEqual(
                service.role_for(
                    workspace_id="project",
                    actor_user_id="viewer",
                ),
                "admin",
            )
            repository.ensure_member(
                workspace_id="project",
                user_id="viewer-only",
                role="viewer",
            )
            repository.ensure_member(
                workspace_id="project",
                user_id="editor",
                role="editor",
            )
            with self.assertRaises(MemoryAccessDeniedError):
                service.create_manual(
                    workspace_id="project",
                    actor_user_id="viewer-only",
                    kind="constraint",
                    title="No secrets",
                    content="Credentials must not be persisted.",
                    importance=5,
                )
            with self.assertRaises(MemoryValidationError):
                service.create_manual(
                    workspace_id="project",
                    actor_user_id="editor",
                    kind="constraint",
                    title="Environment value",
                    content="OPENAI_API_KEY=not-a-real-but-persistable-value",
                    importance=5,
                )
            memory = service.create_manual(
                workspace_id="project",
                actor_user_id="editor",
                kind="constraint",
                title="No secrets",
                content="Credentials must not be persisted.",
                importance=5,
            )
            with self.assertRaises(MemoryConflictError):
                service.update_memory(
                    workspace_id="project",
                    memory_id=memory.id,
                    actor_user_id="editor",
                    expected_version=memory.version + 1,
                    kind=memory.kind,
                    title=memory.title,
                    content=memory.content,
                    importance=memory.importance,
                    expires_at=None,
                )
            with self.assertRaises(MemoryAccessDeniedError):
                service.forget(
                    workspace_id="project",
                    memory_id=memory.id,
                    actor_user_id="editor",
                )
            service.forget(
                workspace_id="project",
                memory_id=memory.id,
                actor_user_id="admin",
            )
            self.assertIsNone(repository.get_memory(memory.id))
            self.assertEqual(
                vector.search(
                    workspace_id="project",
                    workspace_revision=1,
                    query_embedding=[0.0] * 32,
                    limit=10,
                ),
                [],
            )

    def test_extraction_thresholds_idempotency_dedup_and_conflicts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            extractor = StaticExtractor(
                [
                    candidate(
                        title="Discarded",
                        content="This confidence is too low.",
                        confidence=0.59,
                        authority="user_statement",
                    ),
                    candidate(
                        title="Assistant inference",
                        content="This must stay reviewable.",
                        confidence=0.95,
                        authority="assistant_inference",
                    ),
                    candidate(
                        title="Approved decision",
                        content="Use a transactional outbox.",
                        confidence=0.86,
                        authority="user_statement",
                        kind="decision",
                    ),
                ]
            )
            service, repository, _ = self._service(root, extractor=extractor)
            service._workspace_service.register(  # noqa: SLF001
                workspace_id="project",
                root_path=str(workspace),
            )
            service.ensure_workspace_admin(
                workspace_id="project",
                actor_user_id="alice",
            )
            service.update_settings(
                workspace_id="project",
                actor_user_id="alice",
                mode="auto",
            )
            job = service.extract_and_store(
                workspace_id="project",
                actor_user_id="alice",
                source_type="chat",
                source_id="chat_1",
                user_message="project facts",
                assistant_message="answer",
                verified=False,
            )
            assert job is not None
            self.assertEqual(job.status, "completed")
            self.assertEqual(job.candidate_count, 2)
            self.assertEqual(job.active_count, 2)
            self.assertIsNone(
                service.extract_and_store(
                    workspace_id="project",
                    actor_user_id="alice",
                    source_type="chat",
                    source_id="chat_1",
                    user_message="duplicate delivery",
                    assistant_message="answer",
                    verified=False,
                )
            )
            memories = service.list_memories(
                workspace_id="project",
                actor_user_id="alice",
            )
            self.assertEqual(
                sorted(item.status for item in memories),
                ["active", "active"],
            )

            extractor.candidates = [
                candidate(
                    title="Approved decision",
                    content="Use an append-only event log.",
                    confidence=1.0,
                    authority="explicit_user",
                    kind="decision",
                )
            ]
            second_job = service.extract_and_store(
                workspace_id="project",
                actor_user_id="alice",
                source_type="chat",
                source_id="chat_2",
                user_message="remember the replacement",
                assistant_message="answer",
                verified=False,
            )
            assert second_job is not None
            self.assertEqual(second_job.active_count, 1)
            decisions = [
                item
                for item in service.list_memories(
                    workspace_id="project",
                    actor_user_id="alice",
                )
                if item.kind == "decision"
            ]
            self.assertEqual(
                sorted(item.status for item in decisions),
                ["active", "superseded"],
            )

            extractor.candidates = [
                candidate(
                    title="Approved decision",
                    content="Keep the transactional outbox but review it manually.",
                    confidence=0.70,
                    authority="user_statement",
                    kind="decision",
                )
            ]
            conflict_job = service.extract_and_store(
                workspace_id="project",
                actor_user_id="alice",
                source_type="chat",
                source_id="chat_conflict",
                user_message="consider an alternative",
                assistant_message="answer",
                verified=False,
            )
            assert conflict_job is not None
            conflict = next(
                item
                for item in service.list_memories(
                    workspace_id="project",
                    actor_user_id="alice",
                )
                if item.kind == "decision" and "review it manually" in item.content
            )
            with self.assertRaises(MemoryConflictError):
                service.confirm(
                    workspace_id="project",
                    memory_id=conflict.id,
                    actor_user_id="alice",
                    expected_version=conflict.version + 1,
                )
            active_before_confirm = [
                item
                for item in service.list_memories(
                    workspace_id="project",
                    actor_user_id="alice",
                    status="active",
                )
                if item.kind == "decision"
            ]
            self.assertEqual(len(active_before_confirm), 1)
            confirmed = service.confirm(
                workspace_id="project",
                memory_id=conflict.id,
                actor_user_id="alice",
                expected_version=conflict.version,
            )
            self.assertEqual(confirmed.status, "active")
            active_after_confirm = [
                item
                for item in service.list_memories(
                    workspace_id="project",
                    actor_user_id="alice",
                    status="active",
                )
                if item.kind == "decision"
            ]
            self.assertEqual([item.id for item in active_after_confirm], [confirmed.id])

            extractor.candidates = [
                candidate(
                    title="Sensitive candidate",
                    content="token=super-secret-value",
                    confidence=1.0,
                    authority="explicit_user",
                ),
                candidate(
                    title="Safe candidate",
                    content="The test command is pytest -q.",
                    confidence=1.0,
                    authority="explicit_user",
                    kind="convention",
                ),
            ]
            safe_job = service.extract_and_store(
                workspace_id="project",
                actor_user_id="alice",
                source_type="chat",
                source_id="chat_3",
                user_message="mixed input",
                assistant_message="answer",
                verified=False,
            )
            assert safe_job is not None
            self.assertEqual(safe_job.status, "completed")
            self.assertEqual(safe_job.candidate_count, 1)
            security_events = [
                event
                for event in repository._audit_events  # noqa: SLF001
                if event.action == "security_rejected"
            ]
            self.assertEqual(len(security_events), 1)
            self.assertNotIn(
                "super-secret-value",
                repr(security_events[0].metadata),
            )

    def test_source_hash_mismatch_marks_memory_stale(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            source = workspace / "config.py"
            source.write_text("MEMORY_BACKEND = 'postgres'\n", encoding="utf-8")
            extractor = StaticExtractor(
                [
                    candidate(
                        title="Memory backend",
                        content="The current memory backend is PostgreSQL.",
                        confidence=0.91,
                        authority="verified_agent",
                    )
                ]
            )
            service, repository, _ = self._service(root, extractor=extractor)
            service._workspace_service.register(  # noqa: SLF001
                workspace_id="project",
                root_path=str(workspace),
            )
            service.ensure_workspace_admin(
                workspace_id="project",
                actor_user_id="alice",
            )
            service.update_settings(
                workspace_id="project",
                actor_user_id="alice",
                mode="auto",
            )
            raw_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            job = service.extract_and_store(
                workspace_id="project",
                actor_user_id="alice",
                source_type="agent_run",
                source_id="run_1",
                user_message="inspect config",
                assistant_message="verified",
                verified=True,
                source_evidence=[
                    {
                        "kind": "file",
                        "source_id": "run_1",
                        "path": "config.py",
                        "start_line": 1,
                        "end_line": 1,
                        "content_hash": raw_hash,
                    }
                ],
            )
            assert job is not None
            self.assertEqual(job.active_count, 1)
            before = service.retrieve(
                workspace_id="project",
                actor_user_id="alice",
                query="memory backend PostgreSQL",
            )
            self.assertEqual(len(before), 1)
            memory_id = before[0].memory.id

            source.write_text("MEMORY_BACKEND = 'sqlite'\n", encoding="utf-8")
            self.assertEqual(
                service.retrieve(
                    workspace_id="project",
                    actor_user_id="alice",
                    query="memory backend PostgreSQL",
                ),
                [],
            )
            stale = repository.get_memory(memory_id)
            assert stale is not None
            self.assertEqual(stale.status, "stale")

    def test_rule_based_explicit_memory_is_active_but_shadow_is_not_retrieved(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            service, _, _ = self._service(
                root,
                extractor=RuleBasedMemoryExtractor(),
            )
            service._workspace_service.register(  # noqa: SLF001
                workspace_id="project",
                root_path=str(workspace),
            )
            service.ensure_workspace_admin(
                workspace_id="project",
                actor_user_id="alice",
            )
            service.update_settings(
                workspace_id="project",
                actor_user_id="alice",
                mode="shadow",
            )
            job = service.extract_and_store(
                workspace_id="project",
                actor_user_id="alice",
                source_type="chat",
                source_id="chat_1",
                user_message="请记住：必须先运行 pytest 再提交",
                assistant_message="好的",
                verified=False,
            )
            assert job is not None
            self.assertEqual(job.active_count, 1)
            self.assertEqual(
                service.retrieve(
                    workspace_id="project",
                    actor_user_id="alice",
                    query="pytest",
                ),
                [],
            )

    def test_index_consistency_repair_updates_versions_and_removes_orphans(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            service, _, vector = self._service(
                root,
                extractor=StaticExtractor([]),
            )
            service._workspace_service.register(  # noqa: SLF001
                workspace_id="project",
                root_path=str(workspace),
            )
            service.ensure_workspace_admin(
                workspace_id="project",
                actor_user_id="alice",
            )
            memory = service.create_manual(
                workspace_id="project",
                actor_user_id="alice",
                kind="decision",
                title="Index source",
                content="PostgreSQL versions define index freshness.",
                importance=4,
            )
            vector.upsert(replace(memory, version=99), [1.0] + [0.0] * 31)
            orphan = replace(
                memory,
                id="mem_ffffffffffffffff",
                canonical_key="decision:orphan",
            )
            vector.upsert(orphan, [0.0, 1.0] + [0.0] * 30)

            repaired = service.reindex(
                workspace_id="project",
                actor_user_id="alice",
            )
            service.process_index_outbox()

            self.assertEqual(repaired, 2)
            self.assertEqual(
                vector.list_indexed(workspace_id="project"),
                {memory.id: (1, memory.version)},
            )

    @staticmethod
    def _service(
        allowed_root: Path,
        *,
        extractor,
        relevance_weight: float = 0.65,
        recency_weight: float = 0.20,
        importance_weight: float = 0.15,
        recency_half_life_days: float = 180.0,
        result_limit: int = 6,
    ) -> tuple[
        ProjectMemoryService,
        InMemoryProjectMemoryRepository,
        InMemoryMemoryVectorStore,
    ]:
        workspace_service = WorkspaceService(
            store=InMemoryWorkspaceRepository(),
            allowed_roots=(str(allowed_root),),
        )
        repository = InMemoryProjectMemoryRepository()
        vector = InMemoryMemoryVectorStore()
        service = ProjectMemoryService(
            repository=repository,
            workspace_service=workspace_service,
            embedding_provider=HashingEmbeddingProvider(dimensions=32),
            vector_store=vector,
            extractor=extractor,
            enabled=True,
            default_mode="off",
            candidate_threshold=0.60,
            auto_threshold=0.85,
            recall_limit=20,
            result_limit=result_limit,
            max_context_chars=3000,
            relevance_weight=relevance_weight,
            recency_weight=recency_weight,
            importance_weight=importance_weight,
            recency_half_life_days=recency_half_life_days,
        )
        return service, repository, vector


class ProjectMemoryApiTests(unittest.TestCase):
    def test_memory_api_lifecycle_and_removed_chat_route(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            with self._client(root) as client:
                headers = {"X-User-ID": "alice"}
                registered = client.put(
                    "/api/v1/workspaces/project",
                    headers=headers,
                    json={"root_path": str(workspace)},
                )
                self.assertEqual(registered.status_code, 200)
                settings_response = client.patch(
                    "/api/v1/workspaces/project/memory-settings",
                    headers=headers,
                    json={"mode": "auto"},
                )
                self.assertEqual(settings_response.json()["mode"], "auto")
                created = client.post(
                    "/api/v1/workspaces/project/memories",
                    headers=headers,
                    json={
                        "kind": "decision",
                        "title": "Storage authority",
                        "content": "PostgreSQL is the source of truth.",
                        "importance": 5,
                    },
                )
                self.assertEqual(created.status_code, 201)
                memory = created.json()
                updated = client.patch(
                    f"/api/v1/workspaces/project/memories/{memory['id']}",
                    headers=headers,
                    json={
                        "version": memory["version"],
                        "kind": memory["kind"],
                        "title": memory["title"],
                        "content": "PostgreSQL remains the source of truth.",
                        "importance": 5,
                    },
                )
                self.assertEqual(updated.status_code, 200)
                stale_update = client.patch(
                    f"/api/v1/workspaces/project/memories/{memory['id']}",
                    headers=headers,
                    json={
                        "version": memory["version"],
                        "kind": memory["kind"],
                        "title": memory["title"],
                        "content": "A stale write must fail.",
                        "importance": 5,
                    },
                )
                self.assertEqual(stale_update.status_code, 409)
                listed = client.get(
                    "/api/v1/workspaces/project/memories",
                    headers=headers,
                )
                self.assertEqual(len(listed.json()["memories"]), 1)

                session_id = client.post(
                    "/api/v1/sessions",
                    headers=headers,
                    json={"user_id": "alice"},
                ).json()["id"]
                old_client = client.post(
                    "/api/v1/chat/stream",
                    json={
                        "conversation_id": session_id,
                        "message": "hello",
                    },
                )
                self.assertEqual(old_client.status_code, 404)
                self.assertNotIn("event: memory_context", old_client.text)

                deleted = client.delete(
                    f"/api/v1/workspaces/project/memories/{memory['id']}",
                    headers=headers,
                )
                self.assertEqual(deleted.status_code, 204)
                self.assertEqual(
                    client.get(
                        "/api/v1/workspaces/project/memories",
                        headers=headers,
                    ).json()["memories"],
                    [],
                )

    def test_agent_does_not_recall_standalone_project_memory(self) -> None:
        from test_api import wait_for_run
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / 'project'
            other = root / 'other'
            project.mkdir()
            other.mkdir()
            with self._client(root) as client:
                headers = {'X-User-ID': 'alice'}
                for workspace_id, path in (('project', project), ('other', other)):
                    client.put(f'/api/v1/workspaces/{workspace_id}', headers=headers,
                        json={'root_path': str(path)}).raise_for_status()
                client.post('/api/v1/workspaces/project/memories', headers=headers, json={
                    'kind': 'decision', 'title': 'Private project decision',
                    'content': 'LEGACY_MEMORY_SECRET_SENTINEL', 'importance': 5}).raise_for_status()
                for workspace_id in ('project', 'other'):
                    session = client.post('/api/v1/sessions', headers=headers, json={'user_id': 'alice'}).json()['id']
                    started = client.post('/api/v1/agent/runs', headers=headers, json={
                        'conversation_id': session, 'workspace_id': workspace_id, 'message': '项目数据库的事实源是什么？'})
                    body = wait_for_run(client, started.json()['run_id'])
                    self.assertEqual(body['status'], 'completed')
                    state = client.app.state.query_service._runtime.get_run(body['run_id']).runtime_state
                    self.assertNotIn('LEGACY_MEMORY_SECRET_SENTINEL', str(state))
                stored = client.get('/api/v1/workspaces/project/memories', headers=headers).json()['memories']
                self.assertEqual(len(stored), 1)
                self.assertEqual(stored[0]['content'], 'LEGACY_MEMORY_SECRET_SENTINEL')

    def test_trusted_identity_blocks_spoofing_and_cross_user_access(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            settings = Settings(
                llm_provider="fake",
                embedding_provider="local",
                workspace_allowed_roots=(str(root),),
                project_memory_enabled=True,
                project_memory_mode="auto",
                auth_mode="trusted_header",
                gateway_trust_secret="unit-test-trust-secret",
            )
            alice_headers = {
                "X-Authenticated-User": "alice",
                "X-Gateway-Auth": "unit-test-trust-secret",
            }
            bob_headers = {
                "X-Authenticated-User": "bob",
                "X-Gateway-Auth": "unit-test-trust-secret",
            }
            with TestClient(create_app(settings=settings)) as client:
                self.assertEqual(
                    client.get("/api/v1/workspace-directories").status_code,
                    401,
                )
                spoofed = client.post(
                    "/api/v1/sessions",
                    headers={"X-Authenticated-User": "alice"},
                    json={"user_id": "mallory"},
                )
                self.assertEqual(spoofed.status_code, 401)
                session = client.post(
                    "/api/v1/sessions",
                    headers=alice_headers,
                    json={"user_id": "mallory"},
                )
                self.assertEqual(session.json()["user_id"], "alice")
                session_id = session.json()["id"]
                client.put(
                    "/api/v1/workspaces/project",
                    headers=alice_headers,
                    json={"root_path": str(workspace)},
                )
                duplicate = client.put(
                    "/api/v1/workspaces/project-copy",
                    headers=bob_headers,
                    json={"root_path": str(workspace)},
                )
                self.assertEqual(duplicate.status_code, 409)
                created = client.post(
                    "/api/v1/workspaces/project/memories",
                    headers=alice_headers,
                    json={
                        "kind": "constraint",
                        "title": "Safe boundary",
                        "content": "Never persist credentials.",
                        "importance": 5,
                    },
                )
                self.assertEqual(created.status_code, 201)

                self.assertEqual(
                    client.get(
                        f"/api/v1/sessions/{session_id}",
                        headers=bob_headers,
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    client.get(
                        "/api/v1/sessions",
                        headers=bob_headers,
                    ).json()["sessions"],
                    [],
                )
                self.assertEqual(
                    client.get(
                        "/api/v1/workspaces/project",
                        headers=bob_headers,
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    client.get(
                        "/api/v1/workspaces/project/memories",
                        headers=bob_headers,
                    ).status_code,
                    403,
                )
                bob_session = client.post(
                    "/api/v1/sessions",
                    headers=bob_headers,
                    json={"user_id": "alice"},
                ).json()["id"]
                self.assertEqual(
                    client.patch(
                        f"/api/v1/sessions/{bob_session}",
                        headers=bob_headers,
                        json={"configuration": {"workspace_id": "project"}},
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    client.patch(
                        "/api/v1/users/me/preferences",
                        headers=bob_headers,
                        json={"default_workspace_id": "project"},
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    client.post(
                        "/api/v1/agent/runs",
                        headers=bob_headers,
                        json={
                            "conversation_id": bob_session,
                            "workspace_id": "project",
                            "message": "Read the private workspace",
                        },
                    ).status_code,
                    403,
                )

    @staticmethod
    def _wait_for_memory(
        client: TestClient,
        workspace_id: str,
        headers: dict[str, str],
    ) -> list[dict]:
        for _ in range(100):
            response = client.get(
                f"/api/v1/workspaces/{workspace_id}/memories",
                headers=headers,
            )
            memories = response.json()["memories"]
            if memories:
                return memories
            time.sleep(0.01)
        raise AssertionError("memory extraction did not finish")

    @staticmethod
    def _client(allowed_root: Path) -> TestClient:
        settings = Settings(
            llm_provider="fake",
            embedding_provider="local",
            workspace_allowed_roots=(str(allowed_root),),
            project_memory_enabled=True,
            project_memory_mode="auto",
            background_task_workers=2,
        )
        return TestClient(create_app(settings=settings))


if __name__ == "__main__":
    unittest.main()
