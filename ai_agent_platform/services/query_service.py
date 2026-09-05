from __future__ import annotations

from ai_agent_platform.cogent.leases import RunLeaseUnavailable

import asyncio
import logging
import json
import shlex
from dataclasses import replace
from threading import Lock
from time import perf_counter
from typing import Any, AsyncIterator, Optional
from uuid import NAMESPACE_URL, uuid4, uuid5

from ai_agent_platform.agents.coding.models import (
    AgentCheckpoint,
    AgentCheckpointNotFoundError,
    AgentCheckpointRestoreError,
    AgentRunEvent,
    AgentRunInvalidStateError,
    AgentRunNotFoundError,
    AgentRunRecord,
    AgentRunResult,
)
from ai_agent_platform.cogent.protocol import AgentRuntime
from ai_agent_platform.cogent.commands.parser import parse_command
from ai_agent_platform.cogent.commands.catalog import LOCAL_COMMANDS, command_capabilities, resolve_command
from ai_agent_platform.agents.coding.user_questions import (
    normalize_questions,
    parse_question_response,
)
from ai_agent_platform.core import (
    InProcessTaskQueue,
    MetricsRegistry,
    TaskQueue,
    TaskQueueError,
    log_context,
)
from ai_agent_platform.domain import (
    AgentEvent,
    QueryCommand,
    QueryLifecycle,
    QueryParams,
    QueryResult,
    QueryStateError,
)
from ai_agent_platform.model_registry import (
    ModelRegistryService,
    ModelSelection,
    model_selection_scope,
)
from ai_agent_platform.repositories import SessionNotFoundError
from ai_agent_platform.services.session_service import SessionService
from ai_agent_platform.services.workspace_service import WorkspaceService
from ai_agent_platform.services.execution_context import ExecutionContextFactory
from ai_agent_platform.services.query_events import (
    AgentEventEncoder,
    EventStore,
    RuntimeEventStore,
)
from ai_agent_platform.integrations.permissions import (
    PermissionRequest,
    PermissionResolver,
    ToolUseContext,
    canonical_arguments_hash,
    effective_approval_policy,
)
from ai_agent_platform.integrations.tools import ToolRegistry
from ai_agent_platform.integrations.tool_pool import (
    ToolPoolBuilder,
    ToolPoolRestoreError,
)
from ai_agent_platform.usage_ledger import model_usage_scope


logger = logging.getLogger(__name__)


class AgentRunExecutionError(RuntimeError):
    """Signals a completed business failure that must not be blindly retried."""


class QueryService:
    """Entrypoint-independent command lifecycle for coding-agent queries."""

    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        session_service: SessionService,
        workspace_service: WorkspaceService,
        max_workers: int = 4,
        metrics: MetricsRegistry | None = None,
        task_queue: TaskQueue | None = None,
        workspace_authorizer: Any = None,
        max_context_messages: int = 12,
        llm_provider: str = "agent",
        llm_model: str = "aggregated",
        model_registry: ModelRegistryService | None = None,
        execution_context_factory: ExecutionContextFactory | None = None,
        permission_resolver: PermissionResolver | None = None,
        tool_registry: ToolRegistry | None = None,
        tool_pool_builder: ToolPoolBuilder | None = None,
        query_uow=None,
        event_encoder: AgentEventEncoder | None = None,
        event_store: EventStore | None = None,
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
        self._workspace_authorizer = workspace_authorizer
        self._max_context_messages = max_context_messages
        self._llm_provider = llm_provider
        self._llm_model = llm_model
        self._model_registry = model_registry
        self._execution_context_factory = execution_context_factory
        self._permission_resolver = permission_resolver
        self._tool_registry = tool_registry or getattr(runtime, "_tools", None)
        runtime_tool_access = getattr(runtime, "_tool_access", None)
        self._tool_pool_builder = tool_pool_builder or getattr(
            runtime_tool_access,
            "_tool_pool_builder",
            None,
        )
        self._query_uow = query_uow
        self._event_encoder = event_encoder or AgentEventEncoder()
        self._event_store = event_store or RuntimeEventStore(runtime)
        self._run_model_selections: dict[str, ModelSelection] = {}
        self._assistant_message_lock = Lock()
        self._recorded_assistant_runs: set[str] = set()

    @property
    def event_encoder(self) -> AgentEventEncoder:
        return self._event_encoder

    def start(self, params: QueryParams) -> AgentRunRecord:
        name, arguments, is_command = parse_command(params.message)
        command = resolve_command(name) if is_command else None
        if is_command and name == 'exit':
            raise ValueError('/exit is a CLI-local command')
        if command and command.name == 'resume':
            try:
                previous = self.get_latest_run_for_actor(params.conversation_id, params.actor_user_id)
            except AgentRunNotFoundError:
                previous = None
            if previous is None:
                raise ValueError('No Run is available to resume')
            if previous.status == 'waiting_approval':
                if arguments not in {'approve', 'reject'}:
                    raise ValueError('Use /resume approve or /resume reject after reviewing the pending operation')
                return self.resume_run(run_id=previous.run_id, approved=arguments == 'approve', actor_user_id=params.actor_user_id)
            return self.continue_run(run_id=previous.run_id, message=arguments, actor_user_id=params.actor_user_id)
        if command and command.name == 'compact':
            try:
                previous = self.get_latest_run_for_actor(params.conversation_id, params.actor_user_id)
            except AgentRunNotFoundError:
                previous = None
            if previous and previous.status in {'running', 'paused'}:
                return self.compact_run(run_id=previous.run_id, instruction=arguments, actor_user_id=params.actor_user_id)
        try:
            latest = self.get_latest_run_for_actor(params.conversation_id, params.actor_user_id)
        except AgentRunNotFoundError:
            latest = None
        if latest is not None and latest.runtime_engine == 'cogent-v1':
            if latest.status in QueryLifecycle.ACTIVE_STATUSES | QueryLifecycle.SUSPENDED_STATUSES:
                raise AgentRunInvalidStateError(latest.run_id, latest.status)
            if command and command.name == 'permissions' and arguments and arguments != 'plan' and latest.runtime_state.get('permission_mode') == 'plan':
                raise ValueError('Confirm the plan through ExitPlanMode before leaving plan mode')
        if command and command.name == 'permissions' and arguments and arguments not in {'default', 'acceptEdits', 'plan', 'bypassPermissions'}:
            raise ValueError('Unsupported Cogent permission mode')
        if command and command.name == 'plan':
            params = replace(params, permission_mode='plan', message=arguments or 'Inspect the current task and prepare a plan. Ask the user to confirm it with ExitPlanMode before implementation.')
        elif command and command.name == 'review':
            params = replace(params, permission_mode='plan', message=arguments or 'Review the current Git diff. Report concrete issues with file and line references. Do not edit any file.')
        if is_command and (command is None or command.name == 'skill' and arguments):
            capabilities = self.composer_capabilities(conversation_id=params.conversation_id,
                workspace_id=params.workspace_id or '', actor_user_id=params.actor_user_id)
            tokens = shlex.split(arguments)
            skill_name = tokens.pop(0) if command else name
            selected = next((item for item in capabilities['skill_commands']
                if skill_name in {item['name'], item['skill_name'], item['skill_qualified_name'], *item['aliases']}), None)
            mcp = next((item for item in capabilities['mcp_tools'] if item['name'] == name), None)
            if selected is None and mcp is not None:
                params = replace(params, preferred_tool_name=mcp['name'], message=arguments or f"Use MCP tool {mcp['name']} for the current request.")
            elif selected is None:
                unavailable = next((item for item in capabilities.get('unavailable_skill_commands', [])
                    if skill_name in {item['name'], item['skill_name'], item['skill_qualified_name'], *item['aliases']}), None)
                if unavailable is None:
                    raise ValueError(f'Unknown or unavailable Cogent command: /{name}')
                params = replace(params, skill_name=unavailable['skill_qualified_name'], skill_arguments=tuple(tokens),
                                 message=' '.join(tokens) or f'Apply Skill {skill_name}.')
            else:
                params = replace(params, skill_name=selected['skill_qualified_name'], skill_arguments=tuple(tokens),
                                 message=' '.join(tokens) or f"Apply Skill {selected['skill_qualified_name']}.")
            command = None
        metadata = params.metadata_dict()
        if command:
            metadata['cogent_command'] = {'name': command.name, 'arguments': arguments}
        else:
            metadata.pop('cogent_command', None)
        return self._start_query(
            conversation_id=params.conversation_id,
            message=params.message,
            workspace_id=params.workspace_id,
            focus_files=list(params.focus_files),
            actor_user_id=params.actor_user_id,
            provider=params.provider,
            model=params.model,
            thinking_level=params.thinking_level,
            routing_policy=params.routing_policy,
            mode=params.mode,
            permission_mode=params.permission_mode,
            sandbox_enabled=params.sandbox_enabled,
            sandbox_network_enabled=params.sandbox_network_enabled,
            cwd=params.cwd,
            additional_workspace_ids=list(params.additional_workspace_ids),
            skill_name=params.skill_name,
            skill_arguments=params.skill_arguments,
            preferred_tool_name=params.preferred_tool_name,
            evaluation=params.evaluation,
            entrypoint_type=params.entrypoint,
            entrypoint_metadata=metadata,
        )

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
        evaluation: bool = False,
        permission_mode: str = "default",
        sandbox_enabled: bool = True,
        sandbox_network_enabled: bool = False,
    ) -> AgentRunRecord:
        return self.start(
            QueryParams(
                conversation_id=conversation_id,
                message=message,
                workspace_id=workspace_id,
                focus_files=tuple(focus_files or ()),
                provider=provider,
                model=model,
                thinking_level=thinking_level,
                routing_policy=routing_policy,
                cwd=cwd,
                additional_workspace_ids=tuple(additional_workspace_ids or ()),
                actor_user_id=actor_user_id,
                evaluation=evaluation,
                permission_mode=permission_mode,
                sandbox_enabled=sandbox_enabled,
                sandbox_network_enabled=sandbox_network_enabled,
                entrypoint=(
                    self._execution_context_factory.entrypoint_type
                    if self._execution_context_factory is not None
                    else "sdk"
                ),
                entrypoint_metadata={
                    "adapter": "AgentRunService",
                    "evaluation": {
                        "isolated": True,
                    }
                    if evaluation
                    else {},
                },
            )
        )

    def _start_query(
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
        mode: str | None = None,
        cwd: str | None = None,
        additional_workspace_ids: Optional[list[str]] = None,
        entrypoint_type: str = "sdk",
        entrypoint_metadata: Optional[dict[str, object]] = None,
        skill_name: str | None = None,
        skill_arguments: tuple[str, ...] = (),
        preferred_tool_name: str | None = None,
        evaluation: bool = False,
        permission_mode: str = "default",
        sandbox_enabled: bool = True,
        sandbox_network_enabled: bool = False,
    ) -> AgentRunRecord:
        if mode is not None and mode not in {"auto", "manual"}:
            raise ValueError("Query mode must be auto or manual")
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
                mode=mode or registry_selection.mode,
                thinking_level=execution_config["thinking_level"],
            )
        else:
            selection = self._model_selection(
                provider=execution_config["provider"],
                model=execution_config["model"],
                thinking_level=execution_config["thinking_level"],
                routing_policy=routing_policy,
                mode=mode,
            )
        run_id = f"run_{uuid4().hex[:12]}"
        snapshot_entrypoint_metadata = dict(entrypoint_metadata or {})
        snapshot_entrypoint_metadata.update(
            permission_mode=permission_mode,
            sandbox_enabled=sandbox_enabled,
            sandbox_network_enabled=sandbox_network_enabled,
        )
        if evaluation:
            snapshot_entrypoint_metadata["evaluation"] = {
                "isolated": True,
            }
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
                run_id=run_id,
                entrypoint_type=entrypoint_type,
                entrypoint_metadata=snapshot_entrypoint_metadata,
                skill_name=skill_name,
                skill_arguments=skill_arguments,
                preferred_tool_name=preferred_tool_name,
                isolated=evaluation,
            )
            resolved_actor = context_snapshot.identity.actor_user_id
            workspace_root = context_snapshot.project.workspace_root
            history_payload = [
                {"role": item.role, "content": item.content}
                for item in context_snapshot.session.controlled_history
            ]
            try:
                if self._query_uow is not None:
                    QueryLifecycle.assert_transition(None, "queued")
                    create_record = getattr(self._runtime, "create_queued_record", None)
                    record = (
                        create_record(
                            run_id=context_snapshot.metadata.run_id,
                            conversation_id=conversation_id,
                            workspace_id=workspace_id,
                            workspace_root=workspace_root,
                            context_snapshot=context_snapshot,
                        )
                        if callable(create_record)
                        else AgentRunRecord(
                            run_id=context_snapshot.metadata.run_id,
                            thread_id=context_snapshot.metadata.run_id,
                            conversation_id=conversation_id,
                            workspace_id=workspace_id,
                            workspace_root=workspace_root,
                            status="queued",
                            checkpoint_id=None,
                            latest_node=None,
                            next_nodes=["setup_workspace"],
                            trace=[],
                            context_snapshot=context_snapshot,
                        )
                    )
                    preferences = (
                        None
                        if evaluation
                        else self._session_service.get_user_preferences(
                            resolved_actor
                        )
                    )
                    self._query_uow.persist_start(
                        record=record,
                        message_id=f"msg_{uuid4().hex[:12]}",
                        message=message,
                        preferences=preferences,
                    )
                else:
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
            except BaseException:
                self._execution_context_factory.discard_prepared(
                    run_id=context_snapshot.metadata.run_id, workspace_id=workspace_id)
                raise
        else:
            if actor_user_id is not None and self._workspace_authorizer is not None:
                self._workspace_authorizer.authorize(
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
            if evaluation:
                history_payload = []
            elif callable(build_agent_context):
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
        if self._model_registry is not None and record.context_snapshot is None:
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
            if self._execution_context_factory is not None:
                self._execution_context_factory.discard_prepared(run_id=record.run_id, workspace_id=workspace_id)
            raise
        self._metrics.increment("agent_runs_submitted_total")
        return record

    def composer_capabilities(
        self,
        *,
        conversation_id: str,
        workspace_id: str,
        actor_user_id: str | None = None,
    ) -> dict[str, object]:
        """Return the effective Skill commands and MCP tools for one composer."""

        factory = self._execution_context_factory
        if factory is None:
            raise RuntimeError("effective context factory is unavailable")
        selection = (
            self._model_registry.selection_for_session(conversation_id)
            if self._model_registry is not None
            else ModelSelection()
        )
        snapshot = factory.preview(
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            model_selection=selection,
        )
        catalog = factory.effective_skills(snapshot)
        declared_catalog = factory.declared_skills(snapshot)
        tool_access = factory.restore_tool_access(snapshot)
        mcp_specs = sorted(
            (
                spec
                for spec in tool_access.list_specs()
                if spec.name.startswith("mcp.") and spec.provider.startswith("mcp:")
            ),
            key=lambda spec: spec.name,
        )
        skill_diagnostics = (
            [item.message for item in catalog.diagnostics]
            if catalog is not None
            else []
        )
        return {
            "conversation_id": conversation_id,
            "workspace_id": workspace_id,
            "commands": command_capabilities(),
            "skill_commands": [
                {
                    "name": command.name,
                    "description": command.description,
                    "usage": command.usage,
                    "aliases": list(command.aliases),
                    "skill_name": command.skill_name,
                    "skill_qualified_name": command.skill_qualified_name,
                    "source": command.source.value,
                }
                for command in (catalog.commands if catalog is not None else ())
            ],
            "unavailable_skill_commands": [
                {"name": item.name, "skill_name": item.skill_name,
                 "skill_qualified_name": item.skill_qualified_name, "aliases": list(item.aliases)}
                for item in (declared_catalog.commands if declared_catalog else ())
                if catalog is None or catalog.get_skill(item.skill_qualified_name) is None
            ],
            "mcp_tools": [
                {
                    "name": spec.name,
                    "description": spec.description,
                    "provider": spec.provider,
                    "server_name": spec.provider.removeprefix("mcp:"),
                    "permission_level": spec.permission_level,
                    "requires_approval": spec.requires_approval,
                    "input_schema": spec.input_schema,
                }
                for spec in mcp_specs
            ],
            "diagnostics": list(
                dict.fromkeys(
                    [*snapshot.instructions.diagnostics, *skill_diagnostics]
                )
            ),
        }

    def execute(
        self,
        command: QueryCommand | str,
        *,
        params: QueryParams | None = None,
        run_id: str | None = None,
        approved: bool = True,
        message: str = "",
        answers: list[dict[str, Any]] | None = None,
        actor_user_id: str | None = None,
    ) -> AgentRunRecord:
        resolved = QueryCommand(command)
        if resolved is QueryCommand.START:
            if params is None:
                raise ValueError("start requires QueryParams")
            return self.start(params)
        if not run_id:
            raise ValueError(f"{resolved.value} requires run_id")
        if resolved is QueryCommand.RESUME:
            return self.resume_run(
                run_id=run_id,
                approved=approved,
                feedback=message or None,
                actor_user_id=actor_user_id,
            )
        if resolved is QueryCommand.CONTINUE:
            return self.continue_run(
                run_id=run_id,
                message=message,
                answers=answers,
                actor_user_id=actor_user_id,
            )
        if resolved is QueryCommand.COMPACT:
            return self.compact_run(
                run_id=run_id,
                instruction=message,
                actor_user_id=actor_user_id,
            )
        return self.control_run(
            run_id=run_id,
            action=resolved.value,
            message=message,
            actor_user_id=actor_user_id,
        )

    def query(
        self,
        params: QueryParams,
        *,
        cursor: int = 0,
    ) -> AsyncIterator[AgentEvent]:
        """Eagerly start a Run and return a disconnect-safe async event adapter."""
        record = self.start(params)
        return self.iter_events(
            record.run_id,
            actor_user_id=params.actor_user_id,
            cursor=cursor,
        )

    async def iter_events(
        self,
        run_id: str,
        *,
        actor_user_id: str | None = None,
        cursor: int = 0,
        poll_interval: float = 0.25,
    ) -> AsyncIterator[AgentEvent]:
        current = max(0, cursor)
        while True:
            record, events = self.events_for_actor(
                run_id,
                actor_user_id,
                after=current,
            )
            for event in events:
                current = max(current, event.sequence)
                yield event
            if record.status in QueryLifecycle.STREAM_STOP_STATUSES:
                return
            await asyncio.sleep(poll_interval)

    def events_for_actor(
        self,
        run_id: str,
        actor_user_id: str | None,
        *,
        after: int = 0,
    ) -> tuple[AgentRunRecord, list[AgentEvent]]:
        record, stored = self.list_events_for_actor(
            run_id,
            actor_user_id,
            after=after,
        )
        return record, [
            self._event_encoder.from_stored(run_id, event) for event in stored
        ]

    def get_result(
        self,
        run_id: str,
        *,
        actor_user_id: str | None = None,
    ) -> QueryResult:
        record, events = self.events_for_actor(
            run_id,
            actor_user_id,
            after=0,
        )
        result = record.result
        output = {
            "answer": result.answer if result is not None else "",
            "error": record.error,
            "checkpoint_id": record.checkpoint_id,
            "pending": record.pending_approval,
        }
        return QueryResult(
            run_id=record.run_id,
            status=record.status,
            cursor=events[-1].sequence if events else 0,
            output=output,
            resumable=QueryLifecycle.is_resumable(record.status),
        )

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
        self._assert_command(QueryCommand.RESUME, record)
        if approved:
            self._validate_approval_binding(record)
            self._authorize_pending_approval(record, actor_user_id)
        if (
            approved
            and actor_user_id is not None
            and self._workspace_authorizer is not None
            and self._approval_requires_editor(record)
        ):
            self._workspace_authorizer.authorize(
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
        try:
            self._event_store.append(
                run_id,
                AgentRunEvent(
                    sequence=0,
                    type="approval_decided",
                    status=record.status,
                    node=record.latest_node,
                    summary=(
                        "Agent tool plan approved."
                        if approved
                        else "Agent tool plan rejected."
                    ),
                    output={
                        "approved": approved,
                        "feedback": (feedback or "").strip(),
                        "actor_user_id": actor_user_id,
                        "request": record.pending_approval or {},
                    },
                ),
            )
        except RuntimeError as exc:
            if str(exc) != "Agent runtime EventStore is not writable":
                raise
            logger.info(
                "agent approval audit event store unavailable",
                extra={"run_id": run_id},
            )
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
        if record.runtime_engine == 'cogent-v1' and record.result is not None:
            self._record_assistant_message(record.result)
        return record

    def list_runs_for_actor(
        self,
        actor_user_id: str | None,
        *,
        limit: int = 50,
    ) -> list[AgentRunRecord]:
        list_recent = getattr(self._runtime, "list_recent_runs", None)
        if not callable(list_recent):
            return []
        records = list_recent(limit=max(limit, 100 if actor_user_id else limit))
        if actor_user_id is None:
            return records[:limit]
        authorized: list[AgentRunRecord] = []
        for record in records:
            try:
                self._assert_actor(record, actor_user_id)
            except (PermissionError, SessionNotFoundError):
                continue
            authorized.append(record)
            if len(authorized) >= limit:
                break
        return authorized

    def list_checkpoints_for_actor(
        self,
        run_id: str,
        actor_user_id: str | None,
        *,
        limit: int = 100,
    ) -> tuple[AgentRunRecord, list[AgentCheckpoint]]:
        record = self.get_run_for_actor(run_id, actor_user_id)
        list_checkpoints = getattr(self._runtime, "list_checkpoints", None)
        if not callable(list_checkpoints):
            raise RuntimeError("Agent runtime does not expose checkpoint history")
        return record, list_checkpoints(run_id, limit=limit)

    def restore_checkpoint(
        self,
        *,
        run_id: str,
        checkpoint_id: str,
        mode: str,
        message: str = "",
        actor_user_id: str | None = None,
    ) -> tuple[AgentRunRecord, Any | None]:
        source = self.get_run_for_actor(run_id, actor_user_id)
        require_writable = getattr(self._runtime, "require_writable", None)
        if callable(require_writable):
            require_writable(source)
        source_session = self._session_service.get_session(source.conversation_id)
        resolved_actor = actor_user_id or source_session.user_id
        if mode == "rollback" and source_session.archived_at is not None:
            from ai_agent_platform.repositories import SessionArchivedError

            raise SessionArchivedError(source.conversation_id)
        forked_session = None
        target_conversation_id = source.conversation_id
        if mode == "fork":
            forked_session = self._session_service.fork_session_from_run(
                source_session_id=source.conversation_id,
                source_run_id=source.run_id,
                actor_user_id=resolved_actor,
            )
            target_conversation_id = forked_session.id

        restore_message = _checkpoint_restore_message(
            source,
            checkpoint_id=checkpoint_id,
            mode=mode,
            message=message,
        )
        prepare = getattr(self._runtime, "prepare_checkpoint_branch", None)
        if not callable(prepare):
            raise RuntimeError("Agent runtime does not support checkpoint restoration")
        try:
            record = prepare(
                source_run_id=run_id,
                checkpoint_id=checkpoint_id,
                conversation_id=target_conversation_id,
                mode=mode,
                message=message,
            )
            QueryLifecycle.assert_transition(None, "queued")
            if self._query_uow is not None:
                self._query_uow.persist_start(
                    record=record,
                    message_id=f"msg_{uuid4().hex[:12]}",
                    message=restore_message,
                    preferences=self._session_service.get_user_preferences(
                        resolved_actor
                    ),
                )
            else:
                self._runtime.restore_record(record)
                self._session_service.add_message(
                    session_id=target_conversation_id,
                    role="user",
                    content=restore_message,
                    source_run_id=record.run_id,
                )
        except Exception:
            discard = getattr(self._runtime, "discard_checkpoint_thread", None)
            if "record" in locals() and callable(discard):
                discard(record.thread_id, workspace_id=record.workspace_id)
            if forked_session is not None:
                self._session_service.delete_session(forked_session.id)
            raise

        record_event = getattr(
            self._runtime,
            "record_checkpoint_branch_created",
            None,
        )
        if callable(record_event):
            record_event(
                record,
                source_run_id=run_id,
                source_checkpoint_id=checkpoint_id,
                mode=mode,
            )
        try:
            self._task_queue.submit(
                "agent_checkpoint_restore",
                self.execute_checkpoint_restore_task,
                run_id=record.run_id,
                actor_user_id=resolved_actor,
            )
        except TaskQueueError as exc:
            self._mark_queued_run_failed(record.run_id, str(exc))
            self._metrics.increment("agent_checkpoint_restores_rejected_total")
            raise
        self._metrics.increment("agent_checkpoint_restores_submitted_total")
        self._metrics.increment(
            f"agent_checkpoint_restore_{mode}_submitted_total"
        )
        return record, forked_session

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

    def latest_context_shares(self, conversation_id: str) -> dict[str, int]:
        """Return the latest run's resolved context shares, or an empty mapping.

        ``context_shares`` is computed once in ``setup_workspace`` and persisted
        on that run's trace. It is an allocation breakdown (system, tool schema,
        evidence, history, transcript) rather than a provider-counted prompt, so
        callers must not present it as an exact final-Prompt measurement.
        """
        get_latest = getattr(self._runtime, "get_latest_run", None)
        record = get_latest(conversation_id) if callable(get_latest) else None
        for step in getattr(record, "trace", None) or []:
            if step.get("node") != "setup_workspace":
                continue
            shares = (step.get("output") or {}).get("context_shares")
            if isinstance(shares, dict):
                return {
                    key: int(value)
                    for key, value in shares.items()
                    if isinstance(value, (int, float))
                }
        return {}

    def list_events_for_actor(
        self,
        run_id: str,
        actor_user_id: str | None,
        *,
        after: int = 0,
    ):
        record = self.get_run_for_actor(run_id, actor_user_id)
        return record, self._event_store.list(run_id, after=after)

    def control_run(
        self,
        *,
        run_id: str,
        action: str,
        message: str = "",
        actor_user_id: str | None = None,
    ) -> AgentRunRecord:
        record = self.get_run_for_actor(run_id, actor_user_id)
        self._assert_command(QueryCommand(action), record)
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
        answers: list[dict[str, Any]] | None = None,
        actor_user_id: str | None = None,
    ) -> AgentRunRecord:
        record = self.get_run_for_actor(run_id, actor_user_id)
        self._assert_command(QueryCommand.CONTINUE, record)
        input_response: dict[str, Any] | None = None
        if record.status == "waiting_input":
            pending = record.pending_approval or {}
            if pending.get("questions"):
                questions = normalize_questions(pending)
                input_response = parse_question_response(
                    {"answers": list(answers or [])},
                    questions,
                )
            else:
                # Restored legacy checkpoints have no structured batch. Keep
                # their text response path isolated from all newly emitted UI.
                legacy_message = message.strip()
                if not legacy_message:
                    raise ValueError("waiting input requires a non-empty answer")
                input_response = {"legacy": True, "message": legacy_message}
            try:
                self._event_store.append(
                    run_id,
                    AgentRunEvent(
                        sequence=0,
                        type="user_question_answered",
                        status=record.status,
                        node=record.latest_node,
                        summary="Structured user-question answer submitted.",
                        output={
                            "request": pending,
                            "response": input_response,
                            "actor_user_id": actor_user_id,
                        },
                    ),
                )
            except RuntimeError as exc:
                if str(exc) != "Agent runtime EventStore is not writable":
                    raise
                logger.info(
                    "agent question audit event store unavailable",
                    extra={"run_id": run_id},
                )
        elif answers:
            raise ValueError("structured answers are only valid while waiting for input")
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
                input_response=input_response,
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

    def compact_run(
        self,
        *,
        run_id: str,
        instruction: str = "",
        actor_user_id: str | None = None,
    ) -> AgentRunRecord:
        record = self.get_run_for_actor(run_id, actor_user_id)
        self._assert_command(QueryCommand.COMPACT, record)
        request_compaction = getattr(self._runtime, "request_compaction", None)
        if not callable(request_compaction):
            raise RuntimeError("Agent runtime does not support context compaction")
        updated = request_compaction(run_id=run_id, instruction=instruction)
        if record.status != "paused":
            self._metrics.increment("agent_run_compactions_requested_total")
            return updated
        selection = self._selection_for_record(updated)
        mark_resume_queued = getattr(self._runtime, "mark_resume_queued", None)
        queued = mark_resume_queued(run_id) if callable(mark_resume_queued) else updated
        try:
            self._task_queue.submit(
                "agent_resume",
                self.execute_resume_task,
                run_id=run_id,
                approved=True,
                feedback=None,
                actor_user_id=actor_user_id,
                model_selection=(
                    selection.__dict__ if record.context_snapshot is None else None
                ),
            )
        except TaskQueueError:
            restore_record = getattr(self._runtime, "restore_record", None)
            if callable(restore_record):
                restore_record(updated)
            raise
        self._metrics.increment("agent_run_compactions_requested_total")
        return queued

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

    def recover_incomplete_runs(self) -> int:
        """Requeue durable work after API/CLI startup; suspended runs stay suspended."""
        recovered = 0
        for record in self._runtime.list_recent_runs(limit=1000):
            if record.runtime_engine != 'cogent-v1':
                continue
            if record.context_snapshot is None or record.runtime_state.get('internal_maintenance'):
                continue
            if record.status in QueryLifecycle.TERMINAL_STATUSES and record.result is not None:
                self._record_assistant_message(record.result)
                continue
            if record.status not in {'queued', 'running'}:
                continue
            # Approval/input payloads are not a new authorization after restart.
            if record.pending_approval:
                self._runtime.restore_record(replace(record, status=(
                    'paused' if record.pending_approval.get('type') == 'run_pause'
                    else 'waiting_input' if record.pending_approval.get('type') == 'input_required'
                    else 'waiting_approval'), control_action=None))
                continue
            self._task_queue.submit('agent_run', self.execute_run_task,
                                    run_id=record.run_id, broker_redelivered=True)
            recovered += 1
        return recovered

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
        recovery = (
            broker_redelivered and record.status == "running"
            and record.runtime_engine == "cogent-v1"
            and callable(getattr(self._runtime, "recover", None))
        )
        if broker_redelivered and record.status == "running" and not recovery:
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
        if record.status != "queued" and not recovery:
            self._metrics.increment("agent_run_duplicate_deliveries_total")
            if record.result is not None:
                self._record_assistant_message(record.result)
            logger.info(
                "agent run delivery skipped",
                extra={"run_id": run_id, "status": record.status},
            )
            return
        if context_snapshot is not None and self._can_restore_tool_access():
            # Production RuntimeContainer always injects the shared builder. The
            # guard keeps minimal compatibility test runtimes on their legacy path.
            try:
                self._restore_tool_access(context_snapshot)
            except ToolPoolRestoreError:
                self._fail_tool_pool_restore(run_id)
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
                        if recovery:
                            result = self._runtime.recover(run_id)
                        else:
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
            except RunLeaseUnavailable:
                self._metrics.increment('agent_run_duplicate_deliveries_total')
                return
            except Exception as exc:
                self._record_execution_metrics(
                    status="failed",
                    started_at=started_at,
                )
                self.fail_run_task(run_id=run_id, error=str(exc), attempt=1, max_attempts=1)
                logger.exception("agent run failed")
                raise AgentRunExecutionError(str(exc)) from exc
            self._record_execution_metrics(
                status=result.status,
                started_at=started_at,
            )
            logger.info("agent run finished", extra={"status": result.status})
            if not _is_evaluation_record(record):
                self._record_assistant_message(
                    result,
                    user_message=message,
                    actor_user_id=actor_user_id,
                )

    def execute_checkpoint_restore_task(
        self,
        *,
        run_id: str,
        actor_user_id: str | None = None,
        broker_redelivered: bool = False,
    ) -> None:
        started_at = perf_counter()
        record = self.get_run(run_id)
        if broker_redelivered and record.status == "running":
            self._metrics.increment("agent_checkpoint_restore_worker_lost_total")
            self.fail_run_task(
                run_id=run_id,
                error=(
                    "worker was lost during checkpoint restoration; automatic "
                    "replay was blocked to prevent duplicate side effects"
                ),
                attempt=1,
                max_attempts=1,
            )
            return
        if record.status != "queued":
            self._metrics.increment(
                "agent_checkpoint_restore_duplicate_deliveries_total"
            )
            if record.result is not None:
                self._record_assistant_message(record.result)
            return
        with log_context(
            run_id=run_id,
            conversation_id=record.conversation_id,
            workspace_id=record.workspace_id,
        ):
            try:
                if (
                    record.context_snapshot is not None
                    and self._can_restore_tool_access()
                ):
                    self._restore_tool_access(record.context_snapshot)
                snapshot_selection = (
                    record.context_snapshot.session.model_selection.to_dict()
                    if record.context_snapshot is not None
                    else None
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
                        result = self._runtime.run_from_checkpoint(run_id)
            except ToolPoolRestoreError:
                self._record_execution_metrics(
                    status="failed",
                    started_at=started_at,
                )
                self._fail_tool_pool_restore(run_id)
                return
            except RunLeaseUnavailable:
                self._metrics.increment('agent_run_duplicate_deliveries_total')
                return
            except Exception as exc:
                self._record_execution_metrics(
                    status="failed",
                    started_at=started_at,
                )
                logger.exception("agent checkpoint restoration failed")
                raise AgentRunExecutionError(str(exc)) from exc
            self._record_execution_metrics(
                status=result.status,
                started_at=started_at,
            )
            self._record_assistant_message(
                result,
                user_message=(
                    record.context_snapshot.session.user_message
                    if record.context_snapshot is not None
                    else None
                ),
                actor_user_id=actor_user_id,
            )

    def execute_resume_task(
        self,
        *,
        run_id: str,
        approved: bool,
        feedback: Optional[str],
        input_response: dict[str, Any] | None = None,
        actor_user_id: str | None = None,
        model_selection: dict | None = None,
        broker_redelivered: bool = False,
    ) -> None:
        started_at = perf_counter()
        record = self.get_run(run_id)
        resume_pending = (
            record.status == "running" and record.control_action == "resume"
        )
        cogent_replay = broker_redelivered and record.runtime_engine == "cogent-v1"
        recovery = cogent_replay and record.status == "running" and not resume_pending
        if broker_redelivered and (
            resume_pending
            or record.status in QueryLifecycle.SUSPENDED_STATUSES
        ) and not cogent_replay:
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
            record.status not in QueryLifecycle.SUSPENDED_STATUSES
            and not resume_pending
            and not recovery
        ):
            self._metrics.increment("agent_resume_duplicate_deliveries_total")
            if record.result is not None:
                self._record_assistant_message(record.result)
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
                    record.context_snapshot is not None
                    and self._can_restore_tool_access()
                ):
                    self._restore_tool_access(record.context_snapshot)
                if approved and not recovery:
                    self._validate_approval_binding(record)
                    self._authorize_pending_approval(record, actor_user_id)
                if (
                    approved
                    and actor_user_id is not None
                    and self._workspace_authorizer is not None
                    and self._approval_requires_editor(record)
                ):
                    self._workspace_authorizer.authorize(
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
                        if recovery:
                            result = self._runtime.recover(run_id)
                        else:
                            result = self._runtime.resume(
                                run_id=run_id,
                                approved=approved,
                                feedback=feedback,
                                input_response=input_response,
                                approved_by=actor_user_id,
                            )
            except ToolPoolRestoreError:
                self._record_execution_metrics(
                    status="failed",
                    started_at=started_at,
                )
                self._fail_tool_pool_restore(run_id)
                return
            except RunLeaseUnavailable:
                self._metrics.increment('agent_run_duplicate_deliveries_total')
                return
            except Exception as exc:
                self._record_execution_metrics(
                    status="failed",
                    started_at=started_at,
                )
                logger.exception("agent run resume failed")
                self.fail_run_task(run_id=run_id, error=str(exc), attempt=1, max_attempts=1)
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

    @staticmethod
    def _validate_approval_binding(record: AgentRunRecord) -> None:
        pending = record.pending_approval or {}
        required = pending.get("approval_required_tools") or []
        calls = pending.get("tool_calls") or []
        if not required:
            return
        calls_by_id = {
            str(item.get("call_id") or ""): item
            for item in calls
            if isinstance(item, dict) and item.get("call_id")
        }
        has_precise_bindings = all(
            isinstance(item, dict)
            and item.get("run_id")
            and item.get("call_id")
            and item.get("arguments_hash")
            for item in required
        )
        if not has_precise_bindings:
            if record.context_snapshot is not None:
                raise PermissionError("tool approval binding is incomplete")
            return
        for item in required:
            if str(item["run_id"]) != record.run_id:
                raise PermissionError("tool approval is bound to a different run")
            call_id = str(item["call_id"])
            call = calls_by_id.get(call_id)
            if call is None or str(call.get("name") or "") != str(
                item.get("name") or ""
            ):
                raise PermissionError("tool approval binding does not match the plan")
            arguments = call.get("arguments")
            if not isinstance(arguments, dict) or canonical_arguments_hash(
                arguments
            ) != str(item["arguments_hash"]):
                raise PermissionError("tool approval arguments changed after review")

    def _authorize_pending_approval(
        self,
        record: AgentRunRecord,
        actor_user_id: str | None,
    ) -> None:
        validate_runtime_approval = getattr(
            self._runtime,
            "validate_pending_approval",
            None,
        )
        if callable(validate_runtime_approval):
            validate_runtime_approval(record, approved_by=actor_user_id)
            return
        if self._permission_resolver is None or self._tool_registry is None:
            return
        pending = record.pending_approval or {}
        required = pending.get("approval_required_tools") or []
        calls = {
            str(item.get("call_id") or ""): item
            for item in pending.get("tool_calls") or []
            if isinstance(item, dict) and item.get("call_id")
        }
        snapshot = record.context_snapshot
        role = snapshot.identity.workspace_role if snapshot is not None else "admin"
        if actor_user_id is not None and self._workspace_authorizer is not None:
            role_for = getattr(self._workspace_authorizer, "role_for", None)
            if callable(role_for):
                role = str(
                    role_for(
                        workspace_id=record.workspace_id,
                        actor_user_id=actor_user_id,
                    )
                    or ""
                )
        tool_access = (
            self._restore_tool_access(snapshot)
            if snapshot is not None
            else self._tool_registry
        )
        project_tools = (
            tuple(tool_access.allowed_names)
            if snapshot is not None
            else None
        )
        process_tools = tuple(spec.name for spec in self._tool_registry.list_specs())
        approval_policy = (
            _snapshot_approval_policy(snapshot)
            if snapshot is not None
            else "on_request"
        )
        base_context = ToolUseContext(
            conversation_id=record.conversation_id,
            workspace_id=record.workspace_id,
            workspace_root=record.workspace_root,
            authorized_workspace_root=self._workspace_service.resolve_for_run(
                record.workspace_id
            ),
            run_id=record.run_id,
            actor_user_id=actor_user_id or "",
            workspace_role=role,
            approval_policy=approval_policy,
            process_allowed_tools=process_tools,
            project_allowed_tools=project_tools,
        )
        for item in required:
            if not isinstance(item, dict):
                raise PermissionError("tool approval entry is invalid")
            call = calls.get(str(item.get("call_id") or ""))
            spec = tool_access.get_spec(str(item.get("name") or ""))
            if call is None or spec is None or not isinstance(
                call.get("arguments"), dict
            ):
                raise PermissionError("tool approval target is unavailable")
            context = base_context.bind(
                call_id=str(call["call_id"]),
                tool_name=spec.name,
                arguments=call["arguments"],
            )
            decision = self._permission_resolver.resolve(
                PermissionRequest.from_spec(spec),
                context,
                phase="plan",
            )
            if decision.effect == "deny":
                raise PermissionError(decision.reason)

    def _restore_tool_access(self, snapshot: Any):
        if snapshot.metadata.schema_version >= 3:
            if self._tool_pool_builder is None:
                raise ToolPoolRestoreError(
                    "effective tool pool restoration is unavailable"
                )
            return self._tool_pool_builder.restore(snapshot.tools)
        if self._tool_registry is None:
            raise ToolPoolRestoreError("legacy tool restoration is unavailable")
        selected = snapshot.tools.enabled_tools
        if selected is None:
            selected = tuple(
                spec.name for spec in self._tool_registry.list_specs()
            )
        try:
            return self._tool_registry.select(tuple(selected))
        except ValueError as exc:
            raise ToolPoolRestoreError(
                "legacy Run tool selection is unavailable"
            ) from exc

    def _can_restore_tool_access(self) -> bool:
        return self._tool_pool_builder is not None or self._tool_registry is not None

    def _fail_tool_pool_restore(self, run_id: str) -> None:
        message = "The frozen effective tool pool could not be restored safely."
        self.fail_run_task(
            run_id=run_id,
            error=message,
            attempt=1,
            max_attempts=1,
        )
        try:
            self._event_store.append(
                run_id,
                AgentRunEvent(
                    sequence=0,
                    type="tool_pool_restore_failed",
                    status="failed",
                    node="tool_access",
                    summary=message,
                    output={"error_code": "tool_pool_restore_failed"},
                ),
            )
        except RuntimeError:
            logger.info(
                "tool pool restore failure event store is unavailable",
                extra={"run_id": run_id},
            )
        self._metrics.increment("agent_tool_pool_restore_failures_total")

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
        mode: str | None = None,
    ) -> ModelSelection:
        if mode is not None and mode not in {"auto", "manual"}:
            raise ValueError("Query mode must be auto or manual")
        if provider is not None or model is not None:
            return ModelSelection(
                mode=mode or "manual",
                routing_policy=(routing_policy or "smart"),  # type: ignore[arg-type]
                preferred_provider=provider or self._llm_provider,
                preferred_model=model or self._llm_model,
                thinking_level=thinking_level,
                fallback_enabled=True,
            )
        return ModelSelection(
            mode=mode or "auto",
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
        result_run_id = str(getattr(result, "run_id", "") or "")
        if result_run_id:
            try:
                if _is_evaluation_record(self.get_run(result_run_id)):
                    return
            except (KeyError, AgentRunNotFoundError):
                pass
        if result.status not in {"completed", "partial", "blocked"} or not result.answer:
            return
        if self._query_uow is not None:
            assistant_message = self._query_uow.persist_assistant_once(
                run_id=result.run_id,
                conversation_id=result.conversation_id,
                message_id=_assistant_message_id(result.run_id),
                content=result.answer,
            )
        else:
            with self._assistant_message_lock:
                if result.run_id in self._recorded_assistant_runs:
                    return
                assistant_messages = self._session_service.add_message(
                    session_id=result.conversation_id,
                    role="assistant",
                    content=result.answer,
                )
                self._recorded_assistant_runs.add(result.run_id)
                assistant_message = (
                    assistant_messages[-1] if assistant_messages else None
                )
        if assistant_message is None:
            return
        if assistant_message:
            enqueue_compression = getattr(
                self._session_service,
                "enqueue_compression",
                None,
            )
            if callable(enqueue_compression):
                enqueue_compression(
                    task_queue=self._task_queue,
                    session_id=result.conversation_id,
                    trigger_message_id=assistant_message.id,
                )
    def _assert_actor(
        self,
        record: AgentRunRecord,
        actor_user_id: str | None,
    ) -> None:
        if record.runtime_state.get('internal_maintenance'):
            raise AgentRunNotFoundError(record.run_id)
        if actor_user_id is None:
            return
        session = self._session_service.get_session(
            session_id=record.conversation_id
        )
        if session.user_id != actor_user_id:
            raise PermissionError("agent run access denied")

    def _assert_command(
        self,
        command: QueryCommand,
        record: AgentRunRecord,
    ) -> None:
        require_writable = getattr(self._runtime, "require_writable", None)
        if callable(require_writable):
            require_writable(record)
        try:
            QueryLifecycle.assert_command(command, record.status)
        except QueryStateError as exc:
            raise AgentRunInvalidStateError(record.run_id, record.status) from exc


def _assistant_message_id(run_id: str) -> str:
    return f"msg_{uuid5(NAMESPACE_URL, f'assistant:{run_id}').hex[:12]}"


def _checkpoint_restore_message(
    source: AgentRunRecord,
    *,
    checkpoint_id: str,
    mode: str,
    message: str,
) -> str:
    action = "回到历史检查点继续" if mode == "rollback" else "从历史检查点分叉"
    direction = (message or "").strip()
    suffix = f"\n\n新方向：{direction}" if direction else ""
    return (
        f"{action}：Run {source.run_id} / checkpoint {checkpoint_id}。"
        f"{suffix}"
    )


def _is_evaluation_record(record: AgentRunRecord) -> bool:
    snapshot = record.context_snapshot
    if snapshot is None:
        return False
    evaluation = snapshot.metadata.entrypoint_metadata.get("evaluation")
    return bool(isinstance(evaluation, dict) and evaluation.get("isolated"))


def _snapshot_approval_policy(snapshot: object) -> str:
    configured = "on_request"
    project = getattr(snapshot, "project", None)
    config = getattr(project, "project_config", None)
    if isinstance(config, dict):
        sections = config.get("config")
        runtime = sections.get("runtime") if isinstance(sections, dict) else None
        field = runtime.get("agent_approval_policy") if isinstance(runtime, dict) else None
        value = field.get("value") if isinstance(field, dict) else None
        if value in {"always", "on_request", "never", "auto_approve"}:
            configured = str(value)
    metadata = getattr(snapshot, "metadata", None)
    override = None
    if metadata is not None:
        entrypoint = getattr(metadata, "entrypoint_metadata", None)
        if isinstance(entrypoint, dict):
            override = entrypoint.get("approval_policy")
    return effective_approval_policy(configured, override)


__all__ = ["AgentRunExecutionError", "QueryService"]
