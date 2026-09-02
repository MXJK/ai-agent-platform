from pathlib import Path
import shlex
import sys
from tempfile import TemporaryDirectory
import unittest

from ai_agent_platform.agents.coding.models import CodingAgentState
from ai_agent_platform.agents.coding.task_shaping import (
    build_evidence_contract,
    change_validation_state,
    update_evidence_progress,
)
from ai_agent_platform.agents.coding_agent import CodingAgentRuntime
from ai_agent_platform.integrations.llm import LLMToolDecision
from ai_agent_platform.integrations.tools import ToolCall, ToolSpec
from ai_agent_platform.schemas import AgentRunEventsResponse, AgentRunResponse


class _ValidationGatePlanner:
    uses_native_tool_calling = True
    single_tool_per_turn = True

    def __init__(self, command: str, *, mode: str) -> None:
        self.command = command
        self.mode = mode
        self.decisions = 0
        self.final_reason = ""
        self.saw_validation_reminder = False

    def classify_intent(self, user_input: str) -> dict[str, object]:
        del user_input
        return {
            "intent": "change_planning",
            "reason": "validation completion gate regression",
            "confidence": 1.0,
            "source": "test",
        }

    def plan_tool_calls(
        self,
        state: CodingAgentState,
        tool_specs: list[ToolSpec],
    ) -> list[ToolCall]:
        del state, tool_specs
        return []

    def plan_repair_tool_calls(self, state, tool_specs):
        del state, tool_specs
        return []

    def compose_answer(self, state: CodingAgentState) -> str:
        del state
        return "fallback"

    def finalize_tool_session(self, messages, *, reason):
        del messages
        self.final_reason = reason
        return self._decision(text=f"Finalized after {reason}.")

    def decide_tool_calls(self, messages, tool_specs):
        del tool_specs
        self.decisions += 1
        if self.decisions == 1:
            return self._decision(
                ToolCall(
                    call_id="gate-write",
                    name="sandbox.write_file",
                    arguments={"path": "app.py", "content": "value = 2\n"},
                    source="validation_gate_test",
                )
            )
        self.saw_validation_reminder = self.saw_validation_reminder or any(
            message.get("role") == "system"
            and "post-change validation" in str(message.get("content") or "")
            for message in messages
        )
        if self.mode == "missing":
            return self._decision(text="The change is done.")
        if self.mode == "checkpoint" and self.decisions == 2:
            return self._decision(
                ToolCall(
                    call_id="gate-input",
                    name="agent.request_user_input",
                    arguments={
                        "question": "Continue with post-change validation?",
                        "context": "checkpoint validation gate regression",
                    },
                    source="validation_gate_test",
                )
            )
        return self._decision(
            ToolCall(
                call_id="gate-validation",
                name="sandbox.run_command",
                arguments={"command": self.command},
                source="validation_gate_test",
            )
        )

    @staticmethod
    def _decision(
        call: ToolCall | None = None,
        *,
        text: str = "",
    ) -> LLMToolDecision:
        return LLMToolDecision(
            text=text,
            tool_calls=[call] if call is not None else [],
            model="scripted",
            provider="test",
            stop_reason="tool_use" if call is not None else "end_turn",
        )


def _validation_command(*, passes: bool) -> str:
    source = (
        "from pathlib import Path; "
        "assert Path('app.py').read_text(encoding='utf-8') == 'value = 2\\n'"
        if passes
        else "raise SystemExit(7)"
    )
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"


class ChangeValidationGateTests(unittest.TestCase):
    def test_non_change_task_shapes_do_not_activate_validation_gate(self) -> None:
        for task_shape in ("overview", "targeted_read", "investigation", "broad_review"):
            with self.subTest(task_shape=task_shape):
                self.assertEqual(
                    change_validation_state(
                        {
                            "task_shape": task_shape,
                            "changed_files": ["app.py"],
                            "validation_results": [],
                        }
                    ),
                    "not_required",
                )

    def test_successful_write_does_not_cover_current_behavior(self) -> None:
        contract = build_evidence_contract("bounded_change")
        state: CodingAgentState = {
            "task_shape": "bounded_change",
            "evidence_contract": contract,
            "evidence_coverage": [],
            "evidence_keys": [],
        }

        progress = update_evidence_progress(
            state,
            results=[
                {
                    "call_id": "write-only",
                    "name": "sandbox.write_file",
                    "ok": True,
                    "result": {"path": "app.py"},
                }
            ],
            completed_round=True,
        )

        self.assertIn("applied_change", progress["evidence_coverage"])
        self.assertNotIn("current_behavior", progress["evidence_coverage"])
        self.assertIn("validation_result", progress["unresolved_requirements"])

    def test_single_non_readonly_turn_continues_from_write_to_validation(self) -> None:
        planner = _ValidationGatePlanner(
            _validation_command(passes=True),
            mode="passed",
        )
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            runtime = CodingAgentRuntime(planner=planner)

            waiting = runtime.run(
                conversation_id="validation-gate-success",
                user_input="change app.py",
                history=[],
                workspace_id="workspace-main",
                workspace_root=temp_dir,
            )
            validation_approval = runtime.resume(
                run_id=waiting.run_id,
                approved=True,
            )
            result = runtime.resume(
                run_id=waiting.run_id,
                approved=True,
            )

        self.assertEqual(waiting.status, "waiting_approval")
        self.assertEqual(validation_approval.status, "waiting_approval")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.terminal_reason, "completion_contract_satisfied")
        self.assertEqual(result.change_summary.status, "validated")
        self.assertTrue(result.change_summary.validation_passed)
        self.assertEqual(result.change_summary.validation_command_count, 1)
        self.assertTrue(planner.saw_validation_reminder)
        executed = [
            item["name"]
            for item in result.tool_results
            if item["name"] in {"sandbox.write_file", "sandbox.run_command"}
        ]
        self.assertEqual(executed, ["sandbox.write_file", "sandbox.run_command"])

    def test_hard_budget_without_validation_is_partial_validation_missing(self) -> None:
        planner = _ValidationGatePlanner(
            _validation_command(passes=True),
            mode="missing",
        )
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            runtime = CodingAgentRuntime(
                planner=planner,
                max_tool_rounds=2,
                no_progress_rounds=10,
            )

            waiting = runtime.run(
                conversation_id="validation-gate-missing",
                user_input="change app.py",
                history=[],
                workspace_id="workspace-main",
                workspace_root=temp_dir,
            )
            result = runtime.resume(run_id=waiting.run_id, approved=True)

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.terminal_reason, "validation_missing")
        self.assertEqual(result.change_summary.status, "changes_ready")
        self.assertFalse(result.change_summary.validation_passed)
        self.assertEqual(result.change_summary.validation_command_count, 0)
        self.assertIn("no post-change validation command completed", result.answer)
        self.assertEqual(planner.final_reason, "validation_missing")
        response = AgentRunResponse.from_domain(result)
        self.assertEqual(response.terminal_reason, "validation_missing")
        events = AgentRunEventsResponse.from_domain(runtime.get_run(result.run_id))
        self.assertEqual(events.events[-1].type, "run_partial")
        self.assertEqual(
            events.events[-1].output["terminal_reason"],
            "validation_missing",
        )

    def test_failed_validation_is_preserved_and_cannot_complete(self) -> None:
        planner = _ValidationGatePlanner(
            _validation_command(passes=False),
            mode="failed",
        )
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            runtime = CodingAgentRuntime(planner=planner, max_tool_rounds=2)

            waiting = runtime.run(
                conversation_id="validation-gate-failed",
                user_input="change app.py",
                history=[],
                workspace_id="workspace-main",
                workspace_root=temp_dir,
            )
            validation_approval = runtime.resume(
                run_id=waiting.run_id,
                approved=True,
            )
            result = runtime.resume(
                run_id=waiting.run_id,
                approved=True,
            )

        self.assertEqual(validation_approval.status, "waiting_approval")
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.terminal_reason, "validation_failed")
        self.assertEqual(result.change_summary.status, "validation_failed")
        self.assertFalse(result.change_summary.validation_passed)
        self.assertEqual(result.change_summary.validation_command_count, 1)
        reports = [item for item in result.artifacts if item["type"] == "test_report"]
        self.assertEqual([item["status"] for item in reports], ["failed"])
        self.assertIn("post-change validation failed", result.answer)

    def test_checkpoint_resume_keeps_validation_gate_active(self) -> None:
        planner = _ValidationGatePlanner(
            _validation_command(passes=True),
            mode="checkpoint",
        )
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            runtime = CodingAgentRuntime(planner=planner)

            approval = runtime.run(
                conversation_id="validation-gate-checkpoint",
                user_input="change app.py, ask me, then validate it",
                history=[],
                workspace_id="workspace-main",
                workspace_root=temp_dir,
            )
            waiting_input = runtime.resume(run_id=approval.run_id, approved=True)
            validation_approval = runtime.resume(
                run_id=approval.run_id,
                approved=True,
                feedback="continue validation",
            )
            result = runtime.resume(run_id=approval.run_id, approved=True)

        self.assertEqual(waiting_input.status, "waiting_input")
        self.assertEqual(validation_approval.status, "waiting_approval")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.terminal_reason, "completion_contract_satisfied")
        self.assertTrue(result.change_summary.validation_passed)
        self.assertTrue(planner.saw_validation_reminder)


if __name__ == "__main__":
    unittest.main()
