"""Backward-compatible AgentRunService name for the Query Kernel."""

from ai_agent_platform.services.query_service import AgentRunExecutionError, QueryService


class AgentRunService(QueryService):
    """Compatibility facade; new entrypoints should depend on QueryService."""


__all__ = ["AgentRunExecutionError", "AgentRunService"]
