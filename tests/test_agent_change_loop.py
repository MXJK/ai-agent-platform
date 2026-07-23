from pathlib import Path
import shlex
import sys
from tempfile import TemporaryDirectory
import unittest

from ai_agent_platform.agents.coding.models import CodingAgentState
from ai_agent_platform.agents.coding_agent import (
    CodingAgentRuntime,
    create_coding_tool_registry,
)
from ai_agent_platform.integrations.tools import ToolCall, ToolSpec
from ai_agent_platform.schemas import AgentRunResponse


class SuccessfulChangePlanner:
    def __init__(self, command: str) -> None:
        self.command = command

    def classify_intent(self, user_input: str) -> dict[str, object]:
        return {
            "intent": "change_planning",
            "reason": "test planner requests a sandbox change",
            "confidence": 1.0,
            "source": "test",
        }

    def plan_tool_calls(
        self,
        state: CodingAgentState,
        tool_specs: list[ToolSpec],
    ) -> list[ToolCall]:
        return [
            ToolCall(
                name="sandbox.write_file",
                arguments={"path": "app.py", "content": "value = 'new'\n"},
            ),
            ToolCall(
                name="sandbox.run_command",
                arguments={"command": self.command},
            ),
        ]

    def plan_repair_tool_calls(
        self,
        state: CodingAgentState,
        tool_specs: list[ToolSpec],
    ) -> list[ToolCall]:
        return []


class RepairingChangePlanner(SuccessfulChangePlanner):
    def plan_tool_calls(
        self,
        state: CodingAgentState,
        tool_specs: list[ToolSpec],
    ) -> list[ToolCall]:
        return [
            ToolCall(
                name="sandbox.write_file",
                arguments={"path": "app.py", "content": "def broken(:\n"},
            ),
            ToolCall(
                name="sandbox.run_command",
                arguments={"command": self.command},
            ),
        ]

    def plan_repair_tool_calls(
        self,
        state: CodingAgentState,
        tool_specs: list[ToolSpec],
    ) -> list[ToolCall]:
        return [
            ToolCall(
                name="sandbox.write_file",
                arguments={"path": "app.py", "content": "value = 'repaired'\n"},
                source="test_repair",
            )
        ]


class AgentChangeLoopTests(unittest.TestCase):
    def test_executes_validates_and_collects_diff_without_touching_source(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_file = root / "app.py"
            source_file.write_text("value = 'old'\n", encoding="utf-8")
            runtime = self._runtime(
                root,
                SuccessfulChangePlanner(_compile_command()),
            )

            waiting = runtime.run(
                conversation_id="sess_1",
                user_input="修改 app.py 并验证",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
            )
            result = runtime.resume(run_id=waiting.run_id, approved=True)

            self.assertEqual(waiting.status, "waiting_approval")
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.change_summary.status, "validated")
            self.assertEqual(result.change_summary.iteration_count, 1)
            self.assertEqual(result.change_summary.changed_files, ["app.py"])
            self.assertTrue(result.change_summary.validation_passed)
            self.assertEqual(source_file.read_text(encoding="utf-8"), "value = 'old'\n")
            self.assertEqual(
                [artifact["type"] for artifact in result.artifacts],
                ["test_report", "code_diff"],
            )
            self.assertIn("+value = 'new'", result.artifacts[1]["diff"])
            response = AgentRunResponse.from_domain(result).model_dump()
            self.assertEqual(response["change_summary"]["status"], "validated")
            self.assertEqual(response["artifacts"][1]["type"], "code_diff")
            self.assertEqual(
                [step["node"] for step in result.trace],
                [
                    "setup_workspace",
                    "load_project_instructions",
                    "classify_request",
                    "plan_exploration",
                    "execute_exploration",
                    "assess_context",
                    "plan_tools",
                    "review_tool_plan",
                    "inspect_repository",
                    "execute_changes",
                    "validate_changes",
                    "collect_artifacts",
                    "compose_answer",
                ],
            )

    def test_failed_validation_requires_repair_approval_and_retries_once(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("value = 'old'\n", encoding="utf-8")
            runtime = self._runtime(
                root,
                RepairingChangePlanner(_compile_command()),
            )

            initial_wait = runtime.run(
                conversation_id="sess_2",
                user_input="先制造失败再修复 app.py",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
            )
            repair_wait = runtime.resume(
                run_id=initial_wait.run_id,
                approved=True,
            )
            repair_record = runtime.get_run(initial_wait.run_id)
            result = runtime.resume(run_id=initial_wait.run_id, approved=True)

            self.assertEqual(repair_wait.status, "waiting_approval")
            self.assertEqual(
                repair_wait.pending_approval["type"],
                "repair_plan_review",
            )
            self.assertEqual(
                repair_wait.pending_approval["planned_tools"],
                ["sandbox.write_file"],
            )
            self.assertEqual(repair_record.latest_node, "review_repair_plan")
            self.assertEqual(repair_record.next_nodes, ["review_repair_plan"])
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.change_summary.status, "validated")
            self.assertEqual(result.change_summary.iteration_count, 2)
            self.assertEqual(result.metrics.change_iteration_count, 2)
            self.assertEqual(result.metrics.changed_file_count, 1)
            self.assertIn("+value = 'repaired'", result.artifacts[1]["diff"])
            validation_steps = [
                step for step in result.trace if step["node"] == "validate_changes"
            ]
            self.assertEqual(len(validation_steps), 2)
            self.assertFalse(validation_steps[0]["output"]["passed"])
            self.assertTrue(validation_steps[1]["output"]["passed"])

    def test_rejected_repair_stops_without_second_mutation(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("value = 'old'\n", encoding="utf-8")
            runtime = self._runtime(
                root,
                RepairingChangePlanner(_compile_command()),
            )

            initial_wait = runtime.run(
                conversation_id="sess_3",
                user_input="验证失败后拒绝修复",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
            )
            repair_wait = runtime.resume(run_id=initial_wait.run_id, approved=True)
            result = runtime.resume(
                run_id=repair_wait.run_id,
                approved=False,
                feedback="不要继续修改",
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.change_summary.status, "repair_rejected")
            self.assertEqual(result.change_summary.iteration_count, 1)
            self.assertFalse(result.change_summary.validation_passed)
            self.assertIn("不要继续修改", result.answer)
            self.assertEqual(
                sum(
                    1
                    for item in result.tool_results
                    if item["name"] == "sandbox.write_file"
                ),
                1,
            )

    @staticmethod
    def _runtime(root: Path, planner: object) -> CodingAgentRuntime:
        return CodingAgentRuntime(
            tool_registry=create_coding_tool_registry(),
            planner=planner,
        )


def _compile_command() -> str:
    validation = (
        "compile(open('app.py', encoding='utf-8').read(), "
        "'app.py', 'exec')"
    )
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(validation)}"


if __name__ == "__main__":
    unittest.main()
