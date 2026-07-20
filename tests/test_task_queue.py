from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID

from ai_agent_platform.agents.coding_agent import AgentRunRecord
from ai_agent_platform.core import (
    CeleryTaskQueue,
    InProcessTaskQueue,
    MetricsRegistry,
    TaskQueueClosedError,
    TaskQueueError,
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

    def test_redelivery_recovers_a_running_repository_job(self) -> None:
        class RecordingRAGService:
            def __init__(self) -> None:
                self.calls = 0

            def ingest_document(self, **_: object) -> SimpleNamespace:
                self.calls += 1
                return SimpleNamespace(document_id="doc_test")

        with TemporaryDirectory() as temp_dir:
            Path(temp_dir, "app.py").write_text("print('ok')\n", encoding="utf-8")
            store = InMemoryRepositoryIndexRepository()
            job = store.create_index_job(
                repository_id="repo_main",
                root_path=temp_dir,
                include_patterns=[],
                exclude_patterns=[],
                max_file_size=1024,
            )
            store.update_index_job(
                job_id=job.id,
                status="running",
                scanned_files=0,
                indexed_files=0,
                skipped_files=0,
                failed_files=0,
            )
            rag_service = RecordingRAGService()
            service = RepositoryIndexingService(
                rag_service=rag_service,
                index_store=store,
            )

            service.execute_index_job(
                job_id=job.id,
                repository_id="repo_main",
            )
            self.assertEqual(rag_service.calls, 0)

            service.execute_index_job(
                job_id=job.id,
                repository_id="repo_main",
                recover_running=True,
            )
            service.close()

        self.assertEqual(rag_service.calls, 1)
        self.assertEqual(store.get_index_job(job.id).status, "completed")


class CeleryTaskQueueTests(unittest.TestCase):
    def test_publishes_named_json_task_to_celery(self) -> None:
        with patch("celery.Celery") as celery_factory:
            celery_app = celery_factory.return_value
            celery_result = celery_app.send_task.return_value
            queue = CeleryTaskQueue(
                broker_url="redis://localhost:6379/0",
            )

            result = queue.submit(
                "repository_index",
                lambda: None,
                job_id="idxjob_1",
                repository_id="repo_main",
            )
            queue.close()

        self.assertIs(result, celery_result)
        celery_app.send_task.assert_called_once()
        call = celery_app.send_task.call_args
        self.assertEqual(call.args, ("ai_agent_platform.repository_index",))
        self.assertEqual(
            call.kwargs["kwargs"],
            {
                "job_id": "idxjob_1",
                "repository_id": "repo_main",
            },
        )
        UUID(call.kwargs["task_id"])
        self.assertIn("repository_index", call.kwargs["headers"]["idempotency_key"])
        self.assertTrue(call.kwargs["retry"])
        self.assertEqual(call.kwargs["retry_policy"]["max_retries"], 3)
        celery_app.close.assert_called_once_with()

    def test_uses_same_task_id_for_duplicate_payloads(self) -> None:
        with patch("celery.Celery") as celery_factory:
            celery_app = celery_factory.return_value
            queue = CeleryTaskQueue(broker_url="redis://localhost:6379/0")

            for _ in range(2):
                queue.submit(
                    "agent_run",
                    lambda: None,
                    run_id="run_1",
                    conversation_id="session_1",
                )

        task_ids = [
            call.kwargs["task_id"] for call in celery_app.send_task.call_args_list
        ]
        self.assertEqual(task_ids[0], task_ids[1])

    def test_rejects_unknown_distributed_task_name(self) -> None:
        with patch("celery.Celery"):
            queue = CeleryTaskQueue(broker_url="redis://localhost:6379/0")

        with self.assertRaisesRegex(TaskQueueError, "unsupported distributed task"):
            queue.submit("unknown_task", lambda: None)


class AgentWorkerLossTests(unittest.TestCase):
    def test_redelivered_running_agent_is_failed_without_replaying_tools(self) -> None:
        class RuntimeStub:
            def __init__(self) -> None:
                self.run_calls = 0
                self.record = AgentRunRecord(
                    run_id="run_1",
                    thread_id="run_1",
                    conversation_id="session_1",
                    repository_id="repo_main",
                    status="running",
                    checkpoint_id=None,
                    latest_node="execute_changes",
                    next_nodes=["validate_changes"],
                    trace=[],
                )

            def get_run(self, run_id: str) -> AgentRunRecord:
                self.assert_run_id(run_id)
                return self.record

            def mark_run_failed(self, *, run_id: str, error: str, **_: object):
                self.assert_run_id(run_id)
                self.record = replace(
                    self.record,
                    status="failed",
                    error=error,
                )
                return self.record

            def run(self, **_: object) -> None:
                self.run_calls += 1

            @staticmethod
            def assert_run_id(run_id: str) -> None:
                if run_id != "run_1":
                    raise AssertionError(run_id)

        runtime = RuntimeStub()
        service = AgentRunService(
            runtime=runtime,
            session_service=SimpleNamespace(),
        )

        service.execute_run_task(
            run_id="run_1",
            conversation_id="session_1",
            message="change the repository",
            history=[],
            repository_id="repo_main",
            focus_files=[],
            broker_redelivered=True,
        )
        service.close()

        self.assertEqual(runtime.run_calls, 0)
        self.assertEqual(runtime.record.status, "failed")
        self.assertIn("duplicate side effects", runtime.record.error or "")


if __name__ == "__main__":
    unittest.main()
