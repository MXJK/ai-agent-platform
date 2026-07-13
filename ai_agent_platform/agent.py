from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentCommand:
    raw_text: str
    actor_id: str = "player"


@dataclass(frozen=True)
class AgentAction:
    kind: str
    confidence: float
    reason: str


class RuleBasedAgent:
    """A small deterministic agent for learning the agent decision loop."""

    _RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
        (("attack", "fight", "hit", "攻击", "打"), "combat.attack", "combat keyword matched"),
        (("move", "go", "walk", "run", "移动", "去"), "navigation.move", "movement keyword matched"),
        (("talk", "ask", "say", "对话", "问"), "npc.dialogue", "dialogue keyword matched"),
        (("search", "find", "look", "搜索", "找"), "world.search", "search keyword matched"),
    )

    def decide(self, command: AgentCommand) -> AgentAction:
        text = command.raw_text.strip().lower()
        if not text:
            return AgentAction(
                kind="unknown",
                confidence=0.0,
                reason="empty command",
            )

        for keywords, action_kind, reason in self._RULES:
            if any(keyword in text for keyword in keywords):
                return AgentAction(
                    kind=action_kind,
                    confidence=0.8,
                    reason=reason,
                )

        return AgentAction(
            kind="unknown",
            confidence=0.2,
            reason="no rule matched",
        )
