from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_agent_platform.agents.coding_agent import AgentRunRecord, AgentRunResult
from ai_agent_platform.agents.coding.models import (
    AgentCheckpoint,
    AgentRunEvent,
    ContextSource,
)
from ai_agent_platform.schemas.chat import (
    LLMProviderName,
    LLMRoutingPolicy,
    LLMThinkingLevel,
)


class AgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=8000)
    workspace_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    focus_files: list[str] = Field(default_factory=list, max_length=20)
    cwd: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    additional_workspace_ids: list[str] = Field(
        default_factory=list,
        max_length=10,
    )
    provider: Optional[LLMProviderName] = None
    model: Optional[str] = Field(default=None, min_length=1, max_length=128)
    thinking_level: Optional[LLMThinkingLevel] = None
    routing_policy: Optional[LLMRoutingPolicy] = None
    skill_name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    skill_arguments: list[str] = Field(default_factory=list, max_length=32)
    preferred_tool_name: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )

    @field_validator("additional_workspace_ids")
    @classmethod
    def validate_additional_workspace_ids(cls, values: list[str]) -> list[str]:
        for value in values:
            if len(value) > 128 or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]*", value
            ) is None:
                raise ValueError(
                    "additional directories must use registered Workspace IDs"
                )
        return values

    @field_validator("skill_arguments")
    @classmethod
    def validate_skill_arguments(cls, values: list[str]) -> list[str]:
        if any(len(value) > 1000 for value in values):
            raise ValueError("skill arguments must not exceed 1000 characters")
        return values


class ComposerSkillCommandResponse(BaseModel):
    name: str
    description: str
    usage: Optional[str]
    aliases: list[str]
    skill_name: str
    skill_qualified_name: str
    source: str


class ComposerToolResponse(BaseModel):
    name: str
    description: str
    provider: str
    server_name: str
    permission_level: str
    requires_approval: bool
    input_schema: dict[str, Any]


class ComposerCapabilitiesResponse(BaseModel):
    conversation_id: str
    workspace_id: str
    skill_commands: list[ComposerSkillCommandResponse]
    mcp_tools: list[ComposerToolResponse]
    diagnostics: list[str]


class AgentRunResumeRequest(BaseModel):
    approved: bool = True
    feedback: Optional[str] = Field(default=None, max_length=4000)


class AgentRunControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(default="", max_length=4000)


class AgentCheckpointRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["rollback", "fork"]
    message: str = Field(default="", max_length=4000)


class AgentToolCallResponse(BaseModel):
    call_id: str
    name: str
    arguments: dict[str, Any]
    source: str


class AgentTraceStepResponse(BaseModel):
    step: int
    node: str
    summary: str
    output: dict[str, Any]


class AgentRunMetricsResponse(BaseModel):
    elapsed_ms: int
    node_count: int
    tool_call_count: int
    successful_tool_call_count: int
    retry_count: int
    error_count: int
    recovered_error_count: int
    change_iteration_count: int
    changed_file_count: int
    input_tokens: int
    output_tokens: int
    thoughts_tokens: int
    total_tokens: int


class AgentChangeSummaryResponse(BaseModel):
    status: str
    iteration_count: int
    changed_files: list[str]
    validation_command_count: int
    validation_passed: bool


class ContextSourceResponse(BaseModel):
    kind: str
    path: str
    start_line: Optional[int]
    end_line: Optional[int]
    text: str
    reason: str
    content_hash: str
    truncated: bool
    knowledge_base_id: Optional[str] = None
    document_id: Optional[str] = None
    score: Optional[float] = None
    memory_id: Optional[str] = None
    memory_kind: Optional[str] = None
    confidence: Optional[float] = None
    last_confirmed_at: Optional[str] = None
    relevance_score: Optional[float] = None
    recency_score: Optional[float] = None
    importance_score: Optional[float] = None

    @classmethod
    def from_domain(cls, source: ContextSource) -> "ContextSourceResponse":
        return cls(**source.__dict__)


class AgentRunResponse(BaseModel):
    run_id: str
    thread_id: str
    conversation_id: str
    workspace_id: str
    status: str
    checkpoint_id: Optional[str]
    role: str
    objective: str
    intent: str
    context_route: str
    selected_knowledge_base_ids: list[str]
    answer: str
    graph_engine: str
    context_sources: list[ContextSourceResponse]
    tool_calls: list[AgentToolCallResponse]
    tool_results: list[dict[str, Any]]
    trace: list[AgentTraceStepResponse]
    errors: list[dict[str, Any]]
    metrics: AgentRunMetricsResponse
    change_summary: AgentChangeSummaryResponse
    artifacts: list[dict[str, Any]]
    change_set_id: Optional[str]
    pending_approval: Optional[dict[str, Any]]
    workspace_mode: str
    execution_root: Optional[str]
    branch_name: Optional[str]
    worktree_path: Optional[str]

    @classmethod
    def from_domain(cls, result: AgentRunResult) -> "AgentRunResponse":
        return cls(
            run_id=result.run_id,
            thread_id=result.thread_id,
            conversation_id=result.conversation_id,
            workspace_id=result.workspace_id,
            status=result.status,
            checkpoint_id=result.checkpoint_id,
            role=result.role,
            objective=result.objective,
            intent=result.intent,
            context_route=result.context_route,
            selected_knowledge_base_ids=result.selected_knowledge_base_ids,
            answer=result.answer,
            graph_engine=result.graph_engine,
            context_sources=[
                ContextSourceResponse.from_domain(source)
                for source in result.context_sources
            ],
            tool_calls=[
                AgentToolCallResponse(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    arguments=tool_call.arguments,
                    source=tool_call.source,
                )
                for tool_call in result.tool_calls
            ],
            tool_results=result.tool_results,
            trace=[
                AgentTraceStepResponse(
                    step=item["step"],
                    node=item["node"],
                    summary=item["summary"],
                    output=item["output"],
                )
                for item in result.trace
            ],
            errors=result.errors,
            metrics=AgentRunMetricsResponse(
                elapsed_ms=result.metrics.elapsed_ms,
                node_count=result.metrics.node_count,
                tool_call_count=result.metrics.tool_call_count,
                successful_tool_call_count=(
                    result.metrics.successful_tool_call_count
                ),
                retry_count=result.metrics.retry_count,
                error_count=result.metrics.error_count,
                recovered_error_count=result.metrics.recovered_error_count,
                change_iteration_count=result.metrics.change_iteration_count,
                changed_file_count=result.metrics.changed_file_count,
                input_tokens=result.metrics.input_tokens,
                output_tokens=result.metrics.output_tokens,
                thoughts_tokens=result.metrics.thoughts_tokens,
                total_tokens=result.metrics.total_tokens,
            ),
            change_summary=AgentChangeSummaryResponse(
                status=result.change_summary.status,
                iteration_count=result.change_summary.iteration_count,
                changed_files=result.change_summary.changed_files,
                validation_command_count=(
                    result.change_summary.validation_command_count
                ),
                validation_passed=result.change_summary.validation_passed,
            ),
            artifacts=result.artifacts,
            change_set_id=result.change_set_id,
            pending_approval=result.pending_approval,
            workspace_mode=result.workspace_mode,
            execution_root=result.execution_root,
            branch_name=result.branch_name,
            worktree_path=result.worktree_path,
        )


class AgentRunStatusResponse(BaseModel):
    run_id: str
    thread_id: str
    conversation_id: str
    workspace_id: str
    status: str
    checkpoint_id: Optional[str]
    latest_node: Optional[str]
    next_nodes: list[str]
    error: Optional[str]
    pending_approval: Optional[dict[str, Any]]
    errors: list[dict[str, Any]]
    control_action: Optional[str]
    steering_message_count: int
    trace: list[AgentTraceStepResponse]
    result: Optional[AgentRunResponse]
    workspace_mode: str
    execution_root: Optional[str]
    branch_name: Optional[str]
    worktree_path: Optional[str]

    @classmethod
    def from_domain(cls, record: AgentRunRecord) -> "AgentRunStatusResponse":
        execution = (
            record.context_snapshot.execution_workspace
            if record.context_snapshot is not None
            else None
        )
        return cls(
            run_id=record.run_id,
            thread_id=record.thread_id,
            conversation_id=record.conversation_id,
            workspace_id=record.workspace_id,
            status=record.status,
            checkpoint_id=record.checkpoint_id,
            latest_node=record.latest_node,
            next_nodes=record.next_nodes,
            error=record.error,
            pending_approval=record.pending_approval,
            errors=record.errors,
            control_action=record.control_action,
            steering_message_count=len(record.steering_messages),
            trace=[
                AgentTraceStepResponse(
                    step=item["step"],
                    node=item["node"],
                    summary=item["summary"],
                    output=item["output"],
                )
                for item in record.trace
            ],
            result=(
                AgentRunResponse.from_domain(record.result)
                if record.result is not None
                else None
            ),
            workspace_mode=(execution.mode if execution is not None else "patch_only"),
            execution_root=(
                execution.execution_root if execution is not None else None
            ),
            branch_name=(execution.branch_name if execution is not None else None),
            worktree_path=(execution.worktree_path if execution is not None else None),
        )


class AgentCheckpointResponse(BaseModel):
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
    origin_run_id: Optional[str]
    origin_checkpoint_id: Optional[str]
    restore_mode: Optional[str]

    @classmethod
    def from_domain(cls, checkpoint: AgentCheckpoint) -> "AgentCheckpointResponse":
        return cls(**checkpoint.__dict__)


class AgentCheckpointsResponse(BaseModel):
    run_id: str
    current_checkpoint_id: Optional[str]
    checkpoints: list[AgentCheckpointResponse]


class AgentCheckpointRestoreResponse(BaseModel):
    mode: Literal["rollback", "fork"]
    source_run_id: str
    source_checkpoint_id: str
    conversation_id: str
    forked_conversation_id: Optional[str]
    run: AgentRunStatusResponse


class AgentRunEventResponse(BaseModel):
    sequence: int
    type: str
    status: str
    node: Optional[str]
    summary: str
    output: dict[str, Any]


class AgentRunEventsResponse(BaseModel):
    run_id: str
    events: list[AgentRunEventResponse]

    @classmethod
    def from_events(
        cls,
        run_id: str,
        events: list[AgentRunEvent],
    ) -> "AgentRunEventsResponse":
        return cls(
            run_id=run_id,
            events=[
                AgentRunEventResponse(
                    sequence=event.sequence,
                    type=event.type,
                    status=event.status,
                    node=event.node,
                    summary=event.summary,
                    output=event.output,
                )
                for event in events
            ],
        )

    @classmethod
    def from_domain(cls, record: AgentRunRecord) -> "AgentRunEventsResponse":
        events: list[AgentRunEventResponse] = [
            AgentRunEventResponse(
                sequence=1,
                type="run_queued",
                status="queued",
                node=None,
                summary="Agent run accepted and queued for background execution.",
                output={
                    "run_id": record.run_id,
                    "conversation_id": record.conversation_id,
                    "workspace_id": record.workspace_id,
                },
            )
        ]

        if record.status != "queued":
            events.append(
                AgentRunEventResponse(
                    sequence=len(events) + 1,
                    type="run_started",
                    status="running",
                    node="setup_workspace",
                    summary="Background worker started executing the Agent graph.",
                    output={"thread_id": record.thread_id},
                )
            )

        for item in record.trace:
            events.append(
                AgentRunEventResponse(
                    sequence=len(events) + 1,
                    type="node_completed",
                    status="running",
                    node=item["node"],
                    summary=item["summary"],
                    output=item["output"],
                )
            )

        if record.status == "waiting_approval":
            events.append(
                AgentRunEventResponse(
                    sequence=len(events) + 1,
                    type="approval_required",
                    status=record.status,
                    node=record.latest_node,
                    summary="Agent run is paused until the planned tools are reviewed.",
                    output=record.pending_approval or {},
                )
            )
        elif record.status in {"completed", "partial", "blocked", "cancelled"}:
            answer = record.result.answer if record.result is not None else ""
            change_summary = (
                record.result.change_summary if record.result is not None else None
            )
            events.append(
                AgentRunEventResponse(
                    sequence=len(events) + 1,
                    type=f"run_{record.status}",
                    status=record.status,
                    node=record.latest_node,
                    summary=f"Agent run ended with status {record.status}.",
                    output={
                        "answer_chars": len(answer),
                        "change_status": (
                            change_summary.status if change_summary is not None else None
                        ),
                        "changed_files": (
                            change_summary.changed_files
                            if change_summary is not None
                            else []
                        ),
                    },
                )
            )
        elif record.status == "failed":
            events.append(
                AgentRunEventResponse(
                    sequence=len(events) + 1,
                    type="run_failed",
                    status=record.status,
                    node=record.latest_node,
                    summary="Agent run failed.",
                    output={
                        "error": record.error,
                        "errors": record.errors,
                    },
                )
            )

        return cls(run_id=record.run_id, events=events)
