from dataclasses import replace
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
from ai_agent_platform.services import AgentRunService


class InProcessTaskQueueTests(unittest.TestCase):
    def test_applies_backpressure_and_records_metrics(self) -> None:
        metrics = MetricsRegistry()
        queue = InProcessTaskQueue(max_workers=1, max_queue_size=0, metrics=metrics)
        started, release = Event(), Event()

        def blocking_task() -> None:
            started.set()
            release.wait(timeout=2)

        future = queue.submit("blocking_task", blocking_task)
        self.assertTrue(started.wait(timeout=1))
        with self.assertRaises(TaskQueueFullError):
            queue.submit("overflow", lambda: None)
        release.set()
        future.result(timeout=2)
        queue.close()
        counters = metrics.snapshot()["counters"]
        self.assertEqual(counters["background_tasks_completed_total"], 1)
        self.assertEqual(counters["background_tasks_rejected_total"], 1)

    def test_rejects_submissions_after_close(self) -> None:
        queue = InProcessTaskQueue(max_workers=1, max_queue_size=1)
        queue.close()
        with self.assertRaises(TaskQueueClosedError):
            queue.submit("closed", lambda: None)

    def test_agent_run_is_failed_when_queue_rejects_it(self) -> None:
        started, release = Event(), Event()
        queue = InProcessTaskQueue(max_workers=1, max_queue_size=0)
        queue.submit("blocking", lambda: (started.set(), release.wait(timeout=2)))
        self.assertTrue(started.wait(timeout=1))

        class RuntimeStub:
            def __init__(self) -> None:
                self.record: AgentRunRecord | None = None

            def create_queued_run(self, **kwargs: object) -> AgentRunRecord:
                self.record = AgentRunRecord(
                    run_id="run_rejected",
                    thread_id="run_rejected",
                    conversation_id="session_1",
                    workspace_id="workspace_main",
                    workspace_root="/tmp/workspace",
                    status="queued",
                    checkpoint_id=None,
                    latest_node=None,
                    next_nodes=["setup_workspace"],
                    trace=[],
                )
                return self.record

            def mark_queued_run_failed(self, *, run_id: str, error: str):
                assert self.record is not None
                self.record = replace(self.record, status="failed", error=error)
                return self.record

        runtime = RuntimeStub()
        service = AgentRunService(
            runtime=runtime,
            session_service=SimpleNamespace(
                list_messages=lambda **_: [],
                add_message=lambda **_: None,
            ),
            workspace_service=SimpleNamespace(
                resolve_for_run=lambda _: "/tmp/workspace"
            ),
            task_queue=queue,
        )
        with self.assertRaises(TaskQueueFullError):
            service.submit_run(
                conversation_id="session_1",
                message="explain code",
                workspace_id="workspace_main",
            )
        self.assertEqual(runtime.record.status, "failed")
        release.set()
        queue.close()


class CeleryTaskQueueTests(unittest.TestCase):
    def test_publishes_agent_task_and_rejects_removed_index_task(self) -> None:
        with patch("celery.Celery") as celery_factory:
            celery_app = celery_factory.return_value
            queue = CeleryTaskQueue(broker_url="redis://localhost:6379/0")
            queue.submit(
                "agent_run",
                lambda: None,
                run_id="run_1",
                workspace_id="workspace_main",
            )
            with self.assertRaises(TaskQueueError):
                queue.submit("repository_index", lambda: None)
            queue.close()
        call = celery_app.send_task.call_args
        self.assertEqual(call.args, ("ai_agent_platform.agent_run",))
        UUID(call.kwargs["task_id"])

    def test_duplicate_payloads_use_the_same_task_id(self) -> None:
        with patch("celery.Celery") as celery_factory:
            app = celery_factory.return_value
            queue = CeleryTaskQueue(broker_url="redis://localhost:6379/0")
            for _ in range(2):
                queue.submit("agent_run", lambda: None, run_id="run_1")
        ids = [call.kwargs["task_id"] for call in app.send_task.call_args_list]
        self.assertEqual(ids[0], ids[1])


class AgentWorkerLossTests(unittest.TestCase):
    def test_redelivered_running_agent_is_failed_without_replay(self) -> None:
        class RuntimeStub:
            def __init__(self) -> None:
                self.run_calls = 0
                self.record = AgentRunRecord(
                    run_id="run_1",
                    thread_id="run_1",
                    conversation_id="session_1",
                    workspace_id="workspace_main",
                    workspace_root="/tmp/workspace",
                    status="running",
                    checkpoint_id=None,
                    latest_node="execute_changes",
                    next_nodes=["validate_changes"],
                    trace=[],
                )

            def get_run(self, _: str):
                return self.record

            def mark_run_failed(self, *, error: str, **_: object):
                self.record = replace(self.record, status="failed", error=error)
                return self.record

            def run(self, **_: object) -> None:
                self.run_calls += 1

        runtime = RuntimeStub()
        service = AgentRunService(
            runtime=runtime,
            session_service=SimpleNamespace(),
            workspace_service=SimpleNamespace(),
        )
        service.execute_run_task(
            run_id="run_1",
            conversation_id="session_1",
            message="change code",
            history=[],
            workspace_id="workspace_main",
            focus_files=[],
            broker_redelivered=True,
        )
        service.close()
        self.assertEqual(runtime.run_calls, 0)
        self.assertEqual(runtime.record.status, "failed")


if __name__ == "__main__":
    unittest.main()
