from pathlib import Path
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


class NativeToolLoopTests(unittest.TestCase):
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
