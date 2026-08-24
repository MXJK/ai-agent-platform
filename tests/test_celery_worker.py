import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ai_agent_platform.workers.celery_app import (
    initialize_worker_runtime,
    celery_app,
    execute_agent_checkpoint_restore,
    execute_conversation_compression,
    execute_agent_resume,
    execute_agent_run,
    execute_memory_extraction,
    execute_memory_index_outbox,
)


class CeleryWorkerTests(unittest.TestCase):
    def test_worker_process_hook_initializes_shared_runtime(self) -> None:
        with patch(
            "ai_agent_platform.workers.celery_app.get_worker_services"
        ) as get_services:
            initialize_worker_runtime()

        get_services.assert_called_once_with()

    def test_registers_only_agent_distributed_tasks(self) -> None:
        self.assertIn("ai_agent_platform.agent_run", celery_app.tasks)
        self.assertIn("ai_agent_platform.agent_resume", celery_app.tasks)
        self.assertIn("ai_agent_platform.agent_checkpoint_restore", celery_app.tasks)
        self.assertIn("ai_agent_platform.memory_extraction", celery_app.tasks)
        self.assertIn("ai_agent_platform.memory_index_outbox", celery_app.tasks)
        self.assertIn("ai_agent_platform.conversation_compression", celery_app.tasks)
        self.assertNotIn("ai_agent_platform.repository_index", celery_app.tasks)

    def test_agent_handlers_delegate_json_payloads(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []
        services = SimpleNamespace(
            query_service=SimpleNamespace(
                execute_run_task=lambda **kwargs: calls.append(("run", kwargs)),
                execute_resume_task=lambda **kwargs: calls.append(("resume", kwargs)),
                execute_checkpoint_restore_task=lambda **kwargs: calls.append(
                    ("checkpoint", kwargs)
                ),
            )
        )
        with patch(
            "ai_agent_platform.workers.celery_app.get_worker_services",
            return_value=services,
        ):
            execute_agent_run.run(run_id="run_1", history=[])
            execute_agent_resume.run(run_id="run_1", approved=True)
            execute_agent_checkpoint_restore.run(run_id="run_branch")

        self.assertEqual(calls[0][0], "run")
        self.assertEqual(calls[0][1]["run_id"], "run_1")
        self.assertFalse(calls[0][1]["broker_redelivered"])
        self.assertEqual(calls[1][0], "resume")
        self.assertEqual(calls[2][0], "checkpoint")
        self.assertEqual(calls[2][1]["run_id"], "run_branch")

    def test_memory_extraction_handler_delegates_json_payload(self) -> None:
        calls: list[dict[str, object]] = []
        services = SimpleNamespace(
            project_memory_service=SimpleNamespace(
                extract_and_store=lambda **kwargs: calls.append(kwargs),
            )
        )
        with patch(
            "ai_agent_platform.workers.celery_app.get_worker_services",
            return_value=services,
        ):
            execute_memory_extraction.run(
                workspace_id="project",
                source_type="agent_run",
                source_id="run_1",
            )

        self.assertEqual(calls[0]["source_id"], "run_1")

    def test_memory_index_outbox_handler_delegates_json_payload(self) -> None:
        calls: list[dict[str, object]] = []
        services = SimpleNamespace(
            project_memory_service=SimpleNamespace(
                process_index_outbox=lambda **kwargs: calls.append(kwargs),
            )
        )
        with patch(
            "ai_agent_platform.workers.celery_app.get_worker_services",
            return_value=services,
        ):
            execute_memory_index_outbox.run(trigger_id="mem_1:1")

        self.assertEqual(calls, [{"trigger_id": "mem_1:1"}])

    def test_conversation_compression_handler_delegates_json_payload(self) -> None:
        calls: list[dict[str, object]] = []
        services = SimpleNamespace(
            session_service=SimpleNamespace(
                compress_conversation=lambda **kwargs: calls.append(kwargs),
            )
        )
        with patch(
            "ai_agent_platform.workers.celery_app.get_worker_services",
            return_value=services,
        ):
            execute_conversation_compression.run(
                session_id="session_1",
                trigger_message_id="msg_12",
            )

        self.assertEqual(
            calls,
            [{"session_id": "session_1", "trigger_message_id": "msg_12"}],
        )


if __name__ == "__main__":
    unittest.main()
