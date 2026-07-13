import unittest

from ai_agent_platform.agent import AgentCommand, RuleBasedAgent


class RuleBasedAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = RuleBasedAgent()

    def test_decides_combat_action_from_chinese_command(self) -> None:
        action = self.agent.decide(AgentCommand(raw_text="攻击附近的敌人"))

        self.assertEqual(action.kind, "combat.attack")
        self.assertGreater(action.confidence, 0.5)

    def test_decides_navigation_action_from_english_command(self) -> None:
        action = self.agent.decide(AgentCommand(raw_text="move to the castle gate"))

        self.assertEqual(action.kind, "navigation.move")

    def test_unknown_for_empty_command(self) -> None:
        action = self.agent.decide(AgentCommand(raw_text="  "))

        self.assertEqual(action.kind, "unknown")
        self.assertEqual(action.confidence, 0.0)


if __name__ == "__main__":
    unittest.main()
