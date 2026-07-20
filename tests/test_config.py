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
                "RAG_LEXICAL_WEIGHT": "0.45",
                "BACKGROUND_TASK_WORKERS": "6",
                "BACKGROUND_TASK_QUEUE_CAPACITY": "25",
                "TASK_QUEUE_BACKEND": "celery",
                "REDIS_URL": "redis://127.0.0.1:6379/2",
                "CELERY_VISIBILITY_TIMEOUT_SECONDS": "7200",
                "QDRANT_URL": "http://localhost:6333",
                "QDRANT_API_KEY": "qdrant-secret",
                "QDRANT_COLLECTION_NAME": "test_repo_chunks",
                "LOG_LEVEL": "INFO",
                "LOG_FORMAT": "text",
                "MCP_ENABLED": "true",
                "MCP_CONFIG_PATH": "mcp.json",
                "MCP_REQUEST_TIMEOUT_SECONDS": "3.5",
                "SANDBOX_MODE": "docker",
                "SANDBOX_DOCKER_IMAGE": "python:3.12-slim",
                "SANDBOX_COMMAND_TIMEOUT_SECONDS": "7.5",
                "SANDBOX_WORKSPACE_PARENT": "/tmp/agent-workspaces",
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
        self.assertEqual(settings.rag_lexical_weight, 0.45)
        self.assertEqual(settings.background_task_workers, 6)
        self.assertEqual(settings.background_task_queue_capacity, 25)
        self.assertEqual(settings.task_queue_backend, "celery")
        self.assertEqual(settings.redis_url, "redis://127.0.0.1:6379/2")
        self.assertEqual(settings.celery_visibility_timeout_seconds, 7200)
        self.assertEqual(settings.qdrant_url, "http://localhost:6333")
        self.assertEqual(settings.qdrant_api_key, "qdrant-secret")
        self.assertEqual(settings.qdrant_collection_name, "test_repo_chunks")
        self.assertEqual(settings.log_level, "INFO")
        self.assertEqual(settings.log_format, "text")
        self.assertTrue(settings.mcp_enabled)
        self.assertEqual(settings.mcp_config_path, "mcp.json")
        self.assertEqual(settings.mcp_request_timeout_seconds, 3.5)
        self.assertEqual(settings.sandbox_mode, "docker")
        self.assertEqual(settings.sandbox_docker_image, "python:3.12-slim")
        self.assertEqual(settings.sandbox_command_timeout_seconds, 7.5)
        self.assertEqual(settings.sandbox_workspace_parent, "/tmp/agent-workspaces")

    def test_rejects_overlapping_chunks_larger_than_each_chunk(self) -> None:
        with self.assertRaisesRegex(ValueError, "rag_chunk_overlap"):
            Settings(rag_chunk_size=100, rag_chunk_overlap=100)

    def test_rejects_unknown_storage_backend(self) -> None:
        with self.assertRaisesRegex(ValueError, "session_repository"):
            Settings(session_repository="redis")

    def test_rejects_invalid_rag_lexical_weight(self) -> None:
        with self.assertRaisesRegex(ValueError, "rag_lexical_weight"):
            Settings(rag_lexical_weight=1.1)

    def test_rejects_invalid_background_task_capacity(self) -> None:
        with self.assertRaisesRegex(ValueError, "background_task_queue_capacity"):
            Settings(background_task_queue_capacity=-1)

    def test_celery_requires_shared_worker_storage(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires shared storage"):
            Settings(task_queue_backend="celery")

    def test_celery_accepts_postgres_and_qdrant_shared_storage(self) -> None:
        settings = Settings(
            task_queue_backend="celery",
            session_repository="postgres",
            agent_run_store="postgres",
            document_store="postgres",
            repository_index_store="postgres",
            langgraph_checkpointer="postgres",
            rag_vector_store="qdrant",
        )

        self.assertEqual(settings.task_queue_backend, "celery")

    def test_requires_mcp_config_when_mcp_is_enabled(self) -> None:
        with self.assertRaisesRegex(ValueError, "mcp_config_path"):
            Settings(mcp_enabled=True, mcp_config_path=None)


if __name__ == "__main__":
    unittest.main()
