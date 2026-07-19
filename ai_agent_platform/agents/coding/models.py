"""Shared state, records, and protocols for coding-agent execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Protocol, TypedDict

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
    repository_id: str
    history: list[dict[str, str]]
    focus_files: list[str]
    intent: str
    intent_reason: str
    intent_confidence: float
    planner_source: str
    rag_context: list[RetrievedDocument]
    tool_calls: list[ToolCall]
    tool_results: list[dict[str, Any]]
    answer: str
    trace: list[dict[str, Any]]
    started_at: float
    review_decision: dict[str, Any]
    approval_required_tools: list[dict[str, Any]]
    errors: list[dict[str, Any]]


AgentRoute = Literal["retrieve_repository_context", "compose_answer"]
PlanRoute = Literal["review_tool_plan", "inspect_repository"]
ReviewRoute = Literal["inspect_repository", "compose_answer"]
RetrievalRoute = Literal["plan_tools", "handle_error"]
AnswerRoute = Literal["handle_error", "end"]
AgentRunStatus = Literal["queued", "running", "waiting_approval", "completed", "failed"]
MAX_NODE_RETRIES = 2


@dataclass(frozen=True)
class AgentRunMetrics:
    elapsed_ms: int = 0
    node_count: int = 0
    tool_call_count: int = 0
    successful_tool_call_count: int = 0
    retry_count: int = 0
    error_count: int = 0
    recovered_error_count: int = 0


@dataclass(frozen=True)
class AgentRunResult:
    run_id: str
    thread_id: str
    conversation_id: str
    repository_id: str
    status: AgentRunStatus
    checkpoint_id: Optional[str]
    role: str
    objective: str
    intent: str
    answer: str
    graph_engine: str
    rag_context: list[RetrievedDocument]
    tool_calls: list[ToolCall]
    tool_results: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    errors: list[dict[str, Any]] = field(default_factory=list)
    metrics: AgentRunMetrics = field(default_factory=AgentRunMetrics)
    pending_approval: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class AgentRunRecord:
    run_id: str
    thread_id: str
    conversation_id: str
    repository_id: str
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

    def plan_tool_calls(
        self,
        state: CodingAgentState,
        tool_specs: list[ToolSpec],
    ) -> list[ToolCall]:
        ...
