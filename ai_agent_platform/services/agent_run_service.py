from __future__ import annotations

import logging
from time import perf_counter
from typing import Optional

from ai_agent_platform.agents.coding_agent import (
    AgentRunInvalidStateError,
    AgentRunNotFoundError,
    AgentRunRecord,
    AgentRunResult,
    CodingAgentRuntime,
)
from ai_agent_platform.core import (
    InProcessTaskQueue,
    MetricsRegistry,
    TaskQueue,
    TaskQueueError,
    log_context,
)
from ai_agent_platform.services.session_service import SessionService


logger = logging.getLogger(__name__)


class AgentRunService:
    """Submits coding-agent runs to a background executor and records outcomes."""

    def __init__(
        self,
        *,
        runtime: CodingAgentRuntime,
        session_service: SessionService,
        max_workers: int = 4,
        metrics: MetricsRegistry | None = None,
        task_queue: TaskQueue | None = None,
    ) -> None:
        self._runtime = runtime
        self._session_service = session_service
        self._metrics = metrics or MetricsRegistry()
        self._owns_task_queue = task_queue is None
        self._task_queue = task_queue or InProcessTaskQueue(
            max_workers=max_workers,
            metrics=self._metrics,
        )

    def submit_run(
        self,
        *,
        conversation_id: str,
        message: str,
        repository_id: str,
        focus_files: Optional[list[str]] = None,
    ) -> AgentRunRecord:
        history = self._session_service.list_messages(session_id=conversation_id)
        self._session_service.add_message(
            session_id=conversation_id,
            role="user",
            content=message,
        )
        record = self._runtime.create_queued_run(
            conversation_id=conversation_id,
            repository_id=repository_id,
        )
        try:
            self._task_queue.submit(
                "agent_run",
                self._execute_run,
                run_id=record.run_id,
                conversation_id=conversation_id,
                message=message,
                history=history,
                repository_id=repository_id,
                focus_files=focus_files or [],
            )
        except TaskQueueError as exc:
            self._metrics.increment("agent_runs_rejected_total")
            self._mark_queued_run_failed(record.run_id, str(exc))
            raise
        self._metrics.increment("agent_runs_submitted_total")
        return record

    def resume_run(
        self,
        *,
        run_id: str,
        approved: bool,
        feedback: Optional[str] = None,
    ) -> AgentRunRecord:
        record = self.get_run(run_id)
        if record.status != "waiting_approval":
            raise AgentRunInvalidStateError(run_id, record.status)
        try:
            self._task_queue.submit(
                "agent_resume",
                self._execute_resume,
                run_id=run_id,
                approved=approved,
                feedback=feedback,
            )
        except TaskQueueError:
            self._metrics.increment("agent_run_resumes_rejected_total")
            raise
        self._metrics.increment("agent_run_resumes_submitted_total")
        return record

    def get_run(self, run_id: str) -> AgentRunRecord:
        return self._runtime.get_run(run_id)

    def close(self) -> None:
        if self._owns_task_queue:
            self._task_queue.close()

    def _mark_queued_run_failed(self, run_id: str, error: str) -> None:
        mark_failed = getattr(self._runtime, "mark_queued_run_failed", None)
        if callable(mark_failed):
            mark_failed(run_id=run_id, error=error)

    def _execute_run(
        self,
        *,
        run_id: str,
        conversation_id: str,
        message: str,
        history,
        repository_id: str,
        focus_files: list[str],
    ) -> None:
        started_at = perf_counter()
        with log_context(
            run_id=run_id,
            conversation_id=conversation_id,
            repository_id=repository_id,
        ):
            logger.info("agent run started")
            try:
                result = self._runtime.run(
                    run_id=run_id,
                    conversation_id=conversation_id,
                    user_input=message,
                    history=history,
                    repository_id=repository_id,
                    focus_files=focus_files,
                )
            except Exception:
                self._record_execution_metrics(
                    status="failed",
                    started_at=started_at,
                )
                logger.exception("agent run failed")
                return
            self._record_execution_metrics(
                status=result.status,
                started_at=started_at,
            )
            logger.info("agent run finished", extra={"status": result.status})
            self._record_assistant_message(result)

    def _execute_resume(
        self,
        *,
        run_id: str,
        approved: bool,
        feedback: Optional[str],
    ) -> None:
        started_at = perf_counter()
        record = self.get_run(run_id)
        with log_context(
            run_id=run_id,
            conversation_id=record.conversation_id,
            repository_id=record.repository_id,
        ):
            logger.info("agent run resume started", extra={"approved": approved})
            try:
                result = self._runtime.resume(
                    run_id=run_id,
                    approved=approved,
                    feedback=feedback,
                )
            except Exception:
                self._record_execution_metrics(
                    status="failed",
                    started_at=started_at,
                )
                logger.exception("agent run resume failed")
                return
            self._record_execution_metrics(
                status=result.status,
                started_at=started_at,
            )
            logger.info(
                "agent run resume finished",
                extra={"status": result.status},
            )
            self._record_assistant_message(result)

    def _record_execution_metrics(self, *, status: str, started_at: float) -> None:
        duration_ms = int((perf_counter() - started_at) * 1000)
        self._metrics.increment("agent_run_executions_total")
        self._metrics.increment(f"agent_run_executions_{status}_total")
        self._metrics.observe_ms("agent_run_execution_duration_ms", duration_ms)

    def _record_assistant_message(self, result: AgentRunResult) -> None:
        if result.status != "completed" or not result.answer:
            return
        self._session_service.add_message(
            session_id=result.conversation_id,
            role="assistant",
            content=result.answer,
        )
