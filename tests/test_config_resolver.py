import json
import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from ai_agent_platform.core import (
    ConfigResolver,
    ConfigSchemaError,
    ConfigSecurityError,
    ConfigSource,
    ResolvedConfig,
    Settings,
)


class ConfigResolverTests(unittest.TestCase):
    def test_default_env_template_selects_local_profile(self) -> None:
        profile = Path(__file__).resolve().parents[1] / ".env.example"

        settings = ConfigResolver.from_default_locations(
            env={"AI_AGENT_PLATFORM_USER_CONFIG": "/tmp/missing-user-config.json"},
            dotenv_path=profile,
        ).resolve_process().settings

        self.assertEqual(settings.runtime_profile, "local")
        self.assertEqual(settings.session_repository, "sqlite")
        self.assertEqual(settings.task_queue_backend, "in_process")

    def test_production_env_template_selects_shared_backends(self) -> None:
        profile = Path(__file__).resolve().parents[1] / ".env.production.example"

        settings = ConfigResolver.from_default_locations(
            env={"AI_AGENT_PLATFORM_USER_CONFIG": "/tmp/missing-user-config.json"},
            dotenv_path=profile,
        ).resolve_process().settings

        self.assertEqual(settings.runtime_profile, "production")
        self.assertEqual(settings.session_repository, "postgres")
        self.assertEqual(settings.rag_vector_store, "qdrant")
        self.assertEqual(settings.task_queue_backend, "celery")

    def test_local_memory_profile_is_complete_and_enables_review_mode(self) -> None:
        profile = Path(__file__).resolve().parents[1] / ".env.local-memory.example"

        resolved = ConfigResolver.from_default_locations(
            env={"AI_AGENT_PLATFORM_USER_CONFIG": "/tmp/missing-user-config.json"},
            dotenv_path=profile,
        ).resolve_process()
        settings = resolved.settings

        self.assertEqual(settings.runtime_profile, "local")
        self.assertEqual(settings.session_repository, "sqlite")
        self.assertEqual(settings.agent_run_store, "sqlite")
        self.assertEqual(settings.workspace_store, "sqlite")
        self.assertEqual(settings.project_memory_store, "sqlite")
        self.assertEqual(settings.project_memory_vector_store, "sqlite")
        self.assertTrue(settings.project_memory_enabled)
        self.assertEqual(settings.project_memory_mode, "review")
        self.assertTrue(settings.user_memory_enabled)
        self.assertEqual(settings.user_memory_mode, "review")
        self.assertEqual(settings.task_queue_backend, "in_process")
        self.assertEqual(settings.model_registry_store, "memory")
        self.assertEqual(settings.change_set_store, "memory")

    def test_local_profile_expands_single_process_defaults(self) -> None:
        resolved = ConfigResolver(
            env={"RUNTIME_PROFILE": "local"},
        ).resolve_process()
        settings = resolved.settings

        self.assertEqual(settings.runtime_profile, "local")
        self.assertEqual(settings.session_repository, "sqlite")
        self.assertEqual(settings.agent_run_store, "sqlite")
        self.assertEqual(settings.workspace_store, "sqlite")
        self.assertEqual(settings.project_memory_store, "sqlite")
        self.assertEqual(settings.project_memory_vector_store, "sqlite")
        self.assertEqual(settings.change_set_store, "memory")
        self.assertEqual(settings.document_store, "memory")
        self.assertEqual(settings.model_registry_store, "memory")
        self.assertEqual(settings.langgraph_checkpointer, "memory")
        self.assertEqual(settings.rag_vector_store, "memory")
        self.assertEqual(settings.task_queue_backend, "in_process")
        self.assertTrue(settings.project_memory_enabled)
        self.assertTrue(settings.user_memory_enabled)
        self.assertEqual(
            resolved.provenance_for("session_repository").detail,
            "environment:RUNTIME_PROFILE -> runtime_profile=local",
        )

    def test_production_profile_expands_shared_worker_defaults(self) -> None:
        settings = ConfigResolver(
            env={"RUNTIME_PROFILE": "production"},
        ).resolve_process().settings

        self.assertEqual(settings.runtime_profile, "production")
        self.assertEqual(settings.session_repository, "postgres")
        self.assertEqual(settings.agent_run_store, "postgres")
        self.assertEqual(settings.change_set_store, "postgres")
        self.assertEqual(settings.document_store, "postgres")
        self.assertEqual(settings.workspace_store, "postgres")
        self.assertEqual(settings.model_registry_store, "postgres")
        self.assertEqual(settings.langgraph_checkpointer, "postgres")
        self.assertEqual(settings.rag_vector_store, "qdrant")
        self.assertEqual(settings.project_memory_store, "postgres")
        self.assertEqual(settings.project_memory_vector_store, "qdrant")
        self.assertEqual(settings.task_queue_backend, "celery")
        self.assertFalse(settings.project_memory_enabled)
        self.assertFalse(settings.user_memory_enabled)

    def test_named_profile_rejects_manual_backend_mixing(self) -> None:
        with self.assertRaisesRegex(
            ConfigSchemaError,
            "runtime_profile=local has incompatible backends",
        ):
            ConfigResolver(
                env={
                    "RUNTIME_PROFILE": "local",
                    "SESSION_REPOSITORY": "postgres",
                }
            ).resolve_process()

        mixed = ConfigResolver(
            env={
                "RUNTIME_PROFILE": "custom",
                "SESSION_REPOSITORY": "postgres",
                "AGENT_RUN_STORE": "postgres",
            }
        ).resolve_process().settings
        self.assertEqual(mixed.runtime_profile, "custom")
        self.assertEqual(mixed.session_repository, "postgres")

    def test_rejects_unknown_runtime_profile_from_environment(self) -> None:
        with self.assertRaisesRegex(ConfigSchemaError, "runtime_profile"):
            ConfigResolver(
                env={"RUNTIME_PROFILE": "staging"},
            ).resolve_process()

    def test_process_resolution_ignores_service_cwd_project_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service_cwd = root / "service"
            workspace = root / "workspace"
            for item in (service_cwd, workspace):
                (item / ".ai-agent-platform").mkdir(parents=True)
            (service_cwd / ".ai-agent-platform" / "config.json").write_text(
                json.dumps(
                    {"project_session": {"enabled_tools": ["cwd.tool"]}}
                ),
                encoding="utf-8",
            )
            (workspace / ".ai-agent-platform" / "config.json").write_text(
                json.dumps(
                    {
                        "project_session": {
                            "enabled_tools": ["workspace.tool"],
                            "project_instructions": ["workspace rules"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            previous_cwd = Path.cwd()
            os.chdir(service_cwd)
            try:
                process = ConfigResolver.from_default_locations(
                    env={
                        "AI_AGENT_PLATFORM_USER_CONFIG": str(root / "missing.json")
                    },
                    dotenv_path=root / "missing.env",
                ).resolve_process()
            finally:
                os.chdir(previous_cwd)

            self.assertIsNone(process.enabled_tools)
            self.assertEqual(process.project_instructions, ())
            resolved = ConfigResolver.resolve_workspace(
                process,
                workspace_root=workspace,
            )
            self.assertEqual(resolved.enabled_tools, ("workspace.tool",))
            self.assertEqual(resolved.project_instructions, ("workspace rules",))
            self.assertEqual(
                resolved.provenance_for("enabled_tools").detail,
                "workspace:.ai-agent-platform/config.json",
            )

    def test_explicit_project_config_selector_is_process_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project_path = root / "controlled.json"
            project_path.write_text(
                json.dumps(
                    {"project_session": {"enabled_tools": ["controlled.tool"]}}
                ),
                encoding="utf-8",
            )
            resolved = ConfigResolver.from_default_locations(
                env={
                    "AI_AGENT_PLATFORM_PROJECT_CONFIG": str(project_path),
                    "AI_AGENT_PLATFORM_USER_CONFIG": str(root / "missing.json"),
                },
                dotenv_path=root / "missing.env",
            ).resolve_process()
        self.assertEqual(resolved.enabled_tools, ("controlled.tool",))

    def test_merges_all_layers_in_order_and_tracks_every_field(self) -> None:
        resolver = ConfigResolver(
            user_config={
                "runtime": {
                    "llm_provider": "openai",
                    "llm_max_output_tokens": 1000,
                }
            },
            project_config={
                "runtime": {
                    "agent_max_elapsed_seconds": 120,
                    "llm_max_output_tokens": 2000,
                }
            },
            env={
                "LLM_MODEL": "env-model",
                "LLM_MAX_OUTPUT_TOKENS": "3000",
            },
            explicit_overrides={
                "runtime": {
                    "llm_thinking_level": "medium",
                    "llm_max_output_tokens": 4000,
                }
            },
        )

        resolved = resolver.resolve()

        self.assertIsInstance(resolved, ResolvedConfig)
        self.assertEqual(resolved.llm_max_output_tokens, 4000)
        self.assertEqual(resolved.runtime.llm_max_output_tokens, 4000)
        self.assertEqual(resolved.process_security.api_prefix, "/api/v1")
        self.assertEqual(resolved.project_session.project_instructions, ())
        self.assertEqual(
            resolved.source_for("api_prefix"),
            ConfigSource.DEFAULT,
        )
        self.assertEqual(
            resolved.source_for("llm_provider"),
            ConfigSource.USER_CONFIG,
        )
        self.assertEqual(
            resolved.source_for("agent_max_elapsed_seconds"),
            ConfigSource.PROJECT_CONFIG,
        )
        self.assertEqual(
            resolved.source_for("llm_model"),
            ConfigSource.ENVIRONMENT,
        )
        self.assertEqual(
            resolved.source_for("llm_thinking_level"),
            ConfigSource.EXPLICIT_OVERRIDE,
        )
        self.assertEqual(
            set(resolved.sources),
            set(Settings.__dataclass_fields__),
        )

    def test_resolved_config_and_nested_mappings_are_immutable(self) -> None:
        resolved = ConfigResolver(env={}).resolve()

        with self.assertRaises(FrozenInstanceError):
            resolved.settings.llm_model = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            resolved.sources["llm_model"] = ConfigSource.USER_CONFIG  # type: ignore[index]
        with self.assertRaises(TypeError):
            resolved.runtime.values["llm_model"] = "changed"  # type: ignore[index]
        with self.assertRaises(FrozenInstanceError):
            resolved.runtime.extra = "changed"  # type: ignore[misc]

    def test_reads_json_files_and_environment_beats_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            user_path = root / "user.json"
            project_path = root / "project.json"
            dotenv_path = root / ".env"
            user_path.write_text(
                json.dumps({"runtime": {"llm_model": "user-model"}}),
                encoding="utf-8",
            )
            project_path.write_text(
                json.dumps({"runtime": {"llm_model": "project-model"}}),
                encoding="utf-8",
            )
            dotenv_path.write_text(
                "AI_AGENT_PLATFORM_LLM_MODEL=dotenv-model\n",
                encoding="utf-8",
            )

            resolved = ConfigResolver(
                user_config=user_path,
                project_config=project_path,
                dotenv_path=dotenv_path,
                env={"LLM_MODEL": "environment-model"},
            ).resolve()

        self.assertEqual(resolved.llm_model, "environment-model")
        self.assertEqual(resolved.source_for("llm_model"), ConfigSource.ENVIRONMENT)
        self.assertEqual(
            resolved.provenance_for("llm_model").detail,
            "environment:LLM_MODEL",
        )

    def test_each_source_rejects_unknown_fields_and_wrong_types(self) -> None:
        cases = (
            (
                ConfigResolver(user_config={"unknown": {"llm_model": "x"}}, env={}),
                "unknown root fields",
            ),
            (
                ConfigResolver(
                    project_config={"runtime": {"database_url": "x"}},
                    env={},
                ),
                "unknown fields",
            ),
            (
                ConfigResolver(
                    explicit_overrides={"runtime": {"missing_field": True}},
                    env={},
                ),
                "unknown fields",
            ),
            (
                ConfigResolver(
                    user_config={"runtime": {"llm_max_retries": "two"}},
                    env={},
                ),
                "must be an integer",
            ),
            (
                ConfigResolver(env={"AI_AGENT_PLATFORM_UNKNOWN_SETTING": "x"}),
                "environment has unknown fields",
            ),
            (
                ConfigResolver(env={"LLM_MAX_RETRIES": "two"}),
                "must be an integer",
            ),
        )
        for resolver, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                ConfigSchemaError,
                message,
            ):
                resolver.resolve()

    def test_project_cannot_override_process_or_security_fields(self) -> None:
        fields_and_values = {
            "database_url": "postgresql://example.invalid/db",
            "auth_mode": "trusted_header",
            "model_secret_backend": "memory",
            "rag_vector_store": "qdrant",
            "workspace_allowed_roots": ["/tmp"],
            "live_workspace_writes_enabled": True,
            "gateway_trust_secret": "do-not-log-this",
            "sandbox_docker_image": "untrusted/image:latest",
        }
        for field_name, value in fields_and_values.items():
            with self.subTest(field=field_name), self.assertRaisesRegex(
                ConfigSecurityError,
                field_name,
            ):
                ConfigResolver(
                    project_config={"process_security": {field_name: value}},
                    env={},
                ).resolve()

    def test_project_can_tighten_but_cannot_weaken_sandbox_policy(self) -> None:
        tightened = ConfigResolver(
            user_config={
                "runtime": {
                    "sandbox_mode": "local",
                    "sandbox_allowed_commands": ["python", "pytest"],
                    "agent_approval_policy": "on_request",
                }
            },
            project_config={
                "runtime": {
                    "sandbox_mode": "docker",
                    "sandbox_allowed_commands": ["pytest"],
                    "agent_approval_policy": "always",
                }
            },
            env={},
        ).resolve()
        self.assertEqual(tightened.sandbox_mode, "docker")
        self.assertEqual(tightened.sandbox_allowed_commands, ("pytest",))
        self.assertEqual(tightened.agent_approval_policy, "always")

        weakening_cases = (
            (
                {"runtime": {"sandbox_mode": "docker"}},
                {"runtime": {"sandbox_mode": "local"}},
                "sandbox_mode",
            ),
            (
                {"runtime": {"sandbox_allowed_commands": ["python"]}},
                {
                    "runtime": {
                        "sandbox_allowed_commands": ["python", "pytest"]
                    }
                },
                "may only remove commands",
            ),
            (
                {"runtime": {"agent_approval_policy": "always"}},
                {"runtime": {"agent_approval_policy": "never"}},
                "tighten permission",
            ),
            (
                {"runtime": {"agent_approval_policy": "never"}},
                {"runtime": {"agent_approval_policy": "on_request"}},
                "tighten permission",
            ),
        )
        for user_config, project_config, message in weakening_cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                ConfigSecurityError,
                message,
            ):
                ConfigResolver(
                    user_config=user_config,
                    project_config=project_config,
                    env={},
                ).resolve()

        deny_asks = ConfigResolver(
            user_config={
                "runtime": {"agent_approval_policy": "on_request"}
            },
            project_config={
                "runtime": {"agent_approval_policy": "never"}
            },
            env={},
        ).resolve()
        self.assertEqual(deny_asks.agent_approval_policy, "never")

    def test_process_denies_mcp_skills_and_tool_expansion(self) -> None:
        cases = (
            (
                {
                    "process_security": {
                        "mcp_allowed": False,
                        "mcp_config_path": "mcp.json",
                    }
                },
                {"project_session": {"mcp_enabled": True}},
                "mcp_allowed=false",
            ),
            (
                {"process_security": {"skills_allowed": False}},
                {"project_session": {"skills_enabled": True}},
                "skills_allowed=false",
            ),
            (
                {"process_security": {"tool_allowlist": ["read_file"]}},
                {"project_session": {"enabled_tools": ["write_file"]}},
                "process allowlist",
            ),
            (
                {"process_security": {"skill_allowlist": ["review"]}},
                {"project_session": {"enabled_skills": ["deploy"]}},
                "process allowlist",
            ),
        )
        for user_config, project_config, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(
                ConfigSecurityError,
                message,
            ):
                ConfigResolver(
                    user_config=user_config,
                    project_config=project_config,
                    env={},
                ).resolve()

        resolved = ConfigResolver(
            user_config={
                "process_security": {
                    "mcp_allowed": True,
                    "mcp_config_path": "mcp.json",
                    "skills_allowed": True,
                    "tool_allowlist": ["read_file", "search"],
                    "skill_allowlist": ["review", "test"],
                }
            },
            project_config={
                "project_session": {
                    "mcp_enabled": True,
                    "skills_enabled": True,
                    "enabled_tools": ["read_file"],
                    "enabled_skills": ["test"],
                    "project_instructions": ["Run focused tests."],
                }
            },
            env={},
        ).resolve()
        self.assertTrue(resolved.project_session.mcp_enabled)
        self.assertEqual(resolved.enabled_tools, ("read_file",))
        self.assertEqual(resolved.enabled_skills, ("test",))

    def test_safe_snapshot_and_reprs_do_not_expose_secrets(self) -> None:
        secrets = {
            "database_url": "postgresql://alice:db-password@db.local/app",
            "redis_url": "redis://:redis-password@cache.local/0",
            "qdrant_url": "https://qdrant.local/collections?token=query-secret",
            "openai_api_key": "openai-secret-value",
            "qdrant_api_key": "qdrant-secret-value",
            "gateway_trust_secret": "gateway-secret-value",
        }
        resolved = ConfigResolver(
            user_config={
                "process_security": {
                    **secrets,
                    "auth_mode": "trusted_header",
                }
            },
            env={},
        ).resolve()

        serialized = json.dumps(resolved.safe_snapshot(), sort_keys=True)
        combined_repr = repr(resolved) + repr(resolved.settings)
        for secret in (
            "db-password",
            "redis-password",
            "query-secret",
            "openai-secret-value",
            "qdrant-secret-value",
            "gateway-secret-value",
        ):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, serialized)
                self.assertNotIn(secret, combined_repr)
        self.assertIn("***REDACTED***", serialized)
        self.assertIn("postgresql://***:***@db.local/app", serialized)

    def test_errors_do_not_echo_secret_values(self) -> None:
        secret = "should-never-appear-in-error"
        with self.assertRaises(ConfigSchemaError) as raised:
            ConfigResolver(
                user_config={
                    "process_security": {
                        "openai_api_key": secret,
                        "unknown_secret_field": secret,
                    }
                },
                env={},
            ).resolve()
        self.assertNotIn(secret, str(raised.exception))

    def test_legacy_environment_aliases_and_namespaced_variables_work(self) -> None:
        resolved = ConfigResolver(
            env={
                "GEMINI_API_KEY": "legacy-google-key",
                "SESSION_REPOSITORY": "postgres",
                "AGENT_RUN_STORE": "postgres",
                "AI_AGENT_PLATFORM_LLM_MODEL": "namespaced-model",
                "UNRELATED_APPLICATION_SETTING": "ignored",
            }
        ).resolve()

        self.assertEqual(resolved.google_api_key, "legacy-google-key")
        self.assertEqual(resolved.model_registry_store, "postgres")
        self.assertEqual(resolved.change_set_store, "postgres")
        self.assertEqual(resolved.llm_model, "namespaced-model")
        self.assertIn(
            "legacy fallback",
            resolved.provenance_for("google_api_key").detail,
        )

        with patch.dict(
            os.environ,
            {
                "GOOGLE_API_KEY": "canonical-key",
                "GEMINI_API_KEY": "legacy-key",
            },
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.google_api_key, "canonical-key")

    def test_new_workspace_mode_config_wins_over_legacy_environment_fallback(self) -> None:
        resolved = ConfigResolver(
            user_config={
                "process_security": {
                    "agent_workspace_default_mode": "patch_only",
                    "agent_workspace_allowed_modes": ["patch_only"],
                }
            },
            env={"CHANGE_SET_APPLY_MODE": "direct"},
        ).resolve_process()

        self.assertEqual(resolved.agent_workspace_default_mode, "patch_only")
        self.assertEqual(
            resolved.agent_workspace_allowed_modes,
            ("patch_only",),
        )
        self.assertEqual(
            resolved.source_for("agent_workspace_default_mode"),
            ConfigSource.USER_CONFIG,
        )

    def test_explicit_flat_overrides_remain_available_for_entry_points(self) -> None:
        resolved = ConfigResolver(env={}).resolve(
            explicit_overrides={"llm_model": "entry-model"}
        )

        self.assertEqual(resolved.llm_model, "entry-model")
        self.assertEqual(
            resolved.source_for("llm_model"),
            ConfigSource.EXPLICIT_OVERRIDE,
        )


if __name__ == "__main__":
    unittest.main()
