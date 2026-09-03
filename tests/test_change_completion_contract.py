from pathlib import Path
import shlex
import sys
from tempfile import TemporaryDirectory
import unittest

from ai_agent_platform.agents.coding.completion_contract import (
    advance_completion_contract,
    completion_contract_prompt,
    completion_contract_state,
    define_completion_contract,
    ensure_completion_contract,
    extend_completion_contract,
    extend_completion_contract_from_results,
    resolve_change_targets,
)
from ai_agent_platform.agents.coding.models import CodingAgentState, ContextSource
from ai_agent_platform.agents.coding_agent import CodingAgentRuntime
from ai_agent_platform.integrations.llm import LLMToolDecision
from ai_agent_platform.integrations.tools import ToolCall, ToolSpec
from ai_agent_platform.schemas import AgentRunEventsResponse, AgentRunResponse


HTML = """<!doctype html>
<link rel="stylesheet" href="minesweeper.css">
<button id="cell">Open</button>
<script src="minesweeper.js"></script>
"""


def _source() -> ContextSource:
    return ContextSource(
        kind="file",
        path="minesweeper.html",
        start_line=1,
        end_line=4,
        text=HTML,
        reason="live test fixture",
        content_hash="fixture",
    )


def _state(root: Path) -> CodingAgentState:
    return {
        "task_shape": "bounded_change",
        "user_input": "实现 minesweeper.html 引用的缺失 CSS 和 JS",
        "workspace_root": str(root),
        "execution_root": str(root),
        "focus_files": ["minesweeper.html"],
        "context_sources": [_source()],
        "tool_calls": [],
        "tool_results": [],
        "changed_files": [],
        "validation_results": [],
    }


def _result(call_id: str, name: str, *, ok: bool = True, result=None):
    return {
        "call_id": call_id,
        "name": name,
        "ok": ok,
        "result": result if result is not None else {},
    }


class _MinesweeperPlanner:
    uses_native_tool_calling = True
    single_tool_per_turn = True

    def __init__(self, *, stop_after_css: bool = False) -> None:
        self.decision = 0
        self.stop_after_css = stop_after_css
        self.saw_unresolved_js = False
        self.final_reason = ""

    def classify_intent(self, user_input: str):
        del user_input
        return {
            "intent": "change_planning",
            "reason": "completion contract regression",
            "confidence": 1.0,
            "source": "test",
        }

    def plan_tool_calls(self, state: CodingAgentState, tool_specs: list[ToolSpec]):
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
        return self._decision(text=f"finalized: {reason}")

    def decide_tool_calls(self, messages, tool_specs):
        del tool_specs
        self.decision += 1
        serialized = str(messages)
        self.saw_unresolved_js = self.saw_unresolved_js or (
            "minesweeper.js" in serialized and "unresolved" in serialized.casefold()
        )
        if self.decision == 1:
            return self._decision(
                ToolCall(
                    call_id="write-css",
                    name="sandbox.write_file",
                    arguments={
                        "path": "minesweeper.css",
                        "content": "#cell { color: green; }\n",
                    },
                    source="completion_contract_test",
                )
            )
        if self.stop_after_css:
            return self._decision(text="CSS is enough; done.")
        if self.decision == 2:
            # A premature final answer must be rejected and followed by another turn.
            return self._decision(text="The requested game is complete.")
        if self.decision == 3:
            return self._decision(
                ToolCall(
                    call_id="write-js",
                    name="sandbox.write_file",
                    arguments={
                        "path": "minesweeper.js",
                        "content": "document.querySelector('#cell').onclick = () => {};\n",
                    },
                    source="completion_contract_test",
                )
            )
        if self.decision == 4:
            return self._decision(
                ToolCall(
                    call_id="check-js",
                    name="sandbox.run_command",
                    arguments={"command": "node --check minesweeper.js"},
                    source="completion_contract_test",
                )
            )
        if self.decision == 5:
            source = (
                "from pathlib import Path; "
                "assert Path('minesweeper.css').is_file(); "
                "assert Path('minesweeper.js').is_file()"
            )
            return self._decision(
                ToolCall(
                    call_id="check-refs",
                    name="sandbox.run_command",
                    arguments={
                        "command": f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"
                    },
                    source="completion_contract_test",
                )
            )
        return self._decision(text="Everything is complete.")

    @staticmethod
    def _decision(call: ToolCall | None = None, *, text: str = ""):
        return LLMToolDecision(
            text=text,
            tool_calls=[call] if call is not None else [],
            model="scripted",
            provider="test",
            stop_reason="tool_use" if call is not None else "end_turn",
        )


class ChangeCompletionContractUnitTests(unittest.TestCase):
    def test_parent_relative_module_import_stays_inside_workspace(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "pages").mkdir()
            source = "import { boot } from '../feature.js';\nboot();\n"
            (root / "pages" / "app.js").write_text(source, encoding="utf-8")
            state: CodingAgentState = {
                "task_shape": "bounded_change",
                "workspace_completion_required": True,
                "user_input": "继续完成应用",
                "model_target_terms": ["应用"],
                "execution_root": str(root),
                "focus_files": [],
                "context_sources": [
                    ContextSource(
                        kind="file",
                        path="pages/app.js",
                        start_line=1,
                        end_line=2,
                        text="// 应用\n" + source,
                        reason="live file",
                        content_hash="parent-module-import",
                    )
                ],
            }
            resolution = resolve_change_targets(state)
            contract = define_completion_contract({**state, **resolution})

        self.assertEqual(
            [item["target"] for item in contract["required_changes"]],
            ["feature.js"],
        )

    def test_live_module_import_uses_shared_missing_reference_resolution(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = "import { boot } from './feature.js';\nboot();\n"
            (root / "app.js").write_text(source, encoding="utf-8")
            state: CodingAgentState = {
                "task_shape": "bounded_change",
                "workspace_completion_required": True,
                "user_input": "继续完成应用",
                "model_target_terms": ["应用"],
                "execution_root": str(root),
                "focus_files": [],
                "context_sources": [
                    ContextSource(
                        kind="file",
                        path="app.js",
                        start_line=1,
                        end_line=2,
                        text="// 应用\n" + source,
                        reason="live file",
                        content_hash="module-import",
                    )
                ],
            }
            resolution = resolve_change_targets(state)
            contract = define_completion_contract({**state, **resolution})

        self.assertEqual(resolution["target_resolution_status"], "resolved")
        self.assertEqual(
            [item["target"] for item in contract["required_changes"]],
            ["feature.js"],
        )

    def test_live_html_evidence_freezes_two_independent_changes(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "minesweeper.html").write_text(HTML, encoding="utf-8")
            contract = define_completion_contract(_state(root))

        self.assertTrue(contract["frozen"])
        self.assertEqual(contract["revision"], 1)
        self.assertEqual(
            [item["target"] for item in contract["required_changes"]],
            ["minesweeper.css", "minesweeper.js"],
        )
        self.assertEqual(
            {item["category"] for item in contract["required_validations"]},
            {"post_change", "javascript_syntax", "local_asset_references"},
        )

    def test_css_write_satisfies_only_css_and_keeps_js_unresolved(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "minesweeper.html").write_text(HTML, encoding="utf-8")
            state = _state(root)
            state["change_completion_contract"] = define_completion_contract(state)
            (root / "minesweeper.css").write_text("body{}\n", encoding="utf-8")
            contract = advance_completion_contract(
                state,
                results=[
                    _result(
                        "css",
                        "sandbox.write_file",
                        result={"path": "minesweeper.css"},
                    )
                ],
            )

        status = {item["target"]: item["status"] for item in contract["required_changes"]}
        self.assertEqual(status["minesweeper.css"], "satisfied")
        self.assertEqual(status["minesweeper.js"], "pending")
        self.assertIn("minesweeper.js", completion_contract_prompt(contract))
        self.assertFalse(contract["completion_contract_satisfied"])

    def test_files_validation_and_final_artifacts_are_all_required(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "minesweeper.html").write_text(HTML, encoding="utf-8")
            state = _state(root)
            contract = define_completion_contract(state)
            state["change_completion_contract"] = contract
            for target in ("minesweeper.css", "minesweeper.js"):
                (root / target).write_text("// ok\n", encoding="utf-8")
                contract = advance_completion_contract(
                    {**state, "change_completion_contract": contract},
                    results=[
                        _result(
                            f"write-{target}",
                            "sandbox.write_file",
                            result={"path": target},
                        )
                    ],
                )
            self.assertFalse(contract["completion_contract_satisfied"])
            self.assertEqual(contract["unresolved_changes"], [])

            calls = [
                ToolCall(
                    call_id="js",
                    name="sandbox.run_command",
                    arguments={"command": "node --check minesweeper.js"},
                ),
                ToolCall(
                    call_id="refs",
                    name="sandbox.run_command",
                    arguments={
                        "command": (
                            "python -c \"from pathlib import Path; "
                            "assert Path('minesweeper.css').is_file(); "
                            "assert Path('minesweeper.js').is_file()\""
                        )
                    },
                ),
            ]
            contract = advance_completion_contract(
                {
                    **state,
                    "change_completion_contract": contract,
                    "tool_calls": calls,
                },
                results=[
                    _result("js", "sandbox.run_command", result={"exit_code": 0}),
                    _result("refs", "sandbox.run_command", result={"exit_code": 0}),
                ],
            )
            self.assertEqual(contract["unresolved_validations"], [])
            self.assertFalse(contract["completion_contract_satisfied"])
            contract = advance_completion_contract(
                {**state, "change_completion_contract": contract},
                changed_files=["minesweeper.css", "minesweeper.js"],
                final_workspace_status_collected=True,
                final_diff_collected=True,
            )
        self.assertTrue(contract["completion_contract_satisfied"])

    def test_failed_required_validation_cannot_satisfy_contract(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "minesweeper.html").write_text(HTML, encoding="utf-8")
            state = _state(root)
            state["change_completion_contract"] = define_completion_contract(state)
            state["tool_calls"] = [
                ToolCall(
                    call_id="js-failed",
                    name="sandbox.run_command",
                    arguments={"command": "node --check minesweeper.js"},
                )
            ]
            contract = advance_completion_contract(
                state,
                results=[
                    _result(
                        "js-failed",
                        "sandbox.run_command",
                        ok=False,
                        result={"exit_code": 1},
                    )
                ],
            )
        js_validation = next(
            item
            for item in contract["required_validations"]
            if item["category"] == "javascript_syntax"
        )
        self.assertEqual(js_validation["status"], "failed")
        self.assertFalse(contract["completion_contract_satisfied"])

    def test_git_diff_check_alone_does_not_satisfy_functional_validation(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "minesweeper.html").write_text(HTML, encoding="utf-8")
            state = _state(root)
            state["change_completion_contract"] = define_completion_contract(state)
            state["tool_calls"] = [
                ToolCall(
                    call_id="diff-only",
                    name="sandbox.run_command",
                    arguments={"command": "git diff --check"},
                )
            ]
            contract = advance_completion_contract(
                state,
                results=[
                    _result(
                        "diff-only",
                        "sandbox.run_command",
                        result={"exit_code": 0},
                    )
                ],
            )
        self.assertEqual(
            {item["status"] for item in contract["required_validations"]},
            {"pending"},
        )

    def test_contract_cannot_be_silently_shrunk(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "minesweeper.html").write_text(HTML, encoding="utf-8")
            state = _state(root)
            original = define_completion_contract(state)
            state["change_completion_contract"] = original
            restored = ensure_completion_contract(state)
            extended = extend_completion_contract(
                restored,
                additions=[],
                reason="no new authoritative evidence",
            )
        self.assertEqual(extended["revision"], 1)
        self.assertEqual(
            [item["id"] for item in extended["required_changes"]],
            [item["id"] for item in original["required_changes"]],
        )

    def test_new_live_html_evidence_only_appends_with_revision_and_trace(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("value = 1\n", encoding="utf-8")
            (root / "late.html").write_text(HTML, encoding="utf-8")
            state: CodingAgentState = {
                "task_shape": "bounded_change",
                "user_input": "update app.py",
                "workspace_root": str(root),
                "execution_root": str(root),
                "focus_files": ["app.py"],
                "context_sources": [],
                "tool_results": [],
            }
            original = define_completion_contract(state)
            state["change_completion_contract"] = original
            expanded = extend_completion_contract_from_results(
                state,
                results=[
                    _result(
                        "read-html",
                        "repo.read_file",
                        result={"path": "late.html", "content": HTML},
                    )
                ],
            )
        self.assertEqual(expanded["revision"], 2)
        self.assertEqual(expanded["trace"][-1]["reason"], "evidence_backed_extension")
        self.assertEqual(
            {item["target"] for item in expanded["required_changes"]},
            {"app.py", "minesweeper.css", "minesweeper.js"},
        )
        original_id = original["required_changes"][0]["id"]
        self.assertIn(
            original_id, {item["id"] for item in expanded["required_changes"]}
        )

    def test_legacy_checkpoint_uses_explicit_phase1_compatibility(self) -> None:
        state: CodingAgentState = {
            "task_shape": "bounded_change",
            "tool_results": [
                _result("legacy-write", "sandbox.write_file", result={"path": "app.py"})
            ],
            "changed_files": ["app.py"],
        }
        contract = ensure_completion_contract(state)
        self.assertEqual(contract["compatibility_mode"], "legacy_phase1")
        self.assertEqual(completion_contract_state(state), "legacy_phase1")


class ChangeCompletionContractRuntimeTests(unittest.TestCase):
    def test_minesweeper_single_tool_rounds_resume_monotonically_and_complete(self) -> None:
        planner = _MinesweeperPlanner()
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "minesweeper.html").write_text(HTML, encoding="utf-8")
            runtime = CodingAgentRuntime(planner=planner)
            css_wait = runtime.run(
                conversation_id="minesweeper-contract",
                user_input="实现 minesweeper.html 引用的缺失 CSS 和 JS",
                history=[],
                workspace_id="workspace-main",
                workspace_root=temp_dir,
                focus_files=["minesweeper.html"],
            )
            js_wait = runtime.resume(run_id=css_wait.run_id, approved=True)
            css_contract = js_wait.completion_contract
            syntax_wait = runtime.resume(run_id=css_wait.run_id, approved=True)
            refs_wait = runtime.resume(run_id=css_wait.run_id, approved=True)
            result = runtime.resume(run_id=css_wait.run_id, approved=True)

        self.assertEqual(
            [css_wait.status, js_wait.status, syntax_wait.status, refs_wait.status],
            ["waiting_approval"] * 4,
        )
        css_status = {
            item["target"]: item["status"]
            for item in css_contract["required_changes"]
        }
        self.assertEqual(css_status["minesweeper.css"], "satisfied")
        self.assertEqual(css_status["minesweeper.js"], "pending")
        self.assertEqual(css_contract["revision"], 1)
        self.assertEqual(syntax_wait.completion_contract["revision"], 1)
        css_satisfied = {
            item["id"]
            for item in css_contract["required_changes"]
            if item["status"] == "satisfied"
        }
        later_satisfied = {
            item["id"]
            for item in syntax_wait.completion_contract["required_changes"]
            if item["status"] == "satisfied"
        }
        self.assertTrue(css_satisfied.issubset(later_satisfied))
        self.assertTrue(planner.saw_unresolved_js)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.terminal_reason, "completion_contract_satisfied")
        self.assertTrue(result.completion_contract["completion_contract_satisfied"])
        self.assertEqual(result.completion_contract["unresolved_changes"], [])
        self.assertEqual(result.completion_contract["unresolved_validations"], [])
        lifecycle = [
            item["name"]
            for item in result.tool_results
            if item["name"] in {"sandbox.write_file", "sandbox.run_command"}
        ]
        self.assertEqual(
            lifecycle,
            [
                "sandbox.write_file",
                "sandbox.write_file",
                "sandbox.run_command",
                "sandbox.run_command",
            ],
        )

        response = AgentRunResponse.from_domain(result)
        self.assertTrue(
            response.completion_contract["completion_contract_satisfied"]
        )
        events = AgentRunEventsResponse.from_events(
            result.run_id, runtime.list_events(result.run_id)
        )
        event_types = [event.type for event in events.events]
        self.assertIn("evidence_satisfied", event_types)
        self.assertIn("mutation_applied", event_types)
        self.assertIn("validation_passed", event_types)
        self.assertIn("completion_contract_satisfied", event_types)

    def test_hard_budget_preserves_css_partial_and_never_claims_completed(self) -> None:
        planner = _MinesweeperPlanner(stop_after_css=True)
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "minesweeper.html").write_text(HTML, encoding="utf-8")
            runtime = CodingAgentRuntime(
                planner=planner,
                max_tool_rounds=2,
                no_progress_rounds=10,
            )
            waiting = runtime.run(
                conversation_id="minesweeper-partial",
                user_input="实现 minesweeper.html 引用的缺失 CSS 和 JS",
                history=[],
                workspace_id="workspace-main",
                workspace_root=temp_dir,
                focus_files=["minesweeper.html"],
            )
            result = runtime.resume(run_id=waiting.run_id, approved=True)

        self.assertEqual(result.status, "partial")
        self.assertEqual(
            result.terminal_reason, "completion_requirements_unresolved"
        )
        unresolved_targets = {
            item["target"]
            for item in result.completion_contract["required_changes"]
            if item["status"] != "satisfied"
        }
        self.assertEqual(unresolved_targets, {"minesweeper.js"})
        self.assertIn("minesweeper.js", result.answer)


if __name__ == "__main__":
    unittest.main()
