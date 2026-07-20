from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_agent_platform.core import Settings
from ai_agent_platform.workers.reliability import (
    execute_reliable_task,
    is_broker_redelivery,
    is_retry_or_redelivery,
    retry_delay_seconds,
)


class RetryScheduled(Exception):
    pass


class FakeTask:
    def __init__(self, *, retries: int = 0, redelivered: bool = False) -> None:
        self.request = SimpleNamespace(
            retries=retries,
            delivery_info={"redelivered": redelivered},
        )
        self.retry_kwargs: dict[str, object] | None = None

    def retry(self, **kwargs: object) -> Exception:
        self.retry_kwargs = kwargs
        return RetryScheduled()


class TaskReliabilityTests(unittest.TestCase):
    def test_retries_transient_error_with_bounded_backoff(self) -> None:
        task = FakeTask()
        failures: list[tuple[str, int, int]] = []
        with patch(
            "ai_agent_platform.workers.reliability.retry_delay_seconds",
            return_value=2,
        ), self.assertRaises(RetryScheduled):
            execute_reliable_task(
                task=task,
                task_name="repository_index",
                task_reference="idxjob_1",
                settings=Settings(),
                handler=lambda: (_ for _ in ()).throw(
                    ConnectionError("postgres unavailable")
                ),
                failure_handler=lambda error, attempt, max_attempts: failures.append(
                    (error, attempt, max_attempts)
                ),
            )

        self.assertEqual(failures, [])
        self.assertEqual(task.retry_kwargs["countdown"], 2)
        self.assertEqual(task.retry_kwargs["max_retries"], 3)

    def test_persists_permanent_failure_without_retry(self) -> None:
        task = FakeTask()
        failures: list[tuple[str, int, int]] = []

        with self.assertRaisesRegex(ValueError, "invalid payload"):
            execute_reliable_task(
                task=task,
                task_name="agent_run",
                task_reference="run_1",
                settings=Settings(),
                handler=lambda: (_ for _ in ()).throw(
                    ValueError("invalid payload")
                ),
                failure_handler=lambda error, attempt, max_attempts: failures.append(
                    (error, attempt, max_attempts)
                ),
            )

        self.assertIsNone(task.retry_kwargs)
        self.assertEqual(failures[0][1:], (1, 4))
        self.assertIn("invalid payload", failures[0][0])

    def test_persists_transient_failure_after_retries_are_exhausted(self) -> None:
        task = FakeTask(retries=3)
        failures: list[tuple[str, int, int]] = []

        with self.assertRaises(ConnectionError):
            execute_reliable_task(
                task=task,
                task_name="agent_run",
                task_reference="run_1",
                settings=Settings(),
                handler=lambda: (_ for _ in ()).throw(
                    ConnectionError("postgres unavailable")
                ),
                failure_handler=lambda error, attempt, max_attempts: failures.append(
                    (error, attempt, max_attempts)
                ),
            )

        self.assertEqual(failures[0][1:], (4, 4))

    def test_backoff_is_exponential_capped_and_jittered(self) -> None:
        with patch("random.randint", return_value=7) as randint:
            delay = retry_delay_seconds(
                retry_number=5,
                base_seconds=2,
                max_seconds=30,
            )

        self.assertEqual(delay, 7)
        randint.assert_called_once_with(0, 30)

    def test_detects_broker_redelivery_and_celery_retry(self) -> None:
        self.assertTrue(is_broker_redelivery(FakeTask(redelivered=True)))
        self.assertFalse(is_broker_redelivery(FakeTask(retries=1)))
        self.assertTrue(is_retry_or_redelivery(FakeTask(redelivered=True)))
        self.assertTrue(is_retry_or_redelivery(FakeTask(retries=1)))
        self.assertFalse(is_retry_or_redelivery(FakeTask()))


if __name__ == "__main__":
    unittest.main()
