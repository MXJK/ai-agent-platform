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
from ai_agent_platform.services.workspace_service import WorkspaceService
from ai_agent_platform.project_memory.service import ProjectMemoryService


logger = logging.getLogger(__name__)


class AgentRunExecutionError(RuntimeError):
    """Signals a completed business failure that must not be blindly retried."""


class AgentRunService:
    """Submits coding-agent runs to a background executor and records outcomes."""

    def __init__(
        self,
        *,
        runtime: CodingAgentRuntime,
        session_service: SessionService,
        workspace_service: WorkspaceService,
        max_workers: int = 4,
        metrics: MetricsRegistry | None = None,
        task_queue: TaskQueue | None = None,
        project_memory_service: ProjectMemoryService | None = None,
        max_context_messages: int = 12,
        llm_provider: str = "agent",
        llm_model: str = "aggregated",
    ) -> None:
        self._runtime = runtime
        self._session_service = session_service
        self._workspace_service = workspace_service
        self._metrics = metrics or MetricsRegistry()
        self._owns_task_queue = task_queue is None
        self._task_queue = task_queue or InProcessTaskQueue(
            max_workers=max_workers,
            metrics=self._metrics,
        )
        self._project_memory_service = project_memory_service
        self._max_context_messages = max_context_messages
        self._llm_provider = llm_provider
        self._llm_model = llm_model

    def submit_run(
        self,
        *,
        conversation_id: str,
        message: str,
        workspace_id: str,
        focus_files: Optional[list[str]] = None,
        actor_user_id: str | None = None,
    ) -> AgentRunRecord:
        if actor_user_id is not None and self._project_memory_service is not None:
            self._project_memory_service.authorize(
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                required_role="viewer",
            )
        workspace_root = self._workspace_service.resolve_for_run(workspace_id)
        get_session = getattr(self._session_service, "get_session", None)
        session = (
            get_session(session_id=conversation_id)
            if callable(get_session)
            else None
        )
        if (
            actor_user_id is not None
            and session is not None
            and session.user_id != actor_user_id
        ):
            raise PermissionError("conversation access denied")
        resolved_actor = (
            actor_user_id
            or (session.user_id if session is not None else "demo_user")
        )
        build_agent_context = getattr(
            self._session_service,
            "build_agent_context",
            None,
        )
        if callable(build_agent_context):
            history_payload = build_agent_context(
                session_id=conversation_id,
                max_context_messages=self._max_context_messages,
            )
        else:
            history = self._session_service.list_messages(
                session_id=conversation_id
            )
            history_payload = [
                {"role": item.role, "content": item.content}
                for item in history
            ]
        self._session_service.add_message(
            session_id=conversation_id,
            role="user",
            content=message,
        )
        record = self._runtime.create_queued_run(
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            workspace_root=workspace_root,
        )
        try:
            self._task_queue.submit(
                "agent_run",
                self.execute_run_task,
                run_id=record.run_id,
                conversation_id=conversation_id,
                message=message,
                history=history_payload,
                workspace_id=workspace_id,
                focus_files=focus_files or [],
                actor_user_id=resolved_actor,
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
        actor_user_id: str | None = None,
    ) -> AgentRunRecord:
        record = self.get_run(run_id)
        self._assert_actor(record, actor_user_id)
        if record.status != "waiting_approval":
            raise AgentRunInvalidStateError(run_id, record.status)
        try:
            self._task_queue.submit(
                "agent_resume",
                self.execute_resume_task,
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

    def get_run_for_actor(
        self, run_id: str, actor_user_id: str | None
    ) -> AgentRunRecord:
        record = self.get_run(run_id)
        self._assert_actor(record, actor_user_id)
        return record

    def close(self) -> None:
        if self._owns_task_queue:
            self._task_queue.close()

    def _mark_queued_run_failed(self, run_id: str, error: str) -> None:
        mark_failed = getattr(self._runtime, "mark_queued_run_failed", None)
        if callable(mark_failed):
            mark_failed(run_id=run_id, error=error)

    def fail_run_task(
        self,
        *,
        run_id: str,
        error: str,
        attempt: int,
        max_attempts: int,
    ) -> None:
        mark_failed = getattr(self._runtime, "mark_run_failed", None)
        if callable(mark_failed):
            mark_failed(
                run_id=run_id,
                error=error,
                node="task_execution",
                attempt=attempt,
                max_attempts=max_attempts,
            )
            return
        self._mark_queued_run_failed(run_id, error)

    def execute_run_task(
        self,
        *,
        run_id: str,
        conversation_id: str,
        message: str,
        history: list[dict[str, str]],
        workspace_id: str,
        focus_files: list[str],
        actor_user_id: str = "demo_user",
        broker_redelivered: bool = False,
    ) -> None:
        started_at = perf_counter()
        record = self.get_run(run_id)
        if broker_redelivered and record.status == "running":
            self._metrics.increment("agent_run_worker_lost_total")
            self.fail_run_task(
                run_id=run_id,
                error=(
                    "worker was lost during Agent execution; automatic replay was "
                    "blocked to prevent duplicate side effects"
                ),
                attempt=1,
                max_attempts=1,
            )
            return
        if record.status != "queued":
            self._metrics.increment("agent_run_duplicate_deliveries_total")
            logger.info(
                "agent run delivery skipped",
                extra={"run_id": run_id, "status": record.status},
            )
            return
        with log_context(
            run_id=run_id,
            conversation_id=conversation_id,
            workspace_id=record.workspace_id,
        ):
            logger.info("agent run started")
            try:
                result = self._runtime.run(
                    run_id=run_id,
                    conversation_id=conversation_id,
                    user_input=message,
                    history=history,
                    workspace_id=record.workspace_id,
                    workspace_root=record.workspace_root,
                    focus_files=focus_files,
                    actor_user_id=actor_user_id,
                )
            except Exception as exc:
                self._record_execution_metrics(
                    status="failed",
                    started_at=started_at,
                )
                logger.exception("agent run failed")
                raise AgentRunExecutionError(str(exc)) from exc
            self._record_execution_metrics(
                status=result.status,
                started_at=started_at,
            )
            self._record_token_usage(result)
            logger.info("agent run finished", extra={"status": result.status})
            self._record_assistant_message(
                result,
                user_message=message,
                actor_user_id=actor_user_id,
            )

    def execute_resume_task(
        self,
        *,
        run_id: str,
        approved: bool,
        feedback: Optional[str],
        broker_redelivered: bool = False,
    ) -> None:
        started_at = perf_counter()
        record = self.get_run(run_id)
        if broker_redelivered and record.status == "waiting_approval":
            self._metrics.increment("agent_resume_worker_lost_total")
            self.fail_run_task(
                run_id=run_id,
                error=(
                    "worker was lost during Agent resume; automatic replay was "
                    "blocked to prevent duplicate side effects"
                ),
                attempt=1,
                max_attempts=1,
            )
            return
        if record.status != "waiting_approval":
            self._metrics.increment("agent_resume_duplicate_deliveries_total")
            logger.info(
                "agent resume delivery skipped",
                extra={"run_id": run_id, "status": record.status},
            )
            return
        with log_context(
            run_id=run_id,
            conversation_id=record.conversation_id,
            workspace_id=record.workspace_id,
        ):
            logger.info("agent run resume started", extra={"approved": approved})
            try:
                result = self._runtime.resume(
                    run_id=run_id,
                    approved=approved,
                    feedback=feedback,
                )
            except Exception as exc:
                self._record_execution_metrics(
                    status="failed",
                    started_at=started_at,
                )
                logger.exception("agent run resume failed")
                raise AgentRunExecutionError(str(exc)) from exc
            self._record_execution_metrics(
                status=result.status,
                started_at=started_at,
            )
            self._record_token_usage(result)
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

    def _record_token_usage(self, result: AgentRunResult) -> None:
        if result.metrics.total_tokens <= 0:
            return
        record_usage = getattr(
            self._session_service,
            "record_token_usage",
            None,
        )
        if not callable(record_usage):
            return
        record_usage(
            session_id=result.conversation_id,
            workspace_id=result.workspace_id,
            provider=self._llm_provider,
            model=self._llm_model,
            input_tokens=result.metrics.input_tokens,
            output_tokens=result.metrics.output_tokens,
            thoughts_tokens=result.metrics.thoughts_tokens,
            record_id=f"usage_agent_{result.run_id}",
        )

    def _record_assistant_message(
        self,
        result: AgentRunResult,
        *,
        user_message: str | None = None,
        actor_user_id: str | None = None,
    ) -> None:
        if result.status != "completed" or not result.answer:
            return
        assistant_messages = self._session_service.add_message(
            session_id=result.conversation_id,
            role="assistant",
            content=result.answer,
        )
        if assistant_messages:
            enqueue_compression = getattr(
                self._session_service,
                "enqueue_compression",
                None,
            )
            if callable(enqueue_compression):
                enqueue_compression(
                    task_queue=self._task_queue,
                    session_id=result.conversation_id,
                    trigger_message_id=assistant_messages[-1].id,
                )
        if self._project_memory_service is None:
            return
        session = self._session_service.get_session(
            session_id=result.conversation_id
        )
        if user_message is None:
            messages = self._session_service.list_messages(
                session_id=result.conversation_id
            )
            user_message = next(
                (
                    item.content
                    for item in reversed(messages)
                    if item.role == "user"
                ),
                "",
            )
        source_evidence = [
            {
                "kind": source.kind,
                "source_id": result.run_id,
                "path": source.path,
                "start_line": source.start_line,
                "end_line": source.end_line,
                "content_hash": source.content_hash,
                "excerpt": source.text[:500],
            }
            for source in result.context_sources
            if source.kind not in {"knowledge_chunk", "project_memory"}
            and not source.path.startswith(("knowledge://", "memory://"))
        ]
        if result.change_summary.validation_command_count:
            source_evidence.append(
                {
                    "kind": "validation_result",
                    "source_id": result.run_id,
                    "excerpt": (
                        f"{result.change_summary.validation_command_count} "
                        "validation command(s); "
                        f"passed={result.change_summary.validation_passed}"
                    ),
                }
            )
        try:
            self._task_queue.submit(
                "memory_extraction",
                self._project_memory_service.extract_and_store,
                workspace_id=result.workspace_id,
                actor_user_id=actor_user_id or session.user_id,
                source_type="agent_run",
                source_id=result.run_id,
                user_message=user_message or "",
                assistant_message=result.answer,
                verified=(
                    result.change_summary.validation_passed
                    or bool(source_evidence)
                ),
                source_evidence=source_evidence,
            )
        except TaskQueueError:
            self._metrics.increment("project_memory_agent_extraction_enqueue_failed_total")
            logger.warning(
                "agent memory extraction enqueue skipped",
                exc_info=True,
                extra={"run_id": result.run_id},
            )

    def _assert_actor(
        self,
        record: AgentRunRecord,
        actor_user_id: str | None,
    ) -> None:
        if actor_user_id is None:
            return
        session = self._session_service.get_session(
            session_id=record.conversation_id
        )
        if session.user_id != actor_user_id:
            raise PermissionError("agent run access denied")
