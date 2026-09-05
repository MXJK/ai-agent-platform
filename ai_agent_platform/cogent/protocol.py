from __future__ import annotations

from typing import Any, Protocol

from ai_agent_platform.agents.coding.models import AgentCheckpoint, AgentRunEvent, AgentRunRecord, AgentRunResult
from ai_agent_platform.domain import RunContextSnapshot


class AgentRuntime(Protocol):
    def create_queued_record(
        self, *, conversation_id: str, workspace_id: str, workspace_root: str,
        run_id: str, context_snapshot: RunContextSnapshot | None = None,
    ) -> AgentRunRecord: ...

    def create_queued_run(
        self,
        *,
        conversation_id: str,
        workspace_id: str,
        workspace_root: str,
        run_id: str | None = None,
        context_snapshot: RunContextSnapshot | None = None,
    ) -> AgentRunRecord: ...

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
    ) -> AgentRunResult: ...

    def resume(
        self,
        *,
        run_id: str,
        approved: bool,
        feedback: str | None = None,
        input_response: dict[str, Any] | None = None,
        approved_by: str | None = None,
    ) -> AgentRunResult: ...

    def get_run(self, run_id: str) -> AgentRunRecord: ...

    def get_latest_run(self, conversation_id: str) -> AgentRunRecord | None: ...

    def list_recent_runs(self, *, limit: int = 50) -> list[AgentRunRecord]: ...

    def list_events(self, run_id: str, *, after: int = 0) -> list[AgentRunEvent]: ...

    def restore_record(self, record: AgentRunRecord) -> None: ...

    def mark_resume_queued(self, run_id: str) -> AgentRunRecord: ...

    def recover(self, run_id: str) -> AgentRunResult: ...

    def request_control(self, *, run_id: str, action: str, message: str = '') -> AgentRunRecord: ...

    def request_compaction(self, *, run_id: str, instruction: str = '') -> AgentRunRecord: ...

    def list_checkpoints(self, run_id: str, *, limit: int = 100) -> list[AgentCheckpoint]: ...

    def prepare_checkpoint_branch(self, **kwargs: Any) -> AgentRunRecord: ...

    def run_from_checkpoint(self, run_id: str) -> AgentRunResult: ...

    def mark_run_failed(self, *, run_id: str, error: str, node: str = 'runtime',
                        attempt: int = 1, max_attempts: int = 1) -> AgentRunRecord: ...

    def record_change_set_event(self, *, run_id: str, **payload: Any) -> None: ...
