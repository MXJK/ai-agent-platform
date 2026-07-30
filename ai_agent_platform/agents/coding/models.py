"""Shared state, records, and protocols for coding-agent execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Protocol, TypedDict

from ai_agent_platform.domain import KnowledgeBaseRecord
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
    actor_user_id: str
    history: list[dict[str, str]]
    focus_files: list[str]
    intent: str
    intent_reason: str
    intent_confidence: float
    planner_source: str
    context_route: str
    route_reason: str
    selected_knowledge_base_ids: list[str]
    knowledge_base_catalog: list[dict[str, Any]]
    catalog_truncated: bool
    rag_context_sources: list["ContextSource"]
    memory_context_sources: list["ContextSource"]
    context_warnings: list[str]
    project_instructions: list["ContextSource"]
    context_sources: list["ContextSource"]
    exploration_round: int
    context_sufficient: bool
    context_budget_exhausted: bool
    context_chars: int
    context_files: list[str]
    seen_context_keys: list[str]
    tool_calls: list[ToolCall]
    analysis_tool_calls: list[ToolCall]
    change_tool_calls: list[ToolCall]
    validation_tool_calls: list[ToolCall]
    repair_tool_calls: list[ToolCall]
    tool_results: list[dict[str, Any]]
    native_tool_messages: list[dict[str, Any]]
    native_tool_round: int
    native_tool_call_count: int
    native_tool_signatures: list[str]
    native_tool_loop_active: bool
    native_tool_answer: str
    native_tool_stop_reason: str
    exploration_results: list[dict[str, Any]]
    validation_results: list[dict[str, Any]]
    validation_history: list[dict[str, Any]]
    llm_input_tokens: int
    llm_output_tokens: int
    llm_thoughts_tokens: int
    artifacts: list[dict[str, Any]]
    changed_files: list[str]
    change_status: str
    change_iteration: int
    answer: str
    trace: list[dict[str, Any]]
    started_at: float
    review_decision: dict[str, Any]
    repair_review_decision: dict[str, Any]
    approval_required_tools: list[dict[str, Any]]
    errors: list[dict[str, Any]]


AgentRoute = Literal["plan_exploration", "compose_answer"]
PlanRoute = Literal["review_tool_plan", "inspect_repository", "compose_answer"]
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
AgentRunStatus = Literal["queued", "running", "waiting_approval", "completed", "failed"]
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
class AgentRunMetrics:
    elapsed_ms: int = 0
    node_count: int = 0
    tool_call_count: int = 0
    successful_tool_call_count: int = 0
    retry_count: int = 0
    error_count: int = 0
    recovered_error_count: int = 0
    change_iteration_count: int = 0
    changed_file_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    thoughts_tokens: int = 0
    total_tokens: int = 0


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
    pending_approval: Optional[dict[str, Any]] = None


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


class AgentRunNotFoundError(Exception):
    """Raised when a requested agent run does not exist."""


class AgentRunInvalidStateError(Exception):
    def __init__(self, run_id: str, status: str) -> None:
        super().__init__(f"agent run {run_id} cannot be resumed from status {status}")
        self.run_id = run_id
        self.status = status


class AgentRunStore(Protocol):
    def save(self, record: AgentRunRecord) -> None:
        ...

    def get(self, run_id: str) -> AgentRunRecord:
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
