"""Checkpoint lookup and LangGraph resume coordination."""

from __future__ import annotations

from typing import Any, Optional

from langgraph.types import Command

from ai_agent_platform.agents.coding.models import AgentRunRecord, CodingAgentState
from ai_agent_platform.integrations.llm import collect_llm_usage


class CheckpointResumeCoordinator:
    """Keep LangGraph state and resume commands behind the runtime facade."""

    def __init__(
        self,
        *,
        graph: Any,
        recursion_limit: int,
    ) -> None:
        self._graph = graph
        self._recursion_limit = recursion_limit

    def config(self, thread_id: str) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": self._recursion_limit,
        }

    def invoke(
        self,
        initial_state: CodingAgentState,
        config: dict[str, Any],
    ) -> tuple[CodingAgentState, Any]:
        with collect_llm_usage() as usage:
            state = self._graph.invoke(initial_state, config)
        return state, usage

    def resume(
        self,
        record: AgentRunRecord,
        *,
        approved: bool,
        feedback: Optional[str],
        approved_by: str | None = None,
    ) -> tuple[CodingAgentState, Any, dict[str, Any]]:
        config = self.config(record.thread_id)
        with collect_llm_usage() as usage:
            state = self._graph.invoke(
                Command(
                    resume={
                        "approved": approved,
                        "feedback": feedback or "",
                        "message": feedback or "",
                        "action": "continue",
                        "approved_by": approved_by or "",
                    }
                ),
                config,
            )
        return state, usage, config

    def snapshot_for(self, config: dict[str, Any]):
        try:
            return self._graph.get_state(config)
        except Exception:
            return None
