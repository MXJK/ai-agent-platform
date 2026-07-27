import unittest

from ai_agent_platform.integrations.rag import DocumentChunk, QdrantVectorStore


class QdrantVectorStoreTests(unittest.TestCase):
    def test_upserts_and_searches_chunks_by_knowledge_base_id(self) -> None:
        store = QdrantVectorStore(
            url=":memory:",
            api_key=None,
            collection_name="test_repo_chunks",
        )
        chunk = DocumentChunk(
            id="chk_1",
            knowledge_base_id="repo_main",
            document_id="doc_1",
            filename="app.py",
            chunk_index=0,
            text="def hello(): return 'world'",
            start_line=10,
            end_line=12,
            symbols=["hello"],
        )

        store.upsert_chunks([chunk], [[1.0, 0.0, 0.0]])
        results = store.search(
            knowledge_base_id="repo_main",
            query_embedding=[1.0, 0.0, 0.0],
            limit=1,
        )
        scoped_results = store.search(
            knowledge_base_id="other_repo",
            query_embedding=[1.0, 0.0, 0.0],
            limit=1,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].id, "chk_1")
        self.assertEqual(results[0].filename, "app.py")
        self.assertEqual(results[0].text, "def hello(): return 'world'")
        self.assertEqual(results[0].start_line, 10)
        self.assertEqual(results[0].end_line, 12)
        self.assertEqual(results[0].symbols, ["hello"])
        self.assertEqual(scoped_results, [])

        store.delete_knowledge_base(knowledge_base_id="repo_main")
        self.assertEqual(
            store.search(
                knowledge_base_id="repo_main",
                query_embedding=[1.0, 0.0, 0.0],
                limit=1,
            ),
            [],
        )

    def test_replace_document_removes_stale_points_after_upsert(self) -> None:
        store = QdrantVectorStore(
            url=":memory:",
            api_key=None,
            collection_name="replace_document_chunks",
        )
        chunks = [
            DocumentChunk(
                id=f"chk_{index}",
                knowledge_base_id="docs",
                document_id="doc_1",
                filename="guide.md",
                chunk_index=index,
                text=f"chunk {index}",
            )
            for index in range(2)
        ]
        store.upsert_chunks(chunks, [[1.0, 0.0], [1.0, 0.0]])

        store.replace_document(
            document_id="doc_1",
            chunks=[chunks[0]],
            embeddings=[[1.0, 0.0]],
        )

        results = store.search(
            knowledge_base_id="docs",
            query_embedding=[1.0, 0.0],
            limit=10,
        )
        self.assertEqual([item.id for item in results], ["chk_0"])


if __name__ == "__main__":
    unittest.main()
