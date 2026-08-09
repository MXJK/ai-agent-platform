from pathlib import Path
import shlex
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from google.genai import types

from ai_agent_platform.agents.coding.models import CodingAgentState
from ai_agent_platform.agents.coding_agent import (
    CodingAgentRuntime,
    create_coding_tool_registry,
)
from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.llm import (
    LLMClient,
    LLMToolDecision,
    LLMUsage,
)
from ai_agent_platform.integrations.tools import ToolCall, ToolSpec


def _tool_spec(name: str = "repo.read_file") -> ToolSpec:
    return ToolSpec(
        name=name,
        description="Read one file.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        provider="local",
    )


class NativeProviderMappingTests(unittest.TestCase):
    def test_native_provider_can_select_namespaced_mcp_tool(self) -> None:
        client = LLMClient(
            Settings(
                llm_provider="openai",
                llm_model="test-openai",
                openai_api_key="test-key",
            )
        )
        mcp_spec = _tool_spec("mcp.github.search_code")

        response = {
                "model": "test-openai",
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "mcp_call_1",
                        "name": "mcp_github_search_code",
                        "arguments": '{"path":"README.md"}',
                    }
                ],
            }

        def fake_post(url, *, headers, payload):
            if url.endswith("/input_tokens"):
                return {"input_tokens": 8}
            return response

        with patch.object(client, "_post_json", side_effect=fake_post) as post:
            decision = client.decide_tools(
                [{"role": "user", "content": "search GitHub"}],
                [mcp_spec],
            )

        self.assertEqual(decision.tool_calls[0].name, "mcp.github.search_code")
        self.assertEqual(decision.tool_calls[0].call_id, "mcp_call_1")
        self.assertEqual(
            post.call_args.kwargs["payload"]["tools"][0]["name"],
            "mcp_github_search_code",
        )

    def test_openai_uses_native_tools_and_preserves_call_id_for_result(self) -> None:
        client = LLMClient(
            Settings(
                llm_provider="openai",
                llm_model="test-openai",
                openai_api_key="test-key",
            )
        )
        responses = [
            {
                "model": "test-openai",
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "id": "fc_1",
                        "call_id": "call_1",
                        "name": "repo_read_file",
                        "arguments": '{"path":"app.py"}',
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 4},
            },
            {
                "model": "test-openai",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "app.py contains value=1"}
                        ],
                    }
                ],
                "usage": {"input_tokens": 20, "output_tokens": 6},
            },
        ]
        payloads: list[dict[str, object]] = []

        def fake_post(url, *, headers, payload):
            if url.endswith("/input_tokens"):
                return {"input_tokens": 10}
            payloads.append(payload)
            return responses.pop(0)

        with patch.object(client, "_post_json", side_effect=fake_post):
            first = client.decide_tools(
                [{"role": "user", "content": "read app.py"}],
                [_tool_spec()],
            )
            second = client.decide_tools(
                [
                    {"role": "user", "content": "read app.py"},
                    {
                        "role": "assistant",
                        "content": first.text,
                        "provider": first.provider,
                        "provider_items": first.provider_items,
                        "tool_calls": [
                            {
                                "call_id": first.tool_calls[0].call_id,
                                "name": first.tool_calls[0].name,
                                "arguments": first.tool_calls[0].arguments,
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "call_id": "call_1",
                        "name": "repo.read_file",
                        "content": {"ok": True, "result": {"content": "value=1"}},
                    },
                ],
                [_tool_spec()],
            )

        self.assertEqual(first.tool_calls[0].name, "repo.read_file")
        self.assertEqual(first.tool_calls[0].call_id, "call_1")
        self.assertEqual(
            payloads[0]["tools"][0]["name"],
            "repo_read_file",
        )
        result_item = next(
            item
            for item in payloads[1]["input"]
            if item.get("type") == "function_call_output"
        )
        self.assertEqual(result_item["call_id"], "call_1")
        self.assertEqual(second.text, "app.py contains value=1")

    def test_openai_finalization_preserves_tool_transcript_and_disables_tools(self) -> None:
        client = LLMClient(
            Settings(
                llm_provider="openai",
                llm_model="test-openai",
                openai_api_key="test-key",
            )
        )
        captured: dict[str, object] = {}

        def fake_post(url, *, headers, payload):
            del headers
            if url.endswith("/input_tokens"):
                return {"input_tokens": 12}
            captured.update(payload)
            return {
                "model": "test-openai",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "Grounded final answer."}
                        ],
                    }
                ],
            }

        messages = [
            {"role": "user", "content": "read app.py"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "call_id": "call_final",
                        "name": "repo.read_file",
                        "arguments": {"path": "app.py"},
                    }
                ],
            },
            {
                "role": "tool",
                "call_id": "call_final",
                "name": "repo.read_file",
                "content": {"ok": True, "result": {"content": "value=1"}},
            },
        ]
        with patch.object(client, "_post_json", side_effect=fake_post):
            decision = client.finalize_tools(messages, reason="hard_tool_round_budget")

        self.assertEqual(decision.text, "Grounded final answer.")
        self.assertNotIn("tools", captured)
        self.assertNotIn("tool_choice", captured)
        self.assertTrue(
            any(item.get("type") == "function_call_output" for item in captured["input"])
        )

    def test_anthropic_maps_tool_use_and_usage(self) -> None:
        client = LLMClient(
            Settings(
                llm_provider="anthropic",
                llm_model="test-claude",
                anthropic_api_key="test-key",
            )
        )
        captured: dict[str, object] = {}

        def fake_post(url, *, headers, payload):
            if url.endswith("/count_tokens"):
                return {"input_tokens": 11}
            captured.update(payload)
            return {
                "model": "test-claude",
                "stop_reason": "tool_use",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "repo_read_file",
                        "input": {"path": "app.py"},
                    }
                ],
                "usage": {"input_tokens": 11, "output_tokens": 5},
            }

        with patch.object(client, "_post_json", side_effect=fake_post):
            decision = client.decide_tools(
                [{"role": "user", "content": "read app.py"}],
                [_tool_spec()],
            )

        self.assertEqual(decision.tool_calls[0].call_id, "toolu_1")
        self.assertEqual(decision.tool_calls[0].name, "repo.read_file")
        self.assertEqual(decision.usage, LLMUsage(input_tokens=11, output_tokens=5))
        self.assertEqual(captured["tools"][0]["name"], "repo_read_file")

    def test_google_maps_function_call_and_provider_content(self) -> None:
        response_content = types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        id="google_call_1",
                        name="repo_read_file",
                        args={"path": "app.py"},
                    )
                )
            ],
        )
        response = SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=response_content,
                    finish_reason="STOP",
                )
            ],
            usage_metadata=SimpleNamespace(
                prompt_token_count=9,
                candidates_token_count=3,
                thoughts_token_count=1,
            ),
        )

        class FakeModels:
            def __init__(self) -> None:
                self.kwargs: dict[str, object] = {}

            def generate_content(self, **kwargs):
                self.kwargs = kwargs
                return response

            def count_tokens(self, **kwargs):
                return SimpleNamespace(total_tokens=9)

        fake_client = SimpleNamespace(
            models=FakeModels(),
            close=lambda: None,
        )
        client = LLMClient(
            Settings(
                llm_provider="google",
                llm_model="gemini-test",
                google_api_key="test-key",
            )
        )

        with patch("google.genai.Client", return_value=fake_client):
            decision = client.decide_tools(
                [{"role": "user", "content": "read app.py"}],
                [_tool_spec()],
            )

        self.assertEqual(decision.tool_calls[0].call_id, "google_call_1")
        self.assertEqual(decision.tool_calls[0].name, "repo.read_file")
        self.assertEqual(decision.usage.total_tokens, 13)
        self.assertTrue(decision.provider_items)
        config = fake_client.models.kwargs["config"]
        self.assertEqual(
            config.tools[0].function_declarations[0].name,
            "repo_read_file",
        )


class ScriptedNativePlanner:
    uses_native_tool_calling = True

    def __init__(self) -> None:
        self.decisions = 0
        self.observed_tool_result = False

    def classify_intent(self, user_input: str) -> dict[str, object]:
        return {
            "intent": "code_explanation",
            "reason": "native tool loop test",
            "confidence": 1.0,
            "source": "test",
        }

    def plan_tool_calls(
        self,
        state: CodingAgentState,
        tool_specs: list[ToolSpec],
    ) -> list[ToolCall]:
        return []

    def decide_tool_calls(
        self,
        messages: list[dict[str, object]],
        tool_specs: list[ToolSpec],
    ) -> LLMToolDecision:
        self.decisions += 1
        if self.decisions == 1:
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        call_id="native_call_1",
                        name="demo.lookup",
                        arguments={"query": "value"},
                        source="test_native",
                    )
                ],
                model="scripted",
                provider="test",
                stop_reason="tool_use",
            )
        tool_message = messages[-1]
        self.observed_tool_result = (
            tool_message.get("role") == "tool"
            and tool_message.get("call_id") == "native_call_1"
            and bool(tool_message.get("content", {}).get("ok"))
        )
        return LLMToolDecision(
            text="Observed the tool result and found value=42.",
            tool_calls=[],
            model="scripted",
            provider="test",
            stop_reason="end_turn",
        )

    def plan_repair_tool_calls(self, state, tool_specs):
        return []

    def compose_answer(self, state: CodingAgentState) -> str:
        return "fallback"


class RecoveringArtifactNativePlanner(ScriptedNativePlanner):
    def __init__(self) -> None:
        super().__init__()
        self.observed_failure = False
        self.observed_recovery = False

    def decide_tool_calls(
        self,
        messages: list[dict[str, object]],
        tool_specs: list[ToolSpec],
    ) -> LLMToolDecision:
        del tool_specs
        self.decisions += 1
        if self.decisions == 1:
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        call_id="list_failure",
                        name="repo.list_files",
                        arguments={"path": "missing"},
                        source="test_native",
                    ),
                    ToolCall(
                        call_id="workspace_status",
                        name="sandbox.workspace_status",
                        arguments={},
                        source="test_native",
                    ),
                ],
                model="scripted",
                provider="test",
                stop_reason="tool_use",
            )
        if self.decisions == 2:
            tool_messages = [
                message for message in messages if message.get("role") == "tool"
            ]
            self.observed_failure = any(
                message.get("call_id") == "list_failure"
                and not bool(message.get("content", {}).get("ok"))
                for message in tool_messages
            )
            self.observed_tool_result = any(
                message.get("call_id") == "workspace_status"
                and bool(message.get("content", {}).get("ok"))
                for message in tool_messages
            )
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        call_id="read_recovery",
                        name="repo.read_file",
                        arguments={"path": "app.py"},
                        source="test_native",
                    )
                ],
                model="scripted",
                provider="test",
                stop_reason="tool_use",
            )
        tool_messages = [
            message for message in messages if message.get("role") == "tool"
        ]
        self.observed_recovery = any(
            message.get("call_id") == "read_recovery"
            and bool(message.get("content", {}).get("ok"))
            for message in tool_messages
        )
        return LLMToolDecision(
            text="Recovered by reading app.py after the failed listing.",
            tool_calls=[],
            model="scripted",
            provider="test",
            stop_reason="end_turn",
        )


class HardBudgetNativePlanner(ScriptedNativePlanner):
    def __init__(self) -> None:
        super().__init__()
        self.finalizations = 0
        self.finalized_with_results = False

    def decide_tool_calls(self, messages, tool_specs):
        del messages, tool_specs
        self.decisions += 1
        path = "app.py" if self.decisions == 1 else "other.py"
        return LLMToolDecision(
            text="",
            tool_calls=[
                ToolCall(
                    call_id=f"hard_{self.decisions}",
                    name="repo.read_file",
                    arguments={"path": path},
                    source="test_native",
                )
            ],
            model="scripted",
            provider="test",
            stop_reason="tool_use",
        )

    def finalize_tool_session(self, messages, *, reason):
        self.finalizations += 1
        self.finalized_with_results = sum(
            message.get("role") == "tool" and message.get("content", {}).get("ok")
            for message in messages
        ) == 2
        return LLMToolDecision(
            text=f"Partial grounded answer after {reason}.",
            tool_calls=[],
            model="scripted",
            provider="test",
            stop_reason="end_turn",
        )


class UnifiedChangeNativePlanner(ScriptedNativePlanner):
    def __init__(self, command: str) -> None:
        super().__init__()
        self.command = command
        self.observed_order: list[str] = []

    def classify_intent(self, user_input: str) -> dict[str, object]:
        return {
            "intent": "change_planning",
            "reason": "unified native change test",
            "confidence": 1.0,
            "source": "test",
        }

    def decide_tool_calls(self, messages, tool_specs):
        del tool_specs
        self.decisions += 1
        if self.decisions == 1:
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        call_id="write_1",
                        name="sandbox.write_file",
                        arguments={"path": "app.py", "content": "value = 43\n"},
                        source="test_native",
                    ),
                    ToolCall(
                        call_id="validate_1",
                        name="sandbox.run_command",
                        arguments={"command": self.command},
                        source="test_native",
                    ),
                ],
                model="scripted",
                provider="test",
                stop_reason="tool_use",
            )
        self.observed_order = [
            str(message.get("name"))
            for message in messages
            if message.get("role") == "tool"
        ]
        return LLMToolDecision(
            text="Changed and validated app.py after observing all tool results.",
            tool_calls=[],
            model="scripted",
            provider="test",
            stop_reason="end_turn",
        )


class NativeToolLoopTests(unittest.TestCase):
    def test_hard_budget_reserves_text_only_finalization_and_returns_partial(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("value = 42\n", encoding="utf-8")
            (root / "other.py").write_text("other = 1\n", encoding="utf-8")
            planner = HardBudgetNativePlanner()
            runtime = CodingAgentRuntime(
                planner=planner,
                max_tool_rounds=2,
                max_tool_calls=4,
            )

            result = runtime.run(
                conversation_id="sess_hard_budget",
                user_input="read both files until the hard budget",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
            )

        self.assertEqual(result.status, "partial")
        self.assertEqual(
            result.answer,
            "Partial grounded answer after hard_tool_round_budget.",
        )
        self.assertEqual(planner.decisions, 2)
        self.assertEqual(planner.finalizations, 1)
        self.assertTrue(planner.finalized_with_results)

    def test_native_loop_executes_change_and_validation_in_model_order(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "app.py"
            source.write_text("value = 42\n", encoding="utf-8")
            validation = (
                "compile(open('app.py', encoding='utf-8').read(), "
                "'app.py', 'exec')"
            )
            planner = UnifiedChangeNativePlanner(
                f"{shlex.quote(sys.executable)} -c {shlex.quote(validation)}"
            )
            runtime = CodingAgentRuntime(planner=planner)

            waiting = runtime.run(
                conversation_id="sess_unified_change",
                user_input="change app.py and validate it",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
            )
            result = runtime.resume(run_id=waiting.run_id, approved=True)
            original_source = source.read_text(encoding="utf-8")

        self.assertEqual(waiting.status, "waiting_approval")
        self.assertEqual(result.status, "completed")
        self.assertEqual(
            planner.observed_order[:2],
            ["sandbox.write_file", "sandbox.run_command"],
        )
        self.assertIn("sandbox.workspace_status", planner.observed_order)
        self.assertIn("sandbox.git_diff", planner.observed_order)
        self.assertTrue(result.change_summary.validation_passed)
        self.assertEqual(original_source, "value = 42\n")

    def test_tool_result_is_fed_back_before_final_answer(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("value = 42\n", encoding="utf-8")
            registry = create_coding_tool_registry()
            registry.register(
                "demo.lookup",
                lambda query: {"query": query, "value": 42},
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "value": {"type": "integer"},
                    },
                    "required": ["query", "value"],
                    "additionalProperties": False,
                },
            )
            planner = ScriptedNativePlanner()
            runtime = CodingAgentRuntime(
                tool_registry=registry,
                planner=planner,
                max_tool_rounds=3,
                max_tool_calls=4,
            )

            result = runtime.run(
                conversation_id="sess_native",
                user_input="explain app.py using the lookup",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
                focus_files=["app.py"],
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.answer, "Observed the tool result and found value=42.")
        self.assertTrue(planner.observed_tool_result)
        native_result = next(
            item for item in result.tool_results if item["name"] == "demo.lookup"
        )
        self.assertEqual(native_result["call_id"], "native_call_1")
        self.assertEqual(planner.decisions, 2)

    def test_artifact_tool_does_not_preempt_failure_observation_and_replan(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text("value = 42\n", encoding="utf-8")
            planner = RecoveringArtifactNativePlanner()
            runtime = CodingAgentRuntime(
                planner=planner,
                max_tool_rounds=4,
                max_tool_calls=6,
            )

            result = runtime.run(
                conversation_id="sess_recovery",
                user_input="explain app.py and recover from tool failures",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
                focus_files=["app.py"],
            )

        self.assertEqual(
            result.answer,
            "Recovered by reading app.py after the failed listing.",
        )
        self.assertTrue(planner.observed_failure)
        self.assertTrue(planner.observed_tool_result)
        self.assertTrue(planner.observed_recovery)
        self.assertEqual(planner.decisions, 3)
        self.assertNotIn("collect_artifacts", [item["node"] for item in result.trace])
        list_result = next(
            item for item in result.tool_results if item["call_id"] == "list_failure"
        )
        self.assertFalse(list_result["ok"])


if __name__ == "__main__":
    unittest.main()
