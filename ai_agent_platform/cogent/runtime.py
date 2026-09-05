from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
from threading import RLock
from typing import Any, Iterable
from uuid import uuid4

from ai_agent_platform.agents.coding.models import (
    AgentChangeSummary,
    AgentCheckpoint,
    AgentCheckpointNotFoundError,
    AgentCheckpointRestoreError,
    AgentRunEvent,
    AgentRunInvalidStateError,
    AgentRunMetrics,
    AgentRunNotFoundError,
    AgentRunRecord,
    AgentRunResult,
    AgentRuntimeSnapshot,
    AgentToolExecution,
)
from ai_agent_platform.agents.coding.tool_access import ToolAccessCoordinator
from ai_agent_platform.cogent.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from ai_agent_platform.cogent.prompts import PROMPT_VERSION, build_system_prompt
from ai_agent_platform.cogent.prompts import COMPACTION_PROMPT
from ai_agent_platform.cogent.context.manager import compute_compact_threshold, extract_summary
from ai_agent_platform.cogent.context.platform import compact_prefix, token_estimate
from ai_agent_platform.cogent.sandbox import create_sandbox
from ai_agent_platform.cogent.state import (
    RUNTIME_ENGINE,
    RUNTIME_STATE_VERSION,
    CogentState,
)
from ai_agent_platform.cogent.tools import CogentToolAdapter, PreparedCall
from ai_agent_platform.cogent.commands.catalog import LOCAL_COMMANDS, command_capabilities
from ai_agent_platform.domain import QueryLifecycle, RunContextSnapshot
from ai_agent_platform.integrations.permissions import (
    PermissionDecision,
    ToolApproval,
    ToolUseContext,
    canonical_arguments_hash,
)
from ai_agent_platform.integrations.tools import ToolCall, ToolResult
from ai_agent_platform.integrations.tools import summarize_tool_arguments


class LegacyRunReadOnlyError(AgentRunInvalidStateError):
    code = "legacy_run_read_only"

    def __init__(self, run_id: str, status: str) -> None:
        super().__init__(run_id, status)
        self.args = (self.code,)


class ToolExecutionUncertainError(RuntimeError):
    code = "tool_execution_uncertain"


class CogentRuntime:
    graph_engine = RUNTIME_ENGINE

    def __init__(
        self,
        *,
        tool_registry: Any,
        run_store: Any,
        llm_client: Any,
        tool_pool_builder: Any,
        approval_policy: str = "on_request",
        change_set_service: Any = None,
        execution_workspace_runtime: Any = None,
        max_parallel_reads: int = 4,
        tool_result_max_chars: int = 50_000,
        memory_service: Any = None,
    ) -> None:
        self._tools = tool_registry
        self._run_store = run_store
        self._llm = llm_client
        self._tool_access = ToolAccessCoordinator(
            tools=tool_registry,
            default_approval_policy=approval_policy,
            tool_pool_builder=tool_pool_builder,
        )
        self._approval_policy = approval_policy
        self._change_set_service = change_set_service
        self._execution_workspace_runtime = execution_workspace_runtime
        self._max_parallel_reads = max(1, max_parallel_reads)
        self._tool_result_max_chars = max(2_000, tool_result_max_chars)
        self._memory_service = memory_service
        self._locks: dict[str, RLock] = {}
        self._locks_guard = RLock()

    def create_queued_run(
        self,
        *,
        conversation_id: str,
        workspace_id: str,
        workspace_root: str,
        run_id: str | None = None,
        context_snapshot: RunContextSnapshot | None = None,
    ) -> AgentRunRecord:
        resolved = run_id or f"run_{uuid4().hex[:12]}"
        if context_snapshot is not None and context_snapshot.metadata.run_id != resolved:
            raise ValueError("Run context ID does not match queued Run ID")
        record = self.create_queued_record(
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            workspace_root=workspace_root,
            run_id=resolved,
            context_snapshot=context_snapshot,
        )
        self._run_store.save(record)
        return record

    def create_queued_record(
        self,
        *,
        conversation_id: str,
        workspace_id: str,
        workspace_root: str,
        run_id: str,
        context_snapshot: RunContextSnapshot | None = None,
    ) -> AgentRunRecord:
        state = CogentState(
            permission_mode=self._permission_mode(context_snapshot),
            sandbox=self._sandbox_state(context_snapshot),
        )
        previous = self._run_store.get_latest_for_conversation(conversation_id)
        if (previous is not None and previous.runtime_engine == RUNTIME_ENGINE
                and previous.status == "completed" and previous.workspace_root == workspace_root):
            prior = CogentState.from_mapping(previous.runtime_state)
            state.messages = prior.messages
            state.active_skill = prior.active_skill
            state.recalled_memory = prior.recalled_memory
            state.compact_boundaries = prior.compact_boundaries
            state.usage_anchor = prior.usage_anchor
            state.file_history_cursor = prior.file_history_cursor
            state.tool_result_files = prior.tool_result_files
            state.loaded_mcp_tools = prior.loaded_mcp_tools
            if state.permission_mode == "plan":
                state.sandbox["previous_permission_mode"] = prior.permission_mode
            elif state.permission_mode == "default" and prior.sandbox.get("permission_preference"):
                state.permission_mode = prior.permission_mode
                state.sandbox["permission_preference"] = True
            if state.permission_mode == "default" and prior.permission_mode == "plan" and not prior.sandbox.get('readonly_review'):
                state.permission_mode = "plan"
                state.sandbox["previous_permission_mode"] = prior.sandbox.get("previous_permission_mode", "default")
        return AgentRunRecord(
            run_id=run_id,
            thread_id=run_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            workspace_root=workspace_root,
            status="queued",
            checkpoint_id=None,
            latest_node=None,
            next_nodes=["agent_loop"],
            trace=[],
            context_snapshot=context_snapshot,
            runtime_engine=RUNTIME_ENGINE,
            runtime_state_version=RUNTIME_STATE_VERSION,
            runtime_state=state.to_dict(),
        )

    def restore_record(self, record: AgentRunRecord) -> None:
        self._run_store.save(record)

    def get_run(self, run_id: str) -> AgentRunRecord:
        try:
            record = self._run_store.get(run_id)
        except KeyError as exc:
            raise AgentRunNotFoundError(run_id) from exc
        if (
            record.runtime_engine != RUNTIME_ENGINE
            and record.status not in QueryLifecycle.TERMINAL_STATUSES
        ):
            migrated = replace(
                record,
                status="blocked",
                next_nodes=[],
                error="legacy_runtime_retired",
                runtime_state={
                    **record.runtime_state,
                    "legacy_original_status": record.status,
                    "legacy_original_next_nodes": list(record.next_nodes),
                    "legacy_original_error": record.error,
                },
                errors=[
                    *record.errors,
                    {
                        "node": "runtime_migration",
                        "message": "legacy_runtime_retired",
                        "code": "legacy_runtime_retired",
                    },
                ],
            )
            return migrated
        return record

    @staticmethod
    def require_writable(record: AgentRunRecord) -> None:
        if record.runtime_engine != RUNTIME_ENGINE:
            raise LegacyRunReadOnlyError(record.run_id, record.status)

    def require_change_set_writable(self, run_id: str) -> None:
        self.require_writable(self.get_run(run_id))

    def get_latest_run(self, conversation_id: str) -> AgentRunRecord | None:
        record = self._run_store.get_latest_for_conversation(conversation_id)
        if record is None:
            return None
        return self.get_run(record.run_id)

    def list_recent_runs(self, *, limit: int = 50) -> list[AgentRunRecord]:
        return [self.get_run(item.run_id) for item in self._run_store.list_recent(limit=max(limit * 4, 100))
                if not item.runtime_state.get('internal_maintenance')][:limit]

    def list_events(self, run_id: str, *, after: int = 0) -> list[AgentRunEvent]:
        self.get_run(run_id)
        return self._run_store.list_events(run_id, after=after)

    def run(
        self,
        *,
        run_id: str,
        conversation_id: str,
        user_input: str,
        history: list[dict[str, str]],
        workspace_id: str,
        workspace_root: str,
        focus_files: list[str],
        actor_user_id: str,
        run_context: RunContextSnapshot | None = None,
    ) -> AgentRunResult:
        del focus_files
        with self._run_lock(run_id):
            record = self.get_run(run_id)
            if record.runtime_engine != RUNTIME_ENGINE:
                raise LegacyRunReadOnlyError(run_id, record.status)
            if record.status != "queued":
                raise AgentRunInvalidStateError(run_id, record.status)
            state = CogentState.from_mapping(record.runtime_state)
            state.started_at = state.started_at or datetime.now(timezone.utc).timestamp()
            tool_access = self._restore_tools(record, run_context)
            adapter = self._adapter(record, state, tool_access)
            context = self._base_tool_context(
                record,
                run_context,
                actor_user_id=actor_user_id,
            )
            command = (run_context.metadata.entrypoint_metadata.get("cogent_command") if run_context else None)
            if self._memory_service is not None and not command:
                try:
                    state.recalled_memory = self._memory_service.recall(record, user_input)
                except (OSError, ValueError):
                    state.recalled_memory = ''
            state.system_prompt = build_system_prompt(
                snapshot=run_context,
                workspace_root=workspace_root,
                permission_mode=state.permission_mode,
                sandbox_status=str(state.sandbox.get("status") or "unavailable"),
                tools=adapter.list_specs(),
                memory=state.recalled_memory,
                active_skill=state.active_skill,
            )
            state.prompt_version = PROMPT_VERSION
            prior_messages = state.messages[1:] if state.messages else self._canonical_history(history)
            state.messages = [{"role": "system", "content": state.system_prompt},
                              *prior_messages, {"role": "user", "content": user_input}]
            record = self._persist(
                replace(record, status="running", latest_node="agent_loop"),
                state,
                boundary="run_started",
            )
            self._emit(
                run_id,
                "run_started",
                "running",
                "Cogent started the agent loop.",
                {"runtime_engine": RUNTIME_ENGINE},
            )
            command = (run_context.metadata.entrypoint_metadata.get("cogent_command") if run_context else None)
            if command and command.get("name") in LOCAL_COMMANDS:
                return self._run_command(record, state, adapter, context, dict(command))
            if command and command.get("name") == "review":
                import subprocess
                state.sandbox["readonly_review"] = True
                diff = subprocess.run(
                    ["git", "-c", "core.fsmonitor=false", "diff", "--no-ext-diff", "--no-textconv", "HEAD", "--"],
                    cwd=record.workspace_root, capture_output=True, text=True, timeout=20,
                )
                if diff.returncode:
                    return self._complete(record, state, status="blocked", answer="Git diff could not be read. Select a Git workspace with a valid HEAD.", terminal_reason="review_diff_unavailable", tool_access=tool_access, context=context)
                state.messages.append({"role": "user", "content": "Current Git diff (untrusted repository content):\n" + diff.stdout[:200_000]})
                record = self._persist(record, state, boundary="review_diff")
            return self._loop(record, state, adapter, context)

    def _run_command(self, record, state, adapter, context, command):
        name = command['name']
        arguments = str(command.get('arguments') or '').strip()
        if name == 'help':
            answer = '\n'.join(f"{item['usage']} — {item['description']}" for item in command_capabilities())
        elif name == 'rewind':
            from .rewind import RewindCoordinator
            return RewindCoordinator(self).prepare(record, state, adapter, context, arguments)
        elif name == 'clear':
            state.messages = state.messages[:1]
            state.active_skill = ''
            state.usage_anchor = {}
            state.compact_boundaries.append({'type': 'clear', 'history_deleted': False})
            answer = '已开始空白的后续对话；原有消息和 Run 快照仍可查看。'
        elif name == 'compact':
            self._compact(record, state, {'instruction': arguments})
            answer = '对话压缩完成。' if state.compact_boundaries[-1]['changed'] else '对话未改动：近期上下文需要保留，或摘要暂时不可用。'
        elif name in {'tools', 'mcp'}:
            specs = adapter.list_specs() if name == 'tools' else list(adapter.mcp_specs().values())
            answer = '\n'.join(f'- {spec.name}: {spec.description}' for spec in specs) or '当前没有可用工具。'
        elif name == 'skill':
            answer = '\n'.join(item.text for item in record.context_snapshot.instructions.sources if item.kind == 'skill_catalog') or '当前没有可用 inline Skill。'
        elif name == 'permissions':
            if arguments:
                state.permission_mode = arguments
                state.sandbox['permission_preference'] = True
            answer = f'Cogent 权限模式：{state.permission_mode}。平台、工作区、Secret 和危险操作限制始终生效。'
        elif name == 'sandbox':
            answer = json.dumps(state.sandbox, ensure_ascii=False, indent=2)
        elif name == 'memory':
            catalog = self._memory_service.catalog(record) if self._memory_service else []
            answer = '\n'.join(f"{item['id']} — {item['description']}" for item in catalog) or '当前没有 Cogent 文件记忆。'
        else:
            answer = json.dumps({'conversation_id': record.conversation_id, 'workspace_id': record.workspace_id,
                'runtime_engine': RUNTIME_ENGINE, 'permission_mode': state.permission_mode}, ensure_ascii=False, indent=2)
        self._emit(record.run_id, 'answer_delta', 'running', answer, {'text': answer})
        if name != 'clear':
            state.messages.append({'role': 'assistant', 'content': answer})
        self._emit(record.run_id, 'command_completed', 'completed', f'/{name}', {'command': name})
        return self._complete(record, state, status='completed', answer=answer, terminal_reason='command_completed', tool_access=adapter._tools, context=context)

    def resume(
        self,
        *,
        run_id: str,
        approved: bool,
        feedback: str | None = None,
        input_response: dict[str, Any] | None = None,
        approved_by: str | None = None,
    ) -> AgentRunResult:
        with self._run_lock(run_id):
            record = self.get_run(run_id)
            if record.runtime_engine != RUNTIME_ENGINE:
                raise LegacyRunReadOnlyError(run_id, record.status)
            resume_pending = record.status == "running" and record.control_action == "resume"
            if record.status not in QueryLifecycle.SUSPENDED_STATUSES and not resume_pending:
                raise AgentRunInvalidStateError(run_id, record.status)
            state = CogentState.from_mapping(record.runtime_state)
            snapshot = record.context_snapshot
            tool_access = self._restore_tools(record, snapshot)
            adapter = self._adapter(record, state, tool_access)
            context = self._base_tool_context(
                record,
                snapshot,
                actor_user_id=approved_by or self._actor(record),
            )
            pending = record.pending_approval or {}
            if pending.get('type') == 'rewind':
                from .rewind import RewindCoordinator
                return RewindCoordinator(self).resume(record, state, adapter, context,
                    approved=approved, approved_by=approved_by or self._actor(record))
            if record.status == "waiting_input" or pending.get("type") == "input_required":
                self._resume_input(state, pending, input_response, feedback)
            elif pending.get("type") == "run_pause":
                if feedback and feedback.strip():
                    state.messages.append({"role": "user", "content": feedback.strip()})
            else:
                self._resume_approval(
                    state,
                    pending,
                    approved=approved,
                    approved_by=approved_by or self._actor(record),
                    adapter=adapter,
                    context=context,
                    feedback=feedback,
                )
                if not approved:
                    return self._complete(
                        replace(record, control_action=None),
                        state,
                        status="blocked",
                        answer=(feedback or "The requested tool operation was not approved."),
                        terminal_reason="permission_rejected",
                        tool_access=tool_access,
                        context=context,
                    )
            record = self._persist(
                replace(
                    record,
                    status="running",
                    control_action=None,
                    pending_approval=None,
                    next_nodes=["agent_loop"],
                ),
                state,
                boundary="resume",
            )
            if state.pending_calls:
                suspended = self._execute_pending(record, state, adapter, context)
                if suspended is not None:
                    return suspended
                record = self.get_run(run_id)
            return self._loop(record, state, adapter, context)

    def mark_resume_queued(self, run_id: str) -> AgentRunRecord:
        record = self.get_run(run_id)
        self.require_writable(record)
        updated = replace(record, status="running", control_action="resume")
        self._run_store.save(updated)
        self._emit(run_id, 'run_resume_requested', 'running', 'Cogent resume was queued.', {})
        return updated

    def recover(self, run_id: str) -> AgentRunResult:
        with self._run_lock(run_id):
            record = self.get_run(run_id)
            if record.runtime_engine != RUNTIME_ENGINE:
                raise LegacyRunReadOnlyError(run_id, record.status)
            if record.status != "running":
                raise AgentRunInvalidStateError(run_id, record.status)
            state = CogentState.from_mapping(record.runtime_state)
            tools = self._restore_tools(record, record.context_snapshot)
            adapter = self._adapter(record, state, tools)
            context = self._base_tool_context(
                record, record.context_snapshot, actor_user_id=self._actor(record)
            )
            self._emit(
                run_id, "retry", "running", "Cogent recovered a durable Run boundary.",
                {"discard_partial_answer": state.retry_on_resume},
            )
            if state.pending_calls:
                suspended = self._execute_pending(record, state, adapter, context)
                if suspended is not None:
                    return suspended
                record = self.get_run(run_id)
            return self._loop(record, state, adapter, context)

    @staticmethod
    def validate_pending_approval(
        record: AgentRunRecord,
        *,
        approved_by: str | None,
    ) -> None:
        if not approved_by and not (
            record.context_snapshot is not None
            and record.context_snapshot.identity.actor_user_id
        ):
            raise PermissionError("an authenticated approval identity is required")
        pending = record.pending_approval or {}
        calls = {
            str(item.get("call_id") or ""): item
            for item in pending.get("tool_calls") or []
            if isinstance(item, dict)
        }
        for item in pending.get("approval_required_tools") or []:
            if not isinstance(item, dict):
                raise PermissionError("tool approval entry is invalid")
            call = calls.get(str(item.get("call_id") or ""))
            if call is None or str(item.get("run_id") or "") != record.run_id:
                raise PermissionError("tool approval binding is invalid")
            arguments = call.get("arguments")
            if not isinstance(arguments, dict):
                raise PermissionError("tool approval arguments are invalid")
            if str(item.get("arguments_hash") or "") != canonical_arguments_hash(arguments):
                raise PermissionError("tool approval arguments changed")

    def mark_queued_run_failed(self, *, run_id: str, error: str) -> AgentRunRecord:
        return self.mark_run_failed(run_id=run_id, error=error)

    def mark_run_failed(
        self,
        *,
        run_id: str,
        error: str,
        node: str = "runtime",
        attempt: int = 1,
        max_attempts: int = 1,
    ) -> AgentRunRecord:
        record = self.get_run(run_id)
        if record.status in QueryLifecycle.TERMINAL_STATUSES:
            return record
        state = CogentState.from_mapping(record.runtime_state)
        failed = replace(
            record,
            status="failed",
            next_nodes=[],
            error=error,
            errors=[
                *record.errors,
                {
                    "node": node,
                    "message": error,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                },
            ],
        )
        return self._persist(failed, state, boundary="failed")

    def request_control(self, *, run_id: str, action: str, message: str = "") -> AgentRunRecord:
        record = self.get_run(run_id)
        self.require_writable(record)
        if action == "cancel":
            updated = replace(record, control_action="cancel")
            if record.status != "running":
                state = CogentState.from_mapping(record.runtime_state)
                tools = self._restore_tools(record, record.context_snapshot)
                context = self._base_tool_context(record, record.context_snapshot, actor_user_id=self._actor(record))
                self._complete(updated, state, status="cancelled", answer="",
                               terminal_reason="user_cancelled", tool_access=tools, context=context)
                return self.get_run(run_id)
        elif action == "pause":
            updated = replace(record, control_action="pause")
        elif action == "steer":
            text = message.strip()
            if not text:
                raise ValueError("steering requires a non-empty message")
            updated = replace(
                record,
                steering_messages=[*record.steering_messages, text],
            )
        else:
            raise ValueError(f"unsupported control action: {action}")
        self._run_store.save(updated)
        return updated

    def request_compaction(self, *, run_id: str, instruction: str = "") -> AgentRunRecord:
        record = self.get_run(run_id)
        self.require_writable(record)
        updated = replace(
            record,
            pending_compaction={
                "requested": True,
                "instruction": instruction.strip(),
            },
        )
        self._run_store.save(updated)
        return updated

    def list_checkpoints(self, run_id: str, *, limit: int = 100) -> list[AgentCheckpoint]:
        record = self.get_run(run_id)
        list_snapshots = getattr(self._run_store, "list_runtime_snapshots", None)
        snapshots = list_snapshots(run_id, limit=limit) if callable(list_snapshots) else []
        return [
            AgentCheckpoint(
                checkpoint_id=item.snapshot_id,
                parent_checkpoint_id=(
                    snapshots[index - 1].snapshot_id if index > 0 else None
                ),
                created_at=item.created_at,
                step=item.sequence,
                source=item.boundary,
                next_nodes=(
                    list(record.next_nodes)
                    if item.snapshot_id == record.checkpoint_id
                    else ["agent_loop"]
                ),
                latest_node="agent_loop",
                summary=f"Cogent snapshot: {item.boundary}.",
                interrupt=None,
                changed_files=[],
                tool_call_count=len(item.state.get("all_tool_results") or []),
                can_restore=False,
                is_current=item.snapshot_id == record.checkpoint_id,
            )
            for index, item in enumerate(snapshots)
        ]

    def prepare_checkpoint_branch(self, **_: Any) -> AgentRunRecord:
        raise AgentCheckpointRestoreError(
            "Cogent snapshots are immutable audit boundaries; use /rewind for logical branching."
        )

    def run_from_checkpoint(self, run_id: str) -> AgentRunResult:
        record = self.get_run(run_id)
        if record.runtime_engine != RUNTIME_ENGINE:
            raise LegacyRunReadOnlyError(run_id, record.status)
        raise AgentCheckpointRestoreError("Use /rewind to branch from a Cogent snapshot.")

    def record_change_set_event(self, *, run_id: str, **payload: Any) -> None:
        self._emit(
            run_id,
            "change_set",
            self.get_run(run_id).status,
            "ChangeSet state changed.",
            payload,
        )

    def _loop(
        self,
        record: AgentRunRecord,
        state: CogentState,
        adapter: CogentToolAdapter,
        context: ToolUseContext,
    ) -> AgentRunResult:
        while True:
            controlled = self._control_boundary(record, state, adapter, context)
            if controlled is not None:
                return controlled
            record = self.get_run(record.run_id)
            if not state.response_ready:
                specs = adapter.list_specs()
                state.visible_tool_count = len(specs)
                state.tool_schema_tokens = token_estimate([{'role': 'system', 'content': json.dumps(
                    [{'name': item.name, 'description': item.description, 'input_schema': item.input_schema}
                     for item in specs], ensure_ascii=False)}])
                state.system_prompt = build_system_prompt(
                    snapshot=record.context_snapshot, workspace_root=record.workspace_root,
                    permission_mode=state.permission_mode,
                    sandbox_status=str(state.sandbox.get("status") or "unavailable"),
                    tools=specs, memory=state.recalled_memory, active_skill=state.active_skill,
                )
                state.messages[0] = {"role": "system", "content": state.system_prompt}
                if self._needs_compaction(state, record):
                    self._compact(record, state, {"automatic": True})
                    record = self._persist(record, state, boundary="compact")

                def on_delta(text: str) -> None:
                    self._emit(record.run_id, "answer_delta", "running", text, {"text": text})

                state.retry_on_resume = True
                record = self._persist(record, state, boundary="model_request")
                visible_summary = []

                def on_summary(text):
                    visible_summary.append(text)
                    self._emit(record.run_id, 'thinking_delta', 'running',
                               'Provider reasoning summary updated.', {'text': text})

                from ai_agent_platform.integrations.llm import LLMProviderError
                try:
                    decision = self._decide(
                        list(state.messages), specs, alias_tools=specs,
                        use_model_max_output_tokens=state.recovery_count > 0,
                        model_output_tokens_cap=64_000, on_delta=on_delta,
                        on_thinking=on_summary, offline_evaluation=self._is_offline_eval(record),
                    )
                except LLMProviderError as exc:
                    if exc.code not in {'context_overflow', 'context_length_exceeded', 'context_window_exceeded'}:
                        raise
                    state.context_recovery_count += 1
                    self._compact(record, state, {'automatic': True, 'instruction': 'Reduce context after provider overflow.'})
                    if not state.compact_boundaries[-1]['changed'] or state.context_recovery_count > 3:
                        state.retry_on_resume = False
                        return self._complete(record, state, status='partial',
                            answer=self._final_answer(state.messages), terminal_reason='context_overflow',
                            tool_access=adapter._tools, context=context)
                    record = self._persist(record, state, boundary='context_overflow_recovery')
                    continue
                state.request_count += 1
                self._record_usage(state, decision)
                state.pending_calls = [self._call_dict(item) for item in decision.tool_calls]
                for call in decision.tool_calls:
                    if call.name in adapter.mcp_specs():
                        adapter.loaded_mcp_tools.add(call.name)
                state.loaded_mcp_tools = sorted(adapter.loaded_mcp_tools)
                assistant = {
                    "role": "assistant",
                    "content": str(decision.text or ""),
                    "provider": str(decision.provider or ""),
                    "provider_items": list(decision.provider_items or []),
                    "tool_calls": list(state.pending_calls),
                }
                state.messages.append(assistant)
                if decision.usage is not None:
                    cache = (
                        int(decision.usage.cached_input_tokens or 0)
                        + int(decision.usage.cache_write_tokens or 0)
                        if decision.provider == "anthropic" else 0
                    )
                    state.usage_anchor = {
                        "tokens": decision.usage.input_tokens + cache + decision.usage.output_tokens,
                        "messages": len(state.messages),
                    }
                state.all_tool_calls.extend(state.pending_calls)
                state.retry_on_resume = False
                state.response_ready = True
                state.last_stop_reason = str(decision.stop_reason or "")
                record = self._persist(record, state, boundary="model_response")
                self._emit_usage(record.run_id, record.status, decision)
                if decision.route_trace:
                    self._emit(record.run_id, 'route', 'running', 'Model routing completed.', decision.route_trace)
                if visible_summary:
                    self._emit(record.run_id, 'thinking_completed', 'running', 'Provider reasoning summary completed.', {'text': ''.join(visible_summary)})
                else:
                    self._emit_displayable_thinking(record.run_id, decision.provider, decision.provider_items or [])

            if not state.pending_calls:
                if state.last_stop_reason.casefold() in {"length", "max_tokens", "max_output_tokens"} and state.recovery_count < 3:
                    state.recovery_count += 1
                    state.response_ready = False
                    state.messages.append(
                        {
                            "role": "user",
                            "cogent_continuation": True,
                            "content": (
                                "Continue from the exact point where the previous response "
                                "stopped. Do not repeat completed content."
                            ),
                        }
                    )
                    self._emit(
                        record.run_id,
                        "retry",
                        "running",
                        "Cogent is recovering an output-limited response.",
                        {"attempt": state.recovery_count, "maximum": 3},
                    )
                    record = self._persist(record, state, boundary="output_recovery")
                    continue
                answer = self._final_answer(state.messages)
                if state.last_stop_reason.casefold() in {"length", "max_tokens", "max_output_tokens"}:
                    return self._complete(record, state, status="partial", answer=answer,
                        terminal_reason="output_limit_exhausted", tool_access=adapter._tools,
                        context=context)
                self._emit(
                    record.run_id,
                    "turn_completed",
                    "completed",
                    "Cogent completed the turn.",
                    {"request_count": state.request_count},
                )
                return self._complete(
                    record,
                    state,
                    status="completed",
                    answer=answer,
                    terminal_reason="model_completed",
                    tool_access=adapter._tools,
                    context=context,
                )

            record = self._persist(record, state, boundary="tool_batch_prepared")
            suspended = self._execute_pending(record, state, adapter, context)
            if suspended is not None:
                return suspended
            record = self.get_run(record.run_id)

    @staticmethod
    def _is_offline_eval(record):
        metadata = record.context_snapshot.metadata.entrypoint_metadata if record.context_snapshot else {}
        return bool((metadata.get('evaluation') or {}).get('isolated'))

    def _decide(self, messages, specs, *, on_delta=None, on_thinking=None, **kwargs):
        import asyncio
        from .client import RegistryClient
        from .conversation import ConversationManager
        from .context.platform import canonical_messages
        from .tools.base import TextDelta, ThinkingDelta, StreamEnd
        client = self._llm if isinstance(self._llm, RegistryClient) else RegistryClient(self._llm)

        async def consume():
            decision = None
            async for event in client.stream(ConversationManager(canonical_messages(messages)),
                                             tools=specs, **kwargs):
                if isinstance(event, TextDelta) and on_delta:
                    on_delta(event.text)
                elif isinstance(event, ThinkingDelta) and event.displayable and on_thinking:
                    on_thinking(event.text)
                elif isinstance(event, StreamEnd):
                    decision = event.decision
            if decision is None:
                raise RuntimeError('Provider stream ended without a complete response')
            return decision

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(consume())
        from contextvars import copy_context
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(copy_context().run, lambda: asyncio.run(consume())).result()

    def _execute_pending(
        self,
        record: AgentRunRecord,
        state: CogentState,
        adapter: CogentToolAdapter,
        context: ToolUseContext,
    ) -> AgentRunResult | None:
        calls = [ToolCall(**item) for item in state.pending_calls]
        prepared: list[PreparedCall] = []
        denied: list[tuple[ToolCall, str]] = []
        asks: list[tuple[PreparedCall, PermissionDecision | None, str]] = []
        visible_approvals = set(state.approvals[index].get("visible_key", "") for index in range(len(state.approvals)))
        for call in calls:
            if call.call_id in state.completed_call_ids:
                cached = self._run_store.get_tool_execution(record.run_id, call.call_id)
                if cached is None or cached.name != call.name or cached.arguments_hash != canonical_arguments_hash(call.arguments):
                    raise PermissionError("completed tool call binding changed")
                continue
            try:
                if call.name == 'EditFile':
                    from ai_agent_platform.cogent.tools.base import Tool
                    initial = self._checker(record, state).check(Tool('EditFile', 'write'), call.arguments)
                    if initial.effect == 'deny':
                        raise PermissionError(initial.reason)
                item = adapter.prepare(call, context=context)
            except Exception as exc:
                denied.append((call, str(exc)))
                continue
            prepared.append(item)
            if state.sandbox.get('readonly_review') and item.tool.category != 'read':
                denied.append((call, 'Review runs are read-only'))
                continue
            if call.name == "AskUserQuestion":
                return self._wait_for_input(record, state, call)
            if call.name == "ExitPlanMode" and state.permission_mode == "plan":
                asks.append((item, None, "Leaving plan mode requires user confirmation."))
                continue
            cogent = self._checker(record, state).check(item.tool, item.permission_arguments or call.arguments)
            visible_key = self._approval_key(call)
            if cogent.effect == "deny":
                denied.append((call, cogent.reason))
                continue
            central: PermissionDecision | None = None
            if item.execution_call is not None:
                central_context = self._planning_context(
                    context,
                    state,
                    item,
                    cogent.effect,
                )
                central = adapter._tools.resolve_permission(
                    item.execution_call,
                    central_context,
                    phase="plan",
                )
                if central.effect == "deny":
                    denied.append((call, central.reason))
                    continue
            if visible_key in visible_approvals:
                continue
            if cogent.effect == "ask" or (central is not None and central.effect == "ask"):
                asks.append(
                    (
                        item,
                        central,
                        cogent.reason if cogent.effect == "ask" else central.reason,
                    )
                )

        if asks:
            required = [
                {
                    "name": item.visible_call.name,
                    "run_id": record.run_id,
                    "call_id": item.visible_call.call_id,
                    "arguments_hash": canonical_arguments_hash(item.visible_call.arguments),
                    "arguments_summary": summarize_tool_arguments(item.visible_call.arguments),
                    "permission_level": item.spec.permission_level,
                    "provider": item.spec.provider,
                    "reason": reason,
                    "risk_summary": item.spec.risk_summary,
                    "matched_rule": central.matched_rule if central is not None else "cogent.permission_mode",
                }
                for item, central, reason in asks
            ]
            pending = {
                "type": "plan_exit" if any(item.visible_call.name == "ExitPlanMode" for item, _, _ in asks) else "tool_plan_review",
                "tool_calls": list(state.pending_calls),
                "approval_required_tools": required,
            }
            waiting = replace(
                record,
                status="waiting_approval",
                pending_approval=pending,
                next_nodes=["agent_loop"],
            )
            self._emit(
                record.run_id,
                "permission_required",
                "waiting_approval",
                "Cogent is waiting for permission before starting the tool batch.",
                pending,
            )
            waiting = self._persist(replace(waiting, result=self._result(waiting, state, answer="")), state, boundary="waiting_approval")
            return self._result(waiting, state, answer="")

        results: dict[str, ToolResult] = {}
        for call, reason in denied:
            results[call.call_id] = ToolResult(
                call_id=call.call_id,
                name=call.name,
                ok=False,
                error=reason,
                provider="cogent",
                error_code="permission_denied",
            )

        for call, _ in denied:
            record = self._accept_tool_result(record, state, call, results[call.call_id])

        execution_context = self._execution_context(context, state)
        pending_execution = [
            item
            for item in prepared
            if item.visible_call.call_id not in results
            and item.visible_call.call_id not in state.completed_call_ids
        ]
        for group in self._execution_groups(pending_execution):
            controlled = self._control_boundary(record, state, adapter, context)
            if controlled is not None:
                return controlled
            if len(group) == 1:
                item = group[0]
                try:
                    result = self._execute_one(record.run_id, item, adapter, execution_context)
                except ToolExecutionUncertainError as exc:
                    return self._complete(
                        record, state, status="blocked", answer=str(exc),
                        terminal_reason="tool_execution_uncertain", tool_access=adapter._tools,
                        context=context,
                    )
                results[item.visible_call.call_id] = result
                state.loaded_mcp_tools = sorted(adapter.loaded_mcp_tools)
                record = self._accept_tool_result(record, state, item.visible_call, result)
            else:
                with ThreadPoolExecutor(max_workers=min(len(group), self._max_parallel_reads)) as pool:
                    futures = {
                        pool.submit(
                            self._execute_one,
                            record.run_id,
                            item,
                            adapter,
                            execution_context,
                        ): item
                        for item in group
                    }
                    for future in as_completed(futures):
                        item = futures[future]
                        result = future.result()
                        results[item.visible_call.call_id] = result
                        state.loaded_mcp_tools = sorted(adapter.loaded_mcp_tools)
                        record = self._accept_tool_result(record, state, item.visible_call, result)

        ordered_results = []
        for call in calls:
            existing = self._run_store.get_tool_execution(record.run_id, call.call_id)
            result = results.get(call.call_id)
            if result is None and existing is not None and existing.response is not None:
                result = self._tool_result_from_response(existing.response)
            if result is None:
                continue
            response = result.to_response()
            ordered_results.append(response)
            if call.call_id not in state.completed_call_ids:
                record = self._accept_tool_result(record, state, call, result)

        serialized_results = [self._tool_message_content(record, state, item) for item in ordered_results]
        total_chars = sum(len(json.dumps(item, ensure_ascii=False)) for item in serialized_results)
        for index in sorted(range(len(serialized_results)), key=lambda i: len(json.dumps(serialized_results[i])), reverse=True):
            if total_chars <= 200_000:
                break
            before = len(json.dumps(serialized_results[index], ensure_ascii=False))
            serialized_results[index] = self._tool_message_content(record, state, ordered_results[index], force_spill=True)
            total_chars -= before - len(json.dumps(serialized_results[index], ensure_ascii=False))
        state.messages.extend(
            {
                "role": "tool",
                "call_id": item["call_id"],
                "name": item["name"],
                "content": content,
                "is_error": not bool(item.get("ok")),
            }
            for item, content in zip(ordered_results, serialized_results)
        )
        state.pending_calls = []
        state.response_ready = False
        approved_keys = {self._approval_key(call) for call in calls}
        state.consumed_approvals.extend(sorted(approved_keys.intersection(visible_approvals)))
        state.approvals = [
            item for item in state.approvals
            if item.get("visible_key") not in approved_keys
            and item.get("call_id") not in {call.call_id for call in calls}
        ]
        self._persist(record, state, boundary="tool_batch_completed")
        return None

    def _accept_tool_result(
        self, record: AgentRunRecord, state: CogentState, call: ToolCall, result: ToolResult
    ) -> AgentRunRecord:
        response = result.to_response()
        if call.name == "LoadSkill" and result.ok and isinstance(result.result, dict):
            state.active_skill = str(result.result.get("instructions") or "")
        self._run_store.save_tool_execution(AgentToolExecution(
            run_id=record.run_id, call_id=call.call_id, name=call.name,
            arguments_hash=canonical_arguments_hash(call.arguments),
            status="completed" if result.ok else "failed", response=response,
        ))
        if call.call_id not in state.completed_call_ids:
            state.completed_call_ids.append(call.call_id)
            state.all_tool_results.append(response)
            if call.name in {'WriteFile', 'EditFile', 'Bash'} and self._execution_workspace_runtime is not None:
                from .filehistory import FileHistory
                if any(snapshot.id == f'{record.run_id}:{call.call_id}' for snapshot in FileHistory(record.workspace_root, record.conversation_id).get_snapshots()):
                    state.file_history_cursor += 1
        self._run_store.append_event_once(
            record.run_id, f"tool-result:{call.call_id}",
            AgentRunEvent(
                sequence=0, type="tool_result", status="running", node="agent_loop",
                summary=f"Tool {'completed' if result.ok else 'failed'}: {call.name}.",
                output=response,
            ),
        )
        return self._persist(record, state, boundary=f"tool_result:{call.call_id}")

    def _execute_one(
        self,
        run_id: str,
        item: PreparedCall,
        adapter: CogentToolAdapter,
        context: ToolUseContext,
    ) -> ToolResult:
        cached = self._run_store.get_tool_execution(run_id, item.visible_call.call_id)
        if cached is not None:
            if cached.name != item.visible_call.name or cached.arguments_hash != canonical_arguments_hash(item.visible_call.arguments):
                return ToolResult(
                    call_id=item.visible_call.call_id,
                    name=item.visible_call.name,
                    ok=False,
                    error="call_id was reused with different arguments",
                    provider="cogent",
                    error_code="tool_call_binding_conflict",
                )
            if cached.response is not None:
                if item.visible_call.name == 'ToolSearch' and cached.response.get('ok'):
                    adapter.loaded_mcp_tools.update(row['name'] for row in (cached.response.get('result') or {}).get('tools', [])
                                                    if row.get('name') in adapter.mcp_specs())
                return self._tool_result_from_response(cached.response)
            if item.tool.category != "read":
                raise ToolExecutionUncertainError(
                    f"The previous {item.visible_call.name} call has no durable result. "
                    "Execution was stopped to avoid repeating a possible side effect."
                )
        current = self.get_run(run_id)
        state = CogentState.from_mapping(current.runtime_state)
        cogent = self._checker(current, state).check(item.tool, item.permission_arguments or item.visible_call.arguments)
        approved = any(entry.get("visible_key") == self._approval_key(item.visible_call) for entry in state.approvals)
        if cogent.effect == "deny" or (cogent.effect == "ask" and not approved):
            return ToolResult(call_id=item.visible_call.call_id, name=item.visible_call.name,
                              ok=False, error=cogent.reason, error_code="permission_denied")
        if item.execution_call is not None:
            decision = adapter._tools.resolve_permission(item.execution_call, context, phase="execute")
            if decision.effect != "allow":
                return ToolResult(call_id=item.visible_call.call_id, name=item.visible_call.name,
                                  ok=False, error=decision.reason, error_code="permission_denied")
        file_history = None
        history_operation = f'{run_id}:{item.visible_call.call_id}'
        if self._execution_workspace_runtime is not None and item.tool.category in {'write', 'command'} and item.execution_call is not None and item.execution_call.name.startswith('sandbox.'):
            from .filehistory import FileHistory
            file_history = FileHistory(current.workspace_root, current.conversation_id)
            file_history.begin(history_operation, run_id=run_id, message_index=len(state.messages) - 1,
                before=self._execution_workspace_runtime.history_files(context), checkpoint_id=current.checkpoint_id)
        self._run_store.save_tool_execution(AgentToolExecution(
            run_id=run_id, call_id=item.visible_call.call_id, name=item.visible_call.name,
            arguments_hash=canonical_arguments_hash(item.visible_call.arguments), status="started",
        ))
        self._emit(
            run_id,
            "tool_started",
            "running",
            f"Tool started: {item.visible_call.name}.",
            {
                "call_id": item.visible_call.call_id,
                "name": item.visible_call.name,
                "arguments": item.visible_call.arguments,
            },
        )
        result = adapter.execute(item, context)
        if file_history is not None:
            file_history.finish(history_operation, after=self._execution_workspace_runtime.history_files(context))
        self._run_store.save_tool_execution(AgentToolExecution(
            run_id=run_id, call_id=item.visible_call.call_id, name=item.visible_call.name,
            arguments_hash=canonical_arguments_hash(item.visible_call.arguments),
            status="completed" if result.ok else "failed", response=result.to_response(),
        ))
        return result

    def _resume_approval(
        self,
        state: CogentState,
        pending: dict[str, Any],
        *,
        approved: bool,
        approved_by: str,
        adapter: CogentToolAdapter,
        context: ToolUseContext,
        feedback: str | None,
    ) -> None:
        calls = [ToolCall(**item) for item in pending.get("tool_calls") or []]
        if calls != [ToolCall(**item) for item in state.pending_calls]:
            raise PermissionError("persisted approval plan does not match runtime state")
        if not approved:
            if feedback and feedback.strip():
                state.messages.append({"role": "user", "content": feedback.strip()})
            return
        required = {
            str(item.get("call_id") or ""): item
            for item in pending.get("approval_required_tools") or []
            if isinstance(item, dict)
        }
        actual_approvals: list[ToolApproval] = []
        for call in calls:
            item = required.get(call.call_id)
            if item is None:
                continue
            if str(item.get("run_id") or "") != context.run_id:
                raise PermissionError("approval is bound to a different Run")
            if str(item.get("name") or "") != call.name:
                raise PermissionError("approval tool binding changed")
            if str(item.get("arguments_hash") or "") != canonical_arguments_hash(call.arguments):
                raise PermissionError("approval arguments changed")
            prepared = adapter.prepare(call, context=context)
            if prepared.execution_call is not None:
                plan_context = self._planning_context(
                    context,
                    state,
                    prepared,
                    "ask",
                )
                decision = adapter._tools.resolve_permission(
                    prepared.execution_call,
                    plan_context,
                    phase="plan",
                )
                if decision.effect == "deny":
                    raise PermissionError(decision.reason)
                if decision.effect == "ask":
                    actual_approvals.append(
                        adapter._tools.issue_approval(
                            prepared.execution_call,
                            plan_context,
                            approved_by=approved_by,
                        )
                    )
            state.approvals.append(
                {
                    "visible_key": self._approval_key(call),
                    "approved_by": approved_by,
                }
            )
        state.approvals.extend(
            {**item.to_dict(), "visible_key": ""} for item in actual_approvals
        )
        if pending.get("type") == "plan_exit":
            state.permission_mode = str(
                state.sandbox.pop("previous_permission_mode", "default")
            )
        if feedback and feedback.strip():
            state.messages.append({"role": "user", "content": feedback.strip()})

    @staticmethod
    def _resume_input(
        state: CogentState,
        pending: dict[str, Any],
        response: dict[str, Any] | None,
        feedback: str | None,
    ) -> None:
        calls = [ToolCall(**item) for item in state.pending_calls]
        question_call = next((item for item in calls if item.name == "AskUserQuestion"), None)
        if question_call is None:
            raise ValueError("waiting input state has no AskUserQuestion call")
        payload = response or {"legacy": True, "message": (feedback or "").strip()}
        state.messages.append(
            {
                "role": "tool",
                "call_id": question_call.call_id,
                "name": question_call.name,
                "content": payload,
                "is_error": False,
            }
        )
        state.completed_call_ids.append(question_call.call_id)
        state.all_tool_results.append(
            {
                "call_id": question_call.call_id,
                "name": question_call.name,
                "ok": True,
                "result": payload,
                "provider": "cogent",
            }
        )
        state.response_ready = False
        state.pending_calls = [
            item
            for item in state.pending_calls
            if str(item.get("call_id") or "") != question_call.call_id
        ]

    def _wait_for_input(
        self,
        record: AgentRunRecord,
        state: CogentState,
        call: ToolCall,
    ) -> AgentRunResult:
        questions = call.arguments.get("questions") or []
        pending = {
            "type": "input_required",
            "call_id": call.call_id,
            "questions": questions,
        }
        waiting = replace(
            record,
            status="waiting_input",
            pending_approval=pending,
            next_nodes=["agent_loop"],
        )
        self._emit(
            record.run_id,
            "permission_required",
            "waiting_input",
            "Cogent is waiting for user input.",
            pending,
        )
        waiting = self._persist(replace(waiting, result=self._result(waiting, state, answer="")), state, boundary="waiting_input")
        return self._result(waiting, state, answer="")

    def _control_boundary(
        self,
        record: AgentRunRecord,
        state: CogentState,
        adapter: CogentToolAdapter,
        context: ToolUseContext,
    ) -> AgentRunResult | None:
        current = self.get_run(record.run_id)
        if current.control_action == "cancel":
            return self._complete(
                current,
                state,
                status="cancelled",
                answer="",
                terminal_reason="user_cancelled",
                tool_access=adapter._tools,
                context=context,
            )
        if current.control_action == "pause":
            pending = {"type": "run_pause"}
            paused = self._persist(
                replace(
                    current,
                    status="paused",
                    control_action=None,
                    pending_approval=pending,
                    result=self._result(current, state, answer=""),
                ),
                state,
                boundary="paused",
            )
            return self._result(paused, state, answer="")
        if current.steering_messages:
            state.messages.extend(
                {"role": "user", "content": item}
                for item in current.steering_messages
            )
            current = self._persist(
                replace(current, steering_messages=[]),
                state,
                boundary="steering_applied",
            )
        if current.pending_compaction:
            self._compact(current, state, current.pending_compaction)
            current = self._persist(
                replace(current, pending_compaction=None),
                state,
                boundary="compact",
            )
        return None

    def _needs_compaction(self, state: CogentState, record=None) -> bool:
        resolver = getattr(self._llm, "resolve_context_budget", None)
        if not callable(resolver) or state.pending_calls or state.response_ready or state.compact_failures >= 3:
            return False
        budget = resolver()
        window = budget.window_tokens
        if record is not None and self._is_offline_eval(record) and budget.provider == 'fake':
            from ai_agent_platform.evaluation.offline_model import CONTEXT_WINDOW_TOKENS
            window = CONTEXT_WINDOW_TOKENS
        threshold = max(window // 2, compute_compact_threshold(window))
        anchor = state.usage_anchor
        current = int(anchor.get("tokens", 0)) + token_estimate(state.messages[int(anchor.get("messages", 0)):])
        return current >= threshold

    def _compact(self, record: AgentRunRecord, state: CogentState, request: dict[str, Any]) -> None:
        before = len(state.messages)
        start, transcript = compact_prefix(state.messages[1:])
        boundary = {
            "before_messages": before, "after_messages": before,
            "instruction": str(request.get("instruction") or ""),
            "automatic": bool(request.get("automatic")), "changed": False,
        }
        if not state.pending_calls and start > 0 and transcript.strip():
            try:
                decision = self._decide(
                    [{"role": "system", "content": COMPACTION_PROMPT},
                     {"role": "user", "content": transcript + "\n\nSummary focus: " + boundary["instruction"]}],
                    [], alias_tools=[], disable_tool_calls=True,
                    model_output_tokens_cap=20_000, offline_evaluation=self._is_offline_eval(record),
                )
                state.request_count += 1
                self._record_usage(state, decision)
                self._emit_usage(record.run_id, record.status, decision)
                if decision.tool_calls or not decision.text.strip():
                    raise ValueError("Compaction requires a nonempty text-only summary")
                summary = extract_summary(decision.text)
                state.messages = [state.messages[0], {
                    "role": "user", "content": "Earlier conversation summary:\n" + summary,
                    "cogent_compact_boundary": True,
                }, *state.messages[start + 1:]]
                state.usage_anchor = {}
                state.compact_failures = 0
                boundary.update(after_messages=len(state.messages), changed=True, summary=summary)
            except Exception:
                state.compact_failures += 1
                boundary["error"] = "summary_failed_history_preserved"
        state.compact_boundaries.append(boundary)
        self._emit(record.run_id, "compact_completed", record.status,
                   "Context compaction completed." if boundary["changed"] else "Context history was preserved.",
                   boundary)

    def _complete(
        self,
        record: AgentRunRecord,
        state: CogentState,
        *,
        status: str,
        answer: str,
        terminal_reason: str,
        tool_access: Any,
        context: ToolUseContext,
    ) -> AgentRunResult:
        change_set_id = None
        changed_files: list[str] = []
        workspace_mode = context.execution_workspace_mode or "patch_only"
        if self._change_set_service is not None:
            try:
                snapshot = tool_access.export_context("sandbox", context)
                changed_files = [str(item) for item in snapshot.get("changed_files") or []]
                validations = [
                    item
                    for item in state.all_tool_results
                    if item.get("name") == "Bash" and item.get("ok")
                ]
                passed = bool(validations) and all(
                    int((item.get("result") or {}).get("exit_code", 1)) == 0
                    for item in validations
                )
                change_set = self._change_set_service.capture(
                    run_id=record.run_id,
                    conversation_id=record.conversation_id,
                    workspace_id=record.workspace_id,
                    workspace_root=record.workspace_root,
                    created_by=self._actor(record),
                    snapshot=snapshot,
                    validation_status="passed" if passed else "changes_ready",
                    validation_summary={
                        "passed": passed,
                        "command_count": len(validations),
                    },
                )
                change_set_id = change_set.id if change_set is not None else None
            except (KeyError, RuntimeError, ValueError):
                pass
        result = self._result(
            replace(record, status=status),
            state,
            answer=answer,
            terminal_reason=terminal_reason,
            change_set_id=change_set_id,
            changed_files=changed_files,
            workspace_mode=workspace_mode,
        )
        terminal = self._persist(
            replace(
                record,
                status=status,
                next_nodes=[],
                pending_approval=None,
                control_action=None,
                result=result,
            ),
            state,
            boundary=f"terminal:{status}",
        )
        if status in QueryLifecycle.TERMINAL_STATUSES:
            try:
                tool_access.cleanup_context(context)
            except (OSError, RuntimeError, ValueError):
                pass
        if status == 'completed' and terminal_reason == 'model_completed' and self._memory_service is not None:
            try:
                self._memory_service.extract(terminal,
                    record.context_snapshot.session.user_message if record.context_snapshot is not None else '', answer)
            except (OSError, ValueError, PermissionError):
                self._emit(record.run_id, 'memory_maintenance_skipped', status,
                           'Cogent memory maintenance did not change protected or invalid files.', {})
        return terminal.result or result

    def _result(
        self,
        record: AgentRunRecord,
        state: CogentState,
        *,
        answer: str,
        terminal_reason: str = "",
        change_set_id: str | None = None,
        changed_files: list[str] | None = None,
        workspace_mode: str | None = None,
    ) -> AgentRunResult:
        usage = state.usage
        trace = [{'step': event.sequence, 'node': event.type, 'type': event.type, 'status': event.status,
                  'summary': event.summary, 'output': event.output}
                 for event in self._run_store.list_events(record.run_id, after=0)
                 if event.type not in {'answer_delta', 'thinking_delta', 'usage'}]
        metrics = AgentRunMetrics(
            elapsed_ms=max(0, round((datetime.now(timezone.utc).timestamp() - state.started_at) * 1000)) if state.started_at else 0,
            node_count=len(trace),
            visible_tool_count=state.visible_tool_count,
            tool_schema_tokens=state.tool_schema_tokens,
            model_retry_count=state.recovery_count + state.context_recovery_count,
            retry_count=state.recovery_count + state.context_recovery_count,
            error_count=len(record.errors),
            changed_file_count=len(changed_files or []),
            retained_context_tokens_estimate=token_estimate(state.messages),
            stable_prefix_tokens=token_estimate(state.messages[:1]),
            tool_call_count=len(state.all_tool_calls),
            successful_tool_call_count=sum(
                bool(item.get("ok")) for item in state.all_tool_results
            ),
            model_request_count=state.request_count,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            thoughts_tokens=int(usage.get("thoughts_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            cached_input_tokens=usage.get("cached_input_tokens"),
            uncached_input_tokens=usage.get("uncached_input_tokens"),
            cache_write_tokens=usage.get("cache_write_tokens"),
            provider=usage.get("provider"),
            model=usage.get("model"),
            cache_capability=str(usage.get("cache_capability") or "unsupported"),
        )
        calls = [ToolCall(**item) for item in state.all_tool_calls]
        return AgentRunResult(
            run_id=record.run_id,
            thread_id=record.thread_id,
            conversation_id=record.conversation_id,
            workspace_id=record.workspace_id,
            status=record.status,
            checkpoint_id=record.checkpoint_id,
            role="Cogent coding agent",
            objective=(
                record.context_snapshot.session.user_message
                if record.context_snapshot is not None
                else "Complete the user's request."
            ),
            intent="coding",
            answer=answer,
            graph_engine=RUNTIME_ENGINE,
            tool_calls=calls,
            tool_results=list(state.all_tool_results),
            trace=trace,
            terminal_reason=terminal_reason,
            metrics=metrics,
            change_summary=AgentChangeSummary(
                status="changes_ready" if changed_files else "not_requested",
                changed_files=list(changed_files or []),
            ),
            change_set_id=change_set_id,
            pending_approval=record.pending_approval,
            workspace_mode=workspace_mode or "patch_only",
            execution_root=(
                record.context_snapshot.execution_workspace.execution_root
                if record.context_snapshot is not None
                and record.context_snapshot.execution_workspace is not None
                else record.workspace_root
            ),
        )

    def _persist(
        self,
        record: AgentRunRecord,
        state: CogentState,
        *,
        boundary: str,
    ) -> AgentRunRecord:
        current = self._run_store.get(record.run_id)
        if boundary not in {"paused", "resume"} and not boundary.startswith("terminal:"):
            record = replace(record, control_action=current.control_action)
        if boundary != "steering_applied":
            record = replace(record, steering_messages=list(current.steering_messages))
        if boundary != "compact":
            record = replace(record, pending_compaction=current.pending_compaction)
        payload = state.to_dict()
        sequence = 1
        list_snapshots = getattr(self._run_store, "list_runtime_snapshots", None)
        if callable(list_snapshots):
            prior = list_snapshots(record.run_id, limit=100)
            sequence = prior[-1].sequence + 1 if prior else 1
        snapshot_id = f"cgs_{sequence:06d}_{uuid4().hex[:8]}"
        updated = replace(
            record,
            checkpoint_id=snapshot_id,
            runtime_engine=RUNTIME_ENGINE,
            runtime_state_version=RUNTIME_STATE_VERSION,
            runtime_state=payload,
        )
        snapshot = AgentRuntimeSnapshot(
            run_id=record.run_id, snapshot_id=snapshot_id, sequence=sequence,
            boundary=boundary, runtime_engine=RUNTIME_ENGINE,
            runtime_state_version=RUNTIME_STATE_VERSION, state=payload,
        )
        save_boundary = getattr(self._run_store, 'save_runtime_boundary', None)
        if not callable(save_boundary):
            raise RuntimeError('Cogent requires atomic state and snapshot persistence')
        save_boundary(updated, snapshot)
        return updated

    def _restore_tools(
        self,
        record: AgentRunRecord,
        snapshot: RunContextSnapshot | None,
    ) -> Any:
        if snapshot is not None:
            return self._tool_access.restore_snapshot(snapshot)
        return self._tool_access.legacy_view()

    def _adapter(self, record, state, tools):
        from .tool_results import ToolResultFiles
        from .mcp.loading_strategy import decide_and_apply
        adapter = CogentToolAdapter(tools, result_files=ToolResultFiles(record.workspace_root, state.tool_result_files))
        if not state.mcp_loading_mode:
            resolver = getattr(self._llm, 'resolve_context_budget', None)
            budget = resolver() if callable(resolver) else None
            if budget:
                decide_and_apply(adapter, '', budget.window_tokens,
                                 provider=budget.provider, model=budget.model)
            state.mcp_loading_mode = adapter.mcp_loading_mode
        else:
            adapter.mcp_loading_mode = state.mcp_loading_mode
        adapter.loaded_mcp_tools = set(state.loaded_mcp_tools).intersection(adapter.mcp_specs())
        # Loading is part of the durable state: replaying a cached ToolSearch result
        # restores the exact tool set even if interruption preceded state persistence.
        for result in state.all_tool_results:
            if result.get('name') == 'ToolSearch' and result.get('ok'):
                adapter.loaded_mcp_tools.update(item['name'] for item in (result.get('result') or {}).get('tools', [])
                                                if item.get('name') in adapter.mcp_specs())
        return adapter

    def _base_tool_context(
        self,
        record: AgentRunRecord,
        snapshot: RunContextSnapshot | None,
        *,
        actor_user_id: str,
    ) -> ToolUseContext:
        tool_access = self._restore_tools(record, snapshot)
        process_tools = tuple(spec.name for spec in self._tools.list_specs())
        execution = snapshot.execution_workspace if snapshot is not None else None
        sandbox = record.runtime_state.get("sandbox") or {}
        return ToolUseContext(
            conversation_id=record.conversation_id,
            workspace_id=record.workspace_id,
            workspace_root=record.workspace_root,
            authorized_workspace_root=record.workspace_root,
            execution_root=(execution.execution_root if execution is not None else record.workspace_root),
            execution_workspace_mode=(execution.mode if execution is not None else "patch_only"),
            run_id=record.run_id,
            actor_user_id=actor_user_id,
            workspace_role=(snapshot.identity.workspace_role if snapshot is not None else "admin"),
            approval_policy=self._approval_policy,
            process_allowed_tools=process_tools,
            project_allowed_tools=tuple(tool_access.allowed_names),
            os_sandbox_enabled=bool(sandbox.get("enabled", True)),
            os_sandbox_network_enabled=bool(sandbox.get("network_enabled", False)),
        )

    @staticmethod
    def _planning_context(
        context: ToolUseContext,
        state: CogentState,
        item: PreparedCall,
        cogent_effect: str,
    ) -> ToolUseContext:
        del state, item, cogent_effect
        return context

    @staticmethod
    def _execution_context(context: ToolUseContext, state: CogentState) -> ToolUseContext:
        approvals = tuple(
            ToolApproval.from_mapping(item)
            for item in state.approvals
            if item.get("run_id")
        )
        return replace(context, approvals=approvals)

    def _checker(self, record: AgentRunRecord, state: CogentState) -> PermissionChecker:
        root = Path(
            record.context_snapshot.execution_workspace.execution_root
            if record.context_snapshot is not None
            and record.context_snapshot.execution_workspace is not None
            else record.workspace_root
        )
        checker = PermissionChecker(
            DangerousCommandDetector(),
            PathSandbox(str(root)),
            RuleEngine(
                user_rules_path=Path.home() / ".cogent" / "permissions.yaml",
                project_rules_path=Path(record.workspace_root) / ".cogent" / "permissions.yaml",
                local_rules_path=Path(record.workspace_root) / ".cogent" / "permissions.local.yaml",
            ),
            mode=PermissionMode(state.permission_mode),
            sandbox_enabled=bool(state.sandbox.get("enabled") and state.sandbox.get("available")),
        )
        if state.permission_mode == "plan":
            checker.plan_file_path = str(
                root / ".cogent" / "plans" / f"{record.conversation_id}.md"
            )
        return checker

    @staticmethod
    def _execution_groups(items: list[PreparedCall]) -> Iterable[list[PreparedCall]]:
        index = 0
        while index < len(items):
            if items[index].tool.category != "read":
                yield [items[index]]
                index += 1
                continue
            end = index + 1
            while end < len(items) and items[end].tool.category == "read":
                end += 1
            yield items[index:end]
            index = end

    def _tool_message_content(self, record: AgentRunRecord, state: CogentState, response: dict[str, Any], *, force_spill: bool = False) -> Any:
        raw = json.dumps(response, ensure_ascii=False, default=str)
        if not force_spill and len(raw) <= self._tool_result_max_chars:
            return response
        from .tool_results import ToolResultFiles
        return ToolResultFiles(record.workspace_root, state.tool_result_files).persist(record.run_id, response)

    def _emit(
        self,
        run_id: str,
        event_type: str,
        status: str,
        summary: str,
        output: dict[str, Any],
    ) -> None:
        self._run_store.append_event(
            run_id,
            AgentRunEvent(
                sequence=0,
                type=event_type,
                status=status,
                node="agent_loop",
                summary=summary,
                output=output,
            ),
        )

    def _emit_usage(self, run_id: str, status: str, decision: Any) -> None:
        usage = decision.usage
        if usage is None:
            return
        self._emit(
            run_id,
            "usage",
            status,
            "Provider usage was recorded.",
            {
                "provider": decision.provider,
                "model": decision.model,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "thinking_tokens": usage.thoughts_tokens,
                "cache_read_tokens": usage.cached_input_tokens,
                "uncached_input_tokens": usage.uncached_input_tokens,
                "cache_write_tokens": usage.cache_write_tokens,
                "total_tokens": usage.total_tokens,
            },
        )

    def _emit_displayable_thinking(self, run_id: str, provider: str, items: list[dict[str, Any]]) -> None:
        from ai_agent_platform.integrations.public_reasoning import summary_text
        text = summary_text(provider, items)
        if not text:
            return
        self._emit(
            run_id,
            "thinking_delta",
            "running",
            "Provider reasoning summary updated.",
            {"text": text},
        )
        self._emit(
            run_id,
            "thinking_completed",
            "running",
            "Provider reasoning summary completed.",
            {"text": text},
        )

    @staticmethod
    def _record_usage(state: CogentState, decision: Any) -> None:
        usage = decision.usage
        if usage is None:
            return
        current = state.usage
        for field in ("input_tokens", "output_tokens", "thoughts_tokens", "total_tokens"):
            value = usage.total_tokens if field == "total_tokens" else getattr(usage, field)
            current[field] = int(current.get(field) or 0) + int(value or 0)
        for source, target in (
            ("cached_input_tokens", "cached_input_tokens"),
            ("uncached_input_tokens", "uncached_input_tokens"),
            ("cache_write_tokens", "cache_write_tokens"),
        ):
            value = getattr(usage, source)
            if value is not None:
                current[target] = int(current.get(target) or 0) + int(value)
        current.update(
            provider=decision.provider,
            model=decision.model,
            cache_capability=usage.cache_capability,
        )

    @staticmethod
    def _output_exhausted(decision: Any) -> bool:
        return str(decision.stop_reason or "").casefold() in {
            "length",
            "max_tokens",
            "max_output_tokens",
        }

    @staticmethod
    def _final_answer(messages: list[dict[str, Any]]) -> str:
        parts = []
        for message in reversed(messages):
            if message.get("cogent_continuation"):
                continue
            if message.get("role") != "assistant":
                if parts:
                    break
                continue
            if message.get("tool_calls"):
                break
            content = str(message.get("content") or "").strip()
            if content:
                parts.append(content)
            if not message.get("tool_calls") and len(parts) == 1:
                continue
        return "\n".join(reversed(parts)).strip()

    @staticmethod
    def _canonical_history(history: list[dict[str, str]]) -> list[dict[str, Any]]:
        return [
            {"role": item["role"], "content": item["content"]}
            for item in history
            if item.get("role") in {"user", "assistant"} and item.get("content")
        ]

    @staticmethod
    def _call_dict(call: ToolCall) -> dict[str, Any]:
        return {
            "name": call.name,
            "arguments": dict(call.arguments),
            "call_id": call.call_id,
            "source": call.source,
        }

    @staticmethod
    def _tool_result_from_response(value: dict[str, Any]) -> ToolResult:
        return ToolResult(
            call_id=str(value.get("call_id") or ""),
            name=str(value.get("name") or ""),
            ok=bool(value.get("ok")),
            result=value.get("result"),
            error=value.get("error"),
            provider=str(value.get("provider") or "local"),
            permission_level=str(value.get("permission_level") or "read_only"),
            requires_approval=bool(value.get("requires_approval", False)),
            duration_ms=int(value.get("duration_ms") or 0),
            risk_summary=str(value.get("risk_summary") or ""),
            arguments_summary=dict(value.get("arguments_summary") or {}),
            output_truncated=bool(value.get("output_truncated", False)),
            error_code=value.get("error_code"),
            attempts=int(value.get("attempts") or 1),
            cached=bool(value.get("cached", False)),
            permission_decision=value.get("permission_decision"),
        )

    @staticmethod
    def _approval_key(call: ToolCall) -> str:
        return f"{call.call_id}:{call.name}:{canonical_arguments_hash(call.arguments)}"

    @staticmethod
    def _permission_mode(snapshot: RunContextSnapshot | None) -> str:
        if snapshot is None:
            return "default"
        value = snapshot.metadata.entrypoint_metadata.get("permission_mode")
        return str(value or "default")

    @staticmethod
    def _sandbox_state(snapshot: RunContextSnapshot | None) -> dict[str, Any]:
        metadata = snapshot.metadata.entrypoint_metadata if snapshot is not None else {}
        requested = bool(metadata.get("sandbox_enabled", True))
        backend = create_sandbox()
        available = bool(backend is not None and backend.available())
        return {
            "enabled": requested,
            "available": available,
            "backend": type(backend).__name__ if backend is not None else None,
            "network_enabled": bool(metadata.get("sandbox_network_enabled", False)),
            "status": (
                "enabled"
                if requested and available
                else "unavailable"
                if requested
                else "disabled"
            ),
        }

    @staticmethod
    def _actor(record: AgentRunRecord) -> str:
        if record.context_snapshot is not None:
            return record.context_snapshot.identity.actor_user_id
        return "demo_user"

    @contextmanager
    def _run_lock(self, run_id: str):
        with self._locks_guard:
            lock = self._locks.setdefault(run_id, RLock())
        lease = getattr(self._run_store, 'run_lease', None)
        with lock, lease(run_id) if callable(lease) else nullcontext():
            yield
