import json
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
from ai_agent_platform.agents.coding.tool_loop_nodes import _native_output_budget
from ai_agent_platform.agents.coding.tool_loop_nodes import (
    _native_tool_result_message,
    _serialize_tool_result,
)
from ai_agent_platform.agents.coding.runtime_support import error_from_exception
from ai_agent_platform.core import Settings
from ai_agent_platform.core.metrics import MetricsRegistry
from ai_agent_platform.integrations.llm import (
    LLMClient,
    LLMProviderError,
    LLMToolDecision,
    LLMUsage,
    _anthropic_tool_messages,
    _deepseek_tool_messages,
    _effective_model_output_limit,
    collect_llm_usage,
    _google_tool_contents,
    _json_arguments,
    _openai_tool_input,
)
from ai_agent_platform.integrations.model_router import (
    ModelCapabilities,
    ModelConfig,
    ModelRouter,
)
from ai_agent_platform.integrations.mcp import MCPTool, MCPToolProvider
from ai_agent_platform.integrations.tools import ToolCall, ToolSpec
from ai_agent_platform.token_counting import estimate_text_tokens


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


def _runtime_artifact_messages() -> list[dict[str, object]]:
    return [
        {"role": "user", "content": "continue after collecting artifacts"},
        {
            "role": "assistant",
            "content": "Collecting runtime-managed change artifacts.",
            "tool_calls": [
                {
                    "call_id": "runtime_status",
                    "name": "sandbox.workspace_status",
                    "arguments": {},
                },
                {
                    "call_id": "runtime_diff",
                    "name": "sandbox.git_diff",
                    "arguments": {},
                },
            ],
        },
        {
            "role": "tool",
            "call_id": "runtime_status",
            "name": "sandbox.workspace_status",
            "content": {"ok": True, "result": {"changed_files": ["index.html"]}},
        },
        {
            "role": "tool",
            "call_id": "runtime_diff",
            "name": "sandbox.git_diff",
            "content": {"ok": True, "result": {"diff": "+<title>Game</title>"}},
        },
    ]


class NativeProviderMappingTests(unittest.TestCase):
    def test_bounded_tool_result_serializes_for_every_provider(self) -> None:
        tool_message, artifact = _native_tool_result_message(
            {
                "call_id": "large_call_1",
                "name": "demo.large",
                "ok": True,
                "result": {"payload": "x" * 6000},
            },
            max_tokens=128,
        )
        self.assertIsNotNone(artifact)
        placeholder = tool_message["content"]
        self.assertTrue(placeholder["truncated"])
        self.assertTrue(placeholder["head"])
        self.assertTrue(placeholder["tail"])
        self.assertLessEqual(
            estimate_text_tokens(_serialize_tool_result(placeholder)),
            128,
        )
        messages = [
            {"role": "user", "content": "run the large tool"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "call_id": "large_call_1",
                        "name": "demo.large",
                        "arguments": {},
                    }
                ],
            },
            tool_message,
        ]
        aliases = {"demo.large": "demo_large"}

        openai_output = next(
            item
            for item in _openai_tool_input(messages, aliases)
            if item.get("type") == "function_call_output"
        )
        self.assertEqual(openai_output["call_id"], "large_call_1")
        self.assertEqual(json.loads(openai_output["output"]), placeholder)

        anthropic_result = _anthropic_tool_messages(messages, aliases)[-1][
            "content"
        ][0]
        self.assertEqual(anthropic_result["tool_use_id"], "large_call_1")
        self.assertEqual(json.loads(anthropic_result["content"]), placeholder)

        deepseek_result = _deepseek_tool_messages(messages, aliases)[-1]
        self.assertEqual(deepseek_result["tool_call_id"], "large_call_1")
        self.assertEqual(json.loads(deepseek_result["content"]), placeholder)

        google_contents = _google_tool_contents(messages, types, aliases)
        google_text = "\n".join(
            part.text or ""
            for content in google_contents
            for part in content.parts
        )
        self.assertIn("large_call_1", google_text)
        self.assertIn(placeholder["artifact_id"], google_text)

    def test_effective_output_limit_uses_phase_model_and_context_minimum(self) -> None:
        model = ModelConfig(
            provider="deepseek",
            model="deepseek-test",
            context_window_tokens=10_000,
            max_output_tokens=8_192,
        )

        self.assertEqual(
            _effective_model_output_limit(
                model,
                input_tokens=1_000,
                requested_output_tokens=16_384,
            ),
            8_192,
        )
        self.assertEqual(
            _effective_model_output_limit(
                model,
                input_tokens=9_500,
                requested_output_tokens=16_384,
            ),
            500,
        )

    def test_runtime_artifact_tool_history_is_safe_for_every_provider(self) -> None:
        messages = _runtime_artifact_messages()
        aliases = {
            "sandbox.workspace_status": "sandbox_workspace_status",
            "sandbox.git_diff": "sandbox_git_diff",
        }

        openai_items = _openai_tool_input(messages, aliases)
        openai_calls = [
            item for item in openai_items if item.get("type") == "function_call"
        ]
        self.assertEqual(
            [item["name"] for item in openai_calls],
            ["sandbox_workspace_status", "sandbox_git_diff"],
        )
        self.assertEqual(
            [
                item["call_id"]
                for item in openai_items
                if item.get("type") == "function_call_output"
            ],
            ["runtime_status", "runtime_diff"],
        )

        anthropic_messages = _anthropic_tool_messages(messages, aliases)
        anthropic_assistant = anthropic_messages[1]
        self.assertEqual(anthropic_assistant["role"], "assistant")
        self.assertEqual(
            [
                block["name"]
                for block in anthropic_assistant["content"]
                if block["type"] == "tool_use"
            ],
            ["sandbox_workspace_status", "sandbox_git_diff"],
        )
        self.assertEqual(
            [block["tool_use_id"] for block in anthropic_messages[2]["content"]],
            ["runtime_status", "runtime_diff"],
        )

        deepseek_messages = _deepseek_tool_messages(messages, aliases)
        deepseek_assistant = deepseek_messages[1]
        self.assertEqual(deepseek_assistant["reasoning_content"], "")
        self.assertEqual(
            [item["function"]["name"] for item in deepseek_assistant["tool_calls"]],
            ["sandbox_workspace_status", "sandbox_git_diff"],
        )

        google_contents = _google_tool_contents(messages, types, aliases)
        google_parts = [part for content in google_contents for part in content.parts]
        self.assertTrue(
            any("runtime_status" in (part.text or "") for part in google_parts)
        )
        self.assertTrue(all(part.function_call is None for part in google_parts))
        self.assertTrue(all(part.function_response is None for part in google_parts))

    def test_http_provider_error_keeps_safe_detail_and_redacts_credentials(self) -> None:
        class FakeResponse:
            status_code = 400

            @staticmethod
            def json():
                return {
                    "error": {
                        "message": (
                            "The `reasoning_content` field is required; "
                            "api_key=diagnostic-placeholder"
                        )
                    }
                }

        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            @staticmethod
            def post(url, *, headers, json):
                del url, headers, json
                return FakeResponse()

        client = LLMClient(Settings())
        with (
            patch("ai_agent_platform.integrations.llm.httpx.Client", return_value=FakeClient()),
            self.assertRaises(LLMProviderError) as raised,
        ):
            client._post_json(
                "https://provider.example/v1/messages",
                headers={},
                payload={"messages": []},
            )

        self.assertIn("HTTP 400", str(raised.exception))
        self.assertIn("reasoning_content", str(raised.exception))
        self.assertIn("api_key=[REDACTED]", str(raised.exception))
        self.assertNotIn("diagnostic-placeholder", str(raised.exception))
        self.assertEqual(raised.exception.code, "llm_http_error")

    def test_malformed_tool_arguments_expose_safe_retry_diagnostics(self) -> None:
        value = '{"path":"index.html","content":"unterminated'

        with self.assertRaises(LLMProviderError) as raised:
            _json_arguments(
                value,
                finish_reason="length",
                usage=LLMUsage(input_tokens=20, output_tokens=4096),
            )

        error = raised.exception
        self.assertTrue(error.retryable)
        self.assertEqual(error.code, "tool_arguments_truncated")
        self.assertEqual(error.finish_reason, "length")
        self.assertEqual(error.tool_argument_chars, len(value))
        self.assertIsInstance(error.json_error_position, int)
        self.assertNotIn("unterminated", str(error))

        error.llm_usage = SimpleNamespace(
            input_tokens=44,
            output_tokens=4108,
            thoughts_tokens=0,
        )
        persisted = error_from_exception(
            "runtime",
            error,
            attempt=1,
            max_attempts=1,
        )
        self.assertEqual(persisted["code"], "tool_arguments_truncated")
        self.assertEqual(persisted["request_usage"]["output_tokens"], 4096)
        self.assertEqual(persisted["run_usage"]["output_tokens"], 4108)
        self.assertEqual(persisted["tool_argument_chars"], len(value))
        self.assertNotIn("unterminated", str(persisted))

    def test_deepseek_retries_truncated_arguments_and_records_failed_usage(self) -> None:
        class RecordingLedger:
            def __init__(self) -> None:
                self.authorizations: list[dict[str, object]] = []
                self.records: list[dict[str, object]] = []

            def authorize(self, **kwargs):
                self.authorizations.append(dict(kwargs))
                return SimpleNamespace(
                    provider=kwargs["requested_provider"],
                    model=kwargs["requested_model"],
                    max_output_tokens=kwargs["max_output_tokens"],
                    budget_decision="allowed",
                    budget_reason=None,
                )

            def record(self, **kwargs):
                self.records.append(dict(kwargs))

        ledger = RecordingLedger()
        router = ModelRouter(
            [
                ModelConfig(
                    provider="deepseek",
                    model="deepseek-v4-flash",
                    context_window_tokens=128_000,
                    max_output_tokens=8_192,
                    capabilities=ModelCapabilities(
                        tool_calling=True,
                        structured_output=True,
                    ),
                )
            ]
        )
        client = LLMClient(
            Settings(
                llm_provider="deepseek",
                llm_model="deepseek-v4-flash",
                llm_max_retries=1,
            ),
            usage_ledger=ledger,
            model_router=router,
            credential_resolver=lambda provider: (
                "test-key" if provider == "deepseek" else None
            ),
        )
        responses = [
            {
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "truncated_1",
                                    "function": {
                                        "name": "sandbox_apply_patch",
                                        "arguments": '{"patch":"*** Begin Patch',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 4096},
            },
            {
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "recovered_1",
                                    "function": {
                                        "name": "sandbox_apply_patch",
                                        "arguments": '{"patch":"small patch"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 24, "completion_tokens": 12},
            },
        ]
        payloads: list[dict[str, object]] = []

        def fake_post(url, *, headers, payload):
            del url, headers
            payloads.append(payload)
            return responses.pop(0)

        with (
            patch.object(client, "_post_json", side_effect=fake_post),
            patch("ai_agent_platform.integrations.llm.time.sleep"),
            collect_llm_usage() as usage,
        ):
            decision = client.decide_tools(
                [{"role": "user", "content": "create the app"}],
                [_tool_spec("sandbox.apply_patch")],
                max_output_tokens=16_384,
            )

        self.assertEqual(decision.tool_calls[0].call_id, "recovered_1")
        self.assertEqual(decision.tool_calls[0].arguments, {"patch": "small patch"})
        self.assertEqual([item["max_tokens"] for item in payloads], [8_192, 8_192])
        self.assertTrue(
            any(
                "exactly one tool call" in str(message.get("content") or "")
                for message in payloads[1]["messages"]
            )
        )
        self.assertEqual(len(ledger.records), 2)
        self.assertEqual(usage.input_tokens, 44)
        self.assertEqual(usage.output_tokens, 4108)

    def test_google_converts_foreign_tool_history_to_text_without_signatures(self) -> None:
        contents = _google_tool_contents(
            [
                {"role": "user", "content": "inspect the workspace"},
                {
                    "role": "assistant",
                    "provider": "deepseek",
                    "content": "",
                    "tool_calls": [
                        {
                            "call_id": "deepseek_call_1",
                            "name": "repo.list_files",
                            "arguments": {"path": ""},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "call_id": "deepseek_call_1",
                    "name": "repo.list_files",
                    "content": {"ok": True, "result": {"files": []}},
                },
            ],
            types,
            {"repo.list_files": "repo_list_files"},
        )

        parts = [part for content in contents for part in content.parts]
        self.assertTrue(
            any("previous provider" in (part.text or "") for part in parts)
        )
        self.assertTrue(
            any("deepseek_call_1" in (part.text or "") for part in parts)
        )
        self.assertTrue(all(part.function_call is None for part in parts))
        self.assertTrue(all(part.function_response is None for part in parts))

    def test_native_provider_can_select_namespaced_mcp_tool(self) -> None:
        client = LLMClient(
            Settings(
                llm_provider="openai",
                llm_model="test-openai",
            ),
            credential_resolver=lambda provider: (
                "test-key" if provider == "openai" else None
            ),
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
            ),
            credential_resolver=lambda provider: (
                "test-key" if provider == "openai" else None
            ),
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
        self.assertFalse(payloads[0]["parallel_tool_calls"])
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
            ),
            credential_resolver=lambda provider: (
                "test-key" if provider == "openai" else None
            ),
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
            decision = client.finalize_tools(
                messages,
                reason="hard_tool_round_budget",
                tools=[_tool_spec()],
            )

        self.assertEqual(decision.text, "Grounded final answer.")
        # Replayed tool blocks are only valid while the request still declares
        # the tools, so finalization forbids calls through tool_choice instead.
        self.assertEqual(captured["tools"][0]["name"], "repo_read_file")
        self.assertEqual(captured["tool_choice"], "none")
        self.assertTrue(
            any(item.get("type") == "function_call_output" for item in captured["input"])
        )

    def test_finalization_without_tool_definitions_flattens_tool_blocks(self) -> None:
        client = LLMClient(
            Settings(
                llm_provider="anthropic",
                llm_model="test-claude",
            ),
            credential_resolver=lambda provider: (
                "test-key" if provider == "anthropic" else None
            ),
        )
        captured: dict[str, object] = {}

        def fake_post(url, *, headers, payload):
            del headers
            if url.endswith("count_tokens"):
                return {"input_tokens": 24}
            captured.update(payload)
            return {
                "model": "test-claude",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "Grounded final answer."}],
                "usage": {"input_tokens": 24, "output_tokens": 4},
            }

        messages = [
            {"role": "user", "content": "read app.py"},
            {
                "role": "assistant",
                "content": "",
                "provider": "anthropic",
                "provider_items": [
                    {
                        "type": "tool_use",
                        "id": "toolu_final",
                        "name": "repo_read_file",
                        "input": {"path": "app.py"},
                    }
                ],
                "tool_calls": [
                    {
                        "call_id": "toolu_final",
                        "name": "repo.read_file",
                        "arguments": {"path": "app.py"},
                    }
                ],
            },
            {
                "role": "tool",
                "call_id": "toolu_final",
                "name": "repo.read_file",
                "content": {"ok": True, "result": {"content": "value=1"}},
            },
        ]

        with patch.object(client, "_post_json", side_effect=fake_post):
            decision = client.finalize_tools(
                messages,
                reason="permission_denied",
                tools=[],
            )

        self.assertEqual(decision.text, "Grounded final answer.")
        self.assertNotIn("tools", captured)
        block_types = [
            [block.get("type") for block in message["content"]]
            for message in captured["messages"]
        ]
        self.assertEqual(block_types, [["text"], ["text"], ["text"]])
        self.assertIn("value=1", json.dumps(captured["messages"], ensure_ascii=False))

    def test_anthropic_finalization_keeps_tool_definitions_for_replayed_blocks(
        self,
    ) -> None:
        client = LLMClient(
            Settings(
                llm_provider="anthropic",
                llm_model="test-claude",
            ),
            credential_resolver=lambda provider: (
                "test-key" if provider == "anthropic" else None
            ),
        )
        captured: dict[str, object] = {}
        count_payload: dict[str, object] = {}

        def fake_post(url, *, headers, payload):
            del headers
            if url.endswith("count_tokens"):
                count_payload.update(payload)
                return {"input_tokens": 24}
            captured.update(payload)
            return {
                "model": "test-claude",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "Grounded final answer."}],
                "usage": {"input_tokens": 24, "output_tokens": 4},
            }

        messages = [
            {"role": "user", "content": "read app.py"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "call_id": "toolu_final",
                        "name": "repo.read_file",
                        "arguments": {"path": "app.py"},
                    }
                ],
            },
            {
                "role": "tool",
                "call_id": "toolu_final",
                "name": "repo.read_file",
                "content": {"ok": True, "result": {"content": "value=1"}},
            },
        ]

        with patch.object(client, "_post_json", side_effect=fake_post):
            decision = client.finalize_tools(
                messages,
                reason="hard_tool_round_budget",
                tools=[_tool_spec()],
            )

        self.assertEqual(decision.text, "Grounded final answer.")
        self.assertEqual(captured["tools"][0]["name"], "repo_read_file")
        self.assertEqual(captured["tool_choice"], {"type": "none"})
        # The token preflight must describe the same request as the real call.
        self.assertEqual(
            [item["name"] for item in count_payload["tools"]],
            ["repo_read_file"],
        )
        block_types = [
            [block.get("type") for block in message["content"]]
            for message in captured["messages"]
        ]
        self.assertEqual(block_types, [["text"], ["tool_use"], ["tool_result"]])

    def test_anthropic_maps_tool_use_and_usage(self) -> None:
        client = LLMClient(
            Settings(
                llm_provider="anthropic",
                llm_model="test-claude",
            ),
            credential_resolver=lambda provider: (
                "test-key" if provider == "anthropic" else None
            ),
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
                self.count_kwargs: dict[str, object] = {}

            def generate_content(self, **kwargs):
                self.kwargs = kwargs
                return response

            def count_tokens(self, **kwargs):
                self.count_kwargs = kwargs
                return SimpleNamespace(total_tokens=9)

        fake_client = SimpleNamespace(
            models=FakeModels(),
            close=lambda: None,
        )
        client = LLMClient(
            Settings(
                llm_provider="google",
                llm_model="gemini-test",
            ),
            credential_resolver=lambda provider: (
                "test-key" if provider == "google" else None
            ),
        )

        with patch("google.genai.Client", return_value=fake_client):
            decision = client.decide_tools(
                [
                    {"role": "system", "content": "follow repository policy"},
                    {"role": "user", "content": "read app.py"},
                ],
                [_tool_spec()],
            )

        self.assertEqual(decision.tool_calls[0].call_id, "google_call_1")
        self.assertEqual(decision.tool_calls[0].name, "repo.read_file")
        self.assertEqual(decision.usage.total_tokens, 13)
        self.assertTrue(decision.provider_items)
        self.assertEqual(
            fake_client.models.kwargs["config"].system_instruction,
            "follow repository policy",
        )
        self.assertIsNone(
            fake_client.models.count_kwargs["config"].system_instruction
        )
        self.assertIsNone(fake_client.models.count_kwargs["config"].tools)
        count_context = fake_client.models.count_kwargs["contents"][0].parts[0].text
        self.assertIn("follow repository policy", count_context)
        self.assertIn("repo_read_file", count_context)
        self.assertIn(
            '"path"',
            count_context,
        )
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


class OversizedToolResultPlanner(ScriptedNativePlanner):
    def __init__(self, tool_name: str) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.observed_message: dict[str, object] | None = None

    def decide_tool_calls(self, messages, tool_specs):
        del tool_specs
        self.decisions += 1
        if self.decisions == 1:
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        call_id="oversized_call_1",
                        name=self.tool_name,
                        arguments={},
                    )
                ],
                model="scripted",
                provider="test",
                stop_reason="tool_use",
            )
        self.observed_message = next(
            message
            for message in reversed(messages)
            if message.get("role") == "tool"
            and message.get("call_id") == "oversized_call_1"
        )
        return LLMToolDecision(
            text="Observed the bounded result.",
            tool_calls=[],
            model="scripted",
            provider="test",
            stop_reason="end_turn",
        )


class SingleToolNativePlanner(ScriptedNativePlanner):
    single_tool_per_turn = True

    def __init__(self) -> None:
        super().__init__()
        self.observed_suppression = False

    def decide_tool_calls(self, messages, tool_specs):
        del tool_specs
        self.decisions += 1
        if self.decisions == 1:
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        call_id="single_1",
                        name="demo.lookup",
                        arguments={"query": "first"},
                    ),
                    ToolCall(
                        call_id="single_2",
                        name="demo.lookup",
                        arguments={"query": "second"},
                    ),
                ],
                model="scripted",
                provider="test",
                stop_reason="tool_use",
            )
        self.observed_suppression = any(
            message.get("role") == "tool"
            and message.get("call_id") == "single_2"
            and message.get("content", {}).get("error_code") == "single_tool_turn"
            for message in messages
        )
        return LLMToolDecision(
            text="Only one tool was executed in the turn.",
            tool_calls=[],
            model="scripted",
            provider="test",
            stop_reason="end_turn",
        )


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


class DeepSeekArtifactHistoryPlanner(UnifiedChangeNativePlanner):
    def __init__(self, command: str) -> None:
        super().__init__(command)
        self.observed_safe_artifact_history = False

    def decide_tool_calls(self, messages, tool_specs):
        del tool_specs
        self.decisions += 1
        if self.decisions == 1:
            calls = [
                ToolCall(
                    call_id="deepseek_write",
                    name="sandbox.write_file",
                    arguments={"path": "app.py", "content": "value = 43\n"},
                    source="deepseek_native",
                ),
                ToolCall(
                    call_id="deepseek_validate",
                    name="sandbox.run_command",
                    arguments={"command": self.command},
                    source="deepseek_native",
                ),
            ]
            return LLMToolDecision(
                text="",
                tool_calls=calls,
                model="deepseek-v4-flash",
                provider="deepseek",
                stop_reason="tool_calls",
                provider_items=[
                    {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "opaque provider reasoning",
                        "tool_calls": [
                            {
                                "id": call.call_id,
                                "type": "function",
                                "function": {
                                    "name": call.name.replace(".", "_"),
                                    "arguments": "{}",
                                },
                            }
                            for call in calls
                        ],
                    }
                ],
            )
        converted = _deepseek_tool_messages(
            messages,
            {
                "sandbox.workspace_status": "sandbox_workspace_status",
                "sandbox.git_diff": "sandbox_git_diff",
            },
        )
        artifact_turn = next(
            message
            for message in converted
            if message.get("content")
            == "Collecting runtime-managed change artifacts."
        )
        self.observed_safe_artifact_history = (
            artifact_turn.get("reasoning_content") == ""
            and len(artifact_turn.get("tool_calls", [])) == 2
        )
        return LLMToolDecision(
            text="Changed and validated app.py after safe artifact replay.",
            tool_calls=[],
            model="deepseek-v4-flash",
            provider="deepseek",
            stop_reason="end_turn",
        )


class DiagnosticCommandRecoveryPlanner(ScriptedNativePlanner):
    def __init__(self, command: str) -> None:
        super().__init__()
        self.command = command
        self.observed_diagnostic_failure = False

    def classify_intent(self, user_input: str) -> dict[str, object]:
        return {
            "intent": "change_planning",
            "reason": "empty workspace recovery test",
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
                        call_id="denied_listing",
                        name="sandbox.run_command",
                        arguments={"command": "ls -la"},
                        source="test_native",
                    )
                ],
                model="scripted",
                provider="test",
                stop_reason="tool_use",
            )
        if self.decisions == 2:
            self.observed_diagnostic_failure = any(
                message.get("role") == "tool"
                and message.get("call_id") == "denied_listing"
                and not bool(message.get("content", {}).get("ok"))
                for message in messages
            )
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        call_id="create_index",
                        name="sandbox.write_file",
                        arguments={
                            "path": "index.html",
                            "content": "<!doctype html><title>Snake</title>\n",
                        },
                        source="test_native",
                    ),
                    ToolCall(
                        call_id="validate_index",
                        name="sandbox.run_command",
                        arguments={"command": self.command},
                        source="test_native",
                    ),
                ],
                model="scripted",
                provider="test",
                stop_reason="tool_use",
            )
        return LLMToolDecision(
            text="Recovered from the diagnostic failure and created the app.",
            tool_calls=[],
            model="scripted",
            provider="test",
            stop_reason="end_turn",
        )


class RefusalThenMutationPlanner(ScriptedNativePlanner):
    def __init__(self, command: str) -> None:
        super().__init__()
        self.command = command
        self.observed_completion_gate = False

    def classify_intent(self, user_input: str) -> dict[str, object]:
        return {
            "intent": "change_planning",
            "reason": "completion gate test",
            "confidence": 1.0,
            "source": "test",
        }

    def decide_tool_calls(self, messages, tool_specs):
        del tool_specs
        self.decisions += 1
        if self.decisions == 1:
            return LLMToolDecision(
                text="The empty workspace cannot be changed.",
                tool_calls=[],
                model="scripted",
                provider="test",
                stop_reason="end_turn",
            )
        if self.decisions == 2:
            self.observed_completion_gate = any(
                message.get("role") == "system"
                and "empty workspace is valid" in str(message.get("content") or "")
                for message in messages
            )
            return LLMToolDecision(
                text="",
                tool_calls=[
                    ToolCall(
                        call_id="gated_write",
                        name="sandbox.write_file",
                        arguments={
                            "path": "index.html",
                            "content": "<!doctype html><title>Snake</title>\n",
                        },
                        source="test_native",
                    ),
                    ToolCall(
                        call_id="gated_validation",
                        name="sandbox.run_command",
                        arguments={"command": self.command},
                        source="test_native",
                    ),
                ],
                model="scripted",
                provider="test",
                stop_reason="tool_use",
            )
        return LLMToolDecision(
            text="Created and validated the game after the completion gate.",
            tool_calls=[],
            model="scripted",
            provider="test",
            stop_reason="end_turn",
        )


class NativeToolLoopTests(unittest.TestCase):
    def _assert_oversized_result_uses_harness_budget(
        self,
        *,
        registry,
        tool_name: str,
    ) -> None:
        planner = OversizedToolResultPlanner(tool_name)
        metrics = MetricsRegistry()
        with TemporaryDirectory() as temp_dir:
            result = CodingAgentRuntime(
                tool_registry=registry,
                planner=planner,
                tool_result_max_tokens=128,
                metrics=metrics,
            ).run(
                conversation_id=f"sess_budget_{tool_name}",
                user_input="run an oversized tool",
                history=[],
                workspace_id="workspace_main",
                workspace_root=temp_dir,
            )

        self.assertEqual(result.status, "completed")
        self.assertIsNotNone(planner.observed_message)
        message = planner.observed_message or {}
        self.assertEqual(message.get("call_id"), "oversized_call_1")
        placeholder = message.get("content")
        self.assertIsInstance(placeholder, dict)
        placeholder = placeholder if isinstance(placeholder, dict) else {}
        self.assertTrue(placeholder.get("truncated"))
        self.assertGreater(placeholder.get("truncated_from_tokens", 0), 128)
        self.assertTrue(placeholder.get("head"))
        self.assertTrue(placeholder.get("tail"))
        self.assertLessEqual(
            estimate_text_tokens(_serialize_tool_result(placeholder)),
            128,
        )

        full_result = next(
            item
            for item in result.tool_results
            if item.get("call_id") == "oversized_call_1"
        )
        artifact = next(
            item
            for item in result.artifacts
            if item.get("id") == placeholder.get("artifact_id")
        )
        self.assertEqual(artifact["type"], "tool_result")
        self.assertEqual(artifact["call_id"], "oversized_call_1")
        self.assertEqual(artifact["content"], full_result)
        self.assertEqual(
            metrics.snapshot()["counters"][
                "agent_tool_results_truncated_total"
            ],
            1,
        )

    def test_builtin_tool_result_is_bounded_and_externalized(self) -> None:
        registry = create_coding_tool_registry()
        registry.register(
            "demo.large",
            lambda: {"payload": "built-in-" + ("x" * 6000)},
            input_schema={"type": "object", "additionalProperties": False},
            output_schema={"type": "object"},
        )

        self._assert_oversized_result_uses_harness_budget(
            registry=registry,
            tool_name="demo.large",
        )

    def test_mcp_tool_result_uses_the_same_budget_and_artifact_path(self) -> None:
        class LargeMCPClient:
            def list_tools(self):
                return [
                    MCPTool(
                        name="large",
                        description="Return a large MCP payload.",
                        input_schema={
                            "type": "object",
                            "additionalProperties": False,
                        },
                        output_schema={"type": "object"},
                        permission_level="read_only",
                        requires_approval=False,
                    )
                ]

            @staticmethod
            def call_tool(name, arguments):
                del name, arguments
                return {
                    "structuredContent": {
                        "payload": "mcp-" + ("y" * 6000)
                    }
                }

        registry = create_coding_tool_registry(
            mcp_providers=[
                MCPToolProvider(server_name="budget", client=LargeMCPClient())
            ]
        )

        self._assert_oversized_result_uses_harness_budget(
            registry=registry,
            tool_name="mcp.budget.large",
        )

    def test_phase_output_budget_switches_after_change_plan(self) -> None:
        self.assertEqual(
            _native_output_budget(
                {"intent": "change_planning", "tool_results": []},
                plan_tokens=4096,
                mutation_tokens=16384,
            ),
            4096,
        )
        self.assertEqual(
            _native_output_budget(
                {
                    "intent": "change_planning",
                    "tool_results": [
                        {"name": "change_planner", "ok": True}
                    ],
                },
                plan_tokens=4096,
                mutation_tokens=16384,
            ),
            16384,
        )

    def test_single_tool_planner_suppresses_additional_calls_in_same_turn(self) -> None:
        with TemporaryDirectory() as temp_dir:
            registry = create_coding_tool_registry()
            registry.register(
                "demo.lookup",
                lambda query: {"query": query},
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                output_schema={"type": "object"},
            )
            planner = SingleToolNativePlanner()
            result = CodingAgentRuntime(
                tool_registry=registry,
                planner=planner,
            ).run(
                conversation_id="sess_single_tool_turn",
                user_input="look up one value at a time",
                history=[],
                workspace_id="workspace_main",
                workspace_root=temp_dir,
            )

        executed = [
            item
            for item in result.tool_results
            if str(item.get("call_id", "")).startswith("single_")
        ]
        self.assertEqual([item["call_id"] for item in executed], ["single_1"])
        self.assertTrue(planner.observed_suppression)

    def test_change_task_cannot_complete_before_successful_sandbox_mutation(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            validation = "from pathlib import Path; assert Path('index.html').is_file()"
            planner = RefusalThenMutationPlanner(
                f"{shlex.quote(sys.executable)} -c {shlex.quote(validation)}"
            )
            runtime = CodingAgentRuntime(planner=planner)

            waiting = runtime.run(
                conversation_id="sess_change_completion_gate",
                user_input="create a snake game in this empty workspace",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
            )
            result = runtime.resume(run_id=waiting.run_id, approved=True)

        self.assertEqual(waiting.status, "waiting_approval")
        self.assertEqual(result.status, "completed")
        self.assertTrue(planner.observed_completion_gate)
        self.assertEqual(planner.decisions, 3)
        self.assertEqual(result.change_summary.changed_files, ["index.html"])
        self.assertTrue(result.change_summary.validation_passed)

    def test_failed_diagnostic_command_does_not_preempt_empty_workspace_change(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            validation = (
                "from pathlib import Path; "
                "assert Path('index.html').read_text().startswith('<!doctype html>')"
            )
            planner = DiagnosticCommandRecoveryPlanner(
                f"{shlex.quote(sys.executable)} -c {shlex.quote(validation)}"
            )
            runtime = CodingAgentRuntime(planner=planner)

            first_approval = runtime.run(
                conversation_id="sess_empty_workspace_recovery",
                user_input="create a snake game in this empty workspace",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
            )
            second_approval = runtime.resume(
                run_id=first_approval.run_id,
                approved=True,
            )
            result = runtime.resume(
                run_id=first_approval.run_id,
                approved=True,
            )
            source_files = list(root.iterdir())

        self.assertEqual(first_approval.status, "waiting_approval")
        self.assertEqual(second_approval.status, "waiting_approval")
        self.assertEqual(result.status, "completed")
        self.assertTrue(planner.observed_diagnostic_failure)
        self.assertEqual(planner.decisions, 3)
        self.assertEqual(result.change_summary.changed_files, ["index.html"])
        self.assertTrue(result.change_summary.validation_passed)
        self.assertEqual(source_files, [])

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

    def test_deepseek_replays_runtime_artifacts_with_empty_reasoning_content(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "app.py"
            source.write_text("value = 42\n", encoding="utf-8")
            validation = (
                "compile(open('app.py', encoding='utf-8').read(), "
                "'app.py', 'exec')"
            )
            planner = DeepSeekArtifactHistoryPlanner(
                f"{shlex.quote(sys.executable)} -c {shlex.quote(validation)}"
            )
            runtime = CodingAgentRuntime(planner=planner)

            waiting = runtime.run(
                conversation_id="sess_deepseek_artifact_replay",
                user_input="change app.py and validate it",
                history=[],
                workspace_id="workspace_main",
                workspace_root=str(root),
            )
            result = runtime.resume(run_id=waiting.run_id, approved=True)

        self.assertEqual(waiting.status, "waiting_approval")
        self.assertEqual(result.status, "completed")
        self.assertTrue(planner.observed_safe_artifact_history)
        self.assertEqual(planner.decisions, 2)
        self.assertEqual(result.change_summary.changed_files, ["app.py"])
        self.assertTrue(result.change_summary.validation_passed)

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
