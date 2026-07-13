from __future__ import annotations

from ai_agent_platform.agent import AgentCommand, RuleBasedAgent
from ai_agent_platform.domain import AgentDecision


class GameAgentRuntime:
    """First agent runtime boundary for game action selection."""

    def __init__(self, agent: RuleBasedAgent | None = None) -> None:
        self._agent = agent or RuleBasedAgent()

    def decide(self, player_text: str) -> AgentDecision:
        action = self._agent.decide(AgentCommand(raw_text=player_text))
        return AgentDecision(
            kind=action.kind,
            confidence=action.confidence,
            reason=action.reason,
        )
