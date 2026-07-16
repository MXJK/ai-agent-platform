import os
import unittest
from unittest.mock import patch

from ai_agent_platform.core import Settings


class SettingsTests(unittest.TestCase):
    def test_default_embedding_provider_is_local_for_offline_development(self) -> None:
        settings = Settings()

        self.assertEqual(settings.embedding_provider, "local")

    def test_reads_database_and_qdrant_settings_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": (
                    "postgresql://tester:secret@localhost:5432/test_agent_platform"
                ),
                "SESSION_REPOSITORY": "postgres",
                "AGENT_RUN_STORE": "postgres",
                "DOCUMENT_STORE": "postgres",
                "REPOSITORY_INDEX_STORE": "postgres",
                "LANGGRAPH_CHECKPOINTER": "postgres",
                "RAG_VECTOR_STORE": "qdrant",
                "QDRANT_URL": "http://localhost:6333",
                "QDRANT_API_KEY": "qdrant-secret",
                "QDRANT_COLLECTION_NAME": "test_repo_chunks",
                "MCP_ENABLED": "true",
                "MCP_CONFIG_PATH": "mcp.json",
                "MCP_REQUEST_TIMEOUT_SECONDS": "3.5",
            },
        ):
            settings = Settings.from_env()

        self.assertEqual(
            settings.database_url,
            "postgresql://tester:secret@localhost:5432/test_agent_platform",
        )
        self.assertEqual(settings.session_repository, "postgres")
        self.assertEqual(settings.agent_run_store, "postgres")
        self.assertEqual(settings.document_store, "postgres")
        self.assertEqual(settings.repository_index_store, "postgres")
        self.assertEqual(settings.langgraph_checkpointer, "postgres")
        self.assertEqual(settings.rag_vector_store, "qdrant")
        self.assertEqual(settings.qdrant_url, "http://localhost:6333")
        self.assertEqual(settings.qdrant_api_key, "qdrant-secret")
        self.assertEqual(settings.qdrant_collection_name, "test_repo_chunks")
        self.assertTrue(settings.mcp_enabled)
        self.assertEqual(settings.mcp_config_path, "mcp.json")
        self.assertEqual(settings.mcp_request_timeout_seconds, 3.5)


if __name__ == "__main__":
    unittest.main()
