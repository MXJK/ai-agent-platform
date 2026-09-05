"""Shared state, records, and protocols for coding-agent execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Protocol, TypedDict

from ai_agent_platform.domain import RunContextSnapshot
from ai_agent_platform.integrations.tools import ToolCall, ToolSpec


AgentRunStatus = Literal[
    "queued",
    "running",
    "waiting_input",
    "waiting_approval",
    "paused",
    "completed",
    "partial",
    "blocked",
    "cancelled",
    "failed",
]
MAX_NODE_RETRIES = 2


@dataclass(frozen=True)
class ContextSource:
    kind: str
    path: str
    start_line: int | None
    end_line: int | None
    text: str
    reason: str
    content_hash: str
    truncated: bool = False
    knowledge_base_id: str | None = None
    document_id: str | None = None
    score: float | None = None
    memory_id: str | None = None
    memory_kind: str | None = None
    confidence: float | None = None
    last_confirmed_at: str | None = None
    relevance_score: float | None = None
    recency_score: float | None = None
    importance_score: float | None = None


@dataclass(frozen=True)
class EvidencePlan:
    """Validated, bounded instructions for deterministic repository evidence."""

    queries: list[str] = field(default_factory=list)
    candidate_paths: list[str] = field(default_factory=list)
    max_files: int = 8
    max_depth: int = 3
    max_results_per_query: int = 12
    max_chars_per_file: int = 8000
    max_evidence_tokens: int = 12000
    required_evidence: list[str] = field(default_factory=list)
    stop_when: list[str] = field(default_factory=list)


class EvidenceItem(TypedDict):
    path: str
    location: str
    summary: str
    snippet: str
    reason: str
    artifact_id: str


class EvidenceBundle(TypedDict):
    coverage: list[str]
    evidence: list[EvidenceItem]
    unresolved: list[str]
    errors: list[dict[str, Any]]
    raw_result_count: int
    deduplicated_count: int
    truncated: bool


@dataclass(frozen=True)
class AgentRunMetrics:
    elapsed_ms: int = 0
    node_count: int = 0
    tool_call_count: int = 0
    successful_tool_call_count: int = 0
    model_request_count: int = 0
    model_retry_count: int = 0
    retry_count: int = 0
    error_count: int = 0
    recovered_error_count: int = 0
    change_iteration_count: int = 0
    changed_file_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    thoughts_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int | None = None
    uncached_input_tokens: int | None = None
    cache_write_tokens: int | None = None
    prompt_cache_hit_ratio: float | None = None
    stable_prefix_tokens: int = 0
    tool_schema_tokens: int = 0
    visible_tool_count: int = 0
    retained_context_tokens_estimate: int = 0
    provider: str | None = None
    model: str | None = None
    cache_capability: str = "unsupported"


@dataclass(frozen=True)
class AgentChangeSummary:
    status: str = "not_requested"
    iteration_count: int = 0
    changed_files: list[str] = field(default_factory=list)
    validation_command_count: int = 0
    validation_passed: bool = False


@dataclass(frozen=True)
class AgentRunResult:
    run_id: str
    thread_id: str
    conversation_id: str
    workspace_id: str
    status: AgentRunStatus
    checkpoint_id: Optional[str]
    role: str
    objective: str
    intent: str
    answer: str
    graph_engine: str
    tool_calls: list[ToolCall]
    tool_results: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    context_route: str = ""
    selected_knowledge_base_ids: list[str] = field(default_factory=list)
    context_sources: list[ContextSource] = field(default_factory=list)
    terminal_reason: str = ""
    completion_contract: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    metrics: AgentRunMetrics = field(default_factory=AgentRunMetrics)
    change_summary: AgentChangeSummary = field(default_factory=AgentChangeSummary)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    change_set_id: str | None = None
    pending_approval: Optional[dict[str, Any]] = None
    workspace_mode: str = "patch_only"
    execution_root: str | None = None
    branch_name: str | None = None
    worktree_path: str | None = None


@dataclass(frozen=True)
class AgentRunRecord:
    run_id: str
    thread_id: str
    conversation_id: str
    workspace_id: str
    workspace_root: str
    status: AgentRunStatus
    checkpoint_id: Optional[str]
    latest_node: Optional[str]
    next_nodes: list[str]
    trace: list[dict[str, Any]]
    result: Optional[AgentRunResult] = None
    error: Optional[str] = None
    pending_approval: Optional[dict[str, Any]] = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    control_action: Optional[str] = None
    steering_messages: list[str] = field(default_factory=list)
    pending_compaction: Optional[dict[str, Any]] = None
    context_snapshot: RunContextSnapshot | None = None
    runtime_engine: str = "langgraph-v1"
    runtime_state_version: int = 0
    runtime_state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentRunEvent:
    sequence: int
    type: str
    status: str
    node: Optional[str]
    summary: str
    output: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentCheckpoint:
    checkpoint_id: str
    parent_checkpoint_id: Optional[str]
    created_at: Optional[str]
    step: int
    source: str
    next_nodes: list[str]
    latest_node: Optional[str]
    summary: str
    interrupt: Optional[dict[str, Any]]
    changed_files: list[str]
    tool_call_count: int
    can_restore: bool
    is_current: bool
    origin_run_id: Optional[str] = None
    origin_checkpoint_id: Optional[str] = None
    restore_mode: Optional[str] = None


@dataclass(frozen=True)
class AgentToolExecution:
    run_id: str
    call_id: str
    name: str
    arguments_hash: str
    status: str
    response: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class AgentRuntimeSnapshot:
    run_id: str
    snapshot_id: str
    sequence: int
    boundary: str
    runtime_engine: str
    runtime_state_version: int
    state: dict[str, Any]
    created_at: str | None = None


class AgentRunNotFoundError(Exception):
    """Raised when a requested agent run does not exist."""


class AgentRunInvalidStateError(Exception):
    def __init__(self, run_id: str, status: str) -> None:
        super().__init__(f"agent run {run_id} cannot be resumed from status {status}")
        self.run_id = run_id
        self.status = status


class AgentCheckpointNotFoundError(Exception):
    def __init__(self, run_id: str, checkpoint_id: str) -> None:
        super().__init__(
            f"checkpoint {checkpoint_id} does not belong to agent run {run_id}"
        )
        self.run_id = run_id
        self.checkpoint_id = checkpoint_id


class AgentCheckpointRestoreError(RuntimeError):
    """Raised when a durable checkpoint cannot safely start a new path."""


class AgentRunStore(Protocol):
    def save(self, record: AgentRunRecord) -> None:
        ...

    def get(self, run_id: str) -> AgentRunRecord:
        ...

    def get_latest_for_conversation(
        self,
        conversation_id: str,
    ) -> Optional[AgentRunRecord]:
        ...

    def list_recent(self, *, limit: int = 50) -> list[AgentRunRecord]:
        ...

    def list_events(self, run_id: str, *, after: int = 0) -> list[AgentRunEvent]:
        ...

    def append_event(self, run_id: str, event: AgentRunEvent) -> AgentRunEvent:
        ...

    def append_event_once(
        self,
        run_id: str,
        event_key: str,
        event: AgentRunEvent,
    ) -> AgentRunEvent:
        ...

    def get_tool_execution(
        self, run_id: str, call_id: str
    ) -> Optional[AgentToolExecution]:
        ...

    def save_tool_execution(self, execution: AgentToolExecution) -> None:
        ...

    def save_runtime_snapshot(self, snapshot: AgentRuntimeSnapshot) -> None:
        ...

    def list_runtime_snapshots(
        self,
        run_id: str,
        *,
        limit: int = 100,
    ) -> list[AgentRuntimeSnapshot]:
        ...


class LLMCompletionClient(Protocol):
    def complete(self, prompt: str) -> Any:
        ...
