from pathlib import Path
from dataclasses import replace
from tempfile import TemporaryDirectory
import time
import unittest

from fastapi.testclient import TestClient

from ai_agent_platform.core import Settings
from ai_agent_platform.agents.coding_agent import (
    AgentRunRecord,
    create_coding_tool_registry,
)
from ai_agent_platform.integrations import RAGConfigurationError, RAGProviderError
from ai_agent_platform.integrations.llm import LLMResponse, _google_usage
from ai_agent_platform.integrations.rag import RetrievedDocument
from ai_agent_platform.integrations.tools import ToolCall, ToolExecutionContext
from ai_agent_platform.main import create_app
from ai_agent_platform.schemas.chat import ChatStreamRequest


class FlakySearchRAGService:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, **_: object) -> list[RetrievedDocument]:
        self.calls += 1
        if self.calls == 1:
            raise RAGProviderError("temporary vector store outage")
        return [
            RetrievedDocument(
                id="chunk_1",
                knowledge_base_id="repo_main",
                document_id="doc_1",
                filename="agent.py",
                chunk_index=0,
                text="def recoverable_search(): return 'ok'",
                score=0.91,
                recall_score=0.91,
            )
        ]


class BrokenSearchRAGService:
    def search(self, **_: object) -> list[RetrievedDocument]:
        raise RAGConfigurationError("vector store is not configured")


class StructuredPlanningLLMClient:
    def complete(self, prompt: str) -> LLMResponse:
        if "classifying a coding-agent user request" in prompt:
            return LLMResponse(
                text=(
                    '{"intent":"test_strategy","reason":"LLM chose test planning",'
                    '"confidence":0.91}'
                ),
                model="structured-test-model",
            )
        if "planning tool calls for a coding-agent backend" in prompt:
            return LLMResponse(
                text=(
                    '{"tool_calls":['
                    '{"name":"repository_context_search","arguments":{}},'
                    '{"name":"test_designer","arguments":{'
                    '"goal":"cover async agent runs",'
                    '"candidate_files":["tests/test_api.py"]'
                    "}}]}"
                ),
                model="structured-test-model",
            )
        return LLMResponse(text="{}", model="structured-test-model")


class FailingCodingAgentRuntime:
    def __init__(self) -> None:
        self.records = {}

    def create_queued_run(
        self, *, conversation_id: str, repository_id: str
    ) -> AgentRunRecord:
        record = AgentRunRecord(
            run_id="run_failing",
            thread_id="run_failing",
            conversation_id=conversation_id,
            repository_id=repository_id,
            status="queued",
            checkpoint_id=None,
            latest_node=None,
            next_nodes=["setup"],
            trace=[],
        )
        self.records[record.run_id] = record
        return record

    def get_run(self, run_id: str) -> AgentRunRecord:
        return self.records[run_id]

    def run(self, **_: object) -> object:
        run_id = str(_.get("run_id"))
        self.records[run_id] = replace(
            self.records[run_id],
            status="failed",
            error="agent runtime exploded",
            errors=[
                {
                    "node": "runtime",
                    "code": "runtime_error",
                    "message": "agent runtime exploded",
                    "retryable": False,
                    "attempt": 1,
                    "max_attempts": 1,
                    "recovered": False,
                }
            ],
        )
        raise RuntimeError("agent runtime exploded")


class APITests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(
            create_app(
                settings=Settings(
                    llm_provider="fake",
                    embedding_provider="local",
                )
            )
        )

    def wait_for_agent_run(
        self,
        client: TestClient,
        run_id: str,
        terminal_statuses: tuple[str, ...] = ("completed", "failed", "waiting_approval"),
    ) -> dict:
        for _ in range(100):
            response = client.get(f"/api/v1/agent/runs/{run_id}")
            self.assertEqual(response.status_code, 200)
            body = response.json()
            if body["status"] in terminal_statuses:
                return body
            time.sleep(0.02)
        self.fail(f"agent run {run_id} did not reach {terminal_statuses}")

    def wait_for_index_job(
        self,
        client: TestClient,
        repository_id: str,
        job_id: str,
    ) -> dict:
        for _ in range(100):
            response = client.get(
                f"/api/v1/repositories/{repository_id}/index-jobs/{job_id}"
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            if body["status"] in {"completed", "completed_with_errors", "failed"}:
                return body
            time.sleep(0.02)
        self.fail(f"repository index job {job_id} did not finish")

    def test_gets_session_summary(self) -> None:
        create_response = self.client.post(
            "/api/v1/sessions",
            json={"user_id": "user_1"},
        )
        self.assertEqual(create_response.status_code, 201)
        session_id = create_response.json()["id"]

        message_response = self.client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"role": "user", "content": "攻击附近的敌人", "run_agent": True},
        )
        self.assertEqual(message_response.status_code, 201)

        summary_response = self.client.get(f"/api/v1/sessions/{session_id}/summary")

        self.assertEqual(summary_response.status_code, 200)
        self.assertEqual(
            summary_response.json(),
            {
                "session_id": session_id,
                "message_count": 2,
                "last_message": (
                    "agent_action=combat.attack; "
                    "confidence=0.80; "
                    "reason=combat keyword matched"
                ),
            },
        )

    def test_serves_frontend_console(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("AI Agent Platform", response.text)
        self.assertIn('data-view-panel="chat"', response.text)
        self.assertIn('id="approval-card"', response.text)
        self.assertIn('id="settings-dialog"', response.text)
        self.assertIn('aria-live="polite"', response.text)
        self.assertIn("/static/app.js", response.text)

        script_response = self.client.get("/static/app.js")
        stylesheet_response = self.client.get("/static/styles.css")
        self.assertEqual(script_response.status_code, 200)
        self.assertEqual(stylesheet_response.status_code, 200)
        self.assertIn("text/javascript", script_response.headers["content-type"])
        self.assertIn("text/css", stylesheet_response.headers["content-type"])
        self.assertIn("AbortController", script_response.text)
        self.assertIn("prefers-reduced-motion", stylesheet_response.text)

    def test_chat_request_accepts_google_provider(self) -> None:
        request = ChatStreamRequest(
            conversation_id="sess_google",
            message="你好",
            provider="google",
            model="gemini-test-model",
        )

        self.assertEqual(request.provider, "google")

    def test_streams_chat_response_and_records_messages(self) -> None:
        create_response = self.client.post(
            "/api/v1/sessions",
            json={"user_id": "user_1"},
        )
        session_id = create_response.json()["id"]

        stream_response = self.client.post(
            "/api/v1/chat/stream",
            json={
                "conversation_id": session_id,
                "message": "解释一下SSE",
            },
        )

        self.assertEqual(stream_response.status_code, 200)
        self.assertEqual(
            stream_response.headers["content-type"].split(";")[0],
            "text/event-stream",
        )
        self.assertIn("event: meta", stream_response.text)
        self.assertIn("event: delta", stream_response.text)
        self.assertIn("event: usage", stream_response.text)
        self.assertIn("event: done", stream_response.text)

        messages_response = self.client.get(f"/api/v1/sessions/{session_id}/messages")
        messages = messages_response.json()["messages"]
        self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
        self.assertIn("fake model reply to", messages[1]["content"])
        metrics = self.client.get("/api/v1/metrics").json()["counters"]
        self.assertEqual(metrics["chat_streams_completed_total"], 1)
        self.assertGreater(metrics["llm_input_tokens_total"], 0)
        self.assertGreater(metrics["llm_output_tokens_total"], 0)

    def test_chat_stream_returns_404_for_missing_conversation(self) -> None:
        response = self.client.post(
            "/api/v1/chat/stream",
            json={
                "conversation_id": "sess_missing",
                "message": "hello",
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "conversation not found")

    def test_chat_stream_rejects_message_over_context_limit(self) -> None:
        client = TestClient(
            create_app(
                settings=Settings(
                    llm_provider="fake",
                    embedding_provider="local",
                    llm_max_input_chars=4,
                )
            )
        )
        create_response = client.post("/api/v1/sessions", json={"user_id": "user_1"})
        session_id = create_response.json()["id"]

        response = client.post(
            "/api/v1/chat/stream",
            json={
                "conversation_id": session_id,
                "message": "hello",
            },
        )

        self.assertEqual(response.status_code, 413)

    def test_ingests_searches_and_answers_with_rag_citations(self) -> None:
        ingest_response = self.client.post(
            "/api/v1/knowledge-bases/customer_faq/documents",
            json={
                "filename": "refund.md",
                "content": (
                    "# 退款规则\n\n"
                    "用户可以在订单完成后 7 天内申请退款。"
                    "超过 7 天需要人工客服审核。\n\n"
                    "# 发货规则\n\n"
                    "普通商品会在 48 小时内发货。"
                ),
            },
        )
        self.assertEqual(ingest_response.status_code, 201)
        self.assertEqual(ingest_response.json()["knowledge_base_id"], "customer_faq")
        self.assertGreaterEqual(ingest_response.json()["chunk_count"], 1)

        search_response = self.client.post(
            "/api/v1/knowledge-bases/customer_faq/search",
            json={
                "query": "订单完成后多久可以申请退款？",
                "recall_limit": 10,
                "limit": 3,
            },
        )

        self.assertEqual(search_response.status_code, 200)
        results = search_response.json()["results"]
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["filename"], "refund.md")
        self.assertIn("退款", results[0]["text"])
        self.assertIsNotNone(results[0]["recall_score"])
        self.assertIsNotNone(results[0]["lexical_score"])
        self.assertIsNotNone(results[0]["hybrid_score"])

        ask_response = self.client.post(
            "/api/v1/knowledge-bases/customer_faq/ask",
            json={"question": "退款期限是多久？", "recall_limit": 10, "limit": 2},
        )

        self.assertEqual(ask_response.status_code, 200)
        body = ask_response.json()
        self.assertIn("fake model reply to", body["answer"])
        self.assertGreaterEqual(len(body["citations"]), 1)
        self.assertIn("退款", body["citations"][0]["text"])

    def test_code_rag_returns_line_ranges_and_symbols(self) -> None:
        ingest_response = self.client.post(
            "/api/v1/knowledge-bases/repo_main/documents",
            json={
                "filename": "app.py",
                "content": (
                    "import os\n\n\n"
                    "class DemoService:\n"
                    "    def build_answer(self):\n"
                    "        return 'answer from service'\n\n"
                    "def helper_function():\n"
                    "    return 'helper'\n"
                ),
            },
        )
        self.assertEqual(ingest_response.status_code, 201)

        search_response = self.client.post(
            "/api/v1/knowledge-bases/repo_main/search",
            json={"query": "build_answer service", "limit": 5},
        )

        self.assertEqual(search_response.status_code, 200)
        results = search_response.json()["results"]
        service_result = next(
            result
            for result in results
            if "DemoService" in result["symbols"]
            or "build_answer" in result["symbols"]
        )
        self.assertEqual(service_result["filename"], "app.py")
        self.assertIsNotNone(service_result["start_line"])
        self.assertIsNotNone(service_result["end_line"])
        self.assertLessEqual(service_result["start_line"], 5)
        self.assertGreaterEqual(service_result["end_line"], 5)

    def test_runs_coding_agent_with_repo_context_tools_trace_and_memory(self) -> None:
        create_response = self.client.post(
            "/api/v1/sessions",
            json={"user_id": "user_1"},
        )
        session_id = create_response.json()["id"]
        ingest_response = self.client.post(
            "/api/v1/knowledge-bases/repo_main/documents",
            json={
                "filename": "ai_agent_platform/api/routes/chat.py",
                "content": (
                    "def chat_stream(request: ChatStreamRequest) -> StreamingResponse:\n"
                    "    return StreamingResponse(_chat_stream_events(...))\n\n"
                    "def _chat_stream_events(request: ChatStreamRequest):\n"
                    "    yield from llm_client.stream_chat(...)\n"
                ),
            },
        )
        self.assertEqual(ingest_response.status_code, 201)

        first_response = self.client.post(
            "/api/v1/agent/runs",
            json={
                "conversation_id": session_id,
                "message": "解释 chat stream 接口在哪里实现，ChatStreamRequest 是怎么进入流程的？",
                "repository_id": "repo_main",
                "focus_files": ["ai_agent_platform/api/routes/chat.py"],
            },
        )

        self.assertEqual(first_response.status_code, 202)
        first_queued_body = first_response.json()
        self.assertEqual(first_queued_body["status"], "queued")
        self.assertTrue(first_queued_body["run_id"].startswith("run_"))

        status_body = self.wait_for_agent_run(
            self.client,
            first_queued_body["run_id"],
            terminal_statuses=("completed", "failed"),
        )
        body = status_body["result"]
        self.assertEqual(body["graph_engine"], "langgraph")
        self.assertTrue(body["run_id"].startswith("run_"))
        self.assertEqual(body["thread_id"], body["run_id"])
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["metrics"]["node_count"], 6)
        self.assertEqual(
            body["metrics"]["tool_call_count"],
            len(body["tool_calls"]),
        )
        self.assertGreaterEqual(body["metrics"]["elapsed_ms"], 0)
        process_metrics = self.client.get("/api/v1/metrics").json()["counters"]
        self.assertEqual(process_metrics["agent_runs_submitted_total"], 1)
        self.assertEqual(
            process_metrics["agent_run_executions_completed_total"],
            1,
        )
        self.assertIsNotNone(body["checkpoint_id"])
        self.assertEqual(body["repository_id"], "repo_main")
        self.assertEqual(body["role"], "研发助手 / 代码仓库问答 Agent")
        self.assertEqual(body["intent"], "repo_navigation")
        self.assertGreaterEqual(len(body["rag_context"]), 1)
        tool_names = [tool_call["name"] for tool_call in body["tool_calls"]]
        self.assertEqual(tool_names[0], "repository_context_search")
        self.assertIn("repo.search_code", tool_names)
        self.assertIn("repo.read_file", tool_names)
        self.assertIn("file_symbol_locator", tool_names)
        self.assertIn("code_explainer", tool_names)
        result_by_name = {item["name"]: item for item in body["tool_results"]}
        self.assertTrue(result_by_name["repo.search_code"]["ok"])
        self.assertEqual(result_by_name["repo.search_code"]["provider"], "local")
        self.assertEqual(
            result_by_name["repo.search_code"]["permission_level"],
            "read_only",
        )
        self.assertTrue(result_by_name["repo.read_file"]["ok"])
        self.assertIn(
            "def chat_stream",
            result_by_name["repo.read_file"]["result"]["content"],
        )
        self.assertEqual(
            [step["node"] for step in body["trace"]],
            [
                "setup",
                "classify_request",
                "retrieve_repository_context",
                "plan_tools",
                "inspect_repository",
                "compose_answer",
            ],
        )
        self.assertIn("ai_agent_platform/api/routes/chat.py", body["answer"])

        self.assertEqual(status_body["run_id"], body["run_id"])
        self.assertEqual(status_body["thread_id"], body["thread_id"])
        self.assertEqual(status_body["status"], "completed")
        self.assertEqual(status_body["checkpoint_id"], body["checkpoint_id"])
        self.assertEqual(status_body["latest_node"], "compose_answer")
        self.assertEqual(status_body["next_nodes"], [])
        self.assertEqual(status_body["result"]["answer"], body["answer"])
        self.assertEqual(
            [step["node"] for step in status_body["trace"]],
            [step["node"] for step in body["trace"]],
        )
        events_response = self.client.get(f"/api/v1/agent/runs/{body['run_id']}/events")
        self.assertEqual(events_response.status_code, 200)
        event_types = [event["type"] for event in events_response.json()["events"]]
        self.assertEqual(event_types[0], "run_queued")
        self.assertIn("node_completed", event_types)
        self.assertEqual(event_types[-1], "run_completed")

        second_response = self.client.post(
            "/api/v1/agent/runs",
            json={
                "conversation_id": session_id,
                "message": "帮我实现 agent 支持 repository_id 参数并补测试",
                "repository_id": "repo_main",
            },
        )

        self.assertEqual(second_response.status_code, 202)
        second_status_body = self.wait_for_agent_run(
            self.client,
            second_response.json()["run_id"],
        )
        second_body = second_status_body["result"]
        self.assertEqual(second_status_body["status"], "waiting_approval")
        self.assertEqual(second_body["intent"], "change_planning")
        self.assertEqual(second_body["answer"], "")
        second_tool_names = [tool_call["name"] for tool_call in second_body["tool_calls"]]
        self.assertIn("repo.search_code", second_tool_names)
        self.assertIn("change_planner", second_tool_names)
        self.assertIn("test_designer", second_tool_names)
        self.assertEqual(
            second_status_body["pending_approval"]["type"],
            "tool_plan_review",
        )
        self.assertTrue(second_status_body["pending_approval"]["approval_required"])
        self.assertEqual(
            second_status_body["pending_approval"]["planned_tools"][0],
            "repository_context_search",
        )
        self.assertIn(
            "change_planner",
            second_status_body["pending_approval"]["planned_tools"],
        )
        approval_item = next(
            item
            for item in second_status_body["pending_approval"]["approval_required_tools"]
            if item["name"] == "change_planner"
        )
        self.assertEqual(approval_item["permission_level"], "write_safe")
        self.assertIn("human review", approval_item["risk_summary"])
        self.assertEqual(
            approval_item["arguments_summary"]["goal"],
            "帮我实现 agent 支持 repository_id 参数并补测试",
        )
        self.assertEqual(
            [step["node"] for step in second_status_body["trace"]],
            [
                "setup",
                "classify_request",
                "retrieve_repository_context",
                "plan_tools",
            ],
        )
        self.assertEqual(
            second_status_body["trace"][0]["output"]["history_messages"],
            2,
        )

        pending_status_response = self.client.get(
            f"/api/v1/agent/runs/{second_body['run_id']}"
        )
        self.assertEqual(pending_status_response.status_code, 200)
        pending_status_body = pending_status_response.json()
        self.assertEqual(pending_status_body["status"], "waiting_approval")
        self.assertEqual(pending_status_body["latest_node"], "review_tool_plan")
        self.assertEqual(pending_status_body["next_nodes"], ["review_tool_plan"])
        self.assertEqual(
            pending_status_body["pending_approval"]["interrupt_id"],
            second_status_body["pending_approval"]["interrupt_id"],
        )

        resume_response = self.client.post(
            f"/api/v1/agent/runs/{second_body['run_id']}/resume",
            json={"approved": True, "feedback": "可以执行"},
        )
        self.assertEqual(resume_response.status_code, 202)
        resume_status_body = self.wait_for_agent_run(
            self.client,
            second_body["run_id"],
            terminal_statuses=("completed", "failed"),
        )
        resume_body = resume_status_body["result"]
        self.assertEqual(resume_body["run_id"], second_body["run_id"])
        self.assertEqual(resume_body["status"], "completed")
        self.assertIsNone(resume_body["pending_approval"])
        self.assertEqual(
            [step["node"] for step in resume_body["trace"]],
            [
                "setup",
                "classify_request",
                "retrieve_repository_context",
                "plan_tools",
                "review_tool_plan",
                "inspect_repository",
                "compose_answer",
            ],
        )
        self.assertTrue(resume_body["trace"][4]["output"]["approved"])
        resume_results = {item["name"]: item for item in resume_body["tool_results"]}
        self.assertEqual(
            resume_results["change_planner"]["permission_level"],
            "write_safe",
        )
        self.assertTrue(resume_results["change_planner"]["requires_approval"])

        messages_response = self.client.get(f"/api/v1/sessions/{session_id}/messages")
        messages = messages_response.json()["messages"]
        self.assertEqual(
            [message["role"] for message in messages],
            ["user", "assistant", "user", "assistant"],
        )

    def test_agent_uses_structured_llm_for_intent_and_tool_planning(self) -> None:
        client = TestClient(
            create_app(
                settings=Settings(llm_provider="fake", embedding_provider="local"),
                llm_client=StructuredPlanningLLMClient(),
            )
        )
        create_response = client.post("/api/v1/sessions", json={"user_id": "user_1"})
        session_id = create_response.json()["id"]

        run_response = client.post(
            "/api/v1/agent/runs",
            json={
                "conversation_id": session_id,
                "repository_id": "repo_main",
                "message": "帮我设计 agent run 异步流程的测试",
            },
        )

        self.assertEqual(run_response.status_code, 202)
        status_body = self.wait_for_agent_run(
            client,
            run_response.json()["run_id"],
            terminal_statuses=("completed", "failed"),
        )
        body = status_body["result"]
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["intent"], "test_strategy")
        classify_step = body["trace"][1]
        self.assertEqual(classify_step["output"]["planner_source"], "llm_structured")
        self.assertEqual(classify_step["output"]["confidence"], 0.91)
        plan_step = body["trace"][3]
        self.assertEqual(plan_step["output"]["planner_source"], "llm_structured")
        self.assertEqual(
            [tool_call["name"] for tool_call in body["tool_calls"]],
            ["repository_context_search", "test_designer"],
        )
        result_by_name = {item["name"]: item for item in body["tool_results"]}
        self.assertTrue(result_by_name["repository_context_search"]["ok"])
        self.assertTrue(result_by_name["test_designer"]["ok"])

    def test_local_repository_tools_are_scoped_to_repository_root(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text(
                "def target_symbol():\n"
                "    return 'ok'\n",
                encoding="utf-8",
            )
            registry = create_coding_tool_registry(root_path=root)
            specs = {spec.name: spec for spec in registry.list_specs()}
            self.assertIn("repo.list_files", specs)
            self.assertIn("repo.read_file", specs)
            self.assertIn("repo.search_code", specs)
            self.assertEqual(specs["repo.search_code"].permission_level, "read_only")

            context = ToolExecutionContext(
                conversation_id="sess_1",
                repository_id="repo_main",
            )
            search_result = registry.execute(
                ToolCall(
                    name="repo.search_code",
                    arguments={"query": "target_symbol"},
                ),
                context=context,
            )
            self.assertTrue(search_result.ok)
            self.assertEqual(search_result.result["matches"][0]["path"], "app.py")
            search_payload = search_result.to_response()
            self.assertEqual(
                search_payload["arguments_summary"],
                {"query": "target_symbol"},
            )
            self.assertIn("repository root", search_payload["risk_summary"])

            invalid_result = registry.execute(
                ToolCall(
                    name="repo.search_code",
                    arguments={"query": "target_symbol", "max_results": "many"},
                ),
                context=context,
            )
            self.assertFalse(invalid_result.ok)
            self.assertIn("invalid type", invalid_result.error)

            escaped_result = registry.execute(
                ToolCall(
                    name="repo.read_file",
                    arguments={"path": "../outside.py"},
                ),
                context=context,
            )
            self.assertFalse(escaped_result.ok)
            self.assertIn("escapes repository root", escaped_result.error)

            registry.register(
                "demo.large_output",
                lambda: {"content": "x" * 200},
                max_output_chars=40,
            )
            large_result = registry.execute(
                ToolCall(name="demo.large_output", arguments={}),
                context=context,
            )
            large_payload = large_result.to_response()
            self.assertTrue(large_payload["ok"])
            self.assertTrue(large_payload["output_truncated"])
            self.assertIn("truncated_output_preview", large_payload["result"])
            self.assertNotIn("content", large_payload["result"])

    def test_indexes_repository_files_and_skips_unchanged_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text(
                "def build_answer():\n"
                "    return 'repository indexing works'\n",
                encoding="utf-8",
            )
            (root / "README.md").write_text(
                "# Demo Repo\n\nRepository indexing notes.\n",
                encoding="utf-8",
            )
            (root / ".venv").mkdir()
            (root / ".venv" / "ignored.py").write_text(
                "def ignored(): pass\n",
                encoding="utf-8",
            )

            first_response = self.client.post(
                "/api/v1/repositories/repo_main/index",
                json={
                    "root_path": str(root),
                    "include_patterns": ["**/*.py", "**/*.md"],
                    "max_file_size": 10000,
                },
            )

            self.assertEqual(first_response.status_code, 202)
            submitted_body = first_response.json()
            self.assertTrue(
                first_response.headers["location"].endswith(
                    f"/index-jobs/{submitted_body['job_id']}"
                )
            )
            self.assertEqual(submitted_body["repository_id"], "repo_main")
            self.assertEqual(submitted_body["status"], "pending")
            first_body = self.wait_for_index_job(
                self.client,
                "repo_main",
                submitted_body["job_id"],
            )
            self.assertEqual(first_body["status"], "completed")
            self.assertEqual(first_body["scanned_files"], 2)
            self.assertEqual(first_body["indexed_files"], 2)
            self.assertEqual(first_body["skipped_files"], 0)
            self.assertEqual(first_body["failed_files"], 0)
            self.assertIsNotNone(first_body["completed_at"])

            search_response = self.client.post(
                "/api/v1/knowledge-bases/repo_main/search",
                json={"query": "build_answer repository indexing", "limit": 3},
            )
            self.assertEqual(search_response.status_code, 200)
            filenames = {
                result["filename"] for result in search_response.json()["results"]
            }
            self.assertIn("app.py", filenames)

            create_response = self.client.post(
                "/api/v1/sessions",
                json={"user_id": "user_1"},
            )
            session_id = create_response.json()["id"]
            agent_response = self.client.post(
                "/api/v1/agent/runs",
                json={
                    "conversation_id": session_id,
                    "repository_id": "repo_main",
                    "message": "解释 build_answer 在哪里实现",
                },
            )
            self.assertEqual(agent_response.status_code, 202)
            agent_status_body = self.wait_for_agent_run(
                self.client,
                agent_response.json()["run_id"],
                terminal_statuses=("completed", "failed"),
            )
            agent_body = agent_status_body["result"]
            self.assertEqual(agent_body["status"], "completed")
            self.assertTrue(
                any(
                    item["filename"] == "app.py"
                    for item in agent_body["rag_context"]
                )
            )

            second_response = self.client.post(
                "/api/v1/repositories/repo_main/index",
                json={
                    "root_path": str(root),
                    "include_patterns": ["**/*.py", "**/*.md"],
                    "max_file_size": 10000,
                },
            )

            self.assertEqual(second_response.status_code, 202)
            second_body = self.wait_for_index_job(
                self.client,
                "repo_main",
                second_response.json()["job_id"],
            )
            self.assertEqual(second_body["status"], "completed")
            self.assertEqual(second_body["scanned_files"], 2)
            self.assertEqual(second_body["indexed_files"], 0)
            self.assertEqual(second_body["skipped_files"], 2)
            self.assertEqual(second_body["failed_files"], 0)

            metrics = self.client.get("/api/v1/metrics").json()["counters"]
            self.assertGreaterEqual(
                metrics["background_task_repository_index_completed_total"],
                2,
            )

    def test_repository_index_job_returns_404_when_missing(self) -> None:
        response = self.client.get(
            "/api/v1/repositories/repo_main/index-jobs/idxjob_missing"
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "index job not found")

    def test_agent_run_status_returns_404_for_missing_run(self) -> None:
        response = self.client.get("/api/v1/agent/runs/run_missing")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "agent run not found")

    def test_agent_run_records_user_message_before_runtime_failure(self) -> None:
        client = TestClient(
            create_app(
                settings=Settings(llm_provider="fake", embedding_provider="local"),
                coding_agent_runtime=FailingCodingAgentRuntime(),
            )
        )
        create_response = client.post("/api/v1/sessions", json={"user_id": "user_1"})
        session_id = create_response.json()["id"]

        response = client.post(
            "/api/v1/agent/runs",
            json={
                "conversation_id": session_id,
                "repository_id": "repo_main",
                "message": "这条消息即使 agent 失败也要保留",
            },
        )
        self.assertEqual(response.status_code, 202)
        status_body = self.wait_for_agent_run(
            client,
            response.json()["run_id"],
            terminal_statuses=("failed",),
        )
        self.assertEqual(status_body["error"], "agent runtime exploded")

        messages_response = client.get(f"/api/v1/sessions/{session_id}/messages")
        messages = messages_response.json()["messages"]
        self.assertEqual([message["role"] for message in messages], ["user"])
        self.assertEqual(
            messages[0]["content"],
            "这条消息即使 agent 失败也要保留",
        )

    def test_agent_run_resume_can_reject_change_plan(self) -> None:
        create_response = self.client.post(
            "/api/v1/sessions",
            json={"user_id": "user_1"},
        )
        session_id = create_response.json()["id"]

        run_response = self.client.post(
            "/api/v1/agent/runs",
            json={
                "conversation_id": session_id,
                "message": "帮我实现审批拒绝后不要继续执行工具",
                "repository_id": "repo_main",
            },
        )
        self.assertEqual(run_response.status_code, 202)
        run_status_body = self.wait_for_agent_run(
            self.client,
            run_response.json()["run_id"],
        )
        run_body = run_status_body["result"]
        self.assertEqual(run_status_body["status"], "waiting_approval")

        resume_response = self.client.post(
            f"/api/v1/agent/runs/{run_body['run_id']}/resume",
            json={"approved": False, "feedback": "目标还不清楚"},
        )
        self.assertEqual(resume_response.status_code, 202)
        resume_status_body = self.wait_for_agent_run(
            self.client,
            run_body["run_id"],
            terminal_statuses=("completed", "failed"),
        )
        resume_body = resume_status_body["result"]
        self.assertEqual(resume_body["status"], "completed")
        self.assertIn("未批准", resume_body["answer"])
        self.assertIn("目标还不清楚", resume_body["answer"])
        self.assertEqual(
            [step["node"] for step in resume_body["trace"]],
            [
                "setup",
                "classify_request",
                "retrieve_repository_context",
                "plan_tools",
                "review_tool_plan",
                "compose_answer",
            ],
        )

        second_resume_response = self.client.post(
            f"/api/v1/agent/runs/{run_body['run_id']}/resume",
            json={"approved": True},
        )
        self.assertEqual(second_resume_response.status_code, 409)

    def test_agent_rag_node_retries_recoverable_provider_errors(self) -> None:
        rag_service = FlakySearchRAGService()
        client = TestClient(
            create_app(
                settings=Settings(llm_provider="fake", embedding_provider="local"),
                rag_service=rag_service,
            )
        )
        create_response = client.post("/api/v1/sessions", json={"user_id": "user_1"})
        session_id = create_response.json()["id"]

        response = client.post(
            "/api/v1/agent/runs",
            json={
                "conversation_id": session_id,
                "repository_id": "repo_main",
                "message": "解释 recoverable_search 在哪里实现",
            },
        )

        self.assertEqual(response.status_code, 202)
        status_body = self.wait_for_agent_run(
            client,
            response.json()["run_id"],
            terminal_statuses=("completed", "failed"),
        )
        body = status_body["result"]
        self.assertEqual(body["status"], "completed")
        self.assertEqual(rag_service.calls, 2)
        self.assertEqual(len(body["errors"]), 1)
        self.assertEqual(body["errors"][0]["code"], "rag_provider_error")
        self.assertTrue(body["errors"][0]["retryable"])
        self.assertTrue(body["errors"][0]["recovered"])
        self.assertEqual(body["metrics"]["retry_count"], 1)
        self.assertEqual(body["metrics"]["recovered_error_count"], 1)
        self.assertIn("agent.py", body["answer"])
        retrieve_step = body["trace"][2]
        self.assertEqual(retrieve_step["node"], "retrieve_repository_context")
        self.assertEqual(retrieve_step["output"]["attempts"], 2)
        self.assertEqual(retrieve_step["output"]["recovered_error_count"], 1)

    def test_agent_routes_unrecoverable_rag_errors_to_error_answer(self) -> None:
        client = TestClient(
            create_app(
                settings=Settings(llm_provider="fake", embedding_provider="local"),
                rag_service=BrokenSearchRAGService(),
            )
        )
        create_response = client.post("/api/v1/sessions", json={"user_id": "user_1"})
        session_id = create_response.json()["id"]

        response = client.post(
            "/api/v1/agent/runs",
            json={
                "conversation_id": session_id,
                "repository_id": "repo_main",
                "message": "解释配置错误时的 agent 错误分支",
            },
        )

        self.assertEqual(response.status_code, 202)
        status_body = self.wait_for_agent_run(
            client,
            response.json()["run_id"],
            terminal_statuses=("completed", "failed"),
        )
        body = status_body["result"]
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["tool_calls"], [])
        self.assertEqual(len(body["errors"]), 1)
        self.assertEqual(body["errors"][0]["code"], "rag_configuration_error")
        self.assertFalse(body["errors"][0]["retryable"])
        self.assertFalse(body["errors"][0]["recovered"])
        self.assertIn("错误分支", body["answer"])
        self.assertEqual(
            [step["node"] for step in body["trace"]],
            [
                "setup",
                "classify_request",
                "retrieve_repository_context",
                "handle_error",
                "compose_error_answer",
            ],
        )

    def test_rag_search_is_scoped_by_knowledge_base_id(self) -> None:
        self.client.post(
            "/api/v1/knowledge-bases/customer_faq/documents",
            json={
                "filename": "refund.md",
                "content": "退款申请需要在订单完成后 7 天内提交。",
            },
        )
        self.client.post(
            "/api/v1/knowledge-bases/hr_policy/documents",
            json={
                "filename": "vacation.md",
                "content": "年假需要提前 3 个工作日提交审批。",
            },
        )

        response = self.client.post(
            "/api/v1/knowledge-bases/hr_policy/search",
            json={"query": "退款规则是什么？", "limit": 5},
        )

        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertGreaterEqual(len(results), 1)
        self.assertTrue(
            all(result["knowledge_base_id"] == "hr_policy" for result in results)
        )
        self.assertTrue(all(result["filename"] == "vacation.md" for result in results))

    def test_rag_ingest_rejects_unsupported_document_type(self) -> None:
        response = self.client.post(
            "/api/v1/knowledge-bases/customer_faq/documents",
            json={
                "filename": "manual.pdf",
                "content": "PDF binary would need a PDF parser in the next version.",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("unsupported document type", response.json()["detail"])

    def test_google_usage_metadata_is_normalized(self) -> None:
        class UsageMetadata:
            prompt_token_count = 12
            candidates_token_count = 7

        usage = _google_usage(UsageMetadata())

        self.assertIsNotNone(usage)
        assert usage is not None
        self.assertEqual(usage.input_tokens, 12)
        self.assertEqual(usage.output_tokens, 7)
        self.assertEqual(usage.total_tokens, 19)


if __name__ == "__main__":
    unittest.main()
