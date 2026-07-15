from ai_agent_platform.agents.coding_agent import (
    AgentRunResult,
    CodingAgentRuntime,
    create_coding_tool_registry,
)


BusinessAgentRuntime = CodingAgentRuntime
create_business_tool_registry = create_coding_tool_registry


__all__ = [
    "AgentRunResult",
    "BusinessAgentRuntime",
    "CodingAgentRuntime",
    "create_business_tool_registry",
    "create_coding_tool_registry",
]
