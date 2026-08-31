"""Shared state, records, and protocols for coding-agent execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Protocol, TypedDict

from ai_agent_platform.domain import KnowledgeBaseRecord, RunContextSnapshot
from ai_agent_platform.integrations.rag import RetrievedDocument
from ai_agent_platform.integrations.tools import ToolCall, ToolSpec


CODING_AGENT_ROLE = "研发助手 / 代码仓库问答 Agent"
CODING_AGENT_OBJECTIVE = (
    "围绕代码仓库检索上下文、解释实现、定位文件/符号、规划安全改动，"
    "并输出可复盘的执行轨迹。"
)
VALID_AGENT_INTENTS = {
    "repository_question",
    "repo_navigation",
    "code_explanation",
    "change_planning",
    "bug_investigation",
    "test_strategy",
    "small_talk",
}


class CodingAgentState(TypedDict, total=False):
    run_id: str
    conversation_id: str
    user_input: str
    workspace_id: str
    workspace_root: str
    execution_root: str
    execution_workspace_mode: str
    actor_user_id: str
    workspace_role: str
    authorized_workspace_root: str
    approval_policy: str
    tool_approvals: list[dict[str, str]]
    cwd: str
    additional_directories: list[dict[str, Any]]
    enabled_tools: list[str]
    evaluation_isolated: bool
    evaluation_knowledge_base_ids: list[str]
    instructions_snapshotted: bool
    history: list[dict[str, str]]
    focus_files: list[str]
    intent: str
    intent_reason: str
    intent_confidence: float
    planner_source: str
    task_shape: str
    evidence_contract: dict[str, Any]
    task_tool_profile: list[str]
    explicit_requested_tools: list[str]
    explicit_skill_requested: bool
    stable_prefix_tokens: int
    tool_schema_tokens: int
    visible_tool_count: int
    context_route: str
    route_reason: str
    selected_knowledge_base_ids: list[str]
    knowledge_base_catalog: list[dict[str, Any]]
    catalog_truncated: bool
    rag_context_sources: list["ContextSource"]
    memory_context_sources: list["ContextSource"]
    context_warnings: list[str]
    context_shares: dict[str, int]
    project_instructions: list["ContextSource"]
    context_sources: list["ContextSource"]
    exploration_round: int
    exploration_strategy: str
    context_sufficient: bool
    context_budget_exhausted: bool
    context_stop_reason: str
    context_chars: int
    context_files: list[str]
    seen_context_keys: list[str]
    tool_calls: list[ToolCall]
    analysis_tool_calls: list[ToolCall]
    change_tool_calls: list[ToolCall]
    validation_tool_calls: list[ToolCall]
    repair_tool_calls: list[ToolCall]
    repair_approval_tool_calls: list[ToolCall]
    tool_results: list[dict[str, Any]]
    native_tool_messages: list[dict[str, Any]]
    native_tool_round: int
    native_tool_call_count: int
    task_model_request_count: int
    native_pending_tool_calls: list[ToolCall]
    native_parallel_read_batch: bool
    native_tool_signatures: list[str]
    native_tool_loop_active: bool
    native_tool_answer: str
    native_tool_stop_reason: str
    native_soft_limit_warned: bool
    native_no_progress_rounds: int
    evidence_coverage: list[str]
    evidence_keys: list[str]
    new_evidence_count: int
    coverage_delta: int
    unresolved_requirements: list[str]
    duplicate_tool_call_count: int
    evidence_extension_rounds: int
    evidence_rounds_completed: int
    evidence_contract_satisfied: bool
    native_unfulfilled_change_rounds: int
    native_consecutive_failures: int
    native_context_compactions: int
    native_auto_compactions: int
    native_context_chars: int
    native_context_reduction_stages: list[dict[str, Any]]
    native_last_model_request_at: float
    native_snip_candidates: list[dict[str, Any]]
    native_compaction_failures: int
    native_model_compaction_disabled: bool
    native_artifacts_collected: bool
    terminal_status: str
    terminal_reason: str
    exploration_results: list[dict[str, Any]]
    validation_results: list[dict[str, Any]]
    validation_history: list[dict[str, Any]]
    llm_input_tokens: int
    llm_output_tokens: int
    llm_thoughts_tokens: int
    llm_request_count: int
    llm_retry_count: int
    llm_cached_input_tokens: int | None
    llm_uncached_input_tokens: int | None
    llm_cache_write_tokens: int | None
    llm_provider_models: list[tuple[str, str, str]]
    llm_provider_total_tokens: int
    artifacts: list[dict[str, Any]]
    run_artifact_read_enabled: bool
    changed_files: list[str]
    change_status: str
    change_iteration: int
    change_set_id: str
    answer: str
    trace: list[dict[str, Any]]
    started_at: float
    review_decision: dict[str, Any]
    repair_review_decision: dict[str, Any]
    approval_required_tools: list[dict[str, Any]]
    errors: list[dict[str, Any]]


AgentRoute = Literal["plan_exploration", "compose_answer"]
PlanRoute = Literal[
    "plan_tools",
    "review_tool_plan",
    "inspect_repository",
    "collect_artifacts",
    "compose_answer",
]
ReviewRoute = Literal["inspect_repository", "compose_answer"]
ContextRoute = Literal["plan_exploration", "merge_evidence"]
AnswerRoute = Literal["handle_error", "end"]
InspectionRoute = Literal[
    "plan_tools",
    "execute_changes",
    "validate_changes",
    "collect_artifacts",
    "compose_answer",
]
ValidationRoute = Literal["review_repair_plan", "collect_artifacts"]
RepairReviewRoute = Literal["execute_changes", "collect_artifacts"]
ChangeExecutionRoute = Literal["validate_changes", "collect_artifacts"]
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
    context_route: str
    selected_knowledge_base_ids: list[str]
    answer: str
    graph_engine: str
    context_sources: list[ContextSource]
    tool_calls: list[ToolCall]
    tool_results: list[dict[str, Any]]
    trace: list[dict[str, Any]]
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


class LLMCompletionClient(Protocol):
    def complete(self, prompt: str) -> Any:
        ...


class AgentPlanner(Protocol):
    def classify_intent(self, user_input: str) -> dict[str, Any]:
        ...

    def classify_request(
        self,
        user_input: str,
        knowledge_bases: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ...

    def plan_tool_calls(
        self,
        state: CodingAgentState,
        tool_specs: list[ToolSpec],
    ) -> list[ToolCall]:
        ...

    def plan_repair_tool_calls(
        self,
        state: CodingAgentState,
        tool_specs: list[ToolSpec],
    ) -> list[ToolCall]:
        ...

    def compose_answer(self, state: CodingAgentState) -> str:
        ...


class KnowledgeContextProvider(Protocol):
    def list(self) -> list[KnowledgeBaseRecord]:
        ...

    def search(
        self,
        *,
        knowledge_base_id: str,
        query: str,
        limit: int,
        recall_limit: int | None,
    ) -> list[RetrievedDocument]:
        ...


class ProjectMemoryContextProvider(Protocol):
    def retrieve(
        self,
        *,
        workspace_id: str,
        actor_user_id: str,
        query: str,
    ) -> list[Any]:
        ...
