import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_agent_platform.core import Settings, validate_bind_host


class SettingsTests(unittest.TestCase):
    def test_default_embedding_provider_is_local_for_offline_development(self) -> None:
        settings = Settings()

        self.assertNotIn("@", settings.database_url)
        self.assertEqual(settings.embedding_provider, "local")
        self.assertEqual(settings.rag_reranker_provider, "sentence_transformer")
        self.assertEqual(
            settings.sentence_transformer_reranker_model,
            "BAAI/bge-reranker-base",
        )
        self.assertEqual(settings.sentence_transformer_reranker_device, "cpu")
        self.assertFalse(settings.rag_rerank_default_enabled)
        self.assertTrue(settings.workspace_allowed_roots)
        self.assertEqual(
            settings.workspace_allowed_roots,
            (str(Path.home().resolve()),),
        )
        self.assertEqual(settings.agent_soft_tool_rounds, 12)
        self.assertEqual(settings.agent_max_tool_rounds, 24)
        self.assertEqual(settings.agent_soft_tool_calls, 36)
        self.assertEqual(settings.agent_max_tool_calls, 72)
        self.assertEqual(settings.agent_plan_max_output_tokens, 4096)
        self.assertEqual(settings.agent_mutation_max_output_tokens, 16384)
        self.assertEqual(settings.agent_final_max_output_tokens, 4096)
        self.assertEqual(settings.agent_tool_result_max_tokens, 2000)
        self.assertEqual(settings.agent_tool_result_keep_recent, 6)
        self.assertEqual(settings.agent_native_max_compactions, 3)
        self.assertEqual(settings.llm_context_evidence_ratio, 0.25)
        self.assertEqual(settings.llm_context_history_ratio, 0.15)
        self.assertFalse(hasattr(settings, "agent_native_context_token_ratio"))
        self.assertEqual(settings.agent_approval_policy, "on_request")
        self.assertEqual(settings.agent_workspace_default_mode, "patch_only")
        self.assertEqual(settings.agent_workspace_allowed_modes, ("patch_only",))
        self.assertEqual(settings.native_directory_picker_mode, "loopback")
        self.assertEqual(settings.model_probe_interval_seconds, 0)

    def test_model_probe_interval_is_opt_in_and_bounded(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MODEL_PROBE_INTERVAL_SECONDS": "900",
                "LLM_MODEL_CATALOG_JSON": "",
            },
            clear=True,
        ), patch(
            "ai_agent_platform.core.config_resolver._read_dotenv",
            return_value={},
        ):
            settings = Settings.from_env()

        self.assertEqual(settings.model_probe_interval_seconds, 900)
        self.assertEqual(Settings(model_probe_interval_seconds=60).model_probe_interval_seconds, 60)
        with self.assertRaisesRegex(ValueError, "0 or at least 60"):
            Settings(model_probe_interval_seconds=30)
        with self.assertRaisesRegex(ValueError, "greater than or equal to 0"):
            Settings(model_probe_interval_seconds=-1)

    def test_reads_unified_context_share_ratios_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LLM_CONTEXT_EVIDENCE_RATIO": "0.2",
                "LLM_CONTEXT_HISTORY_RATIO": "0.1",
                # The removed ratio is intentionally ignored during migration.
                "AGENT_NATIVE_CONTEXT_TOKEN_RATIO": "0.9",
                "LLM_MODEL_CATALOG_JSON": "",
            },
            clear=True,
        ), patch(
            "ai_agent_platform.core.config_resolver._read_dotenv",
            return_value={},
        ):
            settings = Settings.from_env()

        self.assertEqual(settings.llm_context_evidence_ratio, 0.2)
        self.assertEqual(settings.llm_context_history_ratio, 0.1)
        self.assertFalse(hasattr(settings, "agent_native_context_token_ratio"))

    def test_workspace_mode_environment_precedence_and_legacy_mapping(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CHANGE_SET_APPLY_MODE": "direct",
                "LLM_MODEL_CATALOG_JSON": "",
            },
            clear=True,
        ), patch(
            "ai_agent_platform.core.config_resolver._read_dotenv",
            return_value={},
        ):
            legacy = Settings.from_env()
        self.assertEqual(legacy.agent_workspace_default_mode, "direct")
        self.assertEqual(
            legacy.agent_workspace_allowed_modes,
            ("patch_only", "direct"),
        )

        with patch.dict(
            os.environ,
            {
                "CHANGE_SET_APPLY_MODE": "worktree",
                "AGENT_WORKSPACE_DEFAULT_MODE": "direct",
                "AGENT_WORKSPACE_ALLOWED_MODES": "patch_only,direct",
                "LLM_MODEL_CATALOG_JSON": "",
            },
            clear=True,
        ), patch(
            "ai_agent_platform.core.config_resolver._read_dotenv",
            return_value={},
        ):
            explicit = Settings.from_env()
        self.assertEqual(explicit.agent_workspace_default_mode, "direct")
        self.assertEqual(
            explicit.agent_workspace_allowed_modes,
            ("patch_only", "direct"),
        )

    def test_blank_allowed_roots_falls_back_to_user_home_directory(self) -> None:
        with patch.dict(
            os.environ,
            {
                "WORKSPACE_ALLOWED_ROOTS": "",
                "LLM_MODEL_CATALOG_JSON": "",
            },
        ), patch(
            "ai_agent_platform.core.config_resolver._read_dotenv",
            return_value={},
        ):
            settings = Settings.from_env()
        self.assertEqual(len(settings.workspace_allowed_roots), 1)
        self.assertEqual(
            settings.workspace_allowed_roots,
            (str(Path.home().resolve()),),
        )
        self.assertIsNone(settings.llm_model_catalog_json)

    def test_reads_database_and_qdrant_settings_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": (
                    "postgresql://tester:secret@localhost:5432/test_agent_platform"
                ),
                "SESSION_REPOSITORY": "postgres",
                "AGENT_RUN_STORE": "postgres",
                "CHANGE_SET_STORE": "postgres",
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
                "LLM_PROVIDER": "fake",
                "LLM_MODEL": "fake-chat-1",
                "EMBEDDING_PROVIDER": "local",
                "EMBEDDING_MODEL": "gemini-embedding-001",
                "LLM_MAX_OUTPUT_TOKENS": "8192",
                "AGENT_PLAN_MAX_OUTPUT_TOKENS": "3000",
                "AGENT_MUTATION_MAX_OUTPUT_TOKENS": "12000",
                "AGENT_FINAL_MAX_OUTPUT_TOKENS": "2500",
                "AGENT_TOOL_RESULT_MAX_TOKENS": "1500",
                "AGENT_TOOL_RESULT_KEEP_RECENT": "4",
                "AGENT_NATIVE_MAX_COMPACTIONS": "2",
                "LLM_THINKING_LEVEL": "medium",
                "LLM_MODEL_CATALOG_JSON": (
                    '[{"provider":"fake","model":"fast-fake",'
                    '"context_window_tokens":64000}]'
                ),
                "LLM_MODEL_CONTEXT_WINDOW_TOKENS": "96000",
                "LLM_ROUTING_POLICY": "cost",
                "LLM_MAX_RETRIES": "5",
                "LLM_RETRY_POLICY_JSON": (
                    '{"rate_limit":0,"llm_timeout":3,"default":1}'
                ),
                "LLM_RETRY_BASE_DELAY_SECONDS": "0.25",
                "LLM_RETRY_BACKOFF_MAX_SECONDS": "3.0",
                "LLM_RETRY_AFTER_MAX_SECONDS": "30.0",
                "LLM_RETRY_JITTER_SECONDS": "0.05",
                "LLM_CIRCUIT_FAILURE_THRESHOLD": "4",
                "LLM_CIRCUIT_RECOVERY_TIMEOUT_SECONDS": "45",
                "LLM_CIRCUIT_ERROR_WINDOW_SIZE": "12",
                "LLM_CIRCUIT_ERROR_RATE_MIN_REQUESTS": "6",
                "LLM_CIRCUIT_ERROR_RATE_THRESHOLD": "0.6",
                "SESSION_TOKEN_BUDGET": "50000",
                "WORKSPACE_TOKEN_BUDGET": "250000",
                "TOKEN_BUDGET_ACTION": "downgrade",
                "TOKEN_BUDGET_FALLBACK_PROVIDER": "fake",
                "TOKEN_BUDGET_FALLBACK_MODEL": "fake-cheap",
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
                "SANDBOX_COMMAND_OUTPUT_MAX_CHARS": "9000",
                "SANDBOX_WORKSPACE_PARENT": "/tmp/agent-workspaces",
                "SANDBOX_WORKSPACE_TTL_SECONDS": "600",
                "SANDBOX_ALLOWED_COMMANDS": "python,pytest,node",
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
                "LIVE_WORKSPACE_WRITES_ENABLED": "true",
                "CHANGE_SET_APPLY_MODE": "direct",
                "CHANGE_SET_MAX_FILES": "40",
                "CHANGE_SET_MAX_PATCH_CHARS": "250000",
                "CHANGE_SET_WORKTREE_PARENT": "/tmp/change-worktrees",
                "CHANGE_SET_BRANCH_PREFIX": "agent/",
                "GATEWAY_TRUST_SECRET": "test-trust-secret",
            },
        ), patch(
            "ai_agent_platform.core.config_resolver._read_dotenv",
            return_value={},
        ):
            settings = Settings.from_env()

        self.assertEqual(
            settings.database_url,
            "postgresql://tester:secret@localhost:5432/test_agent_platform",
        )
        self.assertEqual(settings.session_repository, "postgres")
        self.assertEqual(settings.agent_run_store, "postgres")
        self.assertEqual(settings.change_set_store, "postgres")
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
        self.assertEqual(settings.agent_plan_max_output_tokens, 3000)
        self.assertEqual(settings.agent_mutation_max_output_tokens, 12000)
        self.assertEqual(settings.agent_final_max_output_tokens, 2500)
        self.assertEqual(settings.agent_tool_result_max_tokens, 1500)
        self.assertEqual(settings.agent_tool_result_keep_recent, 4)
        self.assertEqual(settings.agent_native_max_compactions, 2)
        self.assertEqual(settings.llm_thinking_level, "medium")
        self.assertIn("fast-fake", settings.llm_model_catalog_json or "")
        self.assertEqual(settings.llm_model_context_window_tokens, 96000)
        self.assertEqual(settings.llm_routing_policy, "cost")
        self.assertEqual(settings.llm_max_retries, 5)
        self.assertEqual(
            settings.llm_retry_policy_json,
            '{"rate_limit":0,"llm_timeout":3,"default":1}',
        )
        self.assertEqual(settings.llm_retry_base_delay_seconds, 0.25)
        self.assertEqual(settings.llm_retry_backoff_max_seconds, 3.0)
        self.assertEqual(settings.llm_retry_after_max_seconds, 30.0)
        self.assertEqual(settings.llm_retry_jitter_seconds, 0.05)
        self.assertEqual(settings.llm_circuit_failure_threshold, 4)
        self.assertEqual(settings.llm_circuit_recovery_timeout_seconds, 45)
        self.assertEqual(settings.llm_circuit_error_window_size, 12)
        self.assertEqual(settings.llm_circuit_error_rate_min_requests, 6)
        self.assertEqual(settings.llm_circuit_error_rate_threshold, 0.6)
        self.assertEqual(settings.session_token_budget, 50000)
        self.assertEqual(settings.workspace_token_budget, 250000)
        self.assertEqual(settings.token_budget_action, "downgrade")
        self.assertEqual(settings.token_budget_fallback_provider, "fake")
        self.assertEqual(settings.token_budget_fallback_model, "fake-cheap")
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
        self.assertEqual(settings.sandbox_command_output_max_chars, 9000)
        self.assertEqual(settings.sandbox_workspace_parent, "/tmp/agent-workspaces")
        self.assertEqual(settings.sandbox_workspace_ttl_seconds, 600)
        self.assertEqual(
            settings.sandbox_allowed_commands,
            ("python", "pytest", "node"),
        )
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
        self.assertTrue(settings.live_workspace_writes_enabled)
        self.assertEqual(settings.change_set_apply_mode, "direct")
        self.assertEqual(settings.change_set_max_files, 40)
        self.assertEqual(settings.change_set_max_patch_chars, 250000)
        self.assertEqual(
            settings.change_set_worktree_parent,
            "/tmp/change-worktrees",
        )
        self.assertEqual(settings.change_set_branch_prefix, "agent/")
        self.assertEqual(settings.gateway_trust_secret, "test-trust-secret")

    def test_rejects_overlapping_chunks_larger_than_each_chunk(self) -> None:
        with self.assertRaisesRegex(ValueError, "rag_chunk_overlap"):
            Settings(rag_chunk_size=100, rag_chunk_overlap=100)

    def test_rejects_unknown_storage_backend(self) -> None:
        with self.assertRaisesRegex(ValueError, "session_repository"):
            Settings(session_repository="redis")

    def test_rejects_non_atomic_session_and_run_store_pair(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "session_repository and agent_run_store must use the same backend",
        ):
            Settings(
                session_repository="memory",
                agent_run_store="postgres",
            )

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

    def test_rejects_unknown_runtime_profile(self) -> None:
        with self.assertRaisesRegex(ValueError, "runtime_profile"):
            Settings(runtime_profile="staging")

    def test_named_runtime_profile_rejects_incompatible_backends(self) -> None:
        with self.assertRaisesRegex(ValueError, "runtime_profile=local"):
            Settings(runtime_profile="local")

    def test_celery_accepts_postgres_and_qdrant_shared_storage(self) -> None:
        settings = Settings(
            task_queue_backend="celery",
            session_repository="postgres",
            agent_run_store="postgres",
            change_set_store="postgres",
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

    def test_rejects_invalid_model_routing_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "llm_routing_policy"):
            Settings(llm_routing_policy="random")
        with self.assertRaisesRegex(ValueError, "valid JSON"):
            Settings(llm_model_catalog_json="not-json")
        with self.assertRaisesRegex(ValueError, "non-empty JSON array"):
            Settings(llm_model_catalog_json="[]")
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            Settings(
                llm_circuit_error_window_size=4,
                llm_circuit_error_rate_min_requests=5,
            )
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            Settings(llm_circuit_error_rate_threshold=1.1)
        with self.assertRaisesRegex(ValueError, "valid JSON"):
            Settings(llm_retry_policy_json="not-json")
        with self.assertRaisesRegex(ValueError, "JSON object"):
            Settings(llm_retry_policy_json="[]")
        with self.assertRaisesRegex(ValueError, "unsupported keys"):
            Settings(llm_retry_policy_json='{"typo": 1}')
        for value in ('{"rate_limit": -1}', '{"rate_limit": true}'):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError,
                "non-negative integers",
            ):
                Settings(llm_retry_policy_json=value)
        with self.assertRaisesRegex(ValueError, "llm_retry_jitter_seconds"):
            Settings(llm_retry_jitter_seconds=-0.1)
        for field_name, value in (
            ("llm_retry_base_delay_seconds", float("nan")),
            ("llm_retry_after_max_seconds", float("inf")),
        ):
            with self.subTest(field_name=field_name), self.assertRaisesRegex(
                ValueError,
                f"{field_name} must be finite",
            ):
                Settings(**{field_name: value})

        with self.assertRaisesRegex(
            ValueError,
            "llm_retry_backoff_max_seconds",
        ):
            Settings(
                llm_retry_base_delay_seconds=1.0,
                llm_retry_backoff_max_seconds=0.5,
            )
        with self.assertRaisesRegex(ValueError, "llm_context_evidence_ratio"):
            Settings(llm_context_evidence_ratio=-0.1)
        with self.assertRaisesRegex(ValueError, "leave room"):
            Settings(
                llm_context_evidence_ratio=0.6,
                llm_context_history_ratio=0.4,
            )

    def test_accepts_granular_llm_network_retry_policy_keys(self) -> None:
        keys = (
            "llm_close_error",
            "llm_connect_timeout",
            "llm_connection_error",
            "llm_decoding_error",
            "llm_dns_error",
            "llm_local_protocol_error",
            "llm_pool_timeout",
            "llm_proxy_error",
            "llm_read_error",
            "llm_read_timeout",
            "llm_remote_protocol_error",
            "llm_tls_certificate_error",
            "llm_tls_error",
            "llm_write_error",
            "llm_write_timeout",
        )

        settings = Settings(
            llm_retry_policy_json=json.dumps({key: 1 for key in keys})
        )

        self.assertIsNotNone(settings.llm_retry_policy_json)

    def test_downgrade_budget_requires_a_fallback_model(self) -> None:
        with self.assertRaisesRegex(ValueError, "require a fallback"):
            Settings(
                session_token_budget=100,
                token_budget_action="downgrade",
            )

    def test_rejects_invalid_memory_thresholds_and_missing_gateway_secret(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            Settings(
                project_memory_candidate_threshold=0.9,
                project_memory_auto_threshold=0.8,
            )
        with self.assertRaisesRegex(ValueError, "gateway_trust_secret"):
            Settings(auth_mode="trusted_header")
        with self.assertRaisesRegex(ValueError, "native_directory_picker_mode"):
            Settings(native_directory_picker_mode="remote")
        with self.assertRaisesRegex(ValueError, "auth_mode=trusted_header"):
            Settings(native_directory_picker_mode="trusted_local_gateway")
        settings = Settings(auth_mode="single_user", single_user_id="owner")
        self.assertEqual(settings.single_user_id, "owner")
        with self.assertRaisesRegex(ValueError, "single_user_id"):
            Settings(auth_mode="single_user", single_user_id="   ")
        with self.assertRaisesRegex(ValueError, "single_user_id"):
            Settings(auth_mode="single_user", single_user_id="x" * 257)
        with self.assertRaisesRegex(ValueError, "weights must sum to 1"):
            Settings(
                project_memory_relevance_weight=0.5,
                project_memory_recency_weight=0.5,
                project_memory_importance_weight=0.5,
            )

    def test_disabled_auth_requires_loopback_bind_host(self) -> None:
        for host in ("localhost", "127.0.0.1", "127.12.0.4", "::1", "[::1]"):
            validate_bind_host(host=host, auth_mode="disabled")

        for host in ("0.0.0.0", "::", "192.168.1.10", "devbox.local"):
            with self.assertRaisesRegex(ValueError, "loopback"):
                validate_bind_host(host=host, auth_mode="disabled")

        validate_bind_host(host="0.0.0.0", auth_mode="trusted_header")
        validate_bind_host(host="0.0.0.0", auth_mode="single_user")

    def test_rejects_invalid_sandbox_command_allowlist(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            Settings(sandbox_allowed_commands=())
        with self.assertRaisesRegex(ValueError, "basenames"):
            Settings(sandbox_allowed_commands=("/usr/bin/python",))
        with self.assertRaisesRegex(ValueError, "keep_recent_messages"):
            Settings(
                conversation_summary_trigger_messages=6,
                conversation_summary_keep_recent_messages=6,
            )

    def test_rejects_invalid_agent_runtime_budgets_and_approval_policy(self) -> None:
        with self.assertRaisesRegex(ValueError, "agent_soft_tool_rounds"):
            Settings(agent_soft_tool_rounds=25, agent_max_tool_rounds=24)
        with self.assertRaisesRegex(ValueError, "agent_soft_tool_calls"):
            Settings(agent_soft_tool_calls=73, agent_max_tool_calls=72)
        with self.assertRaisesRegex(ValueError, "agent_tool_result_max_tokens"):
            Settings(agent_tool_result_max_tokens=63)
        with self.assertRaisesRegex(ValueError, "agent_tool_result_keep_recent"):
            Settings(agent_tool_result_keep_recent=0)
        with self.assertRaisesRegex(ValueError, "agent_native_max_compactions"):
            Settings(agent_native_max_compactions=0)
        with self.assertRaisesRegex(ValueError, "agent_approval_policy"):
            Settings(agent_approval_policy="unsafe")

    def test_rejects_unsafe_change_set_write_configuration(self) -> None:
        with self.assertRaisesRegex(ValueError, "change_set_apply_mode"):
            Settings(change_set_apply_mode="overwrite")
        with self.assertRaisesRegex(ValueError, "requires auth_mode"):
            Settings(live_workspace_writes_enabled=True)
        with self.assertRaisesRegex(ValueError, "change_set_max_files"):
            Settings(change_set_max_files=0)
        for prefix in ("../escape/", "/absolute/", "bad branch/", "refs//bad/"):
            with self.subTest(prefix=prefix), self.assertRaisesRegex(
                ValueError,
                "change_set_branch_prefix",
            ):
                Settings(change_set_branch_prefix=prefix)


if __name__ == "__main__":
    unittest.main()
