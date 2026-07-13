import unittest

from fastapi.testclient import TestClient

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.llm import _google_usage
from ai_agent_platform.main import create_app


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

        ask_response = self.client.post(
            "/api/v1/knowledge-bases/customer_faq/ask",
            json={"question": "退款期限是多久？", "recall_limit": 10, "limit": 2},
        )

        self.assertEqual(ask_response.status_code, 200)
        body = ask_response.json()
        self.assertIn("fake model reply to", body["answer"])
        self.assertGreaterEqual(len(body["citations"]), 1)
        self.assertIn("退款", body["citations"][0]["text"])

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
