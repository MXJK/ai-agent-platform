from __future__ import annotations

import logging
from dataclasses import replace
from time import perf_counter
from typing import Optional
from uuid import uuid4

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
from ai_agent_platform.model_registry import (
    ModelRegistryService,
    ModelSelection,
    model_selection_scope,
)
from ai_agent_platform.services.session_service import SessionService
from ai_agent_platform.services.workspace_service import WorkspaceService
from ai_agent_platform.services.execution_context import ExecutionContextFactory
from ai_agent_platform.usage_ledger import model_usage_scope
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
        model_registry: ModelRegistryService | None = None,
        execution_context_factory: ExecutionContextFactory | None = None,
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
        self._model_registry = model_registry
        self._execution_context_factory = execution_context_factory
        self._run_model_selections: dict[str, ModelSelection] = {}

    def submit_run(
        self,
        *,
        conversation_id: str,
        message: str,
        workspace_id: str | None,
        focus_files: Optional[list[str]] = None,
        actor_user_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
        routing_policy: str | None = None,
        cwd: str | None = None,
        additional_workspace_ids: Optional[list[str]] = None,
    ) -> AgentRunRecord:
        resolve_execution_config = getattr(
            self._session_service,
            "resolve_execution_config",
            None,
        )
        execution_config = (
            resolve_execution_config(
                session_id=conversation_id,
                provider=provider,
                model=model,
                thinking_level=thinking_level,
                workspace_id=workspace_id,
            )
            if callable(resolve_execution_config)
            else {
                "provider": provider,
                "model": model,
                "thinking_level": thinking_level,
                "workspace_id": workspace_id,
            }
        )
        workspace_id = execution_config["workspace_id"]
        if not workspace_id:
            raise ValueError("workspace_id is required for Agent runs")
        registry_selection = (
            self._model_registry.selection_for_session(conversation_id)
            if self._model_registry is not None
            and provider is None
            and model is None
            else None
        )
        registry_preference = registry_selection is not None and (
            registry_selection.mode == "manual"
            or (
                execution_config["provider"] is None
                and execution_config["model"] is None
            )
        )
        if registry_preference:
            selection = replace(
                registry_selection,
                thinking_level=execution_config["thinking_level"],
            )
        else:
            selection = self._model_selection(
                provider=execution_config["provider"],
                model=execution_config["model"],
                thinking_level=execution_config["thinking_level"],
                routing_policy=routing_policy,
            )
        if self._execution_context_factory is not None:
            context_snapshot = self._execution_context_factory.create(
                conversation_id=conversation_id,
                user_message=message,
                workspace_id=workspace_id,
                model_selection=selection,
                actor_user_id=actor_user_id,
                focus_files=focus_files or [],
                cwd=cwd,
                additional_workspace_ids=additional_workspace_ids or [],
                run_id=f"run_{uuid4().hex[:12]}",
            )
            resolved_actor = context_snapshot.identity.actor_user_id
            workspace_root = context_snapshot.project.workspace_root
            history_payload = [
                {"role": item.role, "content": item.content}
                for item in context_snapshot.session.controlled_history
            ]
            self._session_service.add_message(
                session_id=conversation_id,
                role="user",
                content=message,
            )
            record = self._runtime.create_queued_run(
                run_id=context_snapshot.metadata.run_id,
                conversation_id=conversation_id,
                workspace_id=workspace_id,
                workspace_root=workspace_root,
                context_snapshot=context_snapshot,
            )
        else:
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
        if self._model_registry is not None:
            selection = self._model_registry.snapshot_run_selection(
                record.run_id,
                conversation_id,
                selection,
            )
        self._run_model_selections[record.run_id] = selection
        try:
            task_payload = {"run_id": record.run_id}
            if record.context_snapshot is None:
                task_payload.update(
                    conversation_id=conversation_id,
                    message=message,
                    history=history_payload,
                    workspace_id=workspace_id,
                    focus_files=focus_files or [],
                    actor_user_id=resolved_actor,
                    model_selection=selection.__dict__,
                )
            self._task_queue.submit(
                "agent_run",
                self.execute_run_task,
                **task_payload,
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
        if (
            approved
            and actor_user_id is not None
            and self._project_memory_service is not None
            and self._approval_requires_editor(record)
        ):
            self._project_memory_service.authorize(
                workspace_id=record.workspace_id,
                actor_user_id=actor_user_id,
                required_role="editor",
            )
        resolve_execution_config = getattr(
            self._session_service,
            "resolve_execution_config",
            None,
        )
        if callable(resolve_execution_config):
            resolve_execution_config(session_id=record.conversation_id)
        selection = self._selection_for_record(record)
        mark_resume_queued = getattr(self._runtime, "mark_resume_queued", None)
        queued_record = (
            mark_resume_queued(run_id) if callable(mark_resume_queued) else record
        )
        try:
            self._task_queue.submit(
                "agent_resume",
                self.execute_resume_task,
                run_id=run_id,
                approved=approved,
                feedback=feedback,
                actor_user_id=actor_user_id,
                model_selection=(
                    selection.__dict__ if record.context_snapshot is None else None
                ),
            )
        except TaskQueueError:
            restore_record = getattr(self._runtime, "restore_record", None)
            if callable(restore_record):
                restore_record(record)
            self._metrics.increment("agent_run_resumes_rejected_total")
            raise
        self._metrics.increment("agent_run_resumes_submitted_total")
        return queued_record

    def get_run(self, run_id: str) -> AgentRunRecord:
        return self._runtime.get_run(run_id)

    def get_run_for_actor(
        self, run_id: str, actor_user_id: str | None
    ) -> AgentRunRecord:
        record = self.get_run(run_id)
        self._assert_actor(record, actor_user_id)
        return record

    def get_latest_run_for_actor(
        self,
        conversation_id: str,
        actor_user_id: str | None,
    ) -> AgentRunRecord:
        session = self._session_service.get_session(session_id=conversation_id)
        if actor_user_id is not None and session.user_id != actor_user_id:
            raise PermissionError("agent run access denied")
        get_latest = getattr(self._runtime, "get_latest_run", None)
        record = get_latest(conversation_id) if callable(get_latest) else None
        if record is None:
            raise AgentRunNotFoundError(conversation_id)
        self._assert_actor(record, actor_user_id)
        return record

    def list_events_for_actor(
        self,
        run_id: str,
        actor_user_id: str | None,
        *,
        after: int = 0,
    ):
        record = self.get_run_for_actor(run_id, actor_user_id)
        list_events = getattr(self._runtime, "list_events", None)
        if not callable(list_events):
            return record, []
        return record, list_events(run_id, after=after)

    def control_run(
        self,
        *,
        run_id: str,
        action: str,
        message: str = "",
        actor_user_id: str | None = None,
    ) -> AgentRunRecord:
        record = self.get_run_for_actor(run_id, actor_user_id)
        request_control = getattr(self._runtime, "request_control", None)
        if not callable(request_control):
            raise RuntimeError("Agent runtime does not support lifecycle controls")
        updated = request_control(run_id=run_id, action=action, message=message)
        self._metrics.increment(f"agent_run_control_{action}_total")
        return updated

    def continue_run(
        self,
        *,
        run_id: str,
        message: str = "",
        actor_user_id: str | None = None,
    ) -> AgentRunRecord:
        record = self.get_run_for_actor(run_id, actor_user_id)
        if record.status not in {"paused", "waiting_input"}:
            raise AgentRunInvalidStateError(run_id, record.status)
        selection = self._selection_for_record(record)
        mark_resume_queued = getattr(self._runtime, "mark_resume_queued", None)
        queued_record = (
            mark_resume_queued(run_id) if callable(mark_resume_queued) else record
        )
        try:
            self._task_queue.submit(
                "agent_resume",
                self.execute_resume_task,
                run_id=run_id,
                approved=True,
                feedback=message,
                actor_user_id=actor_user_id,
                model_selection=(
                    selection.__dict__ if record.context_snapshot is None else None
                ),
            )
        except TaskQueueError:
            restore_record = getattr(self._runtime, "restore_record", None)
            if callable(restore_record):
                restore_record(record)
            raise
        self._metrics.increment("agent_run_continues_submitted_total")
        return queued_record

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
        conversation_id: str | None = None,
        message: str | None = None,
        history: list[dict[str, str]] | None = None,
        workspace_id: str | None = None,
        focus_files: list[str] | None = None,
        actor_user_id: str = "demo_user",
        model_selection: dict | None = None,
        broker_redelivered: bool = False,
    ) -> None:
        started_at = perf_counter()
        record = self.get_run(run_id)
        context_snapshot = record.context_snapshot
        if context_snapshot is not None:
            if context_snapshot.metadata.run_id != run_id:
                raise AgentRunExecutionError("persisted Run context ID mismatch")
            conversation_id = context_snapshot.session.conversation_id
            message = context_snapshot.session.user_message
            history = [
                {"role": item.role, "content": item.content}
                for item in context_snapshot.session.controlled_history
            ]
            workspace_id = context_snapshot.project.workspace_id
            focus_files = list(context_snapshot.instructions.focus_files)
            actor_user_id = context_snapshot.identity.actor_user_id
            model_selection = context_snapshot.session.model_selection.to_dict()
        if conversation_id is None or message is None or history is None:
            raise AgentRunExecutionError("Run execution context is unavailable")
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
                with model_selection_scope(
                    ModelSelection(**model_selection) if model_selection else None
                ):
                    with model_usage_scope(
                        session_id=conversation_id,
                        workspace_id=record.workspace_id,
                        operation="agent",
                        resource_id=run_id,
                    ):
                        result = self._runtime.run(
                            run_id=run_id,
                            conversation_id=conversation_id,
                            user_input=message,
                            history=history,
                            workspace_id=record.workspace_id,
                            workspace_root=record.workspace_root,
                            focus_files=focus_files,
                            actor_user_id=actor_user_id,
                            run_context=context_snapshot,
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
        actor_user_id: str | None = None,
        model_selection: dict | None = None,
        broker_redelivered: bool = False,
    ) -> None:
        started_at = perf_counter()
        record = self.get_run(run_id)
        resume_pending = (
            record.status == "running" and record.control_action == "resume"
        )
        if broker_redelivered and (
            resume_pending
            or record.status in {
                "waiting_approval",
                "waiting_input",
                "paused",
            }
        ):
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
        if (
            record.status not in {"waiting_approval", "waiting_input", "paused"}
            and not resume_pending
        ):
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
                if (
                    approved
                    and actor_user_id is not None
                    and self._project_memory_service is not None
                    and self._approval_requires_editor(record)
                ):
                    self._project_memory_service.authorize(
                        workspace_id=record.workspace_id,
                        actor_user_id=actor_user_id,
                        required_role="editor",
                    )
                snapshot_selection = (
                    record.context_snapshot.session.model_selection.to_dict()
                    if record.context_snapshot is not None
                    else model_selection
                )
                with model_selection_scope(
                    ModelSelection(**snapshot_selection)
                    if snapshot_selection
                    else None
                ):
                    with model_usage_scope(
                        session_id=record.conversation_id,
                        workspace_id=record.workspace_id,
                        operation="agent",
                        resource_id=run_id,
                    ):
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
            logger.info(
                "agent run resume finished",
                extra={"status": result.status},
            )
            self._record_assistant_message(result)

    @staticmethod
    def _approval_requires_editor(record: AgentRunRecord) -> bool:
        pending = record.pending_approval or {}
        approval_tools = pending.get("approval_required_tools") or []
        if approval_tools:
            return any(
                item.get("permission_level") != "read_only"
                for item in approval_tools
                if isinstance(item, dict)
            )
        return pending.get("type") in {
            "tool_plan_review",
            "repair_plan_review",
        }

    def _record_execution_metrics(self, *, status: str, started_at: float) -> None:
        duration_ms = int((perf_counter() - started_at) * 1000)
        self._metrics.increment("agent_run_executions_total")
        self._metrics.increment(f"agent_run_executions_{status}_total")
        self._metrics.observe_ms("agent_run_execution_duration_ms", duration_ms)

    def _model_selection(
        self,
        *,
        provider: str | None,
        model: str | None,
        thinking_level: str | None,
        routing_policy: str | None,
    ) -> ModelSelection:
        if provider is not None or model is not None:
            return ModelSelection(
                mode="manual",
                routing_policy=(routing_policy or "smart"),  # type: ignore[arg-type]
                preferred_provider=provider or self._llm_provider,
                preferred_model=model or self._llm_model,
                thinking_level=thinking_level,
                fallback_enabled=True,
            )
        return ModelSelection(
            mode="auto",
            routing_policy=(routing_policy or "smart"),  # type: ignore[arg-type]
            thinking_level=thinking_level,
        )

    def _selection_for_record(self, record: AgentRunRecord) -> ModelSelection:
        if record.context_snapshot is not None:
            return ModelSelection(
                **record.context_snapshot.session.model_selection.to_dict()
            )
        if self._model_registry is not None:
            return self._model_registry.selection_for_run(
                record.run_id,
                record.conversation_id,
            )
        return self._run_model_selections.get(
            record.run_id,
            self._model_selection(
                provider=None,
                model=None,
                thinking_level=None,
                routing_policy=None,
            ),
        )

    def _record_assistant_message(
        self,
        result: AgentRunResult,
        *,
        user_message: str | None = None,
        actor_user_id: str | None = None,
    ) -> None:
        if result.status not in {"completed", "partial", "blocked"} or not result.answer:
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
