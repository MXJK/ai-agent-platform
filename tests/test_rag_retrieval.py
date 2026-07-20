import unittest

from ai_agent_platform.integrations.rag import (
    InMemoryVectorStore,
    NoopReranker,
    ParsedDocument,
    RAGService,
    RecursiveCharacterChunker,
    TextDocumentParser,
    evaluate_retrieval,
)


class ConstantEmbeddingProvider:
    def embed_texts(
        self,
        texts: list[str],
        *,
        task_type: str = "document",
    ) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


class RAGRetrievalTests(unittest.TestCase):
    def test_python_ast_keeps_class_methods_in_a_qualified_symbol_block(self) -> None:
        document = ParsedDocument(
            id="doc_1",
            knowledge_base_id="repo",
            filename="services.py",
            text=(
                "@registered\n"
                "class PaymentService:\n"
                "    async def issue_refund(self):\n"
                "        return 'ok'\n\n"
                "def healthcheck():\n"
                "    return True"
            ),
        )

        chunks = RecursiveCharacterChunker(chunk_size=500, chunk_overlap=50).split(
            document
        )

        class_chunk = next(
            chunk for chunk in chunks if "class PaymentService" in chunk.text
        )
        self.assertEqual(class_chunk.start_line, 1)
        self.assertIn("PaymentService", class_chunk.symbols)
        self.assertIn("PaymentService.issue_refund", class_chunk.symbols)
        self.assertFalse(any(chunk.symbols == ["issue_refund"] for chunk in chunks))
        function_chunk = next(chunk for chunk in chunks if "def healthcheck" in chunk.text)
        self.assertEqual(function_chunk.symbols, ["healthcheck"])

    def test_hybrid_retrieval_promotes_exact_symbol_match(self) -> None:
        service = RAGService(
            parser=TextDocumentParser(),
            chunker=RecursiveCharacterChunker(chunk_size=500, chunk_overlap=50),
            embedding_provider=ConstantEmbeddingProvider(),
            vector_store=InMemoryVectorStore(),
            reranker=NoopReranker(),
            default_recall_limit=10,
            max_prompt_chars=2000,
            lexical_weight=0.35,
        )
        service.ingest_document(
            knowledge_base_id="repo",
            filename="payments.py",
            content=(
                "class PaymentService:\n"
                "    def issue_refund(self):\n"
                "        return 'refunded'"
            ),
        )
        service.ingest_document(
            knowledge_base_id="repo",
            filename="shipping.py",
            content=(
                "class ShippingService:\n"
                "    def dispatch_order(self):\n"
                "        return 'sent'"
            ),
        )

        results = service.search(
            knowledge_base_id="repo",
            query="PaymentService issue_refund",
            limit=2,
        )

        self.assertEqual(results[0].filename, "payments.py")
        self.assertGreater(results[0].lexical_score or 0.0, 0.0)
        self.assertIsNotNone(results[0].hybrid_score)
        self.assertEqual(results[0].score, results[0].hybrid_score)

    def test_retrieval_metrics_compute_recall_and_mrr(self) -> None:
        metrics = evaluate_retrieval(
            rankings=[["b.py", "a.py"], ["c.py", "d.py"]],
            relevant_documents=[{"a.py"}, {"missing.py"}],
            k=2,
        )

        self.assertEqual(metrics.evaluated_cases, 2)
        self.assertEqual(metrics.recall_at_k, 0.5)
        self.assertEqual(metrics.mean_reciprocal_rank, 0.25)


if __name__ == "__main__":
    unittest.main()
