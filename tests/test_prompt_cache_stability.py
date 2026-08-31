from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from ai_agent_platform.agents.coding.planner import LLMStructuredAgentPlanner
from ai_agent_platform.agents.coding.runtime_support import build_run_metrics
from ai_agent_platform.agents.coding.task_shaping import freeze_tool_profile
from ai_agent_platform.agents.coding_agent import CodingAgentRuntime
from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.llm import (
    LLMClient,
    LLMStreamEvent,
    LLMToolDecision,
    LLMUsage,
    LLMUsageAccumulator,
    _anthropic_usage_from_mapping,
    _chat_usage_from_mapping,
    _google_usage,
    _merge_stream_usage,
    _usage_from_mapping,
)
from ai_agent_platform.integrations.model_router import (
    ModelCapabilities,
    ModelConfig,
    ModelRouter,
)
from ai_agent_platform.integrations.prompt_cache import (
    canonical_tool_specs,
    prompt_cache_key,
    stable_prefix_bytes,
)
from ai_agent_platform.integrations.tools import ToolSpec
from ai_agent_platform.schemas.agent import AgentRunMetricsResponse
from ai_agent_platform.usage_ledger import UsageLedgerService


def _tool(
    name: str,
    *,
    provider: str = "local",
    permission: str = "read_only",
    schema: dict | None = None,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"Tool {name}",
        input_schema=schema
        or {
            "properties": {"z": {"type": "string"}, "a": {"type": "integer"}},
            "type": "object",
        },
        output_schema={"type": "object"},
        provider=provider,
        permission_level=permission,
    )


class StablePrefixTests(unittest.TestCase):
    def test_prefix_is_byte_stable_across_tool_and_schema_order(self) -> None:
        messages = [
            {"role": "system", "content": "stable instructions"},
            {"role": "user", "content": "analyze"},
        ]
        first = [_tool("z.tool"), _tool("a.tool")]
        second = [
            _tool(
                "a.tool",
                schema={
                    "type": "object",
                    "properties": {
                        "a": {"type": "integer"},
                        "z": {"type": "string"},
                    },
                },
            ),
            _tool("z.tool"),
        ]

        self.assertEqual(
            stable_prefix_bytes(messages, first),
            stable_prefix_bytes(messages, second),
        )
        self.assertEqual(
            [item.name for item in canonical_tool_specs(first)],
            ["a.tool", "z.tool"],
        )

    def test_appended_runtime_configuration_does_not_rewrite_old_prefix(self) -> None:
        base = [
            {"role": "system", "content": "stable instructions"},
            {"role": "user", "content": "analyze"},
        ]
        appended = base + [
            {"role": "system", "content": "configuration changed: read only"}
        ]

        self.assertEqual(
            stable_prefix_bytes(base, [_tool("repo.read_file")]),
            stable_prefix_bytes(appended, [_tool("repo.read_file")]),
        )

    def test_workspace_cache_key_is_stable_across_dynamic_suffixes(self) -> None:
        context = SimpleNamespace(
            workspace_id="workspace-main",
            resource_id="run-one",
        )
        first = [
            {"role": "system", "content": "stable instructions"},
            {"role": "user", "content": "first request"},
        ]
        second = [
            {"role": "system", "content": "stable instructions"},
            {"role": "user", "content": "second request"},
        ]

        self.assertEqual(
            prompt_cache_key(first, [_tool("repo.read_file")], context),
            prompt_cache_key(second, [_tool("repo.read_file")], context),
        )
        self.assertNotEqual(
            prompt_cache_key(first, [_tool("repo.read_file")], context),
            prompt_cache_key(
                first,
                [_tool("repo.read_file")],
                SimpleNamespace(workspace_id="workspace-other", resource_id="run-two"),
            ),
        )


class LazyToolProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.specs = [
            _tool("repo.list_files"),
            _tool("repo.read_file"),
            _tool("repo.collect_evidence"),
            _tool("sandbox.write_file", permission="write"),
            _tool("sandbox.run_command", permission="execute"),
            _tool("agent.load_skill"),
            _tool("mcp.github.search", provider="mcp:github"),
        ]

    def test_overview_never_exposes_mutation_shell_skill_or_mcp(self) -> None:
        profile = freeze_tool_profile(
            "overview",
            list(reversed(self.specs)),
            user_input="分析下当前项目",
        )

        self.assertEqual(
            profile,
            ["repo.list_files", "repo.read_file", "repo.collect_evidence"],
        )

    def test_targeted_read_delays_skill_and_mcp_until_explicitly_needed(self) -> None:
        default = freeze_tool_profile(
            "targeted_read",
            self.specs,
            user_input="解释 LoginService",
        )
        explicit = freeze_tool_profile(
            "targeted_read",
            list(reversed(self.specs)),
            user_input="使用 GitHub 外部工具查 LoginService",
            explicit_tool_names=["mcp.github.search"],
            skill_requested=True,
        )

        self.assertNotIn("sandbox.write_file", default)
        self.assertNotIn("agent.load_skill", default)
        self.assertNotIn("mcp.github.search", default)
        self.assertIn("agent.load_skill", explicit)
        self.assertIn("mcp.github.search", explicit)
        self.assertEqual(explicit, freeze_tool_profile(
            "targeted_read",
            self.specs,
            user_input="使用 GitHub 外部工具查 LoginService",
            explicit_tool_names=["mcp.github.search"],
            skill_requested=True,
        ))


class ProviderCacheUsageTests(unittest.TestCase):
    def test_usage_ledger_receives_provider_total_without_recomputing_it(self) -> None:
        class Repository:
            captured = None

            def add_token_usage(self, **kwargs):
                self.captured = kwargs
                return kwargs

        repository = Repository()
        ledger = UsageLedgerService(repository, Settings())

        ledger.record(
            provider="openai",
            model="gpt-5.6",
            input_tokens=1000,
            output_tokens=120,
            thoughts_tokens=40,
            total_tokens=1120,
        )

        self.assertEqual(repository.captured["output_tokens"], 120)
        self.assertEqual(repository.captured["thoughts_tokens"], 40)
        self.assertEqual(repository.captured["total_tokens"], 1120)

    def test_stream_usage_keeps_cache_fields_from_anthropic_start_event(self) -> None:
        started = _anthropic_usage_from_mapping(
            {
                "input_tokens": 200,
                "output_tokens": 0,
                "cache_read_input_tokens": 700,
                "cache_creation_input_tokens": 50,
            }
        )
        assert started is not None

        merged = _merge_stream_usage(
            started,
            LLMUsage(input_tokens=0, output_tokens=30),
            fallback_input_tokens=999,
        )

        self.assertEqual(merged.input_tokens, 200)
        self.assertEqual(merged.output_tokens, 30)
        self.assertEqual(merged.cached_input_tokens, 700)
        self.assertEqual(merged.cache_write_tokens, 50)
        self.assertIsNone(merged.uncached_input_tokens)

    def test_openai_preserves_raw_usage_and_computes_reliable_uncached(self) -> None:
        usage = _usage_from_mapping(
            {
                "input_tokens": 1000,
                "output_tokens": 120,
                "total_tokens": 1120,
                "input_tokens_details": {
                    "cached_tokens": 800,
                    "cache_write_tokens": 1000,
                },
                "output_tokens_details": {"reasoning_tokens": 40},
            }
        )

        assert usage is not None
        self.assertEqual(usage.input_tokens, 1000)
        self.assertEqual(usage.output_tokens, 120)
        self.assertEqual(usage.total_tokens, 1120)
        self.assertEqual(usage.cached_input_tokens, 800)
        self.assertEqual(usage.uncached_input_tokens, 200)
        self.assertEqual(usage.cache_write_tokens, 1000)

    def test_anthropic_and_google_do_not_invent_uncached_identity(self) -> None:
        anthropic = _anthropic_usage_from_mapping(
            {
                "input_tokens": 200,
                "output_tokens": 30,
                "cache_read_input_tokens": 700,
                "cache_creation_input_tokens": 50,
            }
        )

        class GoogleUsage:
            prompt_token_count = 900
            candidates_token_count = 30
            cached_content_token_count = 700
            total_token_count = 930

        google = _google_usage(GoogleUsage())
        assert anthropic is not None and google is not None
        self.assertIsNone(anthropic.uncached_input_tokens)
        self.assertIsNone(google.uncached_input_tokens)
        self.assertEqual(anthropic.cached_input_tokens, 700)
        self.assertEqual(google.cached_input_tokens, 700)

    def test_deepseek_uses_hit_miss_but_compatible_provider_does_not(self) -> None:
        raw = {
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "total_tokens": 1050,
            "prompt_cache_hit_tokens": 750,
            "prompt_cache_miss_tokens": 250,
        }
        deepseek = _chat_usage_from_mapping(raw, provider="deepseek")
        glm = _chat_usage_from_mapping(raw, provider="glm")

        assert deepseek is not None and glm is not None
        self.assertEqual(deepseek.cached_input_tokens, 750)
        self.assertEqual(deepseek.uncached_input_tokens, 250)
        self.assertIsNone(glm.cached_input_tokens)
        self.assertIsNone(glm.uncached_input_tokens)

    def test_consecutive_same_profile_improves_uncached_without_rewriting_total(self) -> None:
        first = _usage_from_mapping(
            {"input_tokens": 1000, "output_tokens": 20, "total_tokens": 1020,
             "input_tokens_details": {"cached_tokens": 0}}
        )
        second = _usage_from_mapping(
            {"input_tokens": 1000, "output_tokens": 20, "total_tokens": 1020,
             "input_tokens_details": {"cached_tokens": 800}}
        )
        assert first is not None and second is not None
        self.assertGreater(first.uncached_input_tokens, second.uncached_input_tokens)
        self.assertEqual(first.total_tokens, second.total_tokens)
        accumulator = LLMUsageAccumulator()
        accumulator.add(first, provider="openai", model="gpt-5.6")
        accumulator.add(second, provider="openai", model="gpt-5.6")
        self.assertEqual(accumulator.total_tokens, 2040)

    def test_run_metrics_and_api_keep_raw_and_estimated_fields_distinct(self) -> None:
        metrics = build_run_metrics(
            {
                "llm_input_tokens": 1000,
                "llm_output_tokens": 120,
                "llm_thoughts_tokens": 40,
                "llm_provider_total_tokens": 1120,
                "llm_cached_input_tokens": 800,
                "llm_uncached_input_tokens": 200,
                "llm_cache_write_tokens": 1000,
                "llm_provider_models": [
                    ("openai", "gpt-5.6", "explicit_key+breakpoint")
                ],
                "stable_prefix_tokens": 300,
                "tool_schema_tokens": 150,
                "visible_tool_count": 2,
                "native_tool_messages": [
                    {"role": "system", "content": "stable"},
                    {"role": "user", "content": "dynamic"},
                ],
            }
        )
        response = AgentRunMetricsResponse(**metrics.__dict__)

        self.assertEqual(response.input_tokens, 1000)
        self.assertEqual(response.total_tokens, 1120)
        self.assertEqual(response.cached_input_tokens, 800)
        self.assertEqual(response.uncached_input_tokens, 200)
        self.assertEqual(response.prompt_cache_hit_ratio, 0.8)
        self.assertEqual(response.stable_prefix_tokens, 300)
        self.assertEqual(response.tool_schema_tokens, 150)
        self.assertGreater(response.retained_context_tokens_estimate, 0)


class _MeasuredOverviewProvider:
    """Synthetic Provider that exercises the normal LLM accounting boundary."""

    def __init__(self) -> None:
        self.request_index = 0

    def _usage(self, *, input_tokens: int, output_tokens: int) -> LLMUsage:
        warm_cache = self.request_index >= 2
        cached = input_tokens * 3 // 4 if warm_cache else 0
        self.request_index += 1
        return LLMUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached,
            uncached_input_tokens=input_tokens - cached,
            reported_total_tokens=input_tokens + output_tokens,
        )

    def stream_chat(self, messages, *, model, thinking_level):
        del messages, thinking_level
        body = (
            '{"intent":"repository_question","reason":"fixed benchmark",'
            '"confidence":1,"context_route":"repo",'
            '"route_reason":"repository overview",'
            '"selected_knowledge_base_ids":[]}'
        )
        yield LLMStreamEvent(type="delta", text=body)
        yield LLMStreamEvent(
            type="usage",
            usage=self._usage(input_tokens=2_000, output_tokens=100),
            model=model,
        )
        yield LLMStreamEvent(type="done", model=model)

    def decide_tools(self, messages, tools, *, model):
        del messages, tools
        return LLMToolDecision(
            text=(
                "项目用途：AI Agent 平台。主要模块：API、Agent runtime、工具层。"
                "运行入口：ai_agent_platform.main。关键技术栈：FastAPI、LangGraph、pytest。"
            ),
            tool_calls=[],
            model=model,
            provider="openai",
            stop_reason="end_turn",
            usage=self._usage(input_tokens=8_000, output_tokens=240),
        )


class PromptCacheOverviewRegressionTests(unittest.TestCase):
    @staticmethod
    def _runtime() -> CodingAgentRuntime:
        provider = _MeasuredOverviewProvider()
        router = ModelRouter(
            [
                ModelConfig(
                    provider="openai",
                    model="gpt-5.6",
                    context_window_tokens=128_000,
                    max_output_tokens=4_096,
                    capabilities=ModelCapabilities(
                        tool_calling=True,
                        structured_output=True,
                    ),
                )
            ]
        )
        client = LLMClient(
            Settings(
                llm_provider="openai",
                llm_model="gpt-5.6",
                llm_max_retries=0,
            ),
            model_router=router,
            provider_adapters={"openai": provider},
        )
        return CodingAgentRuntime(
            planner=LLMStructuredAgentPlanner(client),
            llm_client=client,
        )

    def test_consecutive_overviews_keep_raw_budget_and_improve_uncached_input(self) -> None:
        for module_count, tool_limit, model_limit, input_limit in (
            (2, 10, 4, 50_000),
            (40, 12, 5, 120_000),
        ):
            with self.subTest(module_count=module_count), TemporaryDirectory() as root:
                runtime = self._runtime()
                workspace = Path(root)
                (workspace / "README.md").write_text(
                    "AI Agent platform built with FastAPI and LangGraph.",
                    encoding="utf-8",
                )
                package = workspace / "ai_agent_platform"
                package.mkdir()
                (package / "main.py").write_text(
                    "app = FastAPI()\n", encoding="utf-8"
                )
                for index in range(module_count):
                    (package / f"module_{index}.py").write_text(
                        f"VALUE = {index}\n", encoding="utf-8"
                    )
                results = [
                    runtime.run(
                        conversation_id=f"cache-overview-{module_count}-{index}",
                        user_input="分析下当前项目",
                        history=[],
                        workspace_id="workspace-main",
                        workspace_root=root,
                    )
                    for index in range(2)
                ]

                first, second = results
                for result in results:
                    self.assertLessEqual(result.metrics.model_request_count, model_limit)
                    self.assertLessEqual(result.metrics.tool_call_count, tool_limit)
                    self.assertLessEqual(result.metrics.input_tokens, input_limit)
                    self.assertEqual(result.metrics.total_tokens, 10_340)
                    self.assertEqual(result.metrics.model_retry_count, 0)
                    for section in ("项目用途", "主要模块", "运行入口", "关键技术栈"):
                        self.assertIn(section, result.answer)
                self.assertEqual(first.metrics.cached_input_tokens, 0)
                self.assertEqual(first.metrics.uncached_input_tokens, 10_000)
                self.assertEqual(second.metrics.cached_input_tokens, 7_500)
                self.assertEqual(second.metrics.uncached_input_tokens, 2_500)
                self.assertEqual(first.metrics.total_tokens, second.metrics.total_tokens)


class ProviderCachePayloadTests(unittest.TestCase):
    def _client(self) -> LLMClient:
        return LLMClient(Settings(), credential_resolver=lambda provider: "test-key")

    def test_openai_uses_key_and_supported_breakpoint(self) -> None:
        client = self._client()
        captured = {}

        def response(provider, url, *, headers, payload, on_delta=None):
            captured.update(payload)
            return {"model": "gpt-5.6", "status": "completed", "output": [],
                    "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}}

        client._native_tool_response = response
        client._decide_openai_tools(
            [{"role": "system", "content": "stable"}, {"role": "user", "content": "hi"}],
            [],
            {},
            "gpt-5.6",
            max_output_tokens=64,
        )

        self.assertRegex(captured["prompt_cache_key"], r"^agent-pcv1-[0-9a-f]{40}$")
        self.assertEqual(captured["prompt_cache_options"], {"mode": "explicit"})
        self.assertEqual(
            captured["input"][0]["content"][0]["prompt_cache_breakpoint"],
            {"mode": "explicit"},
        )

    def test_deepseek_does_not_receive_openai_cache_fields(self) -> None:
        client = self._client()
        captured = {}

        def response(provider, url, *, headers, payload, on_delta=None):
            captured.update(payload)
            return {"model": "deepseek-chat", "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12,
                              "prompt_cache_hit_tokens": 7, "prompt_cache_miss_tokens": 3}}

        client._native_tool_response = response
        decision = client._decide_deepseek_tools(
            [{"role": "system", "content": "stable"}, {"role": "user", "content": "hi"}],
            [],
            {},
            "deepseek-chat",
            max_output_tokens=64,
        )

        self.assertNotIn("prompt_cache_key", captured)
        self.assertNotIn("prompt_cache_options", captured)
        self.assertIsInstance(captured["messages"][0]["content"], str)
        assert decision.usage is not None
        self.assertEqual(decision.usage.cached_input_tokens, 7)
        self.assertEqual(decision.usage.uncached_input_tokens, 3)
        self.assertEqual(decision.usage.total_tokens, 12)

    def test_anthropic_uses_its_own_cache_control(self) -> None:
        client = self._client()
        captured = {}

        def response(provider, url, *, headers, payload, on_delta=None):
            captured.update(payload)
            return {"model": "claude-test", "content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 2}}

        client._native_tool_response = response
        client._decide_anthropic_tools(
            [{"role": "system", "content": "stable"}, {"role": "user", "content": "hi"}],
            [],
            {},
            "claude-test",
            max_output_tokens=64,
        )

        self.assertEqual(captured["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertNotIn("prompt_cache_key", captured)


if __name__ == "__main__":
    unittest.main()
