from pathlib import Path
import unittest

from ai_agent_platform.agents.coding_agent import CodingAgentRuntime


ROOT = Path(__file__).parents[1]


class AgentLoopBoundaryTests(unittest.TestCase):
    def test_graph_topology_remains_the_characterized_contract(self) -> None:
        graph = CodingAgentRuntime()._graph.get_graph()

        self.assertEqual(
            set(graph.nodes),
            {
                "__start__",
                "__end__",
                "setup_workspace",
                "load_project_instructions",
                "classify_request",
                "decide_context_source",
                "retrieve_project_memory",
                "retrieve_knowledge",
                "plan_exploration",
                "execute_exploration",
                "assess_context",
                "merge_evidence",
                "plan_tools",
                "review_tool_plan",
                "inspect_repository",
                "execute_changes",
                "validate_changes",
                "review_repair_plan",
                "collect_artifacts",
                "compose_answer",
                "compose_error_answer",
            },
        )
        self.assertEqual(
            {(edge.source, edge.target, edge.conditional) for edge in graph.edges},
            _EXPECTED_EDGES,
        )

    def test_outer_layers_do_not_import_langgraph_state(self) -> None:
        paths = [
            ROOT / "ai_agent_platform" / directory
            for directory in ("api", "services", "repositories", "workers")
        ]
        violations = []
        for path in paths:
            for source in path.rglob("*.py"):
                text = source.read_text(encoding="utf-8")
                if "CodingAgentState" in text:
                    violations.append(str(source.relative_to(ROOT)))

        self.assertEqual(violations, [])

    def test_runtime_remains_a_compatibility_facade(self) -> None:
        runtime_source = (
            ROOT / "ai_agent_platform" / "agents" / "coding_agent.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("StateGraph(", runtime_source)
        self.assertNotIn("self._graph.invoke(", runtime_source)
        self.assertNotIn("def _plan_tools(", runtime_source)
        self.assertNotIn("def _retrieve_knowledge(", runtime_source)
        self.assertNotIn("def _finish_invocation(", runtime_source)

    def test_graph_nodes_cannot_list_or_select_from_the_global_registry(self) -> None:
        violations = []
        coding_root = ROOT / "ai_agent_platform" / "agents" / "coding"
        for source in coding_root.glob("*.py"):
            if source.name == "tool_access.py":
                continue
            text = source.read_text(encoding="utf-8")
            for forbidden in (
                "self._tools.list_specs",
                "self._tools.select",
                "runtime._tools.list_specs",
                "runtime._tools.select",
            ):
                if forbidden in text:
                    violations.append(f"{source.name}:{forbidden}")

        self.assertEqual(violations, [])


_EXPECTED_EDGES = {
    ("__start__", "setup_workspace", False),
    ("setup_workspace", "load_project_instructions", False),
    ("load_project_instructions", "classify_request", False),
    ("classify_request", "decide_context_source", False),
    ("decide_context_source", "retrieve_project_memory", False),
    ("retrieve_project_memory", "merge_evidence", True),
    ("retrieve_project_memory", "plan_exploration", True),
    ("retrieve_project_memory", "retrieve_knowledge", True),
    ("retrieve_knowledge", "merge_evidence", True),
    ("retrieve_knowledge", "plan_exploration", True),
    ("plan_exploration", "execute_exploration", False),
    ("execute_exploration", "assess_context", False),
    ("assess_context", "merge_evidence", True),
    ("assess_context", "plan_exploration", True),
    ("merge_evidence", "compose_answer", True),
    ("merge_evidence", "plan_tools", True),
    ("plan_tools", "plan_tools", True),
    ("plan_tools", "review_tool_plan", True),
    ("plan_tools", "inspect_repository", True),
    ("plan_tools", "collect_artifacts", True),
    ("plan_tools", "compose_answer", True),
    ("review_tool_plan", "inspect_repository", True),
    ("review_tool_plan", "compose_answer", True),
    ("inspect_repository", "plan_tools", True),
    ("inspect_repository", "execute_changes", True),
    ("inspect_repository", "validate_changes", True),
    ("inspect_repository", "collect_artifacts", True),
    ("inspect_repository", "compose_answer", True),
    ("execute_changes", "validate_changes", True),
    ("execute_changes", "collect_artifacts", True),
    ("validate_changes", "review_repair_plan", True),
    ("validate_changes", "collect_artifacts", True),
    ("review_repair_plan", "execute_changes", True),
    ("review_repair_plan", "collect_artifacts", True),
    ("collect_artifacts", "plan_tools", True),
    ("collect_artifacts", "compose_answer", True),
    ("compose_answer", "__end__", False),
}


if __name__ == "__main__":
    unittest.main()
