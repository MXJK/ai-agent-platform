from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from fastapi.testclient import TestClient

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.llm import (
    LLMProviderError,
    LLMStreamEvent,
    LLMUsage,
    _google_usage,
)
from ai_agent_platform.main import create_app
from ai_agent_platform.schemas.chat import ChatStreamRequest


def wait_for_run(client: TestClient, run_id: str) -> dict:
    for _ in range(200):
        body = client.get(f"/api/v1/agent/runs/{run_id}").json()
        if body["status"] in {"completed", "failed", "waiting_approval"}:
            return body
        time.sleep(0.01)
    raise AssertionError("agent run did not finish")


class ApiTests(unittest.TestCase):
    def test_workspace_registration_listing_and_lookup(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            with self._client(root) as client:
                created = client.put(
                    "/api/v1/workspaces/project",
                    json={"root_path": str(workspace)},
                )
                self.assertEqual(created.status_code, 200)
                self.assertEqual(created.json()["root_path"], str(workspace.resolve()))
                self.assertEqual(
                    client.get("/api/v1/workspaces/project").json()["id"],
                    "project",
                )
                listed = client.get("/api/v1/workspaces").json()["workspaces"]
                self.assertEqual([item["id"] for item in listed], ["project"])

    def test_workspace_rejects_missing_outside_and_symlink_escape(self) -> None:
        with TemporaryDirectory() as allowed_dir, TemporaryDirectory() as outside_dir:
            allowed = Path(allowed_dir)
            outside = Path(outside_dir)
            link = allowed / "escape"
            link.symlink_to(outside, target_is_directory=True)
            with self._client(allowed) as client:
                missing = client.put(
                    "/api/v1/workspaces/missing",
                    json={"root_path": str(allowed / "missing")},
                )
                direct = client.put(
                    "/api/v1/workspaces/outside",
                    json={"root_path": str(outside)},
                )
                linked = client.put(
                    "/api/v1/workspaces/link",
                    json={"root_path": str(link)},
                )
            self.assertEqual(missing.status_code, 400)
            self.assertEqual(direct.status_code, 400)
            self.assertEqual(linked.status_code, 400)

    def test_agent_uses_workspace_contract_and_live_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "project"
            workspace.mkdir()
            source = workspace / "app.py"
            source.write_text("VALUE = 'first'\n", encoding="utf-8")
            with self._client(root) as client:
                session_id = client.post(
                    "/api/v1/sessions", json={"user_id": "tester"}
                ).json()["id"]
                client.put(
                    "/api/v1/workspaces/project",
                    json={"root_path": str(workspace)},
                )
                rejected = client.post(
                    "/api/v1/agent/runs",
                    json={
                        "conversation_id": session_id,
                        "message": "read app.py",
                        "repository_id": "project",
                    },
                )
                self.assertEqual(rejected.status_code, 422)

                first = client.post(
                    "/api/v1/agent/runs",
                    json={
                        "conversation_id": session_id,
                        "message": "app.py 中的 VALUE 是什么？",
                        "workspace_id": "project",
                        "focus_files": ["app.py"],
                    },
                )
                result = wait_for_run(client, first.json()["run_id"])
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["workspace_id"], "project")
                sources = result["result"]["context_sources"]
                self.assertTrue(
                    any(
                        item["path"] == "app.py" and "first" in item["text"]
                        for item in sources
                    )
                )
                self.assertNotIn("rag_context", result["result"])

                source.write_text("VALUE = 'second'\n", encoding="utf-8")
                second = client.post(
                    "/api/v1/agent/runs",
                    json={
                        "conversation_id": session_id,
                        "message": "再次读取 app.py",
                        "workspace_id": "project",
                        "focus_files": ["app.py"],
                    },
                )
                second_result = wait_for_run(client, second.json()["run_id"])
                self.assertTrue(
                    any(
                        item["path"] == "app.py" and "second" in item["text"]
                        for item in second_result["result"]["context_sources"]
                    )
                )

    def test_removed_repository_index_endpoints_return_404(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            response = client.post(
                "/api/v1/repositories/repo_main/index",
                json={"root_path": temp_dir},
            )
            self.assertEqual(response.status_code, 404)

    def test_serves_unified_chat_and_workspace_agent_frontend(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            response = client.get("/")
            script_response = client.get("/static/app.js")
            stylesheet_response = client.get("/static/styles.css")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn('id="composer-mode-input"', response.text)
        self.assertIn('id="thinking-level-input"', response.text)
        self.assertIn('id="workspace-id-input"', response.text)
        self.assertNotIn('id="repository-id-input"', response.text)
        self.assertEqual(script_response.status_code, 200)
        self.assertIn("thinking_level", script_response.text)
        self.assertIn("submitComposerMessage", script_response.text)
        self.assertIn("runAgentFromComposer", script_response.text)
        self.assertIn("workspace_id", script_response.text)
        self.assertNotIn("repository_id", script_response.text)
        self.assertIn("prefers-reduced-motion", stylesheet_response.text)

    def test_chat_request_accepts_google_provider(self) -> None:
        request = ChatStreamRequest(
            conversation_id="sess_google",
            message="你好",
            provider="google",
            model="gemini-test-model",
            thinking_level="medium",
        )

        self.assertEqual(request.provider, "google")
        self.assertEqual(request.thinking_level, "medium")

    def test_streams_chat_response_and_records_messages(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            session_id = client.post(
                "/api/v1/sessions",
                json={"user_id": "user_1"},
            ).json()["id"]
            stream_response = client.post(
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

            messages = client.get(
                f"/api/v1/sessions/{session_id}/messages"
            ).json()["messages"]
            self.assertEqual(
                [message["role"] for message in messages],
                ["user", "assistant"],
            )
            self.assertIn("fake model reply to", messages[1]["content"])
            metrics = client.get("/api/v1/metrics").json()["counters"]
            self.assertEqual(metrics["chat_streams_completed_total"], 1)
            self.assertGreater(metrics["llm_input_tokens_total"], 0)
            self.assertGreater(metrics["llm_output_tokens_total"], 0)

    def test_chat_stream_reports_google_max_tokens_as_error(self) -> None:
        class TruncatedLLMClient:
            def stream_chat(self, messages, **kwargs):
                self.thinking_level = kwargs.get("thinking_level")
                yield LLMStreamEvent(type="delta", text="partial answer")
                yield LLMStreamEvent(
                    type="usage",
                    usage=LLMUsage(
                        input_tokens=12,
                        output_tokens=900,
                        thoughts_tokens=1100,
                    ),
                )
                raise LLMProviderError(
                    "Gemini reached the configured output token limit",
                    code="max_output_tokens",
                    finish_reason="MAX_TOKENS",
                )

        truncated_llm = TruncatedLLMClient()
        with TemporaryDirectory() as temp_dir:
            client = TestClient(
                create_app(
                    settings=Settings(
                        llm_provider="google",
                        llm_model="gemini-3.5-flash",
                        google_api_key="test-key",
                        workspace_allowed_roots=(str(Path(temp_dir).resolve()),),
                    ),
                    llm_client=truncated_llm,
                )
            )
            with client:
                session_id = client.post(
                    "/api/v1/sessions",
                    json={"user_id": "user_1"},
                ).json()["id"]
                response = client.post(
                    "/api/v1/chat/stream",
                    json={
                        "conversation_id": session_id,
                        "message": "long answer",
                        "thinking_level": "high",
                    },
                )

                self.assertEqual(response.status_code, 200)
                self.assertIn("event: delta", response.text)
                self.assertIn('"thoughts_tokens": 1100', response.text)
                self.assertIn("event: error", response.text)
                self.assertIn('"code": "max_output_tokens"', response.text)
                self.assertIn('"finish_reason": "MAX_TOKENS"', response.text)
                self.assertIn('"partial_response": true', response.text)
                self.assertNotIn("event: done", response.text)
                self.assertEqual(truncated_llm.thinking_level, "high")
                counters = client.get("/api/v1/metrics").json()["counters"]
                self.assertEqual(counters["chat_streams_failed_total"], 1)
                self.assertEqual(counters["llm_thoughts_tokens_total"], 1100)

    def test_chat_stream_rejects_missing_session_and_oversized_message(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = Settings(
                llm_provider="fake",
                embedding_provider="local",
                llm_max_input_chars=4,
                workspace_allowed_roots=(str(Path(temp_dir).resolve()),),
            )
            with TestClient(create_app(settings=settings)) as client:
                missing = client.post(
                    "/api/v1/chat/stream",
                    json={
                        "conversation_id": "sess_missing",
                        "message": "hi",
                    },
                )
                self.assertEqual(missing.status_code, 404)
                session_id = client.post(
                    "/api/v1/sessions",
                    json={"user_id": "user_1"},
                ).json()["id"]
                oversized = client.post(
                    "/api/v1/chat/stream",
                    json={
                        "conversation_id": session_id,
                        "message": "hello",
                    },
                )
                self.assertEqual(oversized.status_code, 413)

    def test_independent_knowledge_base_still_ingests_searches_and_answers(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            ingested = client.post(
                "/api/v1/knowledge-bases/docs/documents",
                json={
                    "filename": "guide.md",
                    "content": "Falcon mode enables deterministic offline testing.",
                },
            )
            self.assertEqual(ingested.status_code, 201)
            search = client.post(
                "/api/v1/knowledge-bases/docs/search",
                json={"query": "Falcon deterministic", "limit": 3},
            )
            self.assertEqual(search.status_code, 200)
            self.assertGreaterEqual(len(search.json()["results"]), 1)
            answer = client.post(
                "/api/v1/knowledge-bases/docs/ask",
                json={"question": "What enables offline testing?", "limit": 3},
            )
            self.assertEqual(answer.status_code, 200)
            self.assertGreaterEqual(len(answer.json()["citations"]), 1)

    def test_rag_search_is_scoped_and_rejects_unsupported_types(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            client.post(
                "/api/v1/knowledge-bases/customer_faq/documents",
                json={
                    "filename": "refund.md",
                    "content": "退款申请需要在订单完成后 7 天内提交。",
                },
            )
            client.post(
                "/api/v1/knowledge-bases/hr_policy/documents",
                json={
                    "filename": "vacation.md",
                    "content": "年假需要提前 3 个工作日提交审批。",
                },
            )
            response = client.post(
                "/api/v1/knowledge-bases/hr_policy/search",
                json={"query": "退款规则是什么？", "limit": 5},
            )
            self.assertEqual(response.status_code, 200)
            results = response.json()["results"]
            self.assertGreaterEqual(len(results), 1)
            self.assertTrue(
                all(result["knowledge_base_id"] == "hr_policy" for result in results)
            )
            unsupported = client.post(
                "/api/v1/knowledge-bases/hr_policy/documents",
                json={
                    "filename": "manual.pdf",
                    "content": "PDF binary requires a parser.",
                },
            )
            self.assertEqual(unsupported.status_code, 400)
            self.assertIn("unsupported document type", unsupported.json()["detail"])

    def test_missing_agent_run_returns_404(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            response = client.get("/api/v1/agent/runs/run_missing")
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["detail"], "agent run not found")

    def test_google_usage_metadata_is_normalized(self) -> None:
        class UsageMetadata:
            prompt_token_count = 12
            candidates_token_count = 7
            thoughts_token_count = 5

        usage = _google_usage(UsageMetadata())

        self.assertIsNotNone(usage)
        assert usage is not None
        self.assertEqual(usage.input_tokens, 12)
        self.assertEqual(usage.output_tokens, 7)
        self.assertEqual(usage.thoughts_tokens, 5)
        self.assertEqual(usage.total_tokens, 24)

    @staticmethod
    def _client(allowed_root: Path) -> TestClient:
        settings = Settings(
            llm_provider="fake",
            embedding_provider="local",
            workspace_allowed_roots=(str(allowed_root.resolve()),),
            background_task_workers=2,
        )
        return TestClient(create_app(settings=settings))


if __name__ == "__main__":
    unittest.main()
