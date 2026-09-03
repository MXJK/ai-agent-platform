from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from threading import Lock
from time import perf_counter, sleep
from typing import TypedDict
import unittest

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from ai_agent_platform.agents.coding.checkpoint_coordinator import (
    CheckpointResumeCoordinator,
)
from ai_agent_platform.agents.coding.completion_contract import (
    define_completion_contract,
    resolve_change_targets,
)
from ai_agent_platform.agents.coding.models import CodingAgentState, ContextSource
from ai_agent_platform.agents.coding.policies import BudgetPolicy
from ai_agent_platform.agents.coding.planner import (
    LLMStructuredAgentPlanner,
    RuleBasedAgentPlanner,
)
from ai_agent_platform.agents.coding.task_shaping import build_evidence_contract
from ai_agent_platform.agents.coding.tools import create_coding_tool_registry
from ai_agent_platform.agents.coding_agent import CodingAgentRuntime
from ai_agent_platform.integrations.llm import LLMToolDecision
from ai_agent_platform.integrations.tools import ToolCall


class _AutonomousPlanner:
    def __init__(self) -> None:
        self.history: list[dict[str, str]] = []
        self.focus_files: list[str] = []

    def classify_intent(self, user_input: str):
        del user_input
        return {
            "intent": "change_planning",
            "reason": "test",
            "confidence": 1.0,
            "source": "test",
        }

    def classify_request(
        self,
        user_input,
        knowledge_bases,
        *,
        history=None,
        focus_files=None,
    ):
        del user_input, knowledge_bases
        self.history = list(history or [])
        self.focus_files = list(focus_files or [])
        return {
            **self.classify_intent(""),
            "action": "change",
            "target_hints": ["app.py", "invented.py"],
            "context_route": "repo",
            "route_reason": "live code change",
            "selected_knowledge_base_ids": [],
        }

    def plan_tool_calls(self, state, tool_specs):
        del state, tool_specs
        return []

    def plan_repair_tool_calls(self, state, tool_specs):
        del state, tool_specs
        return []

    def compose_answer(self, state):
        del state
        return "done"


class _ParallelPlanner(_AutonomousPlanner):
    uses_native_tool_calling = True
    single_tool_per_turn = True
    parallel_read_tools = True

    def __init__(self) -> None:
        super().__init__()
        self.decisions = 0

    def classify_request(self, *args, **kwargs):
        decision = super().classify_request(*args, **kwargs)
        return {
            **decision,
            "intent": "bug_investigation",
            "action": "diagnose",
            "target_hints": ["README.md"],
        }

    def decide_tool_calls(self, messages, tool_specs):
        del messages, tool_specs
        self.decisions += 1
        if self.decisions == 1:
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        call_id=f"parallel_{index}",
                        name="demo.lookup",
                        arguments={"query": str(index)},
                    )
                    for index in range(12)
                ],
                model="scripted",
                provider="test",
                stop_reason="tool_use",
            )
        return LLMToolDecision(
            text="done",
            tool_calls=[],
            model="scripted",
            provider="test",
            stop_reason="end_turn",
        )


class _AdvisoryFallbackPlanner(_AutonomousPlanner):
    def classify_request(self, *args, **kwargs):
        decision = super().classify_request(*args, **kwargs)
        decision.pop("action", None)
        decision["target_hints"] = []
        return decision


class _MinesweeperDiscoveryPlanner(_AutonomousPlanner):
    uses_native_tool_calling = True
    single_tool_per_turn = True

    def __init__(self) -> None:
        super().__init__()
        self.native_decisions = 0

    def classify_request(self, *args, **kwargs):
        decision = super().classify_request(*args, **kwargs)
        return {
            **decision,
            "target_terms": ["扫雷"],
            "target_hints": [],
        }

    def decide_tool_calls(self, messages, tool_specs):
        del tool_specs
        self.native_decisions += 1
        if self.native_decisions == 1:
            serialized = str(messages)
            target = "alpha.js" if "alpha.js" in serialized else "minesweeper.js"
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        call_id="write-discovered-target",
                        name="sandbox.write_file",
                        arguments={
                            "path": target,
                            "content": "// pending approved implementation\n",
                        },
                    )
                ],
                model="scripted",
                provider="test",
                stop_reason="tool_use",
            )
        return LLMToolDecision(
            text="no mutation in discovery regression",
            tool_calls=[],
            model="scripted",
            provider="test",
            stop_reason="end_turn",
        )


class _CounterState(TypedDict):
    count: int


class AutonomousUnboundedAgentTests(unittest.TestCase):
    def test_rule_fallback_treats_incomplete_work_continuation_as_change(self) -> None:
        runtime = CodingAgentRuntime(
            planner=RuleBasedAgentPlanner(),
            autonomous_mutation_enabled=True,
            run_budget_mode="unbounded",
        )
        state: CodingAgentState = {
            "user_input": "项目里的扫雷游戏没完成，继续做",
            "history": [],
            "focus_files": [],
            "context_warnings": [],
            "trace": [],
        }

        classified = runtime._context_nodes._classify_request(state)

        self.assertEqual(classified["intent"], "change_planning")
        self.assertEqual(classified["request_mode"], "change")
        self.assertTrue(classified["mutation_authorized"])
        self.assertEqual(classified["task_shape"], "bounded_change")
        self.assertIn("sandbox.apply_patch", classified["task_tool_profile"])

        for advisory, expected_mode in (
            ("这个功能没完成，为什么继续做会失败？", "diagnose"),
            ("这个功能没完成，是否继续做？", "answer"),
        ):
            read_only = runtime._context_nodes._classify_request(
                {
                    **state,
                    "user_input": advisory,
                    "trace": [],
                }
            )
            self.assertEqual(read_only["request_mode"], expected_mode)
            self.assertFalse(read_only["mutation_authorized"])
            self.assertNotIn("sandbox.apply_patch", read_only["task_tool_profile"])

    def test_structured_classifier_receives_history_and_parses_change_action(self) -> None:
        class Client:
            prompt = ""

            def complete(self, prompt):
                self.prompt = prompt
                return SimpleNamespace(
                    text=(
                        '{"intent":"change_planning","action":"change",'
                        '"target_terms":["扫雷"],'
                        '"target_hints":["app.py"],"reason":"continue change",'
                        '"confidence":1,"context_route":"repo",'
                        '"route_reason":"live code","selected_knowledge_base_ids":[]}'
                    )
                )

        client = Client()
        planner = LLMStructuredAgentPlanner(client)

        decision = planner.classify_request(
            "修改",
            [],
            history=[{"role": "user", "content": "请修改 app.py"}],
            focus_files=["app.py"],
        )

        self.assertEqual(decision["action"], "change")
        self.assertEqual(decision["target_terms"], ["扫雷"])
        self.assertEqual(decision["target_hints"], ["app.py"])
        self.assertIn("controlled_history", client.prompt)
        self.assertIn("请修改 app.py", client.prompt)

    def test_autonomous_fallback_never_creates_a_new_plan_mode(self) -> None:
        runtime = CodingAgentRuntime(
            planner=_AdvisoryFallbackPlanner(),
            autonomous_mutation_enabled=True,
            run_budget_mode="unbounded",
        )
        state: CodingAgentState = {
            "user_input": "如何修改 app.py？",
            "history": [],
            "focus_files": [],
            "context_warnings": [],
            "trace": [],
        }

        classified = runtime._context_nodes._classify_request(state)

        self.assertEqual(classified["request_mode"], "answer")
        self.assertFalse(classified["mutation_authorized"])

    def test_model_action_uses_controlled_history_but_filters_invented_targets(
        self,
    ) -> None:
        planner = _AutonomousPlanner()
        runtime = CodingAgentRuntime(
            planner=planner,
            autonomous_mutation_enabled=True,
            run_budget_mode="unbounded",
        )
        state: CodingAgentState = {
            "user_input": "修改",
            "history": [
                {"role": "user", "content": "请修改 app.py 并验证"},
                {"role": "assistant", "content": "我会先检查 app.py"},
            ],
            "focus_files": [],
            "context_warnings": [],
            "trace": [],
        }

        classified = runtime._context_nodes._classify_request(state)

        self.assertEqual(classified["request_mode"], "change")
        self.assertTrue(classified["mutation_authorized"])
        self.assertEqual(classified["task_shape"], "bounded_change")
        self.assertEqual(classified["model_target_hints"], ["app.py"])
        self.assertEqual(classified["evidence_contract"]["run_budget_mode"], "unbounded")
        self.assertNotIn("max_tool_calls", classified["evidence_contract"])
        self.assertEqual(planner.history, state["history"])

    def test_natural_language_target_reads_entry_and_freezes_missing_reference(self) -> None:
        html = (
            "<!doctype html>\n<title>扫雷</title>\n"
            '<link rel="stylesheet" href="minesweeper.css">\n'
            '<script src="minesweeper.js"></script>\n'
        )
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "minesweeper.html").write_text(html, encoding="utf-8")
            (root / "minesweeper.css").write_text("body {}\n", encoding="utf-8")
            result = CodingAgentRuntime(
                planner=_MinesweeperDiscoveryPlanner(),
                autonomous_mutation_enabled=True,
                run_budget_mode="unbounded",
            ).run(
                conversation_id="natural-language-minesweeper",
                user_input="项目的扫雷没完成，继续完成",
                history=[
                    {"role": "system", "content": "profile: system-ui"},
                    {"role": "assistant", "content": "look at gomoku.css"},
                ],
                workspace_id="workspace",
                workspace_root=temp_dir,
                focus_files=[],
            )

        self.assertNotEqual(result.terminal_reason, "completion_contract_unavailable")
        self.assertEqual(result.completion_contract["generation_status"], "frozen")
        self.assertIn(
            "minesweeper.html",
            [
                item.get("result", {}).get("path")
                for item in result.tool_results
                if item.get("name") == "repo.read_file" and item.get("ok")
            ],
        )
        self.assertEqual(
            [item["target"] for item in result.completion_contract["required_changes"]],
            ["minesweeper.js"],
        )
        self.assertEqual(
            result.completion_contract["required_changes"][0]["source"]["kind"],
            "live_repository_evidence",
        )
        searches = [
            str(item.get("result", {}).get("query") or "")
            for item in result.tool_results
            if item.get("name") in {"repo.search_code", "repo.find_files"}
        ]
        self.assertIn("扫雷", searches)
        self.assertNotIn("system-ui", "\n".join(searches))

    def test_native_seed_does_not_stop_with_unresolved_change_target(self) -> None:
        runtime = CodingAgentRuntime(
            planner=_MinesweeperDiscoveryPlanner(),
            autonomous_mutation_enabled=True,
            run_budget_mode="unbounded",
        )
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "style.css").write_text("body {}\n", encoding="utf-8")
            state: CodingAgentState = {
                "user_input": "项目的扫雷没完成，继续完成",
                "task_shape": "bounded_change",
                "run_budget_mode": "unbounded",
                "execution_root": temp_dir,
                "model_target_terms": ["扫雷"],
                "focus_files": [],
                "context_sources": [
                    ContextSource(
                        kind="file",
                        path="style.css",
                        start_line=1,
                        end_line=1,
                        text="body {}\n",
                        reason="unrelated live file",
                        content_hash="style",
                    )
                ],
                "context_files": ["style.css"],
                "context_chars": 8,
                "exploration_round": 2,
                "exploration_results": [],
                "analysis_tool_calls": [
                    ToolCall(name="repo.read_file", arguments={"path": "style.css"})
                ],
                "context_warnings": [],
                "trace": [],
            }
            assessed = runtime._context_nodes._assess_context(state)

        self.assertFalse(assessed["context_sufficient"])
        self.assertNotEqual(assessed["context_stop_reason"], "native_seed_sufficient")
        self.assertEqual(assessed["target_resolution_status"], "unresolved")

    def test_model_target_outside_live_candidates_is_rejected(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            state: CodingAgentState = {
                "task_shape": "bounded_change",
                "execution_root": temp_dir,
                "user_input": "继续完成应用",
                "model_target_terms": ["应用"],
                "focus_files": [],
                "context_sources": [
                    ContextSource(
                        kind="file",
                        path="app.py",
                        start_line=1,
                        end_line=1,
                        text="# 应用\nvalue = 1\n",
                        reason="live file",
                        content_hash="app",
                    )
                ],
            }
            resolution = resolve_change_targets(
                state,
                selected_paths=["invented.py"],
                selection_source="model",
            )

        self.assertEqual(resolution["target_resolution_status"], "ambiguous")
        self.assertEqual(resolution["resolved_change_targets"], [])
        self.assertEqual(resolution["rejected_target_paths"], ["invented.py"])

    def test_equal_live_targets_interrupt_for_user_confirmation(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "alpha.html").write_text(
                '<title>扫雷</title><script src="alpha.js"></script>',
                encoding="utf-8",
            )
            (root / "beta.html").write_text(
                '<title>扫雷</title><script src="beta.js"></script>',
                encoding="utf-8",
            )
            runtime = CodingAgentRuntime(
                planner=_MinesweeperDiscoveryPlanner(),
                autonomous_mutation_enabled=True,
                run_budget_mode="unbounded",
            )
            waiting = runtime.run(
                conversation_id="ambiguous-minesweeper",
                user_input="项目的扫雷没完成，继续完成",
                history=[],
                workspace_id="workspace",
                workspace_root=temp_dir,
            )

            self.assertEqual(waiting.status, "waiting_input")
            self.assertEqual(waiting.pending_approval["type"], "input_required")
            self.assertEqual(
                waiting.pending_approval["candidate_paths"],
                ["alpha.html", "alpha.js", "beta.html", "beta.js"],
            )
            self.assertEqual(
                waiting.pending_approval["question_protocol"],
                "structured-v1",
            )
            self.assertEqual(
                [
                    option["label"]
                    for option in waiting.pending_approval["questions"][0][
                        "options"
                    ]
                ],
                ["alpha.html", "alpha.js", "beta.html", "beta.js"],
            )
            resumed = runtime.resume(
                run_id=waiting.run_id,
                approved=True,
                input_response={
                    "answers": [
                        {
                            "id": "change-target-selection",
                            "selected": ["alpha.html"],
                        }
                    ]
                },
            )

        self.assertNotEqual(resumed.terminal_reason, "completion_contract_unavailable")
        self.assertEqual(resumed.completion_contract["generation_status"], "frozen")
        self.assertEqual(
            [item["target"] for item in resumed.completion_contract["required_changes"]],
            ["alpha.js"],
        )

    def test_explicit_target_selection_skip_returns_partial_without_mutation(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "alpha.html").write_text(
                '<title>扫雷</title><script src="alpha.js"></script>',
                encoding="utf-8",
            )
            (root / "beta.html").write_text(
                '<title>扫雷</title><script src="beta.js"></script>',
                encoding="utf-8",
            )
            runtime = CodingAgentRuntime(
                planner=_MinesweeperDiscoveryPlanner(),
                autonomous_mutation_enabled=True,
                run_budget_mode="unbounded",
            )
            waiting = runtime.run(
                conversation_id="skip-ambiguous-minesweeper",
                user_input="项目的扫雷没完成，继续完成",
                history=[],
                workspace_id="workspace",
                workspace_root=temp_dir,
            )
            resumed = runtime.resume(
                run_id=waiting.run_id,
                approved=True,
                input_response={
                    "answers": [
                        {
                            "id": "change-target-selection",
                            "selected": [],
                            "skipped": True,
                        }
                    ]
                },
            )

        self.assertEqual(waiting.status, "waiting_input")
        self.assertEqual(resumed.status, "partial")
        self.assertEqual(resumed.terminal_reason, "target_selection_skipped")
        self.assertEqual(resumed.change_summary.changed_files, [])
        self.assertEqual(resumed.completion_contract, {})

    def test_short_change_continuation_freezes_target_after_live_read(self) -> None:
        with TemporaryDirectory() as temp_dir:
            Path(temp_dir, "app.py").write_text("value = 1\n", encoding="utf-8")
            result = CodingAgentRuntime(
                planner=_AutonomousPlanner(),
                autonomous_mutation_enabled=True,
                run_budget_mode="unbounded",
            ).run(
                conversation_id="short-change-continuation",
                user_input="修改",
                history=[
                    {"role": "user", "content": "请修改 app.py 并验证"},
                    {"role": "assistant", "content": "我会先检查 app.py"},
                ],
                workspace_id="workspace",
                workspace_root=temp_dir,
            )

        self.assertNotEqual(result.terminal_reason, "completion_contract_unavailable")
        self.assertEqual(result.completion_contract["generation_status"], "frozen")
        self.assertEqual(
            [item["target"] for item in result.completion_contract["required_changes"]],
            ["app.py"],
        )
        self.assertEqual(
            result.completion_contract["required_changes"][0]["source"]["kind"],
            "live_repository_evidence",
        )

    def test_history_target_requires_a_live_file_before_contract_freeze(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "app.py"
            source.write_text("value = 1\n", encoding="utf-8")
            live_source = ContextSource(
                kind="file",
                path="app.py",
                start_line=1,
                end_line=1,
                text="value = 1\n",
                reason="live file read",
                content_hash="live-app",
            )
            base: CodingAgentState = {
                "user_input": "修改",
                "task_shape": "bounded_change",
                "workspace_completion_required": True,
                "execution_root": str(root),
                "model_target_hints": ["app.py"],
                "focus_files": [],
                "context_sources": [],
            }

            unavailable = define_completion_contract(base)
            frozen = define_completion_contract(
                {**base, "context_sources": [live_source]}
            )

        self.assertEqual(unavailable["generation_status"], "invalid")
        self.assertEqual(frozen["generation_status"], "frozen")
        self.assertEqual(frozen["required_changes"][0]["target"], "app.py")
        self.assertEqual(
            frozen["required_changes"][0]["source"]["kind"],
            "live_repository_evidence",
        )

    def test_unbounded_policy_ignores_cumulative_model_tool_and_time_counters(self) -> None:
        policy = BudgetPolicy(
            soft_tool_rounds=1,
            max_tool_rounds=2,
            soft_tool_calls=1,
            max_tool_calls=2,
            max_elapsed_seconds=1,
            no_progress_rounds=3,
            max_consecutive_failures=3,
        )
        state: CodingAgentState = {
            "run_budget_mode": "unbounded",
            "task_shape": "targeted_read",
            "evidence_contract": build_evidence_contract(
                "targeted_read", budget_mode="unbounded"
            ),
            "evidence_coverage": [],
            "native_tool_round": 10_000,
            "native_tool_call_count": 10_000,
            "task_model_request_count": 10_000,
            "started_at": perf_counter() - 10_000,
        }

        self.assertEqual(policy.stop(state), ("", "completed"))
        self.assertFalse(policy.soft_limit_reached(state))

    def test_unbounded_graph_recursion_limit_is_an_automatic_checkpoint_slice(self) -> None:
        builder = StateGraph(_CounterState)
        builder.add_node("tick", lambda state: {"count": state["count"] + 1})
        builder.add_edge(START, "tick")
        builder.add_conditional_edges(
            "tick",
            lambda state: END if state["count"] >= 5 else "tick",
            {"tick": "tick", END: END},
        )
        checkpointer = InMemorySaver()
        graph = builder.compile(checkpointer=checkpointer)
        coordinator = CheckpointResumeCoordinator(
            graph=graph,
            recursion_limit=2,
            checkpointer=checkpointer,
            continue_on_recursion=True,
        )

        state, _ = coordinator.invoke(
            {"count": 0}, coordinator.config("thread")
        )

        self.assertEqual(state["count"], 5)
        self.assertGreater(state["graph_slice_count"], 1)

    def test_one_model_step_runs_at_most_ten_safe_reads_in_parallel(self) -> None:
        active = 0
        peak = 0
        lock = Lock()

        def lookup(query: str):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                sleep(0.03)
                return {"query": query}
            finally:
                with lock:
                    active -= 1

        registry = create_coding_tool_registry()
        registry.register(
            "demo.lookup",
            lookup,
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
        )
        with TemporaryDirectory() as temp_dir:
            Path(temp_dir, "README.md").write_text("# Demo\n", encoding="utf-8")
            result = CodingAgentRuntime(
                tool_registry=registry,
                planner=_ParallelPlanner(),
                autonomous_mutation_enabled=True,
                run_budget_mode="unbounded",
                max_parallel_tools_per_step=50,
            ).run(
                conversation_id="parallel-cap",
                user_input="诊断 README.md 中的问题",
                history=[],
                workspace_id="workspace",
                workspace_root=temp_dir,
                focus_files=["README.md"],
            )

        executed = [
            item for item in result.tool_results if item.get("name") == "demo.lookup"
        ]
        suppressed = [
            item
            for step in result.trace
            for item in step.get("output", {}).get("suppressed_tools", [])
            if item.get("name") == "demo.lookup"
        ]
        self.assertEqual(len(executed), 10)
        self.assertEqual(peak, 10)
        self.assertEqual(
            [(item["call_id"], item["reason"]) for item in suppressed],
            [
                ("parallel_10", "step_tool_limit"),
                ("parallel_11", "step_tool_limit"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
