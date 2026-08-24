from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest.mock import patch

from langgraph.checkpoint.base import create_checkpoint

from ai_agent_platform.agents.coding.change_loop import ChangeLoopExecutor
from ai_agent_platform.agents.coding.models import AgentRunRecord, CodingAgentState
from ai_agent_platform.agents.coding.store import InMemoryAgentRunStore
from ai_agent_platform.agents.coding_agent import CodingAgentRuntime
from ai_agent_platform.integrations.llm import LLMToolDecision
from ai_agent_platform.integrations.tools import ToolCall, ToolRegistry


class SteeringPlanner:
    uses_native_tool_calling = True

    def __init__(self) -> None:
        self.observed_steering = False

    def classify_intent(self, user_input: str):
        return {
            "intent": "code_explanation",
            "reason": "framework test",
            "confidence": 1.0,
            "source": "test",
        }

    def decide_tool_calls(self, messages, tool_specs):
        del tool_specs
        self.observed_steering = any(
            "new direction" in str(message.get("content") or "")
            for message in messages
        )
        return LLMToolDecision(
            text="Steering observed.",
            tool_calls=[],
            model="scripted",
            provider="test",
            stop_reason="end_turn",
        )

    def plan_tool_calls(self, state, tool_specs):
        return []

    def plan_repair_tool_calls(self, state, tool_specs):
        return []

    def compose_answer(self, state):
        return "fallback"


class PausingPlanner(SteeringPlanner):
    def __init__(self) -> None:
        super().__init__()
        self.decisions = 0
        self.observed_steering_content = ""

    def decide_tool_calls(self, messages, tool_specs):
        del tool_specs
        self.decisions += 1
        if self.decisions == 1:
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        call_id="wait_call",
                        name="demo.wait",
                        arguments={},
                    )
                ],
                model="scripted",
                provider="test",
                stop_reason="tool_use",
            )
        self.observed_steering = any(
            "resume direction" in str(message.get("content") or "")
            for message in messages
        )
        self.observed_steering_content = next(
            (
                str(message.get("content") or "")
                for message in messages
                if "resume direction" in str(message.get("content") or "")
            ),
            "",
        )
        return LLMToolDecision(
            text="Resumed and completed.",
            tool_calls=[],
            model="scripted",
            provider="test",
            stop_reason="end_turn",
        )


class InputPlanner(SteeringPlanner):
    def __init__(self) -> None:
        super().__init__()
        self.decisions = 0
        self.answer = ""
        self.observed_branch_direction = ""

    def decide_tool_calls(self, messages, tool_specs):
        del tool_specs
        self.decisions += 1
        self.observed_branch_direction = next(
            (
                str(message.get("content") or "")
                for message in messages
                if "checkpoint-direction" in str(message.get("content") or "")
            ),
            "",
        )
        if self.decisions == 1:
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        call_id="input_call",
                        name="agent.request_user_input",
                        arguments={"question": "Which API should change?"},
                    )
                ],
                model="scripted",
                provider="test",
                stop_reason="tool_use",
            )
        tool_result = next(
            message
            for message in messages
            if message.get("role") == "tool"
            and message.get("call_id") == "input_call"
        )
        self.answer = str(tool_result["content"]["result"]["answer"])
        return LLMToolDecision(
            text=f"Will change {self.answer}.",
            tool_calls=[],
            model="scripted",
            provider="test",
            stop_reason="end_turn",
        )


class AgentRuntimeFrameworkTests(unittest.TestCase):
    def test_in_memory_store_loads_latest_run_for_conversation(self) -> None:
        store = InMemoryAgentRunStore()
        base = AgentRunRecord(
            run_id="run_first",
            thread_id="run_first",
            conversation_id="session_1",
            workspace_id="workspace_main",
            workspace_root="/workspace",
            status="queued",
            checkpoint_id=None,
            latest_node=None,
            next_nodes=["setup_workspace"],
            trace=[],
        )
        store.save(base)
        store.save(replace(base, run_id="run_other", conversation_id="session_2"))
        store.save(replace(base, run_id="run_latest", thread_id="run_latest"))

        latest = store.get_latest_for_conversation("session_1")

        assert latest is not None
        self.assertEqual(latest.run_id, "run_latest")
        self.assertIsNone(store.get_latest_for_conversation("missing"))

    def test_terminal_run_cannot_be_overwritten_by_stale_active_snapshot(self) -> None:
        store = InMemoryAgentRunStore()
        terminal = AgentRunRecord(
            run_id="run_terminal",
            thread_id="run_terminal",
            conversation_id="session_1",
            workspace_id="workspace_main",
            workspace_root="/workspace",
            status="failed",
            checkpoint_id="failed-checkpoint",
            latest_node="runtime",
            next_nodes=[],
            trace=[],
            error="original failure",
        )
        store.save(terminal)

        store.save(
            replace(
                terminal,
                status="running",
                checkpoint_id="stale-checkpoint",
                error=None,
            )
        )

        self.assertEqual(store.get(terminal.run_id), terminal)

    def test_resume_failure_preserves_original_exception_and_terminal_record(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "README.md").write_text("demo\n", encoding="utf-8")
            runtime = CodingAgentRuntime(planner=InputPlanner())
            waiting = runtime.run(
                conversation_id="sess_resume_failure",
                user_input="make the requested API change",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
            )

            with patch.object(
                runtime._checkpoint_coordinator,
                "resume",
                side_effect=RuntimeError("graph resume exploded"),
            ):
                with self.assertRaisesRegex(RuntimeError, "graph resume exploded"):
                    runtime.resume(
                        run_id=waiting.run_id,
                        approved=True,
                        feedback="the /health endpoint",
                    )

            failed = runtime.get_run(waiting.run_id)

        self.assertEqual(waiting.status, "waiting_input")
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error, "graph resume exploded")
        self.assertNotIn("config", failed.error)

    def test_model_can_pause_for_input_and_receive_answer_as_tool_result(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "README.md").write_text("demo\n", encoding="utf-8")
            planner = InputPlanner()
            runtime = CodingAgentRuntime(planner=planner)

            waiting = runtime.run(
                conversation_id="sess_input",
                user_input="make the requested API change",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
            )
            completed = runtime.resume(
                run_id=waiting.run_id,
                approved=True,
                feedback="the /health endpoint",
            )

        self.assertEqual(waiting.status, "waiting_input")
        self.assertEqual(
            waiting.pending_approval["question"],
            "Which API should change?",
        )
        self.assertEqual(completed.status, "completed")
        self.assertEqual(planner.answer, "the /health endpoint")
        self.assertEqual(completed.answer, "Will change the /health endpoint.")

    def test_pause_and_continue_happen_at_a_safe_tool_boundary(self) -> None:
        tool_started = Event()
        release_tool = Event()

        def wait_tool():
            tool_started.set()
            release_tool.wait(timeout=5)
            return {"released": True, "payload": "x" * 6000}

        registry = ToolRegistry()
        registry.register(
            "demo.wait",
            wait_tool,
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "released": {"type": "boolean"},
                    "payload": {"type": "string"},
                },
                "required": ["released", "payload"],
                "additionalProperties": False,
            },
        )
        planner = PausingPlanner()
        runtime = CodingAgentRuntime(
            tool_registry=registry,
            planner=planner,
            native_context_max_chars=3500,
        )
        result_holder = []

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "README.md").write_text("demo\n", encoding="utf-8")
            queued = runtime.create_queued_run(
                conversation_id="sess_pause",
                workspace_id="workspace_main",
                workspace_root=str(root),
            )
            worker = Thread(
                target=lambda: result_holder.append(
                    runtime.run(
                        run_id=queued.run_id,
                        conversation_id=queued.conversation_id,
                        user_input="use the wait tool",
                        history=[],
                        workspace_id=queued.workspace_id,
                        workspace_root=queued.workspace_root,
                    )
                )
            )
            worker.start()
            self.assertTrue(tool_started.wait(timeout=5))
            runtime.request_control(run_id=queued.run_id, action="pause")
            release_tool.set()
            worker.join(timeout=5)

            self.assertFalse(worker.is_alive())
            self.assertEqual(result_holder[0].status, "paused")
            feedback = "resume direction " + "逐字保留" * 80
            completed = runtime.resume(
                run_id=queued.run_id,
                approved=True,
                feedback=feedback,
            )

        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.answer, "Resumed and completed.")
        self.assertTrue(planner.observed_steering)
        self.assertEqual(
            planner.observed_steering_content,
            "User steering for the active run: " + feedback,
        )
        self.assertEqual(runtime.get_run(queued.run_id).steering_messages, [])

    def test_historical_checkpoint_starts_an_independent_graph_thread(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "README.md").write_text("demo\n", encoding="utf-8")
            planner = InputPlanner()
            runtime = CodingAgentRuntime(planner=planner)
            source = runtime.run(
                conversation_id="sess_checkpoint_source",
                user_input="make the requested API change",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
            )
            checkpoints = runtime.list_checkpoints(source.run_id)
            initial = next(item for item in checkpoints if item.step == -1)
            self.assertTrue(initial.next_nodes)
            self.assertTrue(initial.can_restore)
            selected = next(
                item for item in checkpoints if item.next_nodes == ["plan_tools"]
            )
            branch = runtime.prepare_checkpoint_branch(
                source_run_id=source.run_id,
                checkpoint_id=selected.checkpoint_id,
                conversation_id=source.conversation_id,
                mode="rollback",
                message="take a different path",
            )
            runtime.restore_record(branch)
            planner.decisions = 0
            restored = runtime.run_from_checkpoint(branch.run_id)

        self.assertEqual(source.status, "waiting_input")
        self.assertNotEqual(branch.run_id, source.run_id)
        self.assertEqual(branch.thread_id, branch.run_id)
        self.assertEqual(restored.status, "waiting_input")
        self.assertEqual(runtime.get_run(source.run_id).status, "waiting_input")
        branch_history = runtime.list_checkpoints(branch.run_id)
        self.assertTrue(
            any(
                item.origin_run_id == source.run_id
                and item.origin_checkpoint_id == selected.checkpoint_id
                and item.restore_mode == "rollback"
                for item in branch_history
            )
        )

    def test_rollback_and_fork_deliver_checkpoint_direction_verbatim(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "README.md").write_text("demo\n", encoding="utf-8")
            planner = InputPlanner()
            runtime = CodingAgentRuntime(planner=planner)
            source = runtime.run(
                conversation_id="sess_checkpoint_verbatim",
                user_input="make the requested API change",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
            )
            selected = next(
                item
                for item in runtime.list_checkpoints(source.run_id)
                if item.next_nodes == ["plan_tools"]
            )
            for mode in ("rollback", "fork"):
                with self.subTest(mode=mode):
                    direction = f"checkpoint-direction-{mode}-" + "逐字" * 80
                    branch = runtime.prepare_checkpoint_branch(
                        source_run_id=source.run_id,
                        checkpoint_id=selected.checkpoint_id,
                        conversation_id=(
                            source.conversation_id
                            if mode == "rollback"
                            else f"{source.conversation_id}_fork"
                        ),
                        mode=mode,
                        message=direction,
                    )
                    runtime.restore_record(branch)
                    planner.decisions = 0
                    planner.observed_branch_direction = ""
                    restored = runtime.run_from_checkpoint(branch.run_id)

                    self.assertEqual(restored.status, "waiting_input")
                    self.assertEqual(
                        planner.observed_branch_direction,
                        "User steering for the active run: " + direction,
                    )

    def test_legacy_checkpoint_without_compaction_channels_restores_defaults(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "README.md").write_text("demo\n", encoding="utf-8")
            runtime = CodingAgentRuntime(planner=InputPlanner())
            source = runtime.run(
                conversation_id="sess_legacy_checkpoint",
                user_input="make the requested API change",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
            )
            source_record = runtime.get_run(source.run_id)
            selected_info = next(
                item
                for item in runtime.list_checkpoints(source.run_id)
                if item.next_nodes == ["plan_tools"]
            )
            selected = runtime._checkpoint_coordinator.snapshot_by_id(
                source.thread_id,
                selected_info.checkpoint_id,
            )
            stored = runtime._checkpointer.get_tuple(selected.config)
            self.assertIsNotNone(stored)
            checkpoint = create_checkpoint(stored.checkpoint, None, 0)
            for channel in (
                "native_context_compactions",
                "native_context_reduction_stages",
                "native_context_force_compaction",
            ):
                checkpoint["channel_values"].pop(channel, None)
            legacy_thread = "run_legacy_checkpoint"
            checkpoint_ns = str(
                selected.config.get("configurable", {}).get("checkpoint_ns") or ""
            )
            legacy_config = runtime._checkpointer.put(
                {
                    "configurable": {
                        "thread_id": legacy_thread,
                        "checkpoint_ns": checkpoint_ns,
                    }
                },
                checkpoint,
                dict(stored.metadata or {}),
                dict(checkpoint["channel_versions"]),
            )
            writes_by_task = defaultdict(list)
            for task_id, channel, value in stored.pending_writes or []:
                if channel in {
                    "native_context_compactions",
                    "native_context_reduction_stages",
                    "native_context_force_compaction",
                }:
                    continue
                writes_by_task[str(task_id)].append((str(channel), value))
            for task_id, writes in writes_by_task.items():
                runtime._checkpointer.put_writes(legacy_config, writes, task_id)
            legacy_snapshot = runtime._checkpoint_coordinator.snapshot_for(
                legacy_config
            )
            self.assertIsNotNone(legacy_snapshot)
            self.assertNotIn(
                "native_context_compactions", legacy_snapshot.values
            )
            legacy_checkpoint_id = str(
                legacy_snapshot.config["configurable"]["checkpoint_id"]
            )
            legacy_record = replace(
                source_record,
                run_id=legacy_thread,
                thread_id=legacy_thread,
                checkpoint_id=legacy_checkpoint_id,
                next_nodes=list(legacy_snapshot.next),
                trace=list(legacy_snapshot.values.get("trace", [])),
            )
            runtime.restore_record(legacy_record)
            branch = runtime.prepare_checkpoint_branch(
                source_run_id=legacy_record.run_id,
                checkpoint_id=legacy_checkpoint_id,
                conversation_id=legacy_record.conversation_id,
                mode="rollback",
                message="legacy checkpoint direction",
            )
            branch_snapshot = runtime._checkpoint_coordinator.snapshot_by_id(
                branch.thread_id,
                branch.checkpoint_id,
            )

        self.assertEqual(branch_snapshot.values["native_context_compactions"], 0)
        self.assertEqual(branch_snapshot.values["native_context_reduction_stages"], [])
        self.assertFalse(branch_snapshot.values["native_context_force_compaction"])

    def test_checkpoint_clone_preserves_compaction_count_and_force_consumption(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "README.md").write_text("demo\n", encoding="utf-8")
            runtime = CodingAgentRuntime(planner=InputPlanner())
            source = runtime.run(
                conversation_id="sess_compaction_checkpoint",
                user_input="make the requested API change",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
            )
            selected_info = next(
                item
                for item in runtime.list_checkpoints(source.run_id)
                if item.next_nodes == ["plan_tools"]
            )
            selected = runtime._checkpoint_coordinator.snapshot_by_id(
                source.thread_id,
                selected_info.checkpoint_id,
            )
            for label, count, force in (
                ("before", 0, True),
                ("after", 1, False),
            ):
                with self.subTest(label=label):
                    config = runtime._graph.update_state(
                        selected.config,
                        {
                            "native_context_compactions": count,
                            "native_context_reduction_stages": [
                                {"stage": "fold", "compacted": count}
                            ],
                            "native_context_force_compaction": force,
                        },
                    )
                    snapshot = runtime._checkpoint_coordinator.snapshot_for(config)
                    self.assertTrue(snapshot.next)
                    checkpoint_id = str(
                        snapshot.config["configurable"]["checkpoint_id"]
                    )
                    branch = runtime.prepare_checkpoint_branch(
                        source_run_id=source.run_id,
                        checkpoint_id=checkpoint_id,
                        conversation_id=source.conversation_id,
                        mode="rollback",
                        message=f"{label} compaction",
                    )
                    cloned = runtime._checkpoint_coordinator.snapshot_by_id(
                        branch.thread_id,
                        branch.checkpoint_id,
                    )

                    self.assertEqual(
                        cloned.values["native_context_compactions"], count
                    )
                    self.assertEqual(
                        cloned.values["native_context_force_compaction"], force
                    )
                    self.assertEqual(
                        cloned.values["native_context_reduction_stages"],
                        [{"stage": "fold", "compacted": count}],
                    )
                    if label == "after":
                        runtime.restore_record(branch)
                        runtime._planner.decisions = 0
                        runtime.run_from_checkpoint(branch.run_id)
                        completed_record = runtime.get_run(branch.run_id)
                        completed_snapshot = (
                            runtime._checkpoint_coordinator.snapshot_by_id(
                                branch.thread_id,
                                completed_record.checkpoint_id,
                            )
                        )
                        self.assertEqual(
                            completed_snapshot.values[
                                "native_context_compactions"
                            ],
                            1,
                        )
                        self.assertFalse(
                            completed_snapshot.values[
                                "native_context_force_compaction"
                            ]
                        )

    def test_queued_steering_survives_worker_start_and_is_consumed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "README.md").write_text("demo\n", encoding="utf-8")
            planner = SteeringPlanner()
            runtime = CodingAgentRuntime(planner=planner)
            queued = runtime.create_queued_run(
                conversation_id="sess_steer",
                workspace_id="workspace_main",
                workspace_root=str(root),
            )
            runtime.request_control(
                run_id=queued.run_id,
                action="steer",
                message="new direction",
            )

            result = runtime.run(
                run_id=queued.run_id,
                conversation_id=queued.conversation_id,
                user_input="inspect the project",
                history=[],
                workspace_id=queued.workspace_id,
                workspace_root=queued.workspace_root,
            )

        self.assertEqual(result.status, "completed")
        self.assertTrue(planner.observed_steering)
        self.assertEqual(runtime.get_run(queued.run_id).steering_messages, [])

    def test_queued_run_can_be_cancelled_before_worker_execution(self) -> None:
        with TemporaryDirectory() as temp_dir:
            runtime = CodingAgentRuntime()
            queued = runtime.create_queued_run(
                conversation_id="sess_cancel",
                workspace_id="workspace_main",
                workspace_root=temp_dir,
            )

            cancelled = runtime.request_control(
                run_id=queued.run_id,
                action="cancel",
            )

        self.assertEqual(cancelled.status, "cancelled")
        events = runtime.list_events(queued.run_id)
        self.assertEqual(events[-1].type, "run_cancelled")

    def test_repeated_suspension_transitions_keep_distinct_events(self) -> None:
        store = InMemoryAgentRunStore()
        record = AgentRunRecord(
            run_id="run_repeated_pause",
            thread_id="thread_repeated_pause",
            conversation_id="sess_repeated_pause",
            workspace_id="workspace_main",
            workspace_root=".",
            status="paused",
            checkpoint_id="checkpoint-one",
            latest_node="native_tool_loop",
            next_nodes=[],
            trace=[],
        )
        store.save(record)
        store.save(
            replace(
                record,
                status="running",
                checkpoint_id="checkpoint-between",
            )
        )
        store.save(replace(record, checkpoint_id="checkpoint-two"))

        pause_events = [
            event
            for event in store.list_events(record.run_id)
            if event.type == "run_paused"
        ]
        self.assertEqual(len(pause_events), 2)
        self.assertLess(pause_events[0].sequence, pause_events[1].sequence)

    def test_tool_execution_is_replayed_by_durable_call_identity(self) -> None:
        calls = 0

        def lookup(value: int):
            nonlocal calls
            calls += 1
            return {"value": value}

        registry = ToolRegistry()
        registry.register(
            "demo.lookup",
            lookup,
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        )
        store = InMemoryAgentRunStore()
        executor = ChangeLoopExecutor(
            tools=registry,
            planner=object(),
            run_store=store,
        )
        state: CodingAgentState = {
            "run_id": "run_durable",
            "conversation_id": "sess_durable",
            "workspace_id": "workspace_main",
            "workspace_root": ".",
        }
        call = ToolCall(
            call_id="stable_call",
            name="demo.lookup",
            arguments={"value": 7},
        )

        first = executor.execute_tool_calls(state, [call])[0]
        replay = executor.execute_tool_calls(state, [call])[0]
        conflict = executor.execute_tool_calls(
            state,
            [
                ToolCall(
                    call_id="stable_call",
                    name="demo.lookup",
                    arguments={"value": 8},
                )
            ],
        )[0]

        self.assertTrue(first["ok"])
        self.assertTrue(replay["durable_replay"])
        self.assertEqual(calls, 1)
        self.assertEqual(conflict["error_code"], "tool_call_identity_conflict")


if __name__ == "__main__":
    unittest.main()
