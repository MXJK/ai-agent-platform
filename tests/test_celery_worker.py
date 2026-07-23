import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ai_agent_platform.workers.celery_app import (
    celery_app,
    execute_agent_resume,
    execute_agent_run,
)


class CeleryWorkerTests(unittest.TestCase):
    def test_registers_only_agent_distributed_tasks(self) -> None:
        self.assertIn("ai_agent_platform.agent_run", celery_app.tasks)
        self.assertIn("ai_agent_platform.agent_resume", celery_app.tasks)
        self.assertNotIn("ai_agent_platform.repository_index", celery_app.tasks)

    def test_agent_handlers_delegate_json_payloads(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []
        services = SimpleNamespace(
            agent_run_service=SimpleNamespace(
                execute_run_task=lambda **kwargs: calls.append(("run", kwargs)),
                execute_resume_task=lambda **kwargs: calls.append(("resume", kwargs)),
            )
        )
        with patch(
            "ai_agent_platform.workers.celery_app.get_worker_services",
            return_value=services,
        ):
            execute_agent_run.run(run_id="run_1", history=[])
            execute_agent_resume.run(run_id="run_1", approved=True)

        self.assertEqual(calls[0][0], "run")
        self.assertEqual(calls[0][1]["run_id"], "run_1")
        self.assertFalse(calls[0][1]["broker_redelivered"])
        self.assertEqual(calls[1][0], "resume")


if __name__ == "__main__":
    unittest.main()
