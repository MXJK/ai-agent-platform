from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest

from ai_agent_platform.agents.coding_agent import AgentRunRecord
from ai_agent_platform.core import (
    InProcessTaskQueue,
    MetricsRegistry,
    TaskQueueClosedError,
    TaskQueueFullError,
)
from ai_agent_platform.repositories import InMemoryRepositoryIndexRepository
from ai_agent_platform.services import (
    AgentRunService,
    RepositoryIndexConflictError,
    RepositoryIndexingService,
)


class InProcessTaskQueueTests(unittest.TestCase):
    def test_applies_backpressure_and_records_task_metrics(self) -> None:
        metrics = MetricsRegistry()
        queue = InProcessTaskQueue(
            max_workers=1,
            max_queue_size=0,
            metrics=metrics,
        )
        started = Event()
        release = Event()

        def blocking_task() -> None:
            started.set()
            release.wait(timeout=2)

        future = queue.submit("blocking_task", blocking_task)
        self.assertTrue(started.wait(timeout=1))
        with self.assertRaises(TaskQueueFullError):
            queue.submit("overflow_task", lambda: None)
        release.set()
        future.result(timeout=2)
        queue.close()

        counters = metrics.snapshot()["counters"]
        self.assertEqual(counters["background_tasks_submitted_total"], 1)
        self.assertEqual(counters["background_tasks_completed_total"], 1)
        self.assertEqual(counters["background_tasks_rejected_total"], 1)
        timings = metrics.snapshot()["timings"]
        self.assertEqual(timings["background_task_queue_wait_ms"]["count"], 1)

    def test_rejects_submissions_after_close(self) -> None:
        queue = InProcessTaskQueue(max_workers=1, max_queue_size=1)
        queue.close()

        with self.assertRaises(TaskQueueClosedError):
            queue.submit("closed", lambda: None)

    def test_agent_run_is_marked_failed_when_queue_rejects_it(self) -> None:
        started = Event()
        release = Event()
        queue = InProcessTaskQueue(max_workers=1, max_queue_size=0)

        def blocking_task() -> None:
            started.set()
            release.wait(timeout=2)

        queue.submit("blocking", blocking_task)
        self.assertTrue(started.wait(timeout=1))

        class SessionServiceStub:
            def list_messages(self, **_: object) -> list[object]:
                return []

            def add_message(self, **_: object) -> None:
                return None

        class AgentRuntimeStub:
            def __init__(self) -> None:
                self.record: AgentRunRecord | None = None

            def create_queued_run(self, **_: object) -> AgentRunRecord:
                self.record = AgentRunRecord(
                    run_id="run_rejected",
                    thread_id="run_rejected",
                    conversation_id="session_1",
                    repository_id="repo_main",
                    status="queued",
                    checkpoint_id=None,
                    latest_node=None,
                    next_nodes=["setup"],
                    trace=[],
                )
                return self.record

            def mark_queued_run_failed(
                self,
                *,
                run_id: str,
                error: str,
            ) -> AgentRunRecord:
                assert self.record is not None
                self.record = AgentRunRecord(
                    run_id=run_id,
                    thread_id=self.record.thread_id,
                    conversation_id=self.record.conversation_id,
                    repository_id=self.record.repository_id,
                    status="failed",
                    checkpoint_id=None,
                    latest_node=None,
                    next_nodes=[],
                    trace=[],
                    error=error,
                )
                return self.record

        runtime = AgentRuntimeStub()
        service = AgentRunService(
            runtime=runtime,
            session_service=SessionServiceStub(),
            task_queue=queue,
        )
        with self.assertRaises(TaskQueueFullError):
            service.submit_run(
                conversation_id="session_1",
                message="explain the repository",
                repository_id="repo_main",
            )
        self.assertIsNotNone(runtime.record)
        self.assertEqual(runtime.record.status, "failed")
        self.assertIn("capacity exceeded", runtime.record.error or "")

        release.set()
        queue.close()


class RepositoryIndexTaskTests(unittest.TestCase):
    def test_rejects_overlapping_jobs_for_the_same_repository(self) -> None:
        started = Event()
        release = Event()

        class BlockingRAGService:
            def ingest_document(self, **_: object) -> SimpleNamespace:
                started.set()
                release.wait(timeout=2)
                return SimpleNamespace(document_id="doc_test")

        with TemporaryDirectory() as temp_dir:
            Path(temp_dir, "app.py").write_text(
                "def main():\n    return True\n",
                encoding="utf-8",
            )
            queue = InProcessTaskQueue(max_workers=1, max_queue_size=2)
            service = RepositoryIndexingService(
                rag_service=BlockingRAGService(),
                index_store=InMemoryRepositoryIndexRepository(),
                task_queue=queue,
            )
            first_job = service.submit_index_repository(
                repository_id="repo_main",
                root_path=temp_dir,
            )
            self.assertTrue(started.wait(timeout=1))

            with self.assertRaises(RepositoryIndexConflictError):
                service.submit_index_repository(
                    repository_id="repo_main",
                    root_path=temp_dir,
                )

            release.set()
            queue.close()
            completed = service.get_index_job(
                repository_id="repo_main",
                job_id=first_job.id,
            )
            self.assertEqual(completed.status, "completed")


if __name__ == "__main__":
    unittest.main()
