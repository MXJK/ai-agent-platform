from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re


RUNTIME_PROFILE_DEFAULTS: dict[str, dict[str, object]] = {
    "custom": {},
    "local": {
        "session_repository": "sqlite",
        "agent_run_store": "sqlite",
        "change_set_store": "memory",
        "document_store": "memory",
        "eval_store": "memory",
        "workspace_store": "sqlite",
        "langgraph_checkpointer": "memory",
        "model_registry_store": "memory",
        "rag_vector_store": "memory",
        "project_memory_store": "sqlite",
        "project_memory_vector_store": "sqlite",
        "project_memory_enabled": True,
        "project_memory_mode": "auto",
        "user_memory_enabled": True,
        "user_memory_mode": "auto",
        "rag_reranker_provider": "none",
        "task_queue_backend": "in_process",
    },
    "production": {
        "session_repository": "postgres",
        "agent_run_store": "postgres",
        "change_set_store": "postgres",
        "document_store": "postgres",
        "eval_store": "postgres",
        "workspace_store": "postgres",
        "langgraph_checkpointer": "postgres",
        "model_registry_store": "postgres",
        "rag_vector_store": "qdrant",
        "project_memory_store": "postgres",
        "project_memory_vector_store": "qdrant",
        "project_memory_enabled": False,
        "project_memory_mode": "off",
        "user_memory_enabled": False,
        "user_memory_mode": "off",
        "task_queue_backend": "celery",
    },
}

LLM_RETRY_POLICY_KEYS = frozenset(
    {
        "default",
        "invalid_tool_arguments",
        "llm_close_error",
        "llm_connect_timeout",
        "llm_connection_error",
        "llm_decoding_error",
        "llm_dns_error",
        "llm_local_protocol_error",
        "llm_pool_timeout",
        "llm_provider_error",
        "llm_proxy_error",
        "llm_read_error",
        "llm_read_timeout",
        "llm_remote_protocol_error",
        "llm_server_error",
        "llm_tls_certificate_error",
        "llm_tls_error",
        "llm_timeout",
        "llm_transport_error",
        "llm_write_error",
        "llm_write_timeout",
        "rate_limit",
        "token_count_failed",
        "tool_arguments_truncated",
        "tool_output_truncated",
    }
)

_RUNTIME_PROFILE_BACKEND_REQUIREMENTS = {
    profile: {
        name: value
        for name, value in defaults.items()
        if name
        in {
            "session_repository",
            "agent_run_store",
            "change_set_store",
            "document_store",
            "eval_store",
            "workspace_store",
            "langgraph_checkpointer",
            "model_registry_store",
            "rag_vector_store",
            "project_memory_store",
            "project_memory_vector_store",
            "task_queue_backend",
        }
    }
    for profile, defaults in RUNTIME_PROFILE_DEFAULTS.items()
}


def runtime_profile_defaults(profile: str) -> dict[str, object]:
    """Return a copy of the defaults owned by one deployment profile."""

    try:
        return dict(RUNTIME_PROFILE_DEFAULTS[profile])
    except KeyError as exc:
        supported = ", ".join(sorted(RUNTIME_PROFILE_DEFAULTS))
        raise ValueError(
            f"runtime_profile must be one of: {supported}"
        ) from exc


@dataclass(frozen=True)
class Settings:
    app_name: str = "ai-agent-platform"
    api_prefix: str = "/api/v1"
    log_level: str = "WARNING"
    log_format: str = "json"
    llm_provider: str = "fake"
    llm_model: str = "demo-stream-model"
    database_url: str = field(
        default="postgresql://localhost:5432/ai_agent_platform",
        repr=False,
    )
    runtime_profile: str = "custom"
    local_state_path: str = str(
        Path.home() / ".ai-agent-platform" / "state.sqlite3"
    )
    session_repository: str = "memory"
    agent_run_store: str = "memory"
    change_set_store: str = "memory"
    document_store: str = "memory"
    eval_store: str = "memory"
    eval_fault_injection_enabled: bool = False
    eval_workspace_root: str = ""
    workspace_store: str = "memory"
    workspace_allowed_roots: tuple[str, ...] = field(
        default_factory=lambda: (str(Path.home().resolve()),)
    )
    langgraph_checkpointer: str = "memory"
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2
    llm_retry_policy_json: str | None = None
    llm_retry_base_delay_seconds: float = 0.2
    llm_retry_backoff_max_seconds: float = 2.0
    llm_retry_after_max_seconds: float = 60.0
    llm_retry_jitter_seconds: float = 0.1
    llm_max_input_chars: int = 8000
    llm_max_context_messages: int = 12
    llm_max_context_messages_ceiling: int = 48
    llm_context_input_token_ratio: float = 0.6
    llm_context_evidence_ratio: float = 0.25
    llm_context_history_ratio: float = 0.15
    llm_max_output_tokens: int = 4096
    llm_thinking_level: str = "low"
    llm_model_catalog_json: str | None = None
    llm_model_context_window_tokens: int = 128000
    llm_routing_policy: str = "smart"
    llm_circuit_failure_threshold: int = 3
    llm_circuit_recovery_timeout_seconds: float = 30.0
    llm_circuit_error_window_size: int = 20
    llm_circuit_error_rate_min_requests: int = 5
    llm_circuit_error_rate_threshold: float = 0.5
    model_registry_store: str = "memory"
    model_secret_backend: str = "keyring"
    model_probe_interval_seconds: float = 0.0
    session_token_budget: int = 0
    workspace_token_budget: int = 0
    token_budget_action: str = "reject"
    token_budget_fallback_provider: str | None = None
    token_budget_fallback_model: str | None = None
    conversation_summary_enabled: bool = True
    conversation_summary_trigger_messages: int = 12
    conversation_summary_keep_recent_messages: int = 6
    conversation_summary_max_chars: int = 4000
    conversation_summary_max_source_chars: int = 12000
    conversation_summary_sync_on_overflow: bool = True
    sse_heartbeat_seconds: float = 10.0
    rag_vector_store: str = "memory"
    chroma_persist_directory: str = ".chroma"
    chroma_collection_name: str = "rag_chunks"
    qdrant_url: str = field(default="http://localhost:6333", repr=False)
    qdrant_api_key: str | None = field(default=None, repr=False)
    qdrant_collection_name: str = "knowledge_chunks"
    project_memory_enabled: bool = False
    project_memory_mode: str = "off"
    project_memory_store: str = "memory"
    project_memory_vector_store: str = "memory"
    project_memory_candidate_threshold: float = 0.60
    project_memory_auto_threshold: float = 0.85
    project_memory_recall_limit: int = 20
    project_memory_result_limit: int = 6
    project_memory_max_context_chars: int = 3000
    project_memory_qdrant_collection: str = "project_memories"
    project_memory_relevance_weight: float = 0.65
    project_memory_recency_weight: float = 0.20
    project_memory_importance_weight: float = 0.15
    project_memory_recency_half_life_days: float = 180.0
    user_memory_enabled: bool = False
    user_memory_mode: str = "off"
    user_profile_max_context_chars: int = 1500
    embedding_provider: str = "local"
    embedding_model: str = "gemini-embedding-001"
    local_embedding_dimensions: int = 128
    rag_chunk_size: int = 800
    rag_chunk_overlap: int = 120
    rag_recall_limit: int = 20
    rag_lexical_weight: float = 0.35
    rag_rrf_k: int = 60
    rag_reranker_provider: str = "sentence_transformer"
    sentence_transformer_reranker_model: str = "BAAI/bge-reranker-base"
    sentence_transformer_reranker_device: str = "cpu"
    rag_rerank_default_enabled: bool = False
    rag_max_prompt_chars: int = 6000
    background_task_workers: int = 4
    background_task_queue_capacity: int = 100
    task_queue_backend: str = "in_process"
    redis_url: str = field(default="redis://localhost:6379/0", repr=False)
    celery_result_backend_url: str = field(
        default="redis://localhost:6379/1",
        repr=False,
    )
    celery_visibility_timeout_seconds: int = 3600
    celery_task_max_retries: int = 3
    celery_task_retry_backoff_seconds: int = 2
    celery_task_retry_backoff_max_seconds: int = 60
    celery_task_soft_time_limit_seconds: int = 900
    celery_task_time_limit_seconds: int = 960
    celery_result_expires_seconds: int = 86400
    celery_worker_max_tasks_per_child: int = 100
    mcp_enabled: bool = False
    mcp_allowed: bool = True
    mcp_config_path: str | None = str(
        Path.home() / ".ai-agent-platform" / "mcp.json"
    )
    mcp_request_timeout_seconds: float = 10.0
    skills_enabled: bool = False
    skills_allowed: bool = True
    skills_directory_path: str = str(
        Path.home() / ".ai-agent-platform" / "skills"
    )
    tool_allowlist: tuple[str, ...] | None = None
    skill_allowlist: tuple[str, ...] | None = None
    enabled_tools: tuple[str, ...] | None = None
    enabled_skills: tuple[str, ...] | None = None
    project_instructions: tuple[str, ...] = ()
    sandbox_mode: str = "local"
    sandbox_docker_image: str = "python:3.11-slim"
    sandbox_command_timeout_seconds: float = 30.0
    sandbox_command_output_max_chars: int = 12000
    sandbox_workspace_parent: str | None = None
    sandbox_workspace_ttl_seconds: float = 86400.0
    sandbox_allowed_commands: tuple[str, ...] = (
        "alembic",
        "cargo",
        "git",
        "go",
        "mypy",
        "node",
        "npm",
        "npx",
        "poetry",
        "pytest",
        "python",
        "python3",
        "ruff",
        "rustc",
        "tox",
        "uv",
    )
    agent_max_exploration_rounds: int = 4
    agent_max_read_tools_per_round: int = 6
    agent_max_context_files: int = 12
    agent_max_context_chars: int = 32000
    agent_max_instruction_chars: int = 16000
    agent_soft_tool_rounds: int = 12
    agent_max_tool_rounds: int = 24
    agent_soft_tool_calls: int = 36
    agent_max_tool_calls: int = 72
    agent_max_elapsed_seconds: int = 900
    agent_no_progress_rounds: int = 3
    agent_max_consecutive_failures: int = 3
    agent_native_context_max_chars: int = 48000
    agent_native_context_keep_messages: int = 10
    agent_tool_result_keep_recent: int = 6
    agent_native_max_compactions: int = 3
    agent_plan_max_output_tokens: int = 4096
    agent_mutation_max_output_tokens: int = 16384
    agent_final_max_output_tokens: int = 4096
    agent_tool_result_max_tokens: int = 2000
    agent_snip_enabled: bool = True
    agent_snip_pressure_ratio: float = 0.60
    agent_snip_keep_recent_groups: int = 4
    agent_micro_compact_idle_seconds: int = 3600
    agent_micro_compact_keep_recent_results: int = 5
    agent_compaction_max_output_tokens: int = 4096
    agent_compaction_safety_buffer_tokens: int = 2048
    agent_compaction_min_reclaimable_tokens: int = 2048
    agent_graph_recursion_limit: int = 128
    agent_approval_policy: str = "on_request"
    live_workspace_writes_enabled: bool = False
    agent_workspace_default_mode: str = "patch_only"
    agent_workspace_allowed_modes: tuple[str, ...] = ("patch_only",)
    change_set_apply_mode: str = "patch_only"
    change_set_max_files: int = 100
    change_set_max_patch_chars: int = 1_000_000
    change_set_worktree_parent: str | None = None
    change_set_branch_prefix: str = "codex/"
    auth_mode: str = "disabled"
    single_user_id: str = "owner"
    native_directory_picker_mode: str = "loopback"
    gateway_trust_secret: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _require_choice(
            "runtime_profile",
            self.runtime_profile,
            set(RUNTIME_PROFILE_DEFAULTS),
        )
        if not self.api_prefix.startswith("/"):
            raise ValueError("api_prefix must start with '/'")
        _require_choice(
            "log_level",
            self.log_level.upper(),
            {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"},
        )
        _require_choice("log_format", self.log_format, {"json", "text"})
        _require_choice(
            "llm_provider",
            self.llm_provider,
            {"anthropic", "deepseek", "fake", "google", "openai"},
        )
        _require_choice(
            "embedding_provider",
            self.embedding_provider,
            {"gemini", "local", "openai"},
        )
        _require_choice(
            "llm_thinking_level",
            self.llm_thinking_level,
            {"minimal", "low", "medium", "high"},
        )
        _require_choice(
            "llm_routing_policy",
            self.llm_routing_policy,
            {"smart", "quality", "cost", "latency"},
        )
        _require_choice(
            "agent_approval_policy",
            self.agent_approval_policy,
            {"always", "on_request", "never"},
        )
        if self.llm_model_catalog_json:
            _validate_model_catalog_json(self.llm_model_catalog_json)
        if self.llm_retry_policy_json:
            parse_llm_retry_policy_json(self.llm_retry_policy_json)
        _require_choice(
            "token_budget_action",
            self.token_budget_action,
            {"downgrade", "reject"},
        )
        for name, value in (
            ("session_token_budget", self.session_token_budget),
            ("workspace_token_budget", self.workspace_token_budget),
        ):
            if value < 0:
                raise ValueError(f"{name} must be greater than or equal to 0")
        if bool(self.token_budget_fallback_provider) != bool(
            self.token_budget_fallback_model
        ):
            raise ValueError(
                "token budget fallback provider and model must be configured together"
            )
        if self.token_budget_action == "downgrade" and (
            self.session_token_budget > 0 or self.workspace_token_budget > 0
        ):
            if not self.token_budget_fallback_provider:
                raise ValueError(
                    "downgrade token budgets require a fallback provider and model"
                )
        if self.token_budget_fallback_provider is not None:
            _require_choice(
                "token_budget_fallback_provider",
                self.token_budget_fallback_provider,
                {"anthropic", "deepseek", "fake", "google", "openai"},
            )
        for name, value in (
            ("session_repository", self.session_repository),
            ("agent_run_store", self.agent_run_store),
            ("workspace_store", self.workspace_store),
        ):
            _require_choice(name, value, {"memory", "postgres", "sqlite"})
        for name, value in (
            ("change_set_store", self.change_set_store),
            ("document_store", self.document_store),
        ):
            _require_choice(name, value, {"memory", "postgres"})
        _require_choice(
            "model_registry_store",
            self.model_registry_store,
            {"memory", "postgres"},
        )
        _require_choice(
            "model_secret_backend",
            self.model_secret_backend,
            {"encrypted_file", "keyring", "memory"},
        )
        _require_choice(
            "langgraph_checkpointer",
            self.langgraph_checkpointer,
            {"memory", "postgres"},
        )
        _require_choice(
            "rag_vector_store",
            self.rag_vector_store,
            {"memory", "chroma", "qdrant"},
        )
        _require_choice(
            "rag_reranker_provider",
            self.rag_reranker_provider,
            {"none", "sentence_transformer"},
        )
        if self.rag_rerank_default_enabled and self.rag_reranker_provider == "none":
            raise ValueError(
                "rag_rerank_default_enabled requires a configured reranker provider"
            )
        if not self.sentence_transformer_reranker_device.strip():
            raise ValueError(
                "sentence_transformer_reranker_device must not be empty"
            )
        _require_choice(
            "project_memory_mode",
            self.project_memory_mode,
            {"off", "shadow", "review", "auto"},
        )
        _require_choice(
            "project_memory_store",
            self.project_memory_store,
            {"memory", "postgres", "sqlite"},
        )
        _require_choice(
            "project_memory_vector_store",
            self.project_memory_vector_store,
            {"memory", "qdrant", "sqlite"},
        )
        _require_choice(
            "user_memory_mode",
            self.user_memory_mode,
            {"off", "review", "auto"},
        )
        _require_choice(
            "auth_mode",
            self.auth_mode,
            {"disabled", "single_user", "trusted_header"},
        )
        if not self.single_user_id.strip() or len(self.single_user_id) > 256:
            raise ValueError("single_user_id must contain 1-256 non-blank characters")
        _require_choice(
            "native_directory_picker_mode",
            self.native_directory_picker_mode,
            {"disabled", "loopback", "trusted_local_gateway"},
        )
        _require_choice(
            "agent_workspace_default_mode",
            self.agent_workspace_default_mode,
            {"patch_only", "direct", "worktree"},
        )
        if not self.agent_workspace_allowed_modes:
            raise ValueError("agent_workspace_allowed_modes must not be empty")
        invalid_workspace_modes = set(self.agent_workspace_allowed_modes).difference(
            {"patch_only", "direct", "worktree"}
        )
        if invalid_workspace_modes:
            raise ValueError(
                "agent_workspace_allowed_modes contains unsupported modes: "
                + ", ".join(sorted(invalid_workspace_modes))
            )
        if len(set(self.agent_workspace_allowed_modes)) != len(
            self.agent_workspace_allowed_modes
        ):
            raise ValueError("agent_workspace_allowed_modes must contain unique modes")
        if self.agent_workspace_default_mode not in self.agent_workspace_allowed_modes:
            raise ValueError(
                "agent_workspace_default_mode must be included in "
                "agent_workspace_allowed_modes"
            )
        _require_choice(
            "change_set_apply_mode",
            self.change_set_apply_mode,
            {"patch_only", "direct", "worktree"},
        )
        _require_choice("sandbox_mode", self.sandbox_mode, {"local", "docker"})
        if not self.sandbox_allowed_commands:
            raise ValueError("sandbox_allowed_commands must not be empty")
        if any(
            not item.strip() or Path(item).name != item
            for item in self.sandbox_allowed_commands
        ):
            raise ValueError(
                "sandbox_allowed_commands must contain executable basenames"
            )
        _require_choice(
            "task_queue_backend",
            self.task_queue_backend,
            {"celery", "in_process"},
        )
        profile_requirements = _RUNTIME_PROFILE_BACKEND_REQUIREMENTS[
            self.runtime_profile
        ]
        profile_mismatches = [
            f"{name}={getattr(self, name)} (expected {expected})"
            for name, expected in profile_requirements.items()
            if getattr(self, name) != expected
        ]
        if profile_mismatches:
            raise ValueError(
                f"runtime_profile={self.runtime_profile} has incompatible backends: "
                + ", ".join(profile_mismatches)
                + "; use runtime_profile=custom for a manual combination"
            )
        sqlite_selected = any(
            value == "sqlite"
            for value in (
                self.session_repository,
                self.agent_run_store,
                self.workspace_store,
                self.project_memory_store,
                self.project_memory_vector_store,
            )
        ) or self.user_memory_enabled
        if sqlite_selected and self.task_queue_backend != "in_process":
            raise ValueError(
                "SQLite local state requires TASK_QUEUE_BACKEND=in_process"
            )
        if self.session_repository != self.agent_run_store:
            raise ValueError(
                "session_repository and agent_run_store must use the same "
                "backend for atomic Query start"
            )
        if not self.local_state_path.strip():
            raise ValueError("local_state_path must not be empty")
        _require_choice("eval_store", self.eval_store, {"memory", "postgres"})
        for name, value in (
            ("llm_timeout_seconds", self.llm_timeout_seconds),
            (
                "llm_retry_base_delay_seconds",
                self.llm_retry_base_delay_seconds,
            ),
            (
                "llm_retry_backoff_max_seconds",
                self.llm_retry_backoff_max_seconds,
            ),
            (
                "llm_retry_after_max_seconds",
                self.llm_retry_after_max_seconds,
            ),
            ("sse_heartbeat_seconds", self.sse_heartbeat_seconds),
            ("llm_max_input_chars", self.llm_max_input_chars),
            ("llm_max_context_messages", self.llm_max_context_messages),
            (
                "llm_max_context_messages_ceiling",
                self.llm_max_context_messages_ceiling,
            ),
            ("llm_max_output_tokens", self.llm_max_output_tokens),
            (
                "llm_model_context_window_tokens",
                self.llm_model_context_window_tokens,
            ),
            (
                "llm_circuit_failure_threshold",
                self.llm_circuit_failure_threshold,
            ),
            (
                "llm_circuit_recovery_timeout_seconds",
                self.llm_circuit_recovery_timeout_seconds,
            ),
            (
                "llm_circuit_error_window_size",
                self.llm_circuit_error_window_size,
            ),
            (
                "llm_circuit_error_rate_min_requests",
                self.llm_circuit_error_rate_min_requests,
            ),
            (
                "conversation_summary_trigger_messages",
                self.conversation_summary_trigger_messages,
            ),
            (
                "conversation_summary_keep_recent_messages",
                self.conversation_summary_keep_recent_messages,
            ),
            (
                "conversation_summary_max_chars",
                self.conversation_summary_max_chars,
            ),
            (
                "conversation_summary_max_source_chars",
                self.conversation_summary_max_source_chars,
            ),
            ("local_embedding_dimensions", self.local_embedding_dimensions),
            ("rag_chunk_size", self.rag_chunk_size),
            ("rag_recall_limit", self.rag_recall_limit),
            ("rag_rrf_k", self.rag_rrf_k),
            ("rag_max_prompt_chars", self.rag_max_prompt_chars),
            ("project_memory_recall_limit", self.project_memory_recall_limit),
            ("project_memory_result_limit", self.project_memory_result_limit),
            (
                "project_memory_max_context_chars",
                self.project_memory_max_context_chars,
            ),
            (
                "user_profile_max_context_chars",
                self.user_profile_max_context_chars,
            ),
            (
                "project_memory_recency_half_life_days",
                self.project_memory_recency_half_life_days,
            ),
            ("background_task_workers", self.background_task_workers),
            (
                "celery_visibility_timeout_seconds",
                self.celery_visibility_timeout_seconds,
            ),
            (
                "celery_task_retry_backoff_seconds",
                self.celery_task_retry_backoff_seconds,
            ),
            (
                "celery_task_retry_backoff_max_seconds",
                self.celery_task_retry_backoff_max_seconds,
            ),
            (
                "celery_task_soft_time_limit_seconds",
                self.celery_task_soft_time_limit_seconds,
            ),
            (
                "celery_task_time_limit_seconds",
                self.celery_task_time_limit_seconds,
            ),
            ("celery_result_expires_seconds", self.celery_result_expires_seconds),
            (
                "celery_worker_max_tasks_per_child",
                self.celery_worker_max_tasks_per_child,
            ),
            ("mcp_request_timeout_seconds", self.mcp_request_timeout_seconds),
            (
                "sandbox_command_timeout_seconds",
                self.sandbox_command_timeout_seconds,
            ),
            (
                "sandbox_command_output_max_chars",
                self.sandbox_command_output_max_chars,
            ),
            (
                "sandbox_workspace_ttl_seconds",
                self.sandbox_workspace_ttl_seconds,
            ),
            ("agent_max_exploration_rounds", self.agent_max_exploration_rounds),
            (
                "agent_max_read_tools_per_round",
                self.agent_max_read_tools_per_round,
            ),
            ("agent_max_context_files", self.agent_max_context_files),
            ("agent_max_context_chars", self.agent_max_context_chars),
            ("agent_max_instruction_chars", self.agent_max_instruction_chars),
            ("agent_soft_tool_rounds", self.agent_soft_tool_rounds),
            ("agent_max_tool_rounds", self.agent_max_tool_rounds),
            ("agent_soft_tool_calls", self.agent_soft_tool_calls),
            ("agent_max_tool_calls", self.agent_max_tool_calls),
            ("agent_max_elapsed_seconds", self.agent_max_elapsed_seconds),
            ("agent_no_progress_rounds", self.agent_no_progress_rounds),
            (
                "agent_max_consecutive_failures",
                self.agent_max_consecutive_failures,
            ),
            (
                "agent_native_context_max_chars",
                self.agent_native_context_max_chars,
            ),
            (
                "agent_native_context_keep_messages",
                self.agent_native_context_keep_messages,
            ),
            (
                "agent_tool_result_keep_recent",
                self.agent_tool_result_keep_recent,
            ),
            (
                "agent_native_max_compactions",
                self.agent_native_max_compactions,
            ),
            (
                "agent_plan_max_output_tokens",
                self.agent_plan_max_output_tokens,
            ),
            (
                "agent_mutation_max_output_tokens",
                self.agent_mutation_max_output_tokens,
            ),
            (
                "agent_final_max_output_tokens",
                self.agent_final_max_output_tokens,
            ),
            ("agent_tool_result_max_tokens", self.agent_tool_result_max_tokens),
            ("agent_snip_keep_recent_groups", self.agent_snip_keep_recent_groups),
            ("agent_micro_compact_idle_seconds", self.agent_micro_compact_idle_seconds),
            ("agent_micro_compact_keep_recent_results", self.agent_micro_compact_keep_recent_results),
            ("agent_compaction_max_output_tokens", self.agent_compaction_max_output_tokens),
            ("agent_compaction_safety_buffer_tokens", self.agent_compaction_safety_buffer_tokens),
            ("agent_compaction_min_reclaimable_tokens", self.agent_compaction_min_reclaimable_tokens),
            ("agent_graph_recursion_limit", self.agent_graph_recursion_limit),
            ("change_set_max_files", self.change_set_max_files),
            ("change_set_max_patch_chars", self.change_set_max_patch_chars),
        ):
            _require_positive(name, value)
        if self.agent_tool_result_max_tokens < 64:
            raise ValueError("agent_tool_result_max_tokens must be at least 64")
        if not 0 < self.agent_snip_pressure_ratio < 1:
            raise ValueError("agent_snip_pressure_ratio must be between 0 and 1")
        if self.agent_soft_tool_rounds > self.agent_max_tool_rounds:
            raise ValueError(
                "agent_soft_tool_rounds must not exceed agent_max_tool_rounds"
            )
        if self.agent_soft_tool_calls > self.agent_max_tool_calls:
            raise ValueError(
                "agent_soft_tool_calls must not exceed agent_max_tool_calls"
            )
        if self.llm_max_retries < 0:
            raise ValueError("llm_max_retries must be greater than or equal to 0")
        if not math.isfinite(self.model_probe_interval_seconds):
            raise ValueError("model_probe_interval_seconds must be finite")
        if 0 < self.model_probe_interval_seconds < 60:
            raise ValueError(
                "model_probe_interval_seconds must be 0 or at least 60"
            )
        if self.model_probe_interval_seconds < 0:
            raise ValueError(
                "model_probe_interval_seconds must be greater than or equal to 0"
            )
        for name, value in (
            (
                "llm_retry_base_delay_seconds",
                self.llm_retry_base_delay_seconds,
            ),
            (
                "llm_retry_backoff_max_seconds",
                self.llm_retry_backoff_max_seconds,
            ),
            (
                "llm_retry_after_max_seconds",
                self.llm_retry_after_max_seconds,
            ),
            ("llm_retry_jitter_seconds", self.llm_retry_jitter_seconds),
        ):
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.llm_retry_jitter_seconds < 0:
            raise ValueError(
                "llm_retry_jitter_seconds must be greater than or equal to 0"
            )
        if (
            self.llm_retry_backoff_max_seconds
            < self.llm_retry_base_delay_seconds
        ):
            raise ValueError(
                "llm_retry_backoff_max_seconds must not be smaller than "
                "llm_retry_base_delay_seconds"
            )
        if (
            self.llm_circuit_error_rate_min_requests
            > self.llm_circuit_error_window_size
        ):
            raise ValueError(
                "llm_circuit_error_rate_min_requests must not exceed "
                "llm_circuit_error_window_size"
            )
        if not 0.0 <= self.llm_circuit_error_rate_threshold <= 1.0:
            raise ValueError(
                "llm_circuit_error_rate_threshold must be between 0 and 1"
            )
        if self.rag_chunk_overlap < 0:
            raise ValueError("rag_chunk_overlap must be greater than or equal to 0")
        if self.rag_chunk_overlap >= self.rag_chunk_size:
            raise ValueError("rag_chunk_overlap must be smaller than rag_chunk_size")
        if (
            self.conversation_summary_keep_recent_messages
            >= self.conversation_summary_trigger_messages
        ):
            raise ValueError(
                "conversation_summary_keep_recent_messages must be smaller than "
                "conversation_summary_trigger_messages"
            )
        if (
            self.llm_max_context_messages_ceiling
            < self.llm_max_context_messages
        ):
            raise ValueError(
                "llm_max_context_messages_ceiling must not be smaller than "
                "llm_max_context_messages"
            )
        if not 0.0 < self.llm_context_input_token_ratio <= 1.0:
            raise ValueError(
                "llm_context_input_token_ratio must be between 0 and 1"
            )
        if not 0.0 <= self.llm_context_evidence_ratio < 1.0:
            raise ValueError(
                "llm_context_evidence_ratio must be between 0 and 1"
            )
        if not 0.0 <= self.llm_context_history_ratio < 1.0:
            raise ValueError(
                "llm_context_history_ratio must be between 0 and 1"
            )
        if self.llm_context_evidence_ratio + self.llm_context_history_ratio >= 1.0:
            raise ValueError(
                "llm_context_evidence_ratio and llm_context_history_ratio must "
                "leave room for the tool transcript"
            )
        if not 0.0 <= self.rag_lexical_weight <= 1.0:
            raise ValueError("rag_lexical_weight must be between 0 and 1")
        if not 0.0 <= self.project_memory_candidate_threshold <= 1.0:
            raise ValueError(
                "project_memory_candidate_threshold must be between 0 and 1"
            )
        if not 0.0 <= self.project_memory_auto_threshold <= 1.0:
            raise ValueError(
                "project_memory_auto_threshold must be between 0 and 1"
            )
        if (
            self.project_memory_candidate_threshold
            > self.project_memory_auto_threshold
        ):
            raise ValueError(
                "project_memory_candidate_threshold must not exceed "
                "project_memory_auto_threshold"
            )
        if self.project_memory_result_limit > self.project_memory_recall_limit:
            raise ValueError(
                "project_memory_result_limit must not exceed "
                "project_memory_recall_limit"
            )
        memory_weights = (
            self.project_memory_relevance_weight,
            self.project_memory_recency_weight,
            self.project_memory_importance_weight,
        )
        if any(weight < 0.0 or weight > 1.0 for weight in memory_weights):
            raise ValueError("project memory ranking weights must be between 0 and 1")
        if not math.isclose(sum(memory_weights), 1.0, abs_tol=1e-9):
            raise ValueError("project memory ranking weights must sum to 1")
        if self.auth_mode == "trusted_header" and not self.gateway_trust_secret:
            raise ValueError(
                "gateway_trust_secret is required when auth_mode=trusted_header"
            )
        if (
            self.native_directory_picker_mode == "trusted_local_gateway"
            and self.auth_mode != "trusted_header"
        ):
            raise ValueError(
                "native_directory_picker_mode=trusted_local_gateway requires "
                "auth_mode=trusted_header"
            )
        if self.live_workspace_writes_enabled and self.auth_mode == "disabled":
            raise ValueError(
                "live_workspace_writes_enabled requires auth_mode=trusted_header"
            )
        branch_candidate = f"{self.change_set_branch_prefix.strip()}run"
        invalid_branch_tokens = (
            "..",
            "//",
            "@{",
            "\\",
            " ",
            "~",
            "^",
            ":",
            "?",
            "*",
            "[",
        )
        if (
            not self.change_set_branch_prefix.strip()
            or self.change_set_branch_prefix.startswith(("/", "."))
            or any(token in branch_candidate for token in invalid_branch_tokens)
            or re.fullmatch(r"[A-Za-z0-9._/-]+", branch_candidate) is None
            or any(
                not part or part.startswith(".") or part.endswith((".", ".lock"))
                for part in branch_candidate.split("/")
            )
        ):
            raise ValueError("change_set_branch_prefix must be a safe non-empty prefix")
        if self.background_task_queue_capacity < 0:
            raise ValueError(
                "background_task_queue_capacity must be greater than or equal to 0"
            )
        if self.celery_task_max_retries < 0:
            raise ValueError(
                "celery_task_max_retries must be greater than or equal to 0"
            )
        if (
            self.celery_task_retry_backoff_max_seconds
            < self.celery_task_retry_backoff_seconds
        ):
            raise ValueError(
                "celery_task_retry_backoff_max_seconds must be greater than or "
                "equal to celery_task_retry_backoff_seconds"
            )
        if (
            self.celery_task_time_limit_seconds
            <= self.celery_task_soft_time_limit_seconds
        ):
            raise ValueError(
                "celery_task_time_limit_seconds must be greater than "
                "celery_task_soft_time_limit_seconds"
            )
        if (
            self.celery_visibility_timeout_seconds
            <= self.celery_task_time_limit_seconds
        ):
            raise ValueError(
                "celery_visibility_timeout_seconds must be greater than "
                "celery_task_time_limit_seconds"
            )
        if self.task_queue_backend == "celery":
            if not self.redis_url.startswith(("redis://", "rediss://")):
                raise ValueError(
                    "redis_url must start with redis:// or rediss:// for Celery"
                )
            if not self.celery_result_backend_url.startswith(
                ("redis://", "rediss://")
            ):
                raise ValueError(
                    "celery_result_backend_url must start with redis:// or rediss://"
                )
            self._validate_distributed_task_storage()
        if self.mcp_enabled and not self.mcp_config_path:
            raise ValueError("mcp_config_path is required when mcp_enabled is true")
        if self.mcp_enabled and not self.mcp_allowed:
            raise ValueError("mcp_enabled cannot override process-level mcp_allowed=false")
        if self.skills_enabled and not self.skills_allowed:
            raise ValueError(
                "skills_enabled cannot override process-level skills_allowed=false"
            )
        _validate_permission_selection(
            "enabled_tools",
            self.enabled_tools,
            self.tool_allowlist,
        )
        _validate_permission_selection(
            "enabled_skills",
            self.enabled_skills,
            self.skill_allowlist,
        )

    @classmethod
    def from_env(cls) -> "Settings":
        """Resolve default config locations and legacy environment variables.

        The return type intentionally remains ``Settings`` for existing callers. New
        code that needs provenance or safe diagnostics should use ``ConfigResolver``.
        """
        from .config_resolver import ConfigResolver

        return ConfigResolver.from_default_locations().resolve_process().settings

    @classmethod
    def _legacy_from_env(cls) -> "Settings":
        """Previous parser retained as an internal compatibility reference."""
        dotenv = _load_dotenv()
        return cls(
            app_name=_env("APP_NAME", cls.app_name, dotenv),
            api_prefix=_env("API_PREFIX", cls.api_prefix, dotenv),
            log_level=_env("LOG_LEVEL", cls.log_level, dotenv),
            log_format=_env("LOG_FORMAT", cls.log_format, dotenv),
            llm_provider=_env("LLM_PROVIDER", cls.llm_provider, dotenv),
            llm_model=_env("LLM_MODEL", cls.llm_model, dotenv),
            llm_model_catalog_json=(
                _env("LLM_MODEL_CATALOG_JSON", None, dotenv) or None
            ),
            llm_model_context_window_tokens=_int_env(
                "LLM_MODEL_CONTEXT_WINDOW_TOKENS",
                cls.llm_model_context_window_tokens,
                dotenv,
            ),
            llm_routing_policy=_env(
                "LLM_ROUTING_POLICY", cls.llm_routing_policy, dotenv
            ),
            llm_circuit_failure_threshold=_int_env(
                "LLM_CIRCUIT_FAILURE_THRESHOLD",
                cls.llm_circuit_failure_threshold,
                dotenv,
            ),
            llm_circuit_recovery_timeout_seconds=_float_env(
                "LLM_CIRCUIT_RECOVERY_TIMEOUT_SECONDS",
                cls.llm_circuit_recovery_timeout_seconds,
                dotenv,
            ),
            llm_circuit_error_window_size=_int_env(
                "LLM_CIRCUIT_ERROR_WINDOW_SIZE",
                cls.llm_circuit_error_window_size,
                dotenv,
            ),
            llm_circuit_error_rate_min_requests=_int_env(
                "LLM_CIRCUIT_ERROR_RATE_MIN_REQUESTS",
                cls.llm_circuit_error_rate_min_requests,
                dotenv,
            ),
            llm_circuit_error_rate_threshold=_float_env(
                "LLM_CIRCUIT_ERROR_RATE_THRESHOLD",
                cls.llm_circuit_error_rate_threshold,
                dotenv,
            ),
            model_registry_store=_env(
                "MODEL_REGISTRY_STORE",
                _env("SESSION_REPOSITORY", cls.model_registry_store, dotenv),
                dotenv,
            ),
            model_secret_backend=_env(
                "MODEL_SECRET_BACKEND", cls.model_secret_backend, dotenv
            ),
            database_url=_env("DATABASE_URL", cls.database_url, dotenv),
            local_state_path=_env(
                "LOCAL_STATE_PATH", cls.local_state_path, dotenv
            ),
            session_repository=_env(
                "SESSION_REPOSITORY", cls.session_repository, dotenv
            ),
            agent_run_store=_env("AGENT_RUN_STORE", cls.agent_run_store, dotenv),
            eval_store=_env("EVAL_STORE", cls.eval_store, dotenv),
            eval_fault_injection_enabled=_bool_env(
                "EVAL_FAULT_INJECTION_ENABLED",
                cls.eval_fault_injection_enabled,
                dotenv,
            ),
            eval_workspace_root=_env(
                "EVAL_WORKSPACE_ROOT", cls.eval_workspace_root, dotenv
            ),
            change_set_store=_env(
                "CHANGE_SET_STORE",
                _env("AGENT_RUN_STORE", cls.change_set_store, dotenv),
                dotenv,
            ),
            document_store=_env("DOCUMENT_STORE", cls.document_store, dotenv),
            workspace_store=_env(
                "WORKSPACE_STORE", cls.workspace_store, dotenv
            ),
            workspace_allowed_roots=_paths_env(
                "WORKSPACE_ALLOWED_ROOTS",
                (str(Path.home().resolve()),),
                dotenv,
            ),
            langgraph_checkpointer=_env(
                "LANGGRAPH_CHECKPOINTER", cls.langgraph_checkpointer, dotenv
            ),
            llm_timeout_seconds=_float_env(
                "LLM_TIMEOUT_SECONDS", cls.llm_timeout_seconds, dotenv
            ),
            llm_max_retries=_int_env("LLM_MAX_RETRIES", cls.llm_max_retries, dotenv),
            llm_retry_policy_json=(
                _env("LLM_RETRY_POLICY_JSON", None, dotenv) or None
            ),
            llm_retry_base_delay_seconds=_float_env(
                "LLM_RETRY_BASE_DELAY_SECONDS",
                cls.llm_retry_base_delay_seconds,
                dotenv,
            ),
            llm_retry_backoff_max_seconds=_float_env(
                "LLM_RETRY_BACKOFF_MAX_SECONDS",
                cls.llm_retry_backoff_max_seconds,
                dotenv,
            ),
            llm_retry_after_max_seconds=_float_env(
                "LLM_RETRY_AFTER_MAX_SECONDS",
                cls.llm_retry_after_max_seconds,
                dotenv,
            ),
            llm_retry_jitter_seconds=_float_env(
                "LLM_RETRY_JITTER_SECONDS",
                cls.llm_retry_jitter_seconds,
                dotenv,
            ),
            llm_max_input_chars=_int_env(
                "LLM_MAX_INPUT_CHARS", cls.llm_max_input_chars, dotenv
            ),
            llm_max_context_messages=_int_env(
                "LLM_MAX_CONTEXT_MESSAGES", cls.llm_max_context_messages, dotenv
            ),
            llm_max_context_messages_ceiling=_int_env(
                "LLM_MAX_CONTEXT_MESSAGES_CEILING",
                cls.llm_max_context_messages_ceiling,
                dotenv,
            ),
            llm_context_input_token_ratio=_float_env(
                "LLM_CONTEXT_INPUT_TOKEN_RATIO",
                cls.llm_context_input_token_ratio,
                dotenv,
            ),
            llm_context_evidence_ratio=_float_env(
                "LLM_CONTEXT_EVIDENCE_RATIO",
                cls.llm_context_evidence_ratio,
                dotenv,
            ),
            llm_context_history_ratio=_float_env(
                "LLM_CONTEXT_HISTORY_RATIO",
                cls.llm_context_history_ratio,
                dotenv,
            ),
            llm_max_output_tokens=_int_env(
                "LLM_MAX_OUTPUT_TOKENS", cls.llm_max_output_tokens, dotenv
            ),
            llm_thinking_level=_env(
                "LLM_THINKING_LEVEL", cls.llm_thinking_level, dotenv
            ),
            session_token_budget=_int_env(
                "SESSION_TOKEN_BUDGET",
                cls.session_token_budget,
                dotenv,
            ),
            workspace_token_budget=_int_env(
                "WORKSPACE_TOKEN_BUDGET",
                cls.workspace_token_budget,
                dotenv,
            ),
            token_budget_action=_env(
                "TOKEN_BUDGET_ACTION",
                cls.token_budget_action,
                dotenv,
            ),
            token_budget_fallback_provider=_env(
                "TOKEN_BUDGET_FALLBACK_PROVIDER",
                cls.token_budget_fallback_provider,
                dotenv,
            ),
            token_budget_fallback_model=_env(
                "TOKEN_BUDGET_FALLBACK_MODEL",
                cls.token_budget_fallback_model,
                dotenv,
            ),
            conversation_summary_enabled=_bool_env(
                "CONVERSATION_SUMMARY_ENABLED",
                cls.conversation_summary_enabled,
                dotenv,
            ),
            conversation_summary_trigger_messages=_int_env(
                "CONVERSATION_SUMMARY_TRIGGER_MESSAGES",
                cls.conversation_summary_trigger_messages,
                dotenv,
            ),
            conversation_summary_keep_recent_messages=_int_env(
                "CONVERSATION_SUMMARY_KEEP_RECENT_MESSAGES",
                cls.conversation_summary_keep_recent_messages,
                dotenv,
            ),
            conversation_summary_max_chars=_int_env(
                "CONVERSATION_SUMMARY_MAX_CHARS",
                cls.conversation_summary_max_chars,
                dotenv,
            ),
            conversation_summary_max_source_chars=_int_env(
                "CONVERSATION_SUMMARY_MAX_SOURCE_CHARS",
                cls.conversation_summary_max_source_chars,
                dotenv,
            ),
            conversation_summary_sync_on_overflow=_bool_env(
                "CONVERSATION_SUMMARY_SYNC_ON_OVERFLOW",
                cls.conversation_summary_sync_on_overflow,
                dotenv,
            ),
            sse_heartbeat_seconds=_float_env(
                "SSE_HEARTBEAT_SECONDS", cls.sse_heartbeat_seconds, dotenv
            ),
            rag_vector_store=_env("RAG_VECTOR_STORE", cls.rag_vector_store, dotenv),
            chroma_persist_directory=_env(
                "CHROMA_PERSIST_DIRECTORY", cls.chroma_persist_directory, dotenv
            ),
            chroma_collection_name=_env(
                "CHROMA_COLLECTION_NAME", cls.chroma_collection_name, dotenv
            ),
            qdrant_url=_env("QDRANT_URL", cls.qdrant_url, dotenv),
            qdrant_api_key=_env("QDRANT_API_KEY", None, dotenv),
            qdrant_collection_name=_env(
                "QDRANT_COLLECTION_NAME", cls.qdrant_collection_name, dotenv
            ),
            project_memory_enabled=_bool_env(
                "PROJECT_MEMORY_ENABLED",
                cls.project_memory_enabled,
                dotenv,
            ),
            project_memory_mode=_env(
                "PROJECT_MEMORY_MODE",
                cls.project_memory_mode,
                dotenv,
            ),
            project_memory_store=_env(
                "PROJECT_MEMORY_STORE",
                cls.project_memory_store,
                dotenv,
            ),
            project_memory_vector_store=_env(
                "PROJECT_MEMORY_VECTOR_STORE",
                cls.project_memory_vector_store,
                dotenv,
            ),
            project_memory_candidate_threshold=_float_env(
                "PROJECT_MEMORY_CANDIDATE_THRESHOLD",
                cls.project_memory_candidate_threshold,
                dotenv,
            ),
            project_memory_auto_threshold=_float_env(
                "PROJECT_MEMORY_AUTO_THRESHOLD",
                cls.project_memory_auto_threshold,
                dotenv,
            ),
            project_memory_recall_limit=_int_env(
                "PROJECT_MEMORY_RECALL_LIMIT",
                cls.project_memory_recall_limit,
                dotenv,
            ),
            project_memory_result_limit=_int_env(
                "PROJECT_MEMORY_RESULT_LIMIT",
                cls.project_memory_result_limit,
                dotenv,
            ),
            project_memory_max_context_chars=_int_env(
                "PROJECT_MEMORY_MAX_CONTEXT_CHARS",
                cls.project_memory_max_context_chars,
                dotenv,
            ),
            project_memory_qdrant_collection=_env(
                "PROJECT_MEMORY_QDRANT_COLLECTION",
                cls.project_memory_qdrant_collection,
                dotenv,
            ),
            project_memory_relevance_weight=_float_env(
                "PROJECT_MEMORY_RELEVANCE_WEIGHT",
                cls.project_memory_relevance_weight,
                dotenv,
            ),
            project_memory_recency_weight=_float_env(
                "PROJECT_MEMORY_RECENCY_WEIGHT",
                cls.project_memory_recency_weight,
                dotenv,
            ),
            project_memory_importance_weight=_float_env(
                "PROJECT_MEMORY_IMPORTANCE_WEIGHT",
                cls.project_memory_importance_weight,
                dotenv,
            ),
            project_memory_recency_half_life_days=_float_env(
                "PROJECT_MEMORY_RECENCY_HALF_LIFE_DAYS",
                cls.project_memory_recency_half_life_days,
                dotenv,
            ),
            user_memory_enabled=_bool_env(
                "USER_MEMORY_ENABLED",
                cls.user_memory_enabled,
                dotenv,
            ),
            user_memory_mode=_env(
                "USER_MEMORY_MODE",
                cls.user_memory_mode,
                dotenv,
            ),
            user_profile_max_context_chars=_int_env(
                "USER_PROFILE_MAX_CONTEXT_CHARS",
                cls.user_profile_max_context_chars,
                dotenv,
            ),
            embedding_provider=_env(
                "EMBEDDING_PROVIDER", cls.embedding_provider, dotenv
            ),
            embedding_model=_env("EMBEDDING_MODEL", cls.embedding_model, dotenv),
            local_embedding_dimensions=_int_env(
                "LOCAL_EMBEDDING_DIMENSIONS", cls.local_embedding_dimensions, dotenv
            ),
            rag_chunk_size=_int_env("RAG_CHUNK_SIZE", cls.rag_chunk_size, dotenv),
            rag_chunk_overlap=_int_env(
                "RAG_CHUNK_OVERLAP", cls.rag_chunk_overlap, dotenv
            ),
            rag_recall_limit=_int_env("RAG_RECALL_LIMIT", cls.rag_recall_limit, dotenv),
            rag_lexical_weight=_float_env(
                "RAG_LEXICAL_WEIGHT", cls.rag_lexical_weight, dotenv
            ),
            rag_rrf_k=_int_env("RAG_RRF_K", cls.rag_rrf_k, dotenv),
            rag_reranker_provider=_env(
                "RAG_RERANKER_PROVIDER", cls.rag_reranker_provider, dotenv
            ),
            sentence_transformer_reranker_model=_env(
                "SENTENCE_TRANSFORMER_RERANKER_MODEL",
                cls.sentence_transformer_reranker_model,
                dotenv,
            ),
            sentence_transformer_reranker_device=_env(
                "SENTENCE_TRANSFORMER_RERANKER_DEVICE",
                cls.sentence_transformer_reranker_device,
                dotenv,
            ),
            rag_rerank_default_enabled=_bool_env(
                "RAG_RERANK_DEFAULT_ENABLED",
                cls.rag_rerank_default_enabled,
                dotenv,
            ),
            rag_max_prompt_chars=_int_env(
                "RAG_MAX_PROMPT_CHARS", cls.rag_max_prompt_chars, dotenv
            ),
            background_task_workers=_int_env(
                "BACKGROUND_TASK_WORKERS", cls.background_task_workers, dotenv
            ),
            background_task_queue_capacity=_int_env(
                "BACKGROUND_TASK_QUEUE_CAPACITY",
                cls.background_task_queue_capacity,
                dotenv,
            ),
            task_queue_backend=_env(
                "TASK_QUEUE_BACKEND", cls.task_queue_backend, dotenv
            ),
            redis_url=_env("REDIS_URL", cls.redis_url, dotenv),
            celery_result_backend_url=_env(
                "CELERY_RESULT_BACKEND_URL",
                cls.celery_result_backend_url,
                dotenv,
            ),
            celery_visibility_timeout_seconds=_int_env(
                "CELERY_VISIBILITY_TIMEOUT_SECONDS",
                cls.celery_visibility_timeout_seconds,
                dotenv,
            ),
            celery_task_max_retries=_int_env(
                "CELERY_TASK_MAX_RETRIES",
                cls.celery_task_max_retries,
                dotenv,
            ),
            celery_task_retry_backoff_seconds=_int_env(
                "CELERY_TASK_RETRY_BACKOFF_SECONDS",
                cls.celery_task_retry_backoff_seconds,
                dotenv,
            ),
            celery_task_retry_backoff_max_seconds=_int_env(
                "CELERY_TASK_RETRY_BACKOFF_MAX_SECONDS",
                cls.celery_task_retry_backoff_max_seconds,
                dotenv,
            ),
            celery_task_soft_time_limit_seconds=_int_env(
                "CELERY_TASK_SOFT_TIME_LIMIT_SECONDS",
                cls.celery_task_soft_time_limit_seconds,
                dotenv,
            ),
            celery_task_time_limit_seconds=_int_env(
                "CELERY_TASK_TIME_LIMIT_SECONDS",
                cls.celery_task_time_limit_seconds,
                dotenv,
            ),
            celery_result_expires_seconds=_int_env(
                "CELERY_RESULT_EXPIRES_SECONDS",
                cls.celery_result_expires_seconds,
                dotenv,
            ),
            celery_worker_max_tasks_per_child=_int_env(
                "CELERY_WORKER_MAX_TASKS_PER_CHILD",
                cls.celery_worker_max_tasks_per_child,
                dotenv,
            ),
            mcp_enabled=_bool_env("MCP_ENABLED", cls.mcp_enabled, dotenv),
            mcp_config_path=_env("MCP_CONFIG_PATH", cls.mcp_config_path, dotenv),
            skills_directory_path=_env(
                "SKILLS_DIRECTORY_PATH",
                cls.skills_directory_path,
                dotenv,
            ),
            mcp_request_timeout_seconds=_float_env(
                "MCP_REQUEST_TIMEOUT_SECONDS",
                cls.mcp_request_timeout_seconds,
                dotenv,
            ),
            sandbox_mode=_env("SANDBOX_MODE", cls.sandbox_mode, dotenv),
            sandbox_docker_image=_env(
                "SANDBOX_DOCKER_IMAGE",
                cls.sandbox_docker_image,
                dotenv,
            ),
            sandbox_command_timeout_seconds=_float_env(
                "SANDBOX_COMMAND_TIMEOUT_SECONDS",
                cls.sandbox_command_timeout_seconds,
                dotenv,
            ),
            sandbox_command_output_max_chars=_int_env(
                "SANDBOX_COMMAND_OUTPUT_MAX_CHARS",
                cls.sandbox_command_output_max_chars,
                dotenv,
            ),
            sandbox_workspace_parent=_env("SANDBOX_WORKSPACE_PARENT", None, dotenv),
            sandbox_workspace_ttl_seconds=_float_env(
                "SANDBOX_WORKSPACE_TTL_SECONDS",
                cls.sandbox_workspace_ttl_seconds,
                dotenv,
            ),
            sandbox_allowed_commands=_csv_env(
                "SANDBOX_ALLOWED_COMMANDS",
                cls.sandbox_allowed_commands,
                dotenv,
            ),
            agent_max_exploration_rounds=_int_env(
                "AGENT_MAX_EXPLORATION_ROUNDS",
                cls.agent_max_exploration_rounds,
                dotenv,
            ),
            agent_max_read_tools_per_round=_int_env(
                "AGENT_MAX_READ_TOOLS_PER_ROUND",
                cls.agent_max_read_tools_per_round,
                dotenv,
            ),
            agent_max_context_files=_int_env(
                "AGENT_MAX_CONTEXT_FILES",
                cls.agent_max_context_files,
                dotenv,
            ),
            agent_max_context_chars=_int_env(
                "AGENT_MAX_CONTEXT_CHARS",
                cls.agent_max_context_chars,
                dotenv,
            ),
            agent_max_instruction_chars=_int_env(
                "AGENT_MAX_INSTRUCTION_CHARS",
                cls.agent_max_instruction_chars,
                dotenv,
            ),
            agent_soft_tool_rounds=_int_env(
                "AGENT_SOFT_TOOL_ROUNDS",
                cls.agent_soft_tool_rounds,
                dotenv,
            ),
            agent_max_tool_rounds=_int_env(
                "AGENT_MAX_TOOL_ROUNDS",
                cls.agent_max_tool_rounds,
                dotenv,
            ),
            agent_soft_tool_calls=_int_env(
                "AGENT_SOFT_TOOL_CALLS",
                cls.agent_soft_tool_calls,
                dotenv,
            ),
            agent_max_tool_calls=_int_env(
                "AGENT_MAX_TOOL_CALLS",
                cls.agent_max_tool_calls,
                dotenv,
            ),
            agent_max_elapsed_seconds=_int_env(
                "AGENT_MAX_ELAPSED_SECONDS",
                cls.agent_max_elapsed_seconds,
                dotenv,
            ),
            agent_no_progress_rounds=_int_env(
                "AGENT_NO_PROGRESS_ROUNDS",
                cls.agent_no_progress_rounds,
                dotenv,
            ),
            agent_max_consecutive_failures=_int_env(
                "AGENT_MAX_CONSECUTIVE_FAILURES",
                cls.agent_max_consecutive_failures,
                dotenv,
            ),
            agent_native_context_max_chars=_int_env(
                "AGENT_NATIVE_CONTEXT_MAX_CHARS",
                cls.agent_native_context_max_chars,
                dotenv,
            ),
            agent_native_context_keep_messages=_int_env(
                "AGENT_NATIVE_CONTEXT_KEEP_MESSAGES",
                cls.agent_native_context_keep_messages,
                dotenv,
            ),
            agent_tool_result_keep_recent=_int_env(
                "AGENT_TOOL_RESULT_KEEP_RECENT",
                cls.agent_tool_result_keep_recent,
                dotenv,
            ),
            agent_native_max_compactions=_int_env(
                "AGENT_NATIVE_MAX_COMPACTIONS",
                cls.agent_native_max_compactions,
                dotenv,
            ),
            agent_plan_max_output_tokens=_int_env(
                "AGENT_PLAN_MAX_OUTPUT_TOKENS",
                cls.agent_plan_max_output_tokens,
                dotenv,
            ),
            agent_mutation_max_output_tokens=_int_env(
                "AGENT_MUTATION_MAX_OUTPUT_TOKENS",
                cls.agent_mutation_max_output_tokens,
                dotenv,
            ),
            agent_final_max_output_tokens=_int_env(
                "AGENT_FINAL_MAX_OUTPUT_TOKENS",
                cls.agent_final_max_output_tokens,
                dotenv,
            ),
            agent_tool_result_max_tokens=_int_env(
                "AGENT_TOOL_RESULT_MAX_TOKENS",
                cls.agent_tool_result_max_tokens,
                dotenv,
            ),
            agent_snip_enabled=_bool_env(
                "AGENT_SNIP_ENABLED", cls.agent_snip_enabled, dotenv
            ),
            agent_snip_pressure_ratio=_float_env(
                "AGENT_SNIP_PRESSURE_RATIO", cls.agent_snip_pressure_ratio, dotenv
            ),
            agent_snip_keep_recent_groups=_int_env(
                "AGENT_SNIP_KEEP_RECENT_GROUPS", cls.agent_snip_keep_recent_groups, dotenv
            ),
            agent_micro_compact_idle_seconds=_int_env(
                "AGENT_MICRO_COMPACT_IDLE_SECONDS", cls.agent_micro_compact_idle_seconds, dotenv
            ),
            agent_micro_compact_keep_recent_results=_int_env(
                "AGENT_MICRO_COMPACT_KEEP_RECENT_RESULTS", cls.agent_micro_compact_keep_recent_results, dotenv
            ),
            agent_compaction_max_output_tokens=_int_env(
                "AGENT_COMPACTION_MAX_OUTPUT_TOKENS", cls.agent_compaction_max_output_tokens, dotenv
            ),
            agent_compaction_safety_buffer_tokens=_int_env(
                "AGENT_COMPACTION_SAFETY_BUFFER_TOKENS", cls.agent_compaction_safety_buffer_tokens, dotenv
            ),
            agent_compaction_min_reclaimable_tokens=_int_env(
                "AGENT_COMPACTION_MIN_RECLAIMABLE_TOKENS", cls.agent_compaction_min_reclaimable_tokens, dotenv
            ),
            agent_graph_recursion_limit=_int_env(
                "AGENT_GRAPH_RECURSION_LIMIT",
                cls.agent_graph_recursion_limit,
                dotenv,
            ),
            agent_approval_policy=_env(
                "AGENT_APPROVAL_POLICY",
                cls.agent_approval_policy,
                dotenv,
            ),
            live_workspace_writes_enabled=_bool_env(
                "LIVE_WORKSPACE_WRITES_ENABLED",
                cls.live_workspace_writes_enabled,
                dotenv,
            ),
            change_set_apply_mode=_env(
                "CHANGE_SET_APPLY_MODE",
                cls.change_set_apply_mode,
                dotenv,
            ),
            change_set_max_files=_int_env(
                "CHANGE_SET_MAX_FILES",
                cls.change_set_max_files,
                dotenv,
            ),
            change_set_max_patch_chars=_int_env(
                "CHANGE_SET_MAX_PATCH_CHARS",
                cls.change_set_max_patch_chars,
                dotenv,
            ),
            change_set_worktree_parent=_env(
                "CHANGE_SET_WORKTREE_PARENT",
                None,
                dotenv,
            ),
            change_set_branch_prefix=_env(
                "CHANGE_SET_BRANCH_PREFIX",
                cls.change_set_branch_prefix,
                dotenv,
            ),
            auth_mode=_env("AUTH_MODE", cls.auth_mode, dotenv),
            single_user_id=_env("SINGLE_USER_ID", cls.single_user_id, dotenv),
            native_directory_picker_mode=_env(
                "NATIVE_DIRECTORY_PICKER_MODE",
                cls.native_directory_picker_mode,
                dotenv,
            ),
            gateway_trust_secret=_env("GATEWAY_TRUST_SECRET", None, dotenv),
        )

    def _validate_distributed_task_storage(self) -> None:
        required_values = {
            "session_repository": (self.session_repository, "postgres"),
            "agent_run_store": (self.agent_run_store, "postgres"),
            "change_set_store": (self.change_set_store, "postgres"),
            "document_store": (self.document_store, "postgres"),
            "workspace_store": (
                self.workspace_store,
                "postgres",
            ),
            "langgraph_checkpointer": (
                self.langgraph_checkpointer,
                "postgres",
            ),
            "rag_vector_store": (self.rag_vector_store, "qdrant"),
        }
        invalid = [
            f"{name}={actual} (expected {expected})"
            for name, (actual, expected) in required_values.items()
            if actual != expected
        ]
        if invalid:
            raise ValueError(
                "celery task queue requires shared storage: " + ", ".join(invalid)
            )


def _validate_permission_selection(
    name: str,
    selected: tuple[str, ...] | None,
    allowed: tuple[str, ...] | None,
) -> None:
    if selected is not None and any(not item.strip() for item in selected):
        raise ValueError(f"{name} must not contain blank names")
    if allowed is not None and any(not item.strip() for item in allowed):
        raise ValueError(f"{name} process allowlist must not contain blank names")
    if selected is not None and allowed is not None:
        denied = set(selected).difference(allowed)
        if denied:
            raise ValueError(f"{name} cannot widen its process-level allowlist")


def _require_choice(name: str, value: str, allowed: set[str]) -> None:
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{name} must be one of: {choices}")


def _require_positive(name: str, value: float | int) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0")


def _validate_model_catalog_json(value: str) -> None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("llm_model_catalog_json must be valid JSON") from exc
    if not isinstance(parsed, list) or not parsed:
        raise ValueError(
            "llm_model_catalog_json must be a non-empty JSON array"
        )
    if not all(isinstance(item, dict) for item in parsed):
        raise ValueError("each model catalog entry must be an object")


def parse_llm_retry_policy_json(value: str | None) -> dict[str, int]:
    """Parse the strict error-code retry override map used by the model gateway."""

    if value is None or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("llm_retry_policy_json must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("llm_retry_policy_json must be a JSON object")
    unknown = set(parsed).difference(LLM_RETRY_POLICY_KEYS)
    if unknown:
        raise ValueError(
            "llm_retry_policy_json contains unsupported keys: "
            + ", ".join(sorted(str(item) for item in unknown))
        )
    invalid = [
        key
        for key, retries in parsed.items()
        if not isinstance(retries, int)
        or isinstance(retries, bool)
        or retries < 0
    ]
    if invalid:
        raise ValueError(
            "llm_retry_policy_json values must be non-negative integers: "
            + ", ".join(sorted(invalid))
        )
    return {str(key): int(retries) for key, retries in parsed.items()}


def _env(name: str, default: str | None, dotenv: dict[str, str]) -> str | None:
    return os.getenv(name, dotenv.get(name, default))


def _int_env(name: str, default: int, dotenv: dict[str, str]) -> int:
    value = _env(name, None, dotenv)
    return int(value) if value is not None else default


def _float_env(name: str, default: float, dotenv: dict[str, str]) -> float:
    value = _env(name, None, dotenv)
    return float(value) if value is not None else default


def _bool_env(name: str, default: bool, dotenv: dict[str, str]) -> bool:
    value = _env(name, None, dotenv)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _paths_env(
    name: str,
    default: tuple[str, ...],
    dotenv: dict[str, str],
) -> tuple[str, ...]:
    value = _env(name, None, dotenv)
    if value is None:
        return default
    parsed = tuple(item.strip() for item in value.split(os.pathsep) if item.strip())
    return parsed or default


def _csv_env(
    name: str,
    default: tuple[str, ...],
    dotenv: dict[str, str],
) -> tuple[str, ...]:
    value = _env(name, None, dotenv)
    if value is None:
        return default
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _load_dotenv(path: str = ".env") -> dict[str, str]:
    dotenv_path = Path(path)
    if not dotenv_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip("\"'")
        if name:
            values[name] = value
    return values
