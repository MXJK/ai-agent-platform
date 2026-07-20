import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ai_agent_platform.workers.celery_app import (
    celery_app,
    execute_agent_resume,
    execute_agent_run,
    execute_repository_index,
)


class CeleryWorkerTests(unittest.TestCase):
    def test_registers_distributed_task_names(self) -> None:
        self.assertIn("ai_agent_platform.agent_run", celery_app.tasks)
        self.assertIn("ai_agent_platform.agent_resume", celery_app.tasks)
        self.assertIn("ai_agent_platform.repository_index", celery_app.tasks)

    def test_task_handlers_delegate_json_payloads_to_worker_services(self) -> None:
        services = SimpleNamespace(
            agent_run_service=SimpleNamespace(
                execute_run_task=lambda **kwargs: calls.append(("run", kwargs)),
                execute_resume_task=lambda **kwargs: calls.append(("resume", kwargs)),
            ),
            repository_indexing_service=SimpleNamespace(
                execute_index_job=lambda **kwargs: calls.append(("index", kwargs)),
            ),
        )
        calls: list[tuple[str, dict[str, object]]] = []
        with patch(
            "ai_agent_platform.workers.celery_app.get_worker_services",
            return_value=services,
        ):
            execute_agent_run.run(run_id="run_1", history=[])
            execute_agent_resume.run(run_id="run_1", approved=True)
            execute_repository_index.run(
                job_id="idxjob_1",
                repository_id="repo_main",
            )

        self.assertEqual(
            calls,
            [
                ("run", {"run_id": "run_1", "history": []}),
                ("resume", {"run_id": "run_1", "approved": True}),
                (
                    "index",
                    {"job_id": "idxjob_1", "repository_id": "repo_main"},
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
