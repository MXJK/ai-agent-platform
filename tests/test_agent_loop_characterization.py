from __future__ import annotations

import json
from pathlib import Path
import shlex
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest

from ai_agent_platform.agents.coding.models import CodingAgentState
from ai_agent_platform.agents.coding_agent import (
    CodingAgentRuntime,
    create_coding_tool_registry,
)
from ai_agent_platform.integrations.llm import LLMToolDecision
from ai_agent_platform.integrations.tools import ToolCall, ToolRegistry, ToolSpec
from ai_agent_platform.repositories import (
    InMemoryChangeSetRepository,
    InMemoryWorkspaceRepository,
)
from ai_agent_platform.services import ChangeSetService, WorkspaceService


GOLDEN = json.loads(
    (Path(__file__).parent / "golden" / "agent_loop_trajectories.json").read_text(
        encoding="utf-8"
    )
)


class ReadOnlyPlanner:
    def classify_intent(self, user_input: str) -> dict[str, object]:
        return {
            "intent": "code_explanation",
            "reason": "characterization",
            "confidence": 1.0,
            "source": "golden",
        }

    def plan_tool_calls(
        self,
        state: CodingAgentState,
        tool_specs: list[ToolSpec],
    ) -> list[ToolCall]:
        if state.get("intent") == "code_explanation" and state.get("context_sources"):
            return [
                ToolCall(
                    call_id="readonly_inspect",
                    name="repo.read_file",
                    arguments={"path": "app.py"},
                    source="golden",
                )
            ]
        return []

    def plan_repair_tool_calls(self, state, tool_specs):
        return []

    def compose_answer(self, state: CodingAgentState) -> str:
        return "app.py is read-only evidence."


class NativeMultiTurnPlanner(ReadOnlyPlanner):
    uses_native_tool_calling = True

    def __init__(self) -> None:
        self.round = 0

    def decide_tool_calls(self, messages, tool_specs):
        del messages, tool_specs
        self.round += 1
        if self.round <= 2:
            path = "app.py" if self.round == 1 else "other.py"
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        call_id=f"native_read_{path.removesuffix('.py')}",
                        name="repo.read_file",
                        arguments={"path": path, "start_line": 1, "end_line": 1},
                        source="golden",
                    )
                ],
                model="scripted",
                provider="golden",
                stop_reason="tool_use",
            )
        return LLMToolDecision(
            text="Read both files.",
            tool_calls=[],
            model="scripted",
            provider="golden",
            stop_reason="end_turn",
        )


class InputPlanner(NativeMultiTurnPlanner):
    def __init__(self) -> None:
        super().__init__()
        self.answer = ""

    def decide_tool_calls(self, messages, tool_specs):
        del tool_specs
        self.round += 1
        if self.round == 1:
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        call_id="input_1",
                        name="agent.request_user_input",
                        arguments={"question": "Which endpoint?"},
                    )
                ],
                model="scripted",
                provider="golden",
                stop_reason="tool_use",
            )
        result = next(
            message
            for message in messages
            if message.get("role") == "tool" and message.get("call_id") == "input_1"
        )
        self.answer = str(result["content"]["result"]["answer"])
        return LLMToolDecision(
            text=f"Use {self.answer}.",
            tool_calls=[],
            model="scripted",
            provider="golden",
            stop_reason="end_turn",
        )


class RepairPlanner(ReadOnlyPlanner):
    def __init__(self, command: str) -> None:
        self.command = command

    def classify_intent(self, user_input: str) -> dict[str, object]:
        return {
            "intent": "change_planning",
            "reason": "characterization",
            "confidence": 1.0,
            "source": "golden",
        }

    def plan_tool_calls(self, state, tool_specs):
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

    def plan_repair_tool_calls(self, state, tool_specs):
        return [
            ToolCall(
                name="sandbox.write_file",
                arguments={"path": "app.py", "content": "value = 'repaired'\n"},
                source="golden_repair",
            )
        ]


class SuccessfulChangePlanner(RepairPlanner):
    def plan_tool_calls(self, state, tool_specs):
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

    def plan_repair_tool_calls(self, state, tool_specs):
        return []


class BudgetPlanner(NativeMultiTurnPlanner):
    def __init__(self, tool_name: str = "repo.read_file") -> None:
        super().__init__()
        self.tool_name = tool_name

    def decide_tool_calls(self, messages, tool_specs):
        del messages, tool_specs
        self.round += 1
        return LLMToolDecision(
            text="",
            tool_calls=[
                ToolCall(
                    call_id=f"budget_{self.round}",
                    name=self.tool_name,
                    arguments={"path": "app.py"} if self.tool_name == "repo.read_file" else {},
                )
            ],
            model="scripted",
            provider="golden",
            stop_reason="tool_use",
        )

    def finalize_tool_session(self, messages, *, reason):
        del messages
        return LLMToolDecision(
            text=f"Stopped: {reason}",
            tool_calls=[],
            model="scripted",
            provider="golden",
            stop_reason="end_turn",
        )


class PausePlanner(NativeMultiTurnPlanner):
    def __init__(self) -> None:
        super().__init__()
        self.saw_steering = False

    def decide_tool_calls(self, messages, tool_specs):
        del tool_specs
        self.round += 1
        if self.round == 1:
            return LLMToolDecision(
                text="",
                tool_calls=[ToolCall(call_id="wait_1", name="demo.wait", arguments={})],
                model="scripted",
                provider="golden",
                stop_reason="tool_use",
            )
        self.saw_steering = any(
            "continue safely" in str(message.get("content") or "")
            for message in messages
        )
        return LLMToolDecision(
            text="Resumed.",
            tool_calls=[],
            model="scripted",
            provider="golden",
            stop_reason="end_turn",
        )


class AgentLoopCharacterizationTests(unittest.TestCase):
    def test_read_only_and_native_multi_turn_trajectories_match_golden(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            (root / "other.py").write_text("other = 2\n", encoding="utf-8")
            read_only = CodingAgentRuntime(planner=ReadOnlyPlanner()).run(
                conversation_id="golden_readonly",
                user_input="explain app.py",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
                focus_files=["app.py"],
            )
            native = CodingAgentRuntime(planner=NativeMultiTurnPlanner()).run(
                conversation_id="golden_native",
                user_input="read app.py and other.py",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
                focus_files=["app.py"],
            )

        self.assertEqual(read_only.status, GOLDEN["read_only"]["status"])
        self.assertEqual(_nodes(read_only), GOLDEN["read_only"]["trace"])
        self.assertEqual(native.status, GOLDEN["native_multi_turn"]["status"])
        self.assertEqual(
            _nodes(native)[-6:], GOLDEN["native_multi_turn"]["trace_tail"]
        )
        self.assertEqual(
            [
                result["call_id"]
                for result in native.tool_results
                if str(result.get("call_id", "")).startswith("native_")
            ],
            GOLDEN["native_multi_turn"]["native_calls"],
        )

    def test_change_approval_validation_and_one_repair_match_golden(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("value = 'old'\n", encoding="utf-8")
            runtime = CodingAgentRuntime(
                tool_registry=create_coding_tool_registry(),
                planner=RepairPlanner(_compile_command()),
            )
            first = runtime.run(
                conversation_id="golden_repair",
                user_input="break, validate, and repair app.py",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
            )
            second = runtime.resume(run_id=first.run_id, approved=True)
            completed = runtime.resume(run_id=first.run_id, approved=True)

        golden = GOLDEN["change_repair"]
        self.assertEqual([first.status, second.status, completed.status], golden["statuses"])
        self.assertEqual(
            [first.pending_approval["type"], second.pending_approval["type"]],
            golden["pending_types"],
        )
        self.assertEqual(_nodes(completed)[-10:], golden["trace_tail"])
        self.assertEqual(
            [
                step["output"]["passed"]
                for step in completed.trace
                if step["node"] == "validate_changes"
            ],
            golden["validation_passed"],
        )
        self.assertEqual(
            completed.change_summary.iteration_count,
            golden["iteration_count"],
        )

    def test_waiting_input_and_checkpoint_resume_match_golden(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            planner = InputPlanner()
            runtime = CodingAgentRuntime(planner=planner)
            waiting = runtime.run(
                conversation_id="golden_input",
                user_input="explain an endpoint using the input tool",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
                focus_files=["app.py"],
            )
            record = runtime.get_run(waiting.run_id)
            completed = runtime.resume(
                run_id=waiting.run_id,
                approved=True,
                feedback="/health",
            )

        golden = GOLDEN["waiting_input_resume"]
        self.assertEqual([waiting.status, completed.status], golden["statuses"])
        self.assertEqual(waiting.pending_approval["type"], golden["pending_type"])
        self.assertTrue(waiting.checkpoint_id)
        self.assertEqual(record.next_nodes, golden["waiting_next_nodes"])
        self.assertEqual(completed.run_id, waiting.run_id)
        self.assertEqual(completed.thread_id, waiting.thread_id)
        self.assertNotEqual(completed.checkpoint_id, waiting.checkpoint_id)
        self.assertEqual(_nodes(completed)[-4:], golden["trace_tail"])
        self.assertEqual(planner.answer, "/health")

    def test_pause_steer_and_cancel_match_golden(self) -> None:
        tool_started = Event()
        release_tool = Event()

        def wait_tool():
            tool_started.set()
            release_tool.wait(timeout=5)
            return {"released": True}

        registry = ToolRegistry()
        registry.register(
            "demo.wait",
            wait_tool,
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            output_schema={
                "type": "object",
                "properties": {"released": {"type": "boolean"}},
                "required": ["released"],
                "additionalProperties": False,
            },
        )
        planner = PausePlanner()
        runtime = CodingAgentRuntime(tool_registry=registry, planner=planner)
        results = []
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            queued = runtime.create_queued_run(
                conversation_id="golden_pause",
                workspace_id="workspace_main",
                workspace_root=str(root),
            )
            worker = Thread(
                target=lambda: results.append(
                    runtime.run(
                        run_id=queued.run_id,
                        conversation_id=queued.conversation_id,
                        user_input="wait",
                        history=[],
                        workspace_id=queued.workspace_id,
                        workspace_root=queued.workspace_root,
                        focus_files=["app.py"],
                    )
                )
            )
            worker.start()
            self.assertTrue(tool_started.wait(timeout=5))
            runtime.request_control(run_id=queued.run_id, action="pause")
            release_tool.set()
            worker.join(timeout=5)
            resumed = runtime.resume(
                run_id=queued.run_id,
                approved=True,
                feedback="continue safely",
            )

            steer_runtime = CodingAgentRuntime(planner=NativeMultiTurnPlanner())
            steer_queued = steer_runtime.create_queued_run(
                conversation_id="golden_steer",
                workspace_id="workspace_main",
                workspace_root=str(root),
            )
            steer_runtime.request_control(
                run_id=steer_queued.run_id,
                action="steer",
                message="read both files",
            )
            steered = steer_runtime.run(
                run_id=steer_queued.run_id,
                conversation_id=steer_queued.conversation_id,
                user_input="inspect",
                history=[],
                workspace_id=steer_queued.workspace_id,
                workspace_root=steer_queued.workspace_root,
                focus_files=["app.py"],
            )

            cancel_runtime = CodingAgentRuntime()
            cancel_queued = cancel_runtime.create_queued_run(
                conversation_id="golden_cancel",
                workspace_id="workspace_main",
                workspace_root=str(root),
            )
            cancelled = cancel_runtime.request_control(
                run_id=cancel_queued.run_id,
                action="cancel",
            )

        golden = GOLDEN["controls"]
        self.assertFalse(worker.is_alive())
        self.assertEqual([results[0].status, resumed.status], golden["pause_statuses"])
        self.assertTrue(planner.saw_steering)
        self.assertEqual(steered.status, golden["steer_status"])
        self.assertEqual(cancelled.status, golden["cancel_status"])
        observed_events = {
            event.type for event in runtime.list_events(queued.run_id)
        } | {
            event.type for event in steer_runtime.list_events(steer_queued.run_id)
        } | {
            event.type for event in cancel_runtime.list_events(cancel_queued.run_id)
        }
        self.assertTrue(set(golden["event_types"]).issubset(observed_events))

    def test_hard_budget_partial_and_blocked_match_golden(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            partial = CodingAgentRuntime(
                planner=BudgetPlanner(),
                max_tool_rounds=1,
                max_tool_calls=4,
            ).run(
                conversation_id="golden_partial",
                user_input="keep reading",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
                focus_files=["app.py"],
            )

            registry = ToolRegistry()
            registry.register(
                "demo.fail",
                lambda: (_ for _ in ()).throw(RuntimeError("expected failure")),
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                output_schema={"type": "object"},
            )
            blocked = CodingAgentRuntime(
                tool_registry=registry,
                planner=BudgetPlanner("demo.fail"),
                max_consecutive_failures=1,
            ).run(
                conversation_id="golden_blocked",
                user_input="try the failing tool",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
            )

        golden = GOLDEN["hard_budget"]
        self.assertEqual([partial.status, blocked.status], golden["statuses"])
        self.assertEqual(
            [
                _terminal_reason(partial),
                _terminal_reason(blocked),
            ],
            golden["terminal_reasons"],
        )

    def test_change_set_capture_precedes_cleanup_and_matches_golden(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "app.py"
            source.write_text("value = 'old'\n", encoding="utf-8")
            workspace_service = WorkspaceService(
                store=InMemoryWorkspaceRepository(),
                allowed_roots=(str(root),),
            )
            workspace_service.register(
                workspace_id="workspace_main",
                root_path=str(root),
            )
            change_sets = ChangeSetService(
                repository=InMemoryChangeSetRepository(),
                workspace_service=workspace_service,
            )
            runtime = CodingAgentRuntime(
                tool_registry=create_coding_tool_registry(),
                planner=SuccessfulChangePlanner(_compile_command()),
                change_set_service=change_sets,
            )
            change_sets.set_audit_callback(runtime.record_change_set_event)
            waiting = runtime.run(
                conversation_id="golden_changeset",
                user_input="change app.py",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
            )
            completed = runtime.resume(run_id=waiting.run_id, approved=True)
            persisted = change_sets.get_for_run(completed.run_id, actor_user_id=None)
            sandbox = Path(
                next(
                    item["result"]["workspace"]
                    for item in completed.tool_results
                    if item["name"] == "sandbox.write_file" and item["ok"]
                )
            )
            source_text = source.read_text(encoding="utf-8")

        golden = GOLDEN["change_set"]
        self.assertEqual(completed.status, golden["status"])
        self.assertEqual(persisted.status, golden["persisted_status"])
        self.assertEqual(
            [artifact["type"] for artifact in completed.artifacts],
            golden["artifact_types"],
        )
        self.assertIn(
            golden["event_type"],
            [event.type for event in runtime.list_events(completed.run_id)],
        )
        self.assertEqual(not sandbox.exists(), golden["sandbox_cleaned"])
        self.assertEqual(source_text, "value = 'old'\n")


def _nodes(result) -> list[str]:
    return [str(step["node"]) for step in result.trace]


def _terminal_reason(result) -> str:
    plan_steps = [step for step in result.trace if step["node"] == "plan_tools"]
    return str(plan_steps[-1]["output"]["stop_reason"])


def _compile_command() -> str:
    validation = (
        "compile(open('app.py', encoding='utf-8').read(), "
        "'app.py', 'exec')"
    )
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(validation)}"


if __name__ == "__main__":
    unittest.main()
