import os
import unittest
from unittest.mock import patch

from ai_agent_platform.core import Settings


class SettingsTests(unittest.TestCase):
    def test_default_embedding_provider_is_local_for_offline_development(self) -> None:
        settings = Settings()

        self.assertEqual(settings.embedding_provider, "local")
        self.assertEqual(settings.rag_reranker_provider, "sentence_transformer")
        self.assertEqual(
            settings.sentence_transformer_reranker_model,
            "BAAI/bge-reranker-base",
        )
        self.assertEqual(settings.sentence_transformer_reranker_device, "cpu")
        self.assertFalse(settings.rag_rerank_default_enabled)
        self.assertTrue(settings.workspace_allowed_roots)

    def test_blank_allowed_roots_falls_back_to_startup_directory(self) -> None:
        with patch.dict(os.environ, {"WORKSPACE_ALLOWED_ROOTS": ""}):
            settings = Settings.from_env()
        self.assertEqual(len(settings.workspace_allowed_roots), 1)

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
                "WORKSPACE_STORE": "postgres",
                "WORKSPACE_ALLOWED_ROOTS": "/srv/code:/opt/workspaces",
                "LANGGRAPH_CHECKPOINTER": "postgres",
                "RAG_VECTOR_STORE": "qdrant",
                "RAG_LEXICAL_WEIGHT": "0.45",
                "RAG_RERANKER_PROVIDER": "sentence_transformer",
                "SENTENCE_TRANSFORMER_RERANKER_MODEL": "test/bilingual-reranker",
                "SENTENCE_TRANSFORMER_RERANKER_DEVICE": "cuda:0",
                "RAG_RERANK_DEFAULT_ENABLED": "true",
                "BACKGROUND_TASK_WORKERS": "6",
                "BACKGROUND_TASK_QUEUE_CAPACITY": "25",
                "TASK_QUEUE_BACKEND": "celery",
                "REDIS_URL": "redis://127.0.0.1:6379/2",
                "CELERY_RESULT_BACKEND_URL": "redis://127.0.0.1:6379/3",
                "CELERY_VISIBILITY_TIMEOUT_SECONDS": "7200",
                "CELERY_TASK_MAX_RETRIES": "4",
                "CELERY_TASK_RETRY_BACKOFF_SECONDS": "3",
                "CELERY_TASK_RETRY_BACKOFF_MAX_SECONDS": "90",
                "CELERY_TASK_SOFT_TIME_LIMIT_SECONDS": "1200",
                "CELERY_TASK_TIME_LIMIT_SECONDS": "1260",
                "CELERY_RESULT_EXPIRES_SECONDS": "43200",
                "CELERY_WORKER_MAX_TASKS_PER_CHILD": "50",
                "QDRANT_URL": "http://localhost:6333",
                "QDRANT_API_KEY": "qdrant-secret",
                "QDRANT_COLLECTION_NAME": "test_repo_chunks",
                "LOG_LEVEL": "INFO",
                "LOG_FORMAT": "text",
                "LLM_MAX_OUTPUT_TOKENS": "8192",
                "LLM_THINKING_LEVEL": "medium",
                "SSE_HEARTBEAT_SECONDS": "4.5",
                "CONVERSATION_SUMMARY_ENABLED": "true",
                "CONVERSATION_SUMMARY_TRIGGER_MESSAGES": "16",
                "CONVERSATION_SUMMARY_KEEP_RECENT_MESSAGES": "8",
                "CONVERSATION_SUMMARY_MAX_CHARS": "1800",
                "CONVERSATION_SUMMARY_MAX_SOURCE_CHARS": "9000",
                "MCP_ENABLED": "true",
                "MCP_CONFIG_PATH": "mcp.json",
                "MCP_REQUEST_TIMEOUT_SECONDS": "3.5",
                "SANDBOX_MODE": "docker",
                "SANDBOX_DOCKER_IMAGE": "python:3.12-slim",
                "SANDBOX_COMMAND_TIMEOUT_SECONDS": "7.5",
                "SANDBOX_WORKSPACE_PARENT": "/tmp/agent-workspaces",
                "PROJECT_MEMORY_ENABLED": "true",
                "PROJECT_MEMORY_MODE": "review",
                "PROJECT_MEMORY_CANDIDATE_THRESHOLD": "0.65",
                "PROJECT_MEMORY_AUTO_THRESHOLD": "0.9",
                "PROJECT_MEMORY_RECALL_LIMIT": "24",
                "PROJECT_MEMORY_RESULT_LIMIT": "5",
                "PROJECT_MEMORY_MAX_CONTEXT_CHARS": "2500",
                "PROJECT_MEMORY_QDRANT_COLLECTION": "test_project_memories",
                "PROJECT_MEMORY_RELEVANCE_WEIGHT": "0.55",
                "PROJECT_MEMORY_RECENCY_WEIGHT": "0.30",
                "PROJECT_MEMORY_IMPORTANCE_WEIGHT": "0.15",
                "PROJECT_MEMORY_RECENCY_HALF_LIFE_DAYS": "90",
                "AUTH_MODE": "trusted_header",
                "GATEWAY_TRUST_SECRET": "test-trust-secret",
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
        self.assertEqual(settings.workspace_store, "postgres")
        self.assertEqual(
            settings.workspace_allowed_roots,
            ("/srv/code", "/opt/workspaces"),
        )
        self.assertEqual(settings.langgraph_checkpointer, "postgres")
        self.assertEqual(settings.rag_vector_store, "qdrant")
        self.assertEqual(settings.rag_lexical_weight, 0.45)
        self.assertEqual(settings.rag_reranker_provider, "sentence_transformer")
        self.assertEqual(
            settings.sentence_transformer_reranker_model,
            "test/bilingual-reranker",
        )
        self.assertEqual(settings.sentence_transformer_reranker_device, "cuda:0")
        self.assertTrue(settings.rag_rerank_default_enabled)
        self.assertEqual(settings.background_task_workers, 6)
        self.assertEqual(settings.background_task_queue_capacity, 25)
        self.assertEqual(settings.task_queue_backend, "celery")
        self.assertEqual(settings.redis_url, "redis://127.0.0.1:6379/2")
        self.assertEqual(
            settings.celery_result_backend_url,
            "redis://127.0.0.1:6379/3",
        )
        self.assertEqual(settings.celery_visibility_timeout_seconds, 7200)
        self.assertEqual(settings.celery_task_max_retries, 4)
        self.assertEqual(settings.celery_task_retry_backoff_seconds, 3)
        self.assertEqual(settings.celery_task_retry_backoff_max_seconds, 90)
        self.assertEqual(settings.celery_task_soft_time_limit_seconds, 1200)
        self.assertEqual(settings.celery_task_time_limit_seconds, 1260)
        self.assertEqual(settings.celery_result_expires_seconds, 43200)
        self.assertEqual(settings.celery_worker_max_tasks_per_child, 50)
        self.assertEqual(settings.qdrant_url, "http://localhost:6333")
        self.assertEqual(settings.qdrant_api_key, "qdrant-secret")
        self.assertEqual(settings.qdrant_collection_name, "test_repo_chunks")
        self.assertEqual(settings.log_level, "INFO")
        self.assertEqual(settings.log_format, "text")
        self.assertEqual(settings.llm_max_output_tokens, 8192)
        self.assertEqual(settings.llm_thinking_level, "medium")
        self.assertEqual(settings.sse_heartbeat_seconds, 4.5)
        self.assertTrue(settings.conversation_summary_enabled)
        self.assertEqual(settings.conversation_summary_trigger_messages, 16)
        self.assertEqual(settings.conversation_summary_keep_recent_messages, 8)
        self.assertEqual(settings.conversation_summary_max_chars, 1800)
        self.assertEqual(settings.conversation_summary_max_source_chars, 9000)
        self.assertTrue(settings.mcp_enabled)
        self.assertEqual(settings.mcp_config_path, "mcp.json")
        self.assertEqual(settings.mcp_request_timeout_seconds, 3.5)
        self.assertEqual(settings.sandbox_mode, "docker")
        self.assertEqual(settings.sandbox_docker_image, "python:3.12-slim")
        self.assertEqual(settings.sandbox_command_timeout_seconds, 7.5)
        self.assertEqual(settings.sandbox_workspace_parent, "/tmp/agent-workspaces")
        self.assertTrue(settings.project_memory_enabled)
        self.assertEqual(settings.project_memory_mode, "review")
        self.assertEqual(settings.project_memory_candidate_threshold, 0.65)
        self.assertEqual(settings.project_memory_auto_threshold, 0.9)
        self.assertEqual(settings.project_memory_recall_limit, 24)
        self.assertEqual(settings.project_memory_result_limit, 5)
        self.assertEqual(settings.project_memory_max_context_chars, 2500)
        self.assertEqual(
            settings.project_memory_qdrant_collection,
            "test_project_memories",
        )
        self.assertEqual(settings.project_memory_relevance_weight, 0.55)
        self.assertEqual(settings.project_memory_recency_weight, 0.30)
        self.assertEqual(settings.project_memory_importance_weight, 0.15)
        self.assertEqual(settings.project_memory_recency_half_life_days, 90)
        self.assertEqual(settings.auth_mode, "trusted_header")
        self.assertEqual(settings.gateway_trust_secret, "test-trust-secret")

    def test_rejects_overlapping_chunks_larger_than_each_chunk(self) -> None:
        with self.assertRaisesRegex(ValueError, "rag_chunk_overlap"):
            Settings(rag_chunk_size=100, rag_chunk_overlap=100)

    def test_rejects_unknown_storage_backend(self) -> None:
        with self.assertRaisesRegex(ValueError, "session_repository"):
            Settings(session_repository="redis")

    def test_rejects_invalid_rag_lexical_weight(self) -> None:
        with self.assertRaisesRegex(ValueError, "rag_lexical_weight"):
            Settings(rag_lexical_weight=1.1)

    def test_rejects_unknown_reranker_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, "rag_reranker_provider"):
            Settings(rag_reranker_provider="unknown")

    def test_rejects_default_reranking_without_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, "rag_rerank_default_enabled"):
            Settings(
                rag_reranker_provider="none",
                rag_rerank_default_enabled=True,
            )

    def test_rejects_blank_reranker_device(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "sentence_transformer_reranker_device",
        ):
            Settings(sentence_transformer_reranker_device=" ")

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
            workspace_store="postgres",
            langgraph_checkpointer="postgres",
            rag_vector_store="qdrant",
        )

        self.assertEqual(settings.task_queue_backend, "celery")

    def test_rejects_celery_hard_limit_not_above_soft_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "time_limit_seconds"):
            Settings(
                celery_task_soft_time_limit_seconds=60,
                celery_task_time_limit_seconds=60,
            )

    def test_rejects_visibility_timeout_below_task_time_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "visibility_timeout_seconds"):
            Settings(
                celery_task_soft_time_limit_seconds=50,
                celery_task_time_limit_seconds=60,
                celery_visibility_timeout_seconds=60,
            )

    def test_requires_mcp_config_when_mcp_is_enabled(self) -> None:
        with self.assertRaisesRegex(ValueError, "mcp_config_path"):
            Settings(mcp_enabled=True, mcp_config_path=None)

    def test_rejects_invalid_llm_thinking_level(self) -> None:
        with self.assertRaisesRegex(ValueError, "llm_thinking_level"):
            Settings(llm_thinking_level="extreme")

    def test_rejects_invalid_memory_thresholds_and_missing_gateway_secret(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            Settings(
                project_memory_candidate_threshold=0.9,
                project_memory_auto_threshold=0.8,
            )
        with self.assertRaisesRegex(ValueError, "gateway_trust_secret"):
            Settings(auth_mode="trusted_header")
        with self.assertRaisesRegex(ValueError, "weights must sum to 1"):
            Settings(
                project_memory_relevance_weight=0.5,
                project_memory_recency_weight=0.5,
                project_memory_importance_weight=0.5,
            )
        with self.assertRaisesRegex(ValueError, "keep_recent_messages"):
            Settings(
                conversation_summary_trigger_messages=6,
                conversation_summary_keep_recent_messages=6,
            )


if __name__ == "__main__":
    unittest.main()
