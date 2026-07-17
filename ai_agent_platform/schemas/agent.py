from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from ai_agent_platform.agents.coding_agent import AgentRunRecord, AgentRunResult
from ai_agent_platform.schemas.rag import RAGChunkResponse


class AgentRunRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=8000)
    repository_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    knowledge_base_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    focus_files: list[str] = Field(default_factory=list, max_length=20)

    @property
    def resolved_repository_id(self) -> str:
        return self.repository_id or self.knowledge_base_id or "repo_main"


class AgentRunResumeRequest(BaseModel):
    approved: bool = True
    feedback: Optional[str] = Field(default=None, max_length=4000)


class AgentToolCallResponse(BaseModel):
    name: str
    arguments: dict[str, Any]


class AgentTraceStepResponse(BaseModel):
    step: int
    node: str
    summary: str
    output: dict[str, Any]


class AgentRunResponse(BaseModel):
    run_id: str
    thread_id: str
    conversation_id: str
    repository_id: str
    status: str
    checkpoint_id: Optional[str]
    role: str
    objective: str
    intent: str
    answer: str
    graph_engine: str
    rag_context: list[RAGChunkResponse]
    tool_calls: list[AgentToolCallResponse]
    tool_results: list[dict[str, Any]]
    trace: list[AgentTraceStepResponse]
    errors: list[dict[str, Any]]
    pending_approval: Optional[dict[str, Any]]

    @classmethod
    def from_domain(cls, result: AgentRunResult) -> "AgentRunResponse":
        return cls(
            run_id=result.run_id,
            thread_id=result.thread_id,
            conversation_id=result.conversation_id,
            repository_id=result.repository_id,
            status=result.status,
            checkpoint_id=result.checkpoint_id,
            role=result.role,
            objective=result.objective,
            intent=result.intent,
            answer=result.answer,
            graph_engine=result.graph_engine,
            rag_context=[
                RAGChunkResponse.from_domain(citation)
                for citation in result.rag_context
            ],
            tool_calls=[
                AgentToolCallResponse(
                    name=tool_call.name,
                    arguments=tool_call.arguments,
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
            pending_approval=result.pending_approval,
        )


class AgentRunStatusResponse(BaseModel):
    run_id: str
    thread_id: str
    conversation_id: str
    repository_id: str
    status: str
    checkpoint_id: Optional[str]
    latest_node: Optional[str]
    next_nodes: list[str]
    error: Optional[str]
    pending_approval: Optional[dict[str, Any]]
    errors: list[dict[str, Any]]
    trace: list[AgentTraceStepResponse]
    result: Optional[AgentRunResponse]

    @classmethod
    def from_domain(cls, record: AgentRunRecord) -> "AgentRunStatusResponse":
        return cls(
            run_id=record.run_id,
            thread_id=record.thread_id,
            conversation_id=record.conversation_id,
            repository_id=record.repository_id,
            status=record.status,
            checkpoint_id=record.checkpoint_id,
            latest_node=record.latest_node,
            next_nodes=record.next_nodes,
            error=record.error,
            pending_approval=record.pending_approval,
            errors=record.errors,
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
        )


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
                    "repository_id": record.repository_id,
                },
            )
        ]

        if record.status in {"running", "waiting_approval", "completed", "failed"}:
            events.append(
                AgentRunEventResponse(
                    sequence=len(events) + 1,
                    type="run_started",
                    status="running",
                    node="setup",
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
        elif record.status == "completed":
            answer = record.result.answer if record.result is not None else ""
            events.append(
                AgentRunEventResponse(
                    sequence=len(events) + 1,
                    type="run_completed",
                    status=record.status,
                    node=record.latest_node,
                    summary="Agent run completed.",
                    output={"answer_chars": len(answer)},
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
