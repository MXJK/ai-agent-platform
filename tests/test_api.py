from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

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


def upload_document(
    client: TestClient,
    knowledge_base_id: str,
    filename: str,
    content: str | bytes,
):
    payload = content.encode("utf-8") if isinstance(content, str) else content
    return client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": (filename, payload)},
    )


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

    def test_workspace_directory_browser_stays_within_allowed_roots(self) -> None:
        with TemporaryDirectory() as allowed_dir, TemporaryDirectory() as outside_dir:
            allowed = Path(allowed_dir)
            alpha = allowed / "alpha"
            nested = alpha / "nested"
            beta = allowed / "Beta"
            alpha.mkdir()
            nested.mkdir()
            beta.mkdir()
            (allowed / "notes.txt").write_text("not a directory", encoding="utf-8")
            (allowed / "escape").symlink_to(
                Path(outside_dir),
                target_is_directory=True,
            )

            with self._client(allowed) as client:
                roots = client.get("/api/v1/workspace-directories")
                listing = client.get(
                    "/api/v1/workspace-directories",
                    params={"path": str(allowed)},
                )
                nested_listing = client.get(
                    "/api/v1/workspace-directories",
                    params={"path": str(alpha)},
                )
                outside = client.get(
                    "/api/v1/workspace-directories",
                    params={"path": outside_dir},
                )

            self.assertEqual(roots.status_code, 200)
            self.assertIsNone(roots.json()["current_path"])
            self.assertEqual(
                [item["path"] for item in roots.json()["directories"]],
                [str(allowed.resolve())],
            )
            self.assertEqual(listing.status_code, 200)
            self.assertEqual(listing.json()["current_path"], str(allowed.resolve()))
            self.assertIsNone(listing.json()["parent_path"])
            self.assertEqual(
                [item["name"] for item in listing.json()["directories"]],
                ["alpha", "Beta"],
            )
            self.assertEqual(
                nested_listing.json()["parent_path"],
                str(allowed.resolve()),
            )
            self.assertEqual(
                [item["name"] for item in nested_listing.json()["directories"]],
                ["nested"],
            )
            self.assertEqual(outside.status_code, 400)
            self.assertEqual(
                outside.json()["detail"],
                "workspace root is outside WORKSPACE_ALLOWED_ROOTS",
            )

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
                usage = result["result"]["metrics"]
                self.assertGreater(usage["input_tokens"], 0)
                self.assertGreater(usage["output_tokens"], 0)
                self.assertEqual(
                    usage["total_tokens"],
                    usage["input_tokens"]
                    + usage["output_tokens"]
                    + usage["thoughts_tokens"],
                )
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
        self.assertNotIn('id="session-token-usage"', response.text)
        self.assertNotIn('id="composer-attachment-btn"', response.text)
        self.assertNotIn('id="composer-provider-input"', response.text)
        self.assertIn('id="thinking-level-input"', response.text)
        self.assertIn('id="workspace-id-input"', response.text)
        self.assertIn('id="open-workspace-picker-btn"', response.text)
        self.assertIn('id="workspace-picker-dialog"', response.text)
        self.assertIn('id="knowledge-base-list"', response.text)
        self.assertIn('id="document-files-input"', response.text)
        self.assertIn("重排数量", response.text)
        self.assertNotIn('id="document-content-input"', response.text)
        self.assertNotIn('id="document-filename-input"', response.text)
        self.assertNotIn('id="repository-id-input"', response.text)
        self.assertEqual(script_response.status_code, 200)
        self.assertIn("thinking_level", script_response.text)
        self.assertIn("submitComposerMessage", script_response.text)
        self.assertIn("runAgentFromComposer", script_response.text)
        self.assertNotIn('switchView("agent");', script_response.text)
        self.assertIn("onSubmitted", script_response.text)
        self.assertIn("renderExecutionProcess", script_response.text)
        self.assertIn("traceToolNames", script_response.text)
        self.assertIn("renderResponseMetrics", script_response.text)
        self.assertIn("createAgentProgressPresenter", script_response.text)
        self.assertIn("await onProgress", script_response.text)
        self.assertIn("workspace_id", script_response.text)
        self.assertIn("browseWorkspaceDirectories", script_response.text)
        self.assertIn("/workspace-directories", script_response.text)
        self.assertIn("createKnowledgeBase", script_response.text)
        self.assertNotIn("repository_id", script_response.text)
        self.assertIn("prefers-reduced-motion", stylesheet_response.text)
        self.assertIn(".execution-process", stylesheet_response.text)
        self.assertIn(".response-metrics", stylesheet_response.text)
        self.assertIn("width: fit-content", stylesheet_response.text)

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
            usage = client.get(
                f"/api/v1/sessions/{session_id}/token-usage"
            ).json()
            self.assertEqual(usage["session_id"], session_id)
            self.assertGreater(usage["input_tokens"], 0)
            self.assertGreater(usage["output_tokens"], 0)
            self.assertEqual(
                usage["total_tokens"],
                usage["input_tokens"] + usage["output_tokens"],
            )
            self.assertEqual(len(usage["records"]), 1)

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
            created = client.post(
                "/api/v1/knowledge-bases",
                json={
                    "id": "docs",
                    "name": "Documentation",
                    "description": "Falcon mode and offline testing guides.",
                    "tags": ["falcon", "testing"],
                },
            )
            self.assertEqual(created.status_code, 201)
            ingested = upload_document(
                client,
                "docs",
                "guide.md",
                "Falcon mode enables deterministic offline testing.",
            )
            self.assertEqual(ingested.status_code, 201)
            self.assertEqual(ingested.json()["index_status"], "active")
            index_job_id = ingested.json()["index_job_id"]
            self.assertTrue(index_job_id.startswith("idx_"))
            jobs = client.get(
                "/api/v1/knowledge-bases/docs/index-jobs"
            ).json()["index_jobs"]
            self.assertEqual([job["status"] for job in jobs], ["active"])
            loaded_job = client.get(
                f"/api/v1/knowledge-bases/docs/index-jobs/{index_job_id}"
            )
            self.assertEqual(loaded_job.status_code, 200)
            self.assertEqual(loaded_job.json()["chunk_count"], 1)
            search = client.post(
                "/api/v1/knowledge-bases/docs/search",
                json={"query": "Falcon deterministic", "limit": 3},
            )
            self.assertEqual(search.status_code, 200)
            self.assertGreaterEqual(len(search.json()["results"]), 1)
            self.assertIsNotNone(search.json()["results"][0]["fusion_score"])
            answer = client.post(
                "/api/v1/knowledge-bases/docs/ask",
                json={"question": "What enables offline testing?", "limit": 3},
            )
            self.assertEqual(answer.status_code, 200)
            self.assertGreaterEqual(len(answer.json()["citations"]), 1)

    def test_knowledge_base_catalog_crud_and_cascade_delete(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            missing_ingest = upload_document(client, "missing", "missing.md", "missing")
            self.assertEqual(missing_ingest.status_code, 404)

            created = client.post(
                "/api/v1/knowledge-bases",
                json={
                    "id": "product_docs",
                    "name": "Product Docs",
                    "description": "Product manuals and API policies.",
                    "tags": ["product", "manual", "product"],
                },
            )
            self.assertEqual(created.status_code, 201)
            self.assertEqual(created.json()["tags"], ["product", "manual"])
            duplicate = client.post(
                "/api/v1/knowledge-bases",
                json={
                    "id": "product_docs",
                    "name": "Duplicate",
                    "description": "",
                    "tags": [],
                },
            )
            self.assertEqual(duplicate.status_code, 409)

            ingested = upload_document(
                client,
                "product_docs",
                "manual.md",
                "Falcon mode is enabled from the product settings.",
            )
            self.assertEqual(ingested.status_code, 201)
            loaded = client.get("/api/v1/knowledge-bases/product_docs")
            self.assertEqual(loaded.json()["document_count"], 1)
            updated = client.put(
                "/api/v1/knowledge-bases/product_docs",
                json={
                    "name": "Product Knowledge",
                    "description": "Updated product reference.",
                    "tags": ["product", "reference"],
                },
            )
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(updated.json()["name"], "Product Knowledge")
            listed = client.get("/api/v1/knowledge-bases").json()["knowledge_bases"]
            self.assertEqual([item["id"] for item in listed], ["product_docs"])

            deleted = client.delete("/api/v1/knowledge-bases/product_docs")
            self.assertEqual(deleted.status_code, 204)
            self.assertEqual(
                client.get("/api/v1/knowledge-bases/product_docs").status_code,
                404,
            )
            self.assertEqual(
                client.post(
                    "/api/v1/knowledge-bases/product_docs/search",
                    json={"query": "Falcon"},
                ).status_code,
                404,
            )

    def test_document_upload_rejects_empty_invalid_and_oversized_files(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            client.post(
                "/api/v1/knowledge-bases",
                json={
                    "id": "uploads",
                    "name": "Uploads",
                    "description": "",
                    "tags": [],
                },
            ).raise_for_status()

            empty = upload_document(client, "uploads", "empty.md", b"")
            invalid_utf8 = upload_document(
                client,
                "uploads",
                "invalid.md",
                b"\xff\xfe",
            )
            with patch(
                "ai_agent_platform.api.routes.knowledge_bases.MAX_DOCUMENT_BYTES",
                4,
            ):
                oversized = upload_document(
                    client,
                    "uploads",
                    "large.md",
                    b"12345",
                )

            self.assertEqual(empty.status_code, 400)
            self.assertIn("document text is empty", empty.json()["detail"])
            self.assertEqual(invalid_utf8.status_code, 400)
            self.assertIn("UTF-8", invalid_utf8.json()["detail"])
            self.assertEqual(oversized.status_code, 413)
            self.assertIn("20 MiB", oversized.json()["detail"])

    def test_agent_automatically_routes_to_rag_and_hybrid_context(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app.py").write_text(
                "FALCON_ENABLED = False\n",
                encoding="utf-8",
            )
            with self._client(root) as client:
                session_id = client.post(
                    "/api/v1/sessions",
                    json={"user_id": "routing-test"},
                ).json()["id"]
                client.put(
                    "/api/v1/workspaces/project",
                    json={"root_path": str(root)},
                ).raise_for_status()
                client.post(
                    "/api/v1/knowledge-bases",
                    json={
                        "id": "falcon_docs",
                        "name": "Falcon Guide",
                        "description": "Falcon mode product policy and setup manual.",
                        "tags": ["Falcon", "manual", "policy"],
                    },
                ).raise_for_status()
                upload_document(
                    client,
                    "falcon_docs",
                    "falcon.md",
                    "Falcon mode enables deterministic offline testing.",
                ).raise_for_status()

                rag_run = client.post(
                    "/api/v1/agent/runs",
                    json={
                        "conversation_id": session_id,
                        "workspace_id": "project",
                        "message": "根据 Falcon 知识库文档说明它的用途",
                    },
                )
                rag_result = wait_for_run(client, rag_run.json()["run_id"])["result"]
                self.assertEqual(rag_result["context_route"], "rag")
                self.assertEqual(
                    rag_result["selected_knowledge_base_ids"],
                    ["falcon_docs"],
                )
                self.assertTrue(
                    any(
                        item["kind"] == "knowledge_chunk"
                        and item["knowledge_base_id"] == "falcon_docs"
                        and item["path"].startswith("knowledge://falcon_docs/")
                        for item in rag_result["context_sources"]
                    )
                )

                hybrid_run = client.post(
                    "/api/v1/agent/runs",
                    json={
                        "conversation_id": session_id,
                        "workspace_id": "project",
                        "focus_files": ["app.py"],
                        "message": "根据 Falcon 规范修改 app.py 的实现方案",
                    },
                )
                hybrid_result = wait_for_run(
                    client,
                    hybrid_run.json()["run_id"],
                )["result"]
                self.assertEqual(hybrid_result["context_route"], "hybrid")
                source_kinds = {
                    item["kind"] for item in hybrid_result["context_sources"]
                }
                self.assertIn("knowledge_chunk", source_kinds)
                self.assertIn("file", source_kinds)
                trace_nodes = [item["node"] for item in hybrid_result["trace"]]
                self.assertIn("retrieve_knowledge", trace_nodes)
                self.assertIn("merge_evidence", trace_nodes)

    def test_rag_search_is_scoped_and_rejects_unsupported_types(self) -> None:
        with TemporaryDirectory() as temp_dir, self._client(Path(temp_dir)) as client:
            for knowledge_base_id, name in (
                ("customer_faq", "Customer FAQ"),
                ("hr_policy", "HR Policy"),
            ):
                response = client.post(
                    "/api/v1/knowledge-bases",
                    json={
                        "id": knowledge_base_id,
                        "name": name,
                        "description": name,
                        "tags": [],
                    },
                )
                self.assertEqual(response.status_code, 201)
            upload_document(
                client,
                "customer_faq",
                "refund.md",
                "退款申请需要在订单完成后 7 天内提交。",
            )
            upload_document(
                client,
                "hr_policy",
                "vacation.md",
                "年假需要提前 3 个工作日提交审批。",
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
            unsupported = upload_document(
                client,
                "hr_policy",
                "legacy.doc",
                b"legacy Word binary",
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
