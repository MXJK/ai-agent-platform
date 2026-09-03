from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ai_agent_platform.agents.coding.models import CodingAgentState
from ai_agent_platform.agents.coding.policies import BudgetPolicy
from ai_agent_platform.agents.coding.tool_loop_nodes import (
    _artifact_read_progressed,
    _change_action_tool_specs,
)
from ai_agent_platform.agents.coding.task_shaping import (
    build_evidence_contract,
    clamp_evidence_call,
    classify_request_authority,
    classify_task_shape,
    evidence_contract_satisfied,
    freeze_tool_profile,
    update_evidence_progress,
)
from ai_agent_platform.agents.coding.tools import create_coding_tool_registry
from ai_agent_platform.agents.coding_agent import CodingAgentRuntime
from ai_agent_platform.integrations.llm import LLMToolDecision
from ai_agent_platform.integrations.tools import ToolCall, ToolRegistry


def _budget_policy() -> BudgetPolicy:
    return BudgetPolicy(
        soft_tool_rounds=12,
        max_tool_rounds=24,
        soft_tool_calls=36,
        max_tool_calls=72,
        max_elapsed_seconds=900,
        no_progress_rounds=3,
        max_consecutive_failures=3,
    )


class TaskShapeTests(unittest.TestCase):
    def test_recommendation_and_explicit_change_have_separate_authority(self) -> None:
        recommendation = classify_request_authority(
            "这个项目还建议添加什么小游戏？",
            intent="change_planning",
        )
        how_to = classify_request_authority(
            "如何添加扫雷？",
            intent="change_planning",
        )
        explicit = classify_request_authority(
            "请直接添加扫雷并修改代码",
            intent="change_planning",
        )

        self.assertEqual(recommendation[:2], ("plan", False))
        self.assertEqual(how_to[:2], ("plan", False))
        self.assertEqual(explicit[:2], ("change", True))
        self.assertEqual(
            classify_task_shape(
                "这个项目还建议添加什么小游戏？",
                intent="change_planning",
                mutation_authorized=False,
            ),
            "broad_review",
        )
        self.assertEqual(
            classify_task_shape(
                "请直接添加扫雷并修改代码",
                intent="change_planning",
                mutation_authorized=True,
            ),
            "bounded_change",
        )

    def test_plain_continuation_inherits_only_explicit_prior_mutation(self) -> None:
        self.assertEqual(
            classify_request_authority(
                "继续",
                intent="change_planning",
                prior_user_inputs=["请修改代码并运行测试"],
            )[:2],
            ("change", True),
        )
        self.assertEqual(
            classify_request_authority(
                "继续",
                intent="change_planning",
                prior_user_inputs=["请解释应该怎么修改"],
            )[:2],
            ("plan", False),
        )

    def test_incomplete_work_continuation_is_an_explicit_change(self) -> None:
        self.assertEqual(
            classify_request_authority(
                "项目里的扫雷游戏没完成，继续做",
                intent="repository_question",
            )[:2],
            ("change", True),
        )
        self.assertEqual(
            classify_request_authority(
                "这个功能没完成，为什么继续做会失败？",
                intent="repository_question",
            )[:2],
            ("answer", False),
        )
        self.assertEqual(
            classify_request_authority(
                "这个功能没完成，是否继续做？",
                intent="repository_question",
            )[:2],
            ("answer", False),
        )

    def test_overview_synonyms_are_normalized_and_stable(self) -> None:
        phrases = [
            "分析下当前项目",
            "分析当前项目。",
            "看看当前项目",
            "介绍一下这个项目",
            "说明项目结构",
            "这个仓库有哪些文件",
            "SUMMARIZE   THIS PROJECT!",
            "project overview",
        ]
        self.assertEqual(
            [classify_task_shape(item) for item in phrases],
            ["overview"] * len(phrases),
        )

    def test_specific_failure_change_and_target_do_not_become_overview(self) -> None:
        self.assertEqual(
            classify_task_shape("分析当前项目的登录故障"),
            "investigation",
        )
        self.assertEqual(
            classify_task_shape("分析当前项目并修改登录流程"),
            "bounded_change",
        )
        self.assertEqual(
            classify_task_shape(
                "分析当前项目的 LoginService",
                symbols=["LoginService"],
            ),
            "targeted_read",
        )

    def test_shapes_receive_independent_contract_budgets(self) -> None:
        overview = build_evidence_contract("overview")
        targeted = build_evidence_contract("targeted_read")
        change = build_evidence_contract("bounded_change")
        investigation = build_evidence_contract("investigation")
        review = build_evidence_contract("broad_review")

        self.assertEqual(
            (
                overview["max_model_requests"],
                overview["soft_tool_rounds"],
                overview["max_tool_rounds"],
                overview["soft_tool_calls"],
                overview["max_tool_calls"],
                overview["max_evidence_tokens"],
                overview["max_extension_rounds"],
            ),
            (5, 2, 3, 8, 12, 12000, 1),
        )
        self.assertEqual(len({item["max_tool_calls"] for item in (
            overview, targeted, change, investigation, review
        )}), 5)
        self.assertEqual(change["max_tool_rounds"], 24)
        self.assertEqual(change["max_tool_calls"], 72)
        self.assertGreater(investigation["max_tool_calls"], overview["max_tool_calls"])

    def test_overview_profile_is_repository_read_only_and_stable(self) -> None:
        specs = create_coding_tool_registry().list_specs()
        first = freeze_tool_profile("overview", specs)
        second = freeze_tool_profile("overview", list(reversed(specs)))

        self.assertEqual(first, second)
        self.assertEqual(
            first,
            [
                "repo.list_files",
                "repo.find_files",
                "repo.search_code",
                "repo.read_file",
                "repo.collect_evidence",
            ],
        )
        self.assertFalse(any("write" in name or "run_command" in name for name in first))

    def test_mutation_tools_are_removed_without_server_authority(self) -> None:
        specs = create_coding_tool_registry().list_specs()
        profile = freeze_tool_profile(
            "bounded_change",
            specs,
            user_input="这个项目还建议添加什么小游戏？",
            mutation_authorized=False,
        )

        self.assertNotIn("sandbox.write_file", profile)
        self.assertNotIn("sandbox.apply_patch", profile)
        self.assertIn("repo.read_file", profile)

    def test_evidence_call_is_clamped_to_contract_tokens_and_child_budget(self) -> None:
        call = clamp_evidence_call(
            ToolCall(
                name="repo.collect_evidence",
                arguments={
                    "queries": ["a", "b", "c", "d", "e"],
                    "max_files": 32,
                    "max_evidence_tokens": 24000,
                },
            ),
            max_evidence_tokens=12000,
            max_child_calls=12,
        )
        self.assertEqual(call.arguments["max_evidence_tokens"], 12000)
        self.assertLessEqual(
            1 + 2 * len(call.arguments["queries"]) + call.arguments["max_files"],
            12,
        )


class EvidenceStopTests(unittest.TestCase):
    def test_contract_completion_and_one_no_progress_round_stop(self) -> None:
        contract = build_evidence_contract("targeted_read")
        complete: CodingAgentState = {
            "evidence_contract": contract,
            "evidence_coverage": ["target_location", "target_behavior"],
        }
        self.assertTrue(evidence_contract_satisfied(complete))
        self.assertEqual(
            _budget_policy().stop(complete),
            ("evidence_contract_satisfied", "completed"),
        )

        stalled: CodingAgentState = {
            "evidence_contract": contract,
            "evidence_coverage": ["target_location"],
            "unresolved_requirements": ["target_behavior"],
            "evidence_rounds_completed": 1,
            "new_evidence_count": 0,
        }
        self.assertEqual(
            _budget_policy().stop(stalled),
            ("no_new_evidence", "partial"),
        )

    def test_durable_replay_is_not_counted_as_new_evidence(self) -> None:
        state: CodingAgentState = {
            "evidence_contract": build_evidence_contract("targeted_read"),
            "evidence_keys": [],
            "evidence_coverage": [],
        }
        progress = update_evidence_progress(
            state,
            results=[
                {
                    "name": "repo.read_file",
                    "ok": True,
                    "durable_replay": True,
                    "result": {"path": "app.py", "content": "value = 1"},
                }
            ],
            completed_round=True,
        )
        self.assertEqual(progress["new_evidence_count"], 0)
        self.assertEqual(progress["evidence_rounds_completed"], 1)


class _RepeatedPlanner:
    uses_native_tool_calling = True
    single_tool_per_turn = True

    def __init__(self) -> None:
        self.decisions = 0
        self.final_tool_names: list[str] | None = None

    def decide_tool_calls(self, messages, tool_specs):
        del messages, tool_specs
        self.decisions += 1
        return LLMToolDecision(
            text="",
            tool_calls=[
                ToolCall(
                    call_id=f"repeat-{self.decisions}",
                    name="demo.lookup",
                    arguments={"query": "same"},
                )
            ],
            model="test",
            provider="test",
            stop_reason="tool_use",
        )

    def finalize_tool_session(
        self,
        messages,
        *,
        reason,
        tool_specs,
        max_output_tokens,
    ):
        del messages, reason, max_output_tokens
        self.final_tool_names = [spec.name for spec in tool_specs]
        return LLMToolDecision(
            text="Final answer from collected evidence.",
            tool_calls=[],
            model="test",
            provider="test",
            stop_reason="end_turn",
        )

    def compose_answer(self, state):
        del state
        return "fallback"


class _DistinctReadPlanner(_RepeatedPlanner):
    def decide_tool_calls(self, messages, tool_specs):
        del messages, tool_specs
        self.decisions += 1
        return LLMToolDecision(
            text="",
            tool_calls=[
                ToolCall(
                    call_id=f"distinct-{self.decisions}",
                    name="demo.lookup",
                    arguments={"query": f"page-{self.decisions}"},
                )
            ],
            model="test",
            provider="test",
            stop_reason="tool_use",
        )


def _direct_state() -> CodingAgentState:
    contract = build_evidence_contract("broad_review")
    return {
        "run_id": "",
        "conversation_id": "direct-session",
        "workspace_id": "workspace-main",
        "workspace_root": "/tmp",
        "execution_root": "/tmp",
        "user_input": "audit the repository",
        "intent": "repository_question",
        "context_route": "repo",
        "history": [],
        "focus_files": [],
        "project_instructions": [],
        "context_sources": [],
        "context_warnings": [],
        "context_shares": {},
        "run_artifact_read_enabled": False,
        "enabled_tools": ["demo.lookup"],
        "task_shape": "broad_review",
        "evidence_contract": contract,
        "task_tool_profile": ["demo.lookup"],
        "unresolved_requirements": list(contract["required_evidence"]),
        "tool_calls": [],
        "tool_results": [],
        "native_tool_messages": [],
        "native_tool_round": 0,
        "native_tool_call_count": 0,
        "task_model_request_count": 0,
        "native_tool_signatures": [],
        "native_context_compactions": 0,
        "artifacts": [],
        "errors": [],
        "trace": [],
        "native_no_progress_rounds": 0,
        "native_unfulfilled_change_rounds": 0,
        "native_consecutive_failures": 0,
        "native_soft_limit_warned": False,
        "evidence_coverage": [],
        "evidence_keys": [],
        "new_evidence_count": 0,
        "coverage_delta": 0,
        "duplicate_tool_call_count": 0,
        "evidence_extension_rounds": 0,
        "evidence_rounds_completed": 0,
        "change_iteration": 0,
    }


class NativeContractLoopTests(unittest.TestCase):
    def test_artifact_read_progress_requires_forward_cursor_movement(self) -> None:
        def read(start: int, end: int) -> dict[str, object]:
            return {
                "name": "run.read_artifact",
                "ok": True,
                "result": {
                    "artifact_id": "tool_result_0123456789abcdef0123",
                    "ranges": [{"start_char": start, "end_char": end}],
                },
            }

        state: CodingAgentState = {"tool_results": [read(100, 200)]}

        self.assertFalse(_artifact_read_progressed(state, [read(0, 100)]))
        self.assertTrue(_artifact_read_progressed(state, [read(200, 300)]))

    def test_successful_read_advances_unresolved_contract_stall_counter(self) -> None:
        registry = ToolRegistry()
        registry.register("demo.lookup", lambda query: {"query": query})
        planner = _DistinctReadPlanner()
        runtime = CodingAgentRuntime(tool_registry=registry, planner=planner)
        state = _direct_state()
        state.update(
            {
                "task_shape": "bounded_change",
                "intent": "change_planning",
                "mutation_authorized": True,
                "workspace_completion_required": True,
                "evidence_contract": build_evidence_contract("bounded_change"),
                "unresolved_requirements": [
                    "applied_change",
                    "validation_result",
                ],
                "completion_unresolved_rounds": 0,
                "change_completion_contract": {
                    "schema_version": 1,
                    "applicable": True,
                    "compatibility_mode": "strict",
                    "generation_status": "frozen",
                    "frozen": True,
                    "required_changes": [
                        {
                            "id": "change:create:app.py",
                            "target": "app.py",
                            "operation": "create",
                            "status": "pending",
                        }
                    ],
                    "required_validations": [],
                    "unresolved_changes": ["change:create:app.py"],
                    "unresolved_validations": [],
                    "completion_contract_satisfied": False,
                },
            }
        )

        state = {**state, **runtime._tool_loop_nodes._plan_tools(state)}
        state = {**state, **runtime._tool_loop_nodes._inspect_repository(state)}

        self.assertEqual(state["completion_unresolved_rounds"], 1)
        self.assertEqual(state["native_no_progress_rounds"], 1)
        self.assertFalse(state["trace"][-1]["output"]["semantic_progress"])

    def test_distinct_successful_reads_do_not_mask_semantic_stagnation(self) -> None:
        registry = ToolRegistry()
        registry.register(
            "demo.lookup",
            lambda query: {"query": query, "value": 42},
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        )
        planner = _DistinctReadPlanner()
        runtime = CodingAgentRuntime(tool_registry=registry, planner=planner)
        state = _direct_state()
        state["run_budget_mode"] = "unbounded"

        for expected_rounds in range(1, 4):
            state = {**state, **runtime._tool_loop_nodes._plan_tools(state)}
            state = {**state, **runtime._tool_loop_nodes._inspect_repository(state)}
            self.assertEqual(state["native_no_progress_rounds"], expected_rounds)
            self.assertEqual(state["coverage_delta"], 0)

        terminal = runtime._tool_loop_nodes._plan_tools(state)

        self.assertEqual(planner.decisions, 3)
        self.assertEqual(terminal["terminal_reason"], "no_progress")
        self.assertEqual(terminal["terminal_status"], "partial")
        self.assertEqual(planner.final_tool_names, [])

    def test_change_action_phase_hides_only_exploratory_read_tools(self) -> None:
        specs = create_coding_tool_registry().list_specs()
        visible, suppressed = _change_action_tool_specs(
            {
                "task_shape": "bounded_change",
                "mutation_authorized": True,
                "native_no_progress_rounds": 2,
                "unresolved_requirements": [
                    "applied_change",
                    "validation_result",
                ],
                "change_completion_contract": {
                    "applicable": True,
                    "generation_status": "frozen",
                    "compatibility_mode": "strict",
                    "completion_contract_satisfied": False,
                },
            },
            specs,
            no_progress_rounds=3,
        )
        visible_names = {spec.name for spec in visible}

        self.assertIn("repo.collect_evidence", suppressed)
        self.assertIn("repo.read_file", suppressed)
        self.assertNotIn("repo.collect_evidence", visible_names)
        self.assertNotIn("repo.read_file", visible_names)
        self.assertIn("agent.request_user_input", visible_names)
        self.assertIn("test_designer", visible_names)
        self.assertIn("change_planner", visible_names)
        self.assertIn("sandbox.write_file", visible_names)
        self.assertIn("sandbox.run_command", visible_names)

    def test_equivalent_call_is_blocked_and_finalization_has_no_tools(self) -> None:
        registry = ToolRegistry()
        registry.register(
            "demo.lookup",
            lambda query: {"query": query, "value": 42},
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        )
        planner = _RepeatedPlanner()
        runtime = CodingAgentRuntime(tool_registry=registry, planner=planner)
        state = _direct_state()

        planned = runtime._tool_loop_nodes._plan_tools(state)
        state = {**state, **planned}
        inspected = runtime._tool_loop_nodes._inspect_repository(state)
        state = {**state, **inspected}
        terminal = runtime._tool_loop_nodes._plan_tools(state)

        self.assertEqual(len(state["tool_results"]), 1)
        self.assertEqual(terminal["terminal_reason"], "duplicate_equivalent_tool_call")
        self.assertEqual(terminal["duplicate_tool_call_count"], 1)
        self.assertEqual(planner.final_tool_names, [])

    def test_only_one_limited_extension_is_available(self) -> None:
        planner = _RepeatedPlanner()
        registry = ToolRegistry()
        registry.register("demo.lookup", lambda query: {"query": query})
        runtime = CodingAgentRuntime(tool_registry=registry, planner=planner)
        state = _direct_state()
        state.update(
            {
                "native_tool_round": 6,
                "evidence_extension_rounds": 1,
            }
        )

        terminal = runtime._tool_loop_nodes._plan_tools(state)

        self.assertEqual(terminal["terminal_reason"], "evidence_extension_exhausted")
        self.assertEqual(terminal["evidence_extension_rounds"], 1)
        self.assertEqual(planner.final_tool_names, [])


class _OverviewPlanner:
    uses_native_tool_calling = True

    def __init__(self) -> None:
        self.final_tool_names: list[str] | None = None
        self.model_requests = 0

    def classify_intent(self, user_input):
        del user_input
        return {
            "intent": "repository_question",
            "reason": "test",
            "confidence": 1.0,
            "source": "test",
        }

    def plan_tool_calls(self, state, tool_specs):
        del state, tool_specs
        return []

    def decide_tool_calls(self, messages, tool_specs):
        del messages, tool_specs
        self.model_requests += 1
        return self._answer()

    def finalize_tool_session(
        self, messages, *, reason, tool_specs, max_output_tokens
    ):
        del messages, reason, max_output_tokens
        self.model_requests += 1
        self.final_tool_names = [spec.name for spec in tool_specs]
        return self._answer()

    def compose_answer(self, state):
        del state
        return self._answer().text

    @staticmethod
    def _answer():
        return LLMToolDecision(
            text=(
                "项目用途：AI Agent 平台。主要模块：API、Agent runtime、工具层。"
                "运行入口：ai_agent_platform.main。关键技术栈：FastAPI、LangGraph、pytest。"
            ),
            tool_calls=[],
            model="test",
            provider="test",
            stop_reason="end_turn",
        )


class _CheckpointPlanner(_OverviewPlanner):
    def __init__(self) -> None:
        super().__init__()
        self.decisions = 0

    def decide_tool_calls(self, messages, tool_specs):
        del tool_specs
        self.decisions += 1
        if self.decisions == 1:
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        call_id="checkpoint-input",
                        name="agent.request_user_input",
                        arguments={"question": "Which missing symbol?"},
                    )
                ],
                model="test",
                provider="test",
                stop_reason="tool_use",
            )
        self.asserted_answer = any(
            message.get("role") == "tool"
            and message.get("call_id") == "checkpoint-input"
            for message in messages
        )
        return LLMToolDecision(
            text="Checkpoint state resumed consistently.",
            tool_calls=[],
            model="test",
            provider="test",
            stop_reason="end_turn",
        )


class _AdvicePlanner(_OverviewPlanner):
    def __init__(self) -> None:
        super().__init__()
        self.visible_tools: list[str] = []

    def classify_intent(self, user_input):
        del user_input
        return {
            "intent": "change_planning",
            "reason": "model over-classified a recommendation",
            "confidence": 1.0,
            "source": "test",
        }

    def decide_tool_calls(self, messages, tool_specs):
        del messages
        self.visible_tools = [spec.name for spec in tool_specs]
        self.model_requests += 1
        return LLMToolDecision(
            text="建议添加扫雷，但本次只提供建议，没有修改代码。",
            tool_calls=[],
            model="test",
            provider="test",
            stop_reason="end_turn",
        )


class OverviewRegressionTests(unittest.TestCase):
    def test_recommendation_run_cannot_inherit_write_tools_from_llm_intent(self) -> None:
        planner = _AdvicePlanner()
        with TemporaryDirectory() as root:
            runtime = CodingAgentRuntime(planner=planner)
            result = runtime.run(
                conversation_id="advice-no-write",
                user_input="这个项目还建议添加什么小游戏？",
                history=[],
                workspace_id="workspace-main",
                workspace_root=root,
            )
            state = runtime._checkpoint_coordinator.snapshot_for(
                runtime._checkpoint_coordinator.config(result.thread_id)
            ).values

        self.assertEqual(result.status, "completed")
        self.assertEqual(state["request_mode"], "plan")
        self.assertFalse(state["mutation_authorized"])
        self.assertNotIn("applied_change", state["evidence_contract"]["required_evidence"])
        self.assertNotIn("sandbox.write_file", planner.visible_tools)
        self.assertNotIn("sandbox.apply_patch", planner.visible_tools)

    def test_scripted_small_and_large_overviews_stay_within_gates(self) -> None:
        for module_count, tool_limit, model_limit in ((2, 10, 4), (40, 12, 5)):
            with self.subTest(module_count=module_count), TemporaryDirectory() as root:
                workspace = Path(root)
                (workspace / "README.md").write_text(
                    "AI Agent platform built with FastAPI and LangGraph. "
                    "Run with python -m ai_agent_platform.main.",
                    encoding="utf-8",
                )
                (workspace / "pyproject.toml").write_text(
                    "[project]\nname='agent-platform'\n",
                    encoding="utf-8",
                )
                package = workspace / "ai_agent_platform"
                package.mkdir()
                (package / "main.py").write_text("app = FastAPI()\n", encoding="utf-8")
                for index in range(module_count):
                    (package / f"module_{index}.py").write_text(
                        f"VALUE = {index}\n", encoding="utf-8"
                    )
                planner = _OverviewPlanner()
                result = CodingAgentRuntime(planner=planner).run(
                    conversation_id=f"overview-{module_count}",
                    user_input="分析下当前项目",
                    history=[],
                    workspace_id="workspace-main",
                    workspace_root=root,
                )

                self.assertLessEqual(result.metrics.model_request_count, model_limit)
                self.assertLessEqual(planner.model_requests, model_limit)
                self.assertLessEqual(result.metrics.tool_call_count, tool_limit)
                self.assertEqual(planner.final_tool_names, [])
                for section in ("项目用途", "主要模块", "运行入口", "关键技术栈"):
                    self.assertIn(section, result.answer)

    def test_checkpoint_resume_preserves_contract_profile_and_coverage(self) -> None:
        planner = _CheckpointPlanner()
        with TemporaryDirectory() as root:
            runtime = CodingAgentRuntime(planner=planner)
            waiting = runtime.run(
                conversation_id="shape-checkpoint",
                user_input="explain missing.py using the input tool",
                history=[],
                workspace_id="workspace-main",
                workspace_root=root,
            )
            before = runtime._checkpoint_coordinator.snapshot_for(
                runtime._checkpoint_coordinator.config(waiting.thread_id)
            ).values
            completed = runtime.resume(
                run_id=waiting.run_id,
                approved=True,
                feedback="MissingService",
            )
            after = runtime._checkpoint_coordinator.snapshot_for(
                runtime._checkpoint_coordinator.config(waiting.thread_id)
            ).values

        self.assertEqual(waiting.status, "waiting_input")
        self.assertEqual(completed.status, "completed")
        self.assertTrue(planner.asserted_answer)
        for key in (
            "task_shape",
            "evidence_contract",
            "task_tool_profile",
            "duplicate_tool_call_count",
            "evidence_extension_rounds",
        ):
            self.assertEqual(before[key], after[key])
        self.assertTrue(
            set(before["evidence_coverage"]).issubset(after["evidence_coverage"])
        )
        self.assertGreater(after["task_model_request_count"], before["task_model_request_count"])


if __name__ == "__main__":
    unittest.main()
