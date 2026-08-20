"""Layered, provenance-aware runtime configuration resolution.

The resolver deliberately uses JSON and the Python standard library only. ``Settings``
remains the compatibility value object consumed by the existing runtime, while this
module owns source schemas, security policy and redacted diagnostics.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from enum import Enum
import json
import os
from pathlib import Path
import stat
from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import Settings, runtime_profile_defaults


CONFIG_SNAPSHOT_SCHEMA_VERSION = 2
WORKSPACE_PROJECT_CONFIG_RELATIVE_PATH = ".ai-agent-platform/config.json"


class ConfigError(ValueError):
    """Base error for layered configuration failures."""


class ConfigSchemaError(ConfigError):
    """A configuration source does not match its declared schema."""


class ConfigSecurityError(ConfigError):
    """A project source attempted to widen a trusted process policy."""


class ConfigSource(str, Enum):
    DEFAULT = "default"
    USER_CONFIG = "user_config"
    PROJECT_CONFIG = "project_config"
    ENVIRONMENT = "environment"
    EXPLICIT_OVERRIDE = "explicit_override"


@dataclass(frozen=True)
class ConfigFieldSource:
    source: ConfigSource
    detail: str


@dataclass(frozen=True)
class _ConfigSection:
    _values: Mapping[str, Any] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_values", MappingProxyType(dict(self._values)))

    def __getattr__(self, name: str) -> Any:
        try:
            return self._values[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __getitem__(self, name: str) -> Any:
        return self._values[name]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    @property
    def values(self) -> Mapping[str, Any]:
        return self._values

    def __repr__(self) -> str:
        names = ", ".join(sorted(self._values))
        return f"{type(self).__name__}(fields=[{names}])"


@dataclass(frozen=True)
class ProcessSecurityConfig(_ConfigSection):
    """Process-owned infrastructure, authentication and security policy."""


@dataclass(frozen=True)
class RuntimeConfig(_ConfigSection):
    """Model, budget, Agent Loop and retrieval runtime settings."""


@dataclass(frozen=True)
class ProjectSessionConfig(_ConfigSection):
    """Project instructions and project/session tool integration selection."""


@dataclass(frozen=True)
class ResolvedConfig:
    """Immutable resolved values, sections and per-field provenance."""

    settings: Settings = field(repr=False)
    process_security: ProcessSecurityConfig = field(repr=False)
    runtime: RuntimeConfig = field(repr=False)
    project_session: ProjectSessionConfig = field(repr=False)
    sources: Mapping[str, ConfigSource] = field(repr=False)
    source_details: Mapping[str, ConfigFieldSource] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", MappingProxyType(dict(self.sources)))
        object.__setattr__(
            self,
            "source_details",
            MappingProxyType(dict(self.source_details)),
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        source: ConfigSource = ConfigSource.EXPLICIT_OVERRIDE,
        detail: str = "injected Settings",
    ) -> "ResolvedConfig":
        provenance = {
            item.name: ConfigFieldSource(source, detail) for item in fields(Settings)
        }
        return _resolved_config(settings, provenance)

    def __getattr__(self, name: str) -> Any:
        """Allow a gradual migration from ``Settings`` to ``ResolvedConfig``."""
        try:
            return getattr(self.settings, name)
        except AttributeError as exc:
            raise AttributeError(name) from exc

    def source_for(self, field_name: str) -> ConfigSource:
        try:
            return self.sources[field_name]
        except KeyError as exc:
            raise KeyError(f"unknown Settings field: {field_name}") from exc

    def provenance_for(self, field_name: str) -> ConfigFieldSource:
        try:
            return self.source_details[field_name]
        except KeyError as exc:
            raise KeyError(f"unknown Settings field: {field_name}") from exc

    def safe_snapshot(self) -> dict[str, object]:
        """Return a redacted representation safe for logs and run snapshots."""
        sections: dict[str, dict[str, object]] = {}
        for section_name, section in (
            ("process_security", self.process_security),
            ("runtime", self.runtime),
            ("project_session", self.project_session),
        ):
            section_values: dict[str, object] = {}
            for name, value in section.values.items():
                section_values[name] = {
                    "value": _redact_value(name, value),
                    "source": self.sources[name].value,
                    "detail": self.source_details[name].detail,
                }
            sections[section_name] = section_values
        return {
            "schema_version": CONFIG_SNAPSHOT_SCHEMA_VERSION,
            "config": sections,
        }

    def diagnostics(self) -> dict[str, object]:
        """Alias emphasizing that diagnostics are always redacted."""
        return self.safe_snapshot()

    def __repr__(self) -> str:
        counts = {
            source.value: sum(1 for item in self.sources.values() if item == source)
            for source in ConfigSource
        }
        return f"ResolvedConfig(source_counts={counts!r})"


ConfigInput = Mapping[str, object] | str | Path | None


_ROOT_SECTIONS = frozenset({"process_security", "runtime", "project_session"})

# These fields establish process boundaries or may contain credentials/connection
# strings. A project file may not set them, even if it would happen to choose the
# same value.
PROCESS_SECURITY_FIELDS = frozenset(
    {
        "app_name",
        "api_prefix",
        "log_level",
        "log_format",
        "database_url",
        "runtime_profile",
        "local_state_path",
        "session_repository",
        "agent_run_store",
        "change_set_store",
        "document_store",
        "workspace_store",
        "workspace_allowed_roots",
        "langgraph_checkpointer",
        "model_registry_store",
        "model_secret_backend",
        "chroma_persist_directory",
        "chroma_collection_name",
        "rag_vector_store",
        "qdrant_url",
        "qdrant_api_key",
        "qdrant_collection_name",
        "project_memory_qdrant_collection",
        "project_memory_store",
        "project_memory_vector_store",
        "user_memory_enabled",
        "user_memory_mode",
        "user_profile_max_context_chars",
        "task_queue_backend",
        "redis_url",
        "celery_result_backend_url",
        "celery_visibility_timeout_seconds",
        "celery_task_max_retries",
        "celery_task_retry_backoff_seconds",
        "celery_task_retry_backoff_max_seconds",
        "celery_task_soft_time_limit_seconds",
        "celery_task_time_limit_seconds",
        "celery_result_expires_seconds",
        "celery_worker_max_tasks_per_child",
        "background_task_workers",
        "background_task_queue_capacity",
        "mcp_allowed",
        "mcp_config_path",
        "skills_allowed",
        "tool_allowlist",
        "skill_allowlist",
        "sandbox_mode",
        "sandbox_docker_image",
        "sandbox_command_timeout_seconds",
        "sandbox_command_output_max_chars",
        "sandbox_workspace_parent",
        "sandbox_workspace_ttl_seconds",
        "sandbox_allowed_commands",
        "live_workspace_writes_enabled",
        "agent_workspace_default_mode",
        "agent_workspace_allowed_modes",
        "change_set_apply_mode",
        "change_set_worktree_parent",
        "change_set_branch_prefix",
        "auth_mode",
        "single_user_id",
        "native_directory_picker_mode",
        "gateway_trust_secret",
    }
)

PROJECT_SESSION_FIELDS = frozenset(
    {
        "project_instructions",
        "enabled_tools",
        "skills_enabled",
        "enabled_skills",
        "mcp_enabled",
    }
)

_SETTINGS_FIELDS = frozenset(item.name for item in fields(Settings))
RUNTIME_FIELDS = _SETTINGS_FIELDS.difference(
    PROCESS_SECURITY_FIELDS | PROJECT_SESSION_FIELDS
)

if PROCESS_SECURITY_FIELDS.difference(_SETTINGS_FIELDS):  # pragma: no cover
    raise RuntimeError("process/security field registry contains unknown Settings fields")

_SECTION_FIELDS = {
    "process_security": PROCESS_SECURITY_FIELDS,
    "runtime": RUNTIME_FIELDS,
    "project_session": PROJECT_SESSION_FIELDS,
}

_TUPLE_FIELDS = frozenset(
    {
        "workspace_allowed_roots",
        "sandbox_allowed_commands",
        "project_instructions",
        "agent_workspace_allowed_modes",
    }
)
_OPTIONAL_TUPLE_FIELDS = frozenset(
    {"tool_allowlist", "skill_allowlist", "enabled_tools", "enabled_skills"}
)

_SECRET_FIELDS = frozenset(
    {
        "qdrant_api_key",
        "gateway_trust_secret",
    }
)
_CONNECTION_FIELDS = frozenset(
    {"database_url", "qdrant_url", "redis_url", "celery_result_backend_url"}
)

_CONFIG_PATH_ENV_NAMES = frozenset(
    {
        "AI_AGENT_PLATFORM_USER_CONFIG",
        "AI_AGENT_PLATFORM_PROJECT_CONFIG",
    }
)
_ENV_PREFIX = "AI_AGENT_PLATFORM_"


class ConfigResolver:
    """Merge defaults, user JSON, project JSON, environment and overrides."""

    def __init__(
        self,
        *,
        user_config: ConfigInput = None,
        project_config: ConfigInput = None,
        env: Mapping[str, str] | None = None,
        explicit_overrides: Mapping[str, object] | None = None,
        dotenv_path: str | Path | None = None,
    ) -> None:
        self._user_config = user_config
        self._project_config = project_config
        self._env = dict(os.environ if env is None else env)
        self._explicit_overrides = explicit_overrides
        self._dotenv_path = Path(dotenv_path) if dotenv_path is not None else None

    @classmethod
    def from_default_locations(
        cls,
        *,
        env: Mapping[str, str] | None = None,
        explicit_overrides: Mapping[str, object] | None = None,
        dotenv_path: str | Path = ".env",
        project_root: str | Path | None = None,
    ) -> "ConfigResolver":
        """Discover process-owned inputs without consulting the service cwd.

        ``AI_AGENT_PLATFORM_PROJECT_CONFIG`` remains a supported, explicit
        process-controlled input. Automatic project discovery only happens later,
        through :meth:`resolve_workspace`, after a registered Workspace root has
        been authorized. ``project_root`` is retained as an explicit compatibility
        input for callers that already possess such a trusted root.
        """
        environment = dict(os.environ if env is None else env)
        dotenv = _read_dotenv(Path(dotenv_path), required=False)
        selectors = {**dotenv, **environment}

        user_candidate = Path(
            selectors.get("AI_AGENT_PLATFORM_USER_CONFIG")
            or str(Path.home() / ".config" / "ai-agent-platform" / "config.json")
        ).expanduser()
        explicit_project_path = selectors.get("AI_AGENT_PLATFORM_PROJECT_CONFIG")
        project_candidate = (
            Path(explicit_project_path).expanduser()
            if explicit_project_path
            else (
                Path(project_root) / WORKSPACE_PROJECT_CONFIG_RELATIVE_PATH
                if project_root is not None
                else None
            )
        )

        return cls(
            user_config=user_candidate if user_candidate.is_file() else None,
            project_config=(
                project_candidate
                if project_candidate is not None and project_candidate.is_file()
                else None
            ),
            env=environment,
            explicit_overrides=explicit_overrides,
            dotenv_path=dotenv_path,
        )

    def resolve(
        self,
        *,
        explicit_overrides: Mapping[str, object] | None = None,
    ) -> ResolvedConfig:
        """Compatibility alias for :meth:`resolve_process`."""
        return self.resolve_process(explicit_overrides=explicit_overrides)

    def resolve_process(
        self,
        *,
        explicit_overrides: Mapping[str, object] | None = None,
    ) -> ResolvedConfig:
        """Resolve the immutable process baseline without Workspace discovery."""
        default_settings = Settings()
        values = {
            item.name: _freeze_value(getattr(default_settings, item.name))
            for item in fields(Settings)
        }
        provenance = {
            name: ConfigFieldSource(ConfigSource.DEFAULT, "Settings default")
            for name in values
        }

        self._merge_config_input(
            values,
            provenance,
            self._user_config,
            source=ConfigSource.USER_CONFIG,
            project=False,
        )
        self._merge_config_input(
            values,
            provenance,
            self._project_config,
            source=ConfigSource.PROJECT_CONFIG,
            project=True,
        )
        self._merge_environment(values, provenance)

        configured_overrides = (
            explicit_overrides
            if explicit_overrides is not None
            else self._explicit_overrides
        )
        if configured_overrides is not None:
            flattened = _flatten_config_mapping(
                configured_overrides,
                source_label=ConfigSource.EXPLICIT_OVERRIDE.value,
                allow_flat=True,
            )
            self._merge_values(
                values,
                provenance,
                flattened,
                source=ConfigSource.EXPLICIT_OVERRIDE,
                detail="explicit entry override",
                project=False,
            )

        try:
            _apply_runtime_profile_defaults(values, provenance)
            settings = Settings(**values)
        except (TypeError, ValueError) as exc:
            raise ConfigSchemaError(f"resolved configuration is invalid: {exc}") from exc

        return _resolved_config(settings, provenance)

    @classmethod
    def resolve_workspace(
        cls,
        process_config: ResolvedConfig,
        *,
        workspace_root: str | Path,
    ) -> ResolvedConfig:
        """Apply the project file under one already-authorized Workspace root.

        The supplied process snapshot is the only baseline. Environment variables,
        dotenv files, user configuration, and explicit overrides are not consulted
        again at Run submission time.
        """
        root = Path(workspace_root).resolve(strict=True)
        if not root.is_dir():
            raise ConfigSecurityError("registered Workspace root is not a directory")
        raw = _load_workspace_project_config(root)
        if raw is None:
            return process_config

        values = {
            item.name: _freeze_value(getattr(process_config.settings, item.name))
            for item in fields(Settings)
        }
        provenance = dict(process_config.source_details)
        flattened = _flatten_config_mapping(
            raw,
            source_label=ConfigSource.PROJECT_CONFIG.value,
            allow_flat=False,
        )
        resolver = cls(env={})
        resolver._merge_values(
            values,
            provenance,
            flattened,
            source=ConfigSource.PROJECT_CONFIG,
            detail=f"workspace:{WORKSPACE_PROJECT_CONFIG_RELATIVE_PATH}",
            project=True,
        )
        try:
            settings = Settings(**values)
        except (TypeError, ValueError) as exc:
            raise ConfigSchemaError(
                f"resolved Workspace configuration is invalid: {exc}"
            ) from exc
        return _resolved_config(settings, provenance)

    def _merge_config_input(
        self,
        values: dict[str, Any],
        provenance: dict[str, ConfigFieldSource],
        config_input: ConfigInput,
        *,
        source: ConfigSource,
        project: bool,
    ) -> None:
        if config_input is None:
            return
        raw, detail = _load_config_input(config_input, source.value)
        flattened = _flatten_config_mapping(
            raw,
            source_label=source.value,
            allow_flat=False,
        )
        self._merge_values(
            values,
            provenance,
            flattened,
            source=source,
            detail=detail,
            project=project,
        )

    def _merge_environment(
        self,
        values: dict[str, Any],
        provenance: dict[str, ConfigFieldSource],
    ) -> None:
        dotenv = (
            _read_dotenv(self._dotenv_path, required=False)
            if self._dotenv_path is not None
            else {}
        )
        _validate_namespaced_environment(dotenv)
        _validate_namespaced_environment(self._env)

        combined = {**dotenv, **self._env}
        environment_values: dict[str, object] = {}
        details: dict[str, str] = {}
        for field_name in _SETTINGS_FIELDS:
            selected = _select_environment_name(field_name, self._env, dotenv)
            if selected is None:
                continue
            selected_name, location = selected
            selected_values = self._env if location == "environment" else dotenv
            environment_values[field_name] = _parse_environment_value(
                field_name,
                selected_values[selected_name],
                values[field_name],
            )
            details[field_name] = f"{location}:{selected_name}"

        # Preserve compatible legacy fallbacks when their canonical name is absent.
        # Store aliases predate the one-field/one-env-name schema, but SQLite is not
        # a valid model-registry or ChangeSet backend and must not leak into them.
        _apply_legacy_environment_fallback(
            environment_values,
            details,
            combined,
            self._env,
            target="model_registry_store",
            canonical="MODEL_REGISTRY_STORE",
            fallback="SESSION_REPOSITORY",
            current=values["model_registry_store"],
            allowed_values=frozenset({"memory", "postgres"}),
        )
        _apply_legacy_environment_fallback(
            environment_values,
            details,
            combined,
            self._env,
            target="change_set_store",
            canonical="CHANGE_SET_STORE",
            fallback="AGENT_RUN_STORE",
            current=values["change_set_store"],
            allowed_values=frozenset({"memory", "postgres"}),
        )
        _apply_legacy_workspace_mode_fallback(
            environment_values,
            details,
            combined,
            self._env,
            provenance=provenance,
        )

        for field_name, raw_value in environment_values.items():
            value = _coerce_native_value(
                field_name,
                raw_value,
                values[field_name],
                source_label=details[field_name],
            )
            values[field_name] = _freeze_value(value)
            provenance[field_name] = ConfigFieldSource(
                ConfigSource.ENVIRONMENT,
                details[field_name],
            )

    def _merge_values(
        self,
        values: dict[str, Any],
        provenance: dict[str, ConfigFieldSource],
        incoming: Mapping[str, object],
        *,
        source: ConfigSource,
        detail: str,
        project: bool,
    ) -> None:
        coerced: dict[str, Any] = {}
        for field_name, raw_value in incoming.items():
            coerced[field_name] = _coerce_native_value(
                field_name,
                raw_value,
                values[field_name],
                source_label=source.value,
            )
        if project:
            _validate_project_overrides(values, coerced)
        for field_name, value in coerced.items():
            values[field_name] = _freeze_value(value)
            provenance[field_name] = ConfigFieldSource(source, detail)


def _load_config_input(
    config_input: ConfigInput,
    source_label: str,
) -> tuple[Mapping[str, object], str]:
    if isinstance(config_input, Mapping):
        return config_input, f"inline {source_label}"

    path = Path(config_input).expanduser()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigSchemaError(f"{source_label} file does not exist: {path}") from exc
    except OSError as exc:
        raise ConfigSchemaError(f"cannot read {source_label} file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigSchemaError(
            f"{source_label} must be valid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    if not isinstance(raw, dict):
        raise ConfigSchemaError(f"{source_label} root must be a JSON object")
    return raw, str(path)


def _load_workspace_project_config(
    workspace_root: Path,
) -> Mapping[str, object] | None:
    """Read the conventional project JSON without following path symlinks."""
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    root_fd: int | None = None
    config_dir_fd: int | None = None
    config_fd: int | None = None
    try:
        root_fd = os.open(workspace_root, directory_flags | nofollow | cloexec)
        try:
            config_dir_fd = os.open(
                ".ai-agent-platform",
                directory_flags | nofollow | cloexec,
                dir_fd=root_fd,
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ConfigSecurityError(
                "Workspace project configuration directory is unsafe"
            ) from exc
        try:
            config_fd = os.open(
                "config.json",
                os.O_RDONLY | nofollow | cloexec | getattr(os, "O_NONBLOCK", 0),
                dir_fd=config_dir_fd,
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ConfigSecurityError(
                "Workspace project configuration file is unsafe"
            ) from exc

        before = os.fstat(config_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ConfigSecurityError(
                "Workspace project configuration must be a regular file"
            )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(config_fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(config_fd)
        current = os.stat(
            "config.json",
            dir_fd=config_dir_fd,
            follow_symlinks=False,
        )
        if not _same_stable_file(before, after, current):
            raise ConfigSecurityError(
                "Workspace project configuration changed while being read"
            )
        try:
            raw = json.loads(b"".join(chunks).decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ConfigSchemaError(
                "project_config must be UTF-8 encoded"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ConfigSchemaError(
                "project_config must be valid JSON at line "
                f"{exc.lineno}, column {exc.colno}"
            ) from exc
        if not isinstance(raw, dict):
            raise ConfigSchemaError("project_config root must be a JSON object")
        return raw
    finally:
        for descriptor in (config_fd, config_dir_fd, root_fd):
            if descriptor is not None:
                os.close(descriptor)


def _same_stable_file(*items: os.stat_result) -> bool:
    first = items[0]
    identity = (first.st_dev, first.st_ino, first.st_mode)
    metadata = (first.st_size, first.st_mtime_ns, first.st_ctime_ns)
    return all(
        (item.st_dev, item.st_ino, item.st_mode) == identity
        and (item.st_size, item.st_mtime_ns, item.st_ctime_ns) == metadata
        for item in items[1:]
    )


def _resolved_config(
    settings: Settings,
    provenance: Mapping[str, ConfigFieldSource],
) -> ResolvedConfig:
    sources = {name: item.source for name, item in provenance.items()}
    return ResolvedConfig(
        settings=settings,
        process_security=ProcessSecurityConfig(
            {name: getattr(settings, name) for name in PROCESS_SECURITY_FIELDS}
        ),
        runtime=RuntimeConfig(
            {name: getattr(settings, name) for name in RUNTIME_FIELDS}
        ),
        project_session=ProjectSessionConfig(
            {name: getattr(settings, name) for name in PROJECT_SESSION_FIELDS}
        ),
        sources=sources,
        source_details=provenance,
    )


def _apply_runtime_profile_defaults(
    values: dict[str, Any],
    provenance: dict[str, ConfigFieldSource],
) -> None:
    profile = str(values["runtime_profile"])
    defaults = runtime_profile_defaults(profile)
    if not defaults:
        return
    profile_source = provenance["runtime_profile"]
    for name, value in defaults.items():
        if provenance[name].source is not ConfigSource.DEFAULT:
            continue
        values[name] = _freeze_value(value)
        provenance[name] = ConfigFieldSource(
            profile_source.source,
            f"{profile_source.detail} -> runtime_profile={profile}",
        )


def _flatten_config_mapping(
    raw: Mapping[str, object],
    *,
    source_label: str,
    allow_flat: bool,
) -> dict[str, object]:
    keys = set(raw)
    if allow_flat and keys and keys.issubset(_SETTINGS_FIELDS):
        return dict(raw)

    unknown_sections = keys.difference(_ROOT_SECTIONS)
    if unknown_sections:
        names = ", ".join(sorted(str(name) for name in unknown_sections))
        raise ConfigSchemaError(f"{source_label} has unknown root fields: {names}")

    flattened: dict[str, object] = {}
    for section_name, payload in raw.items():
        if not isinstance(payload, Mapping):
            raise ConfigSchemaError(
                f"{source_label}.{section_name} must be a JSON object"
            )
        allowed = _SECTION_FIELDS[section_name]
        unknown_fields = set(payload).difference(allowed)
        if unknown_fields:
            names = ", ".join(sorted(str(name) for name in unknown_fields))
            raise ConfigSchemaError(
                f"{source_label}.{section_name} has unknown fields: {names}"
            )
        flattened.update(payload)
    return flattened


def _coerce_native_value(
    field_name: str,
    value: object,
    current: object,
    *,
    source_label: str,
) -> object:
    if field_name in _TUPLE_FIELDS | _OPTIONAL_TUPLE_FIELDS:
        if value is None and field_name in _OPTIONAL_TUPLE_FIELDS:
            return None
        if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
            raise ConfigSchemaError(
                f"{source_label}.{field_name} must be an array of strings"
            )
        if not all(isinstance(item, str) for item in value):
            raise ConfigSchemaError(
                f"{source_label}.{field_name} must contain only strings"
            )
        if field_name in _OPTIONAL_TUPLE_FIELDS and len(set(value)) != len(value):
            raise ConfigSchemaError(
                f"{source_label}.{field_name} must contain unique strings"
            )
        return tuple(value)

    if current is None:
        if value is None or isinstance(value, str):
            return value
        raise ConfigSchemaError(f"{source_label}.{field_name} must be a string or null")
    if isinstance(current, bool):
        if type(value) is bool:
            return value
        raise ConfigSchemaError(f"{source_label}.{field_name} must be a boolean")
    if isinstance(current, int):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        raise ConfigSchemaError(f"{source_label}.{field_name} must be an integer")
    if isinstance(current, float):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        raise ConfigSchemaError(f"{source_label}.{field_name} must be a number")
    if isinstance(current, str):
        if isinstance(value, str):
            return value
        raise ConfigSchemaError(f"{source_label}.{field_name} must be a string")
    raise ConfigSchemaError(f"{source_label}.{field_name} has an unsupported type")


def _parse_environment_value(
    field_name: str,
    value: str,
    current: object,
) -> object:
    label = f"environment.{field_name}"
    if field_name in _TUPLE_FIELDS | _OPTIONAL_TUPLE_FIELDS:
        separator = os.pathsep if field_name == "workspace_allowed_roots" else ","
        parsed = tuple(item.strip() for item in value.split(separator) if item.strip())
        if field_name == "workspace_allowed_roots" and not parsed:
            return current
        return parsed
    if isinstance(current, bool):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ConfigSchemaError(f"{label} must be a boolean")
    if isinstance(current, int):
        try:
            return int(value)
        except ValueError as exc:
            raise ConfigSchemaError(f"{label} must be an integer") from exc
    if isinstance(current, float):
        try:
            return float(value)
        except ValueError as exc:
            raise ConfigSchemaError(f"{label} must be a number") from exc
    if current is None and not value and field_name == "llm_model_catalog_json":
        return None
    return value


def _validate_project_overrides(
    current: Mapping[str, object],
    incoming: Mapping[str, object],
) -> None:
    forbidden = set(incoming).intersection(PROCESS_SECURITY_FIELDS)
    if forbidden:
        names = ", ".join(sorted(forbidden))
        raise ConfigSecurityError(
            f"project_config cannot override process/security fields: {names}"
        )

    if "agent_approval_policy" in incoming:
        candidate = incoming["agent_approval_policy"]
        baseline = current["agent_approval_policy"]
        tightening_transitions = {
            "on_request": {"on_request", "always", "never"},
            "always": {"always"},
            "never": {"never"},
        }
        if candidate not in tightening_transitions.get(str(baseline), set()):
            raise ConfigSecurityError(
                "project_config agent_approval_policy may only tighten permission"
            )

    if incoming.get("mcp_enabled") is True and current["mcp_allowed"] is False:
        raise ConfigSecurityError(
            "project_config cannot enable MCP when process mcp_allowed=false"
        )
    if incoming.get("skills_enabled") is True and current["skills_allowed"] is False:
        raise ConfigSecurityError(
            "project_config cannot enable skills when process skills_allowed=false"
        )
    _validate_project_selection(
        current,
        incoming,
        selected_field="enabled_tools",
        allowed_field="tool_allowlist",
    )
    _validate_project_selection(
        current,
        incoming,
        selected_field="enabled_skills",
        allowed_field="skill_allowlist",
    )


def _validate_project_selection(
    current: Mapping[str, object],
    incoming: Mapping[str, object],
    *,
    selected_field: str,
    allowed_field: str,
) -> None:
    if selected_field not in incoming:
        return
    candidate = incoming[selected_field]
    process_allowed = current[allowed_field]
    previous_selection = current[selected_field]
    if candidate is None and previous_selection is not None:
        raise ConfigSecurityError(
            f"project_config {selected_field}=null would widen the current selection"
        )
    if candidate is not None and process_allowed is not None:
        if not set(candidate).issubset(process_allowed):
            raise ConfigSecurityError(
                f"project_config {selected_field} cannot widen its process allowlist"
            )
    if candidate is not None and previous_selection is not None:
        if not set(candidate).issubset(previous_selection):
            raise ConfigSecurityError(
                f"project_config {selected_field} may only narrow user selection"
            )


def _select_environment_name(
    field_name: str,
    process_environment: Mapping[str, str],
    dotenv: Mapping[str, str],
) -> tuple[str, str] | None:
    namespaced = f"{_ENV_PREFIX}{field_name.upper()}"
    legacy = field_name.upper()
    for values, location in (
        (process_environment, "environment"),
        (dotenv, ".env"),
    ):
        if namespaced in values:
            return namespaced, location
        if legacy in values:
            return legacy, location
    return None


def _apply_legacy_environment_fallback(
    environment_values: dict[str, object],
    details: dict[str, str],
    combined: Mapping[str, str],
    process_environment: Mapping[str, str],
    *,
    target: str,
    canonical: str,
    fallback: str,
    current: object,
    allowed_values: frozenset[str] | None = None,
) -> None:
    namespaced = f"{_ENV_PREFIX}{canonical}"
    if target in environment_values or namespaced in combined or canonical in combined:
        return
    if fallback not in combined:
        return
    parsed = _parse_environment_value(
        target,
        combined[fallback],
        current,
    )
    if allowed_values is not None and parsed not in allowed_values:
        return
    environment_values[target] = parsed
    location = "environment" if fallback in process_environment else ".env"
    details[target] = f"{location}:{fallback} (legacy fallback)"


def _apply_legacy_workspace_mode_fallback(
    environment_values: dict[str, object],
    details: dict[str, str],
    combined: Mapping[str, str],
    process_environment: Mapping[str, str],
    *,
    provenance: Mapping[str, ConfigFieldSource],
) -> None:
    """Map the old promotion mode only when the new execution settings are absent."""

    legacy_name = "CHANGE_SET_APPLY_MODE"
    if legacy_name not in combined:
        return
    legacy_mode = str(combined[legacy_name]).strip()
    location = "environment" if legacy_name in process_environment else ".env"
    if (
        "agent_workspace_default_mode" not in environment_values
        and provenance["agent_workspace_default_mode"].source is ConfigSource.DEFAULT
    ):
        environment_values["agent_workspace_default_mode"] = legacy_mode
        details["agent_workspace_default_mode"] = (
            f"{location}:{legacy_name} (deprecated workspace-mode fallback)"
        )
    if (
        "agent_workspace_allowed_modes" not in environment_values
        and provenance["agent_workspace_allowed_modes"].source is ConfigSource.DEFAULT
    ):
        modes = tuple(dict.fromkeys(("patch_only", legacy_mode)))
        environment_values["agent_workspace_allowed_modes"] = modes
        details["agent_workspace_allowed_modes"] = (
            f"{location}:{legacy_name} (deprecated workspace-mode fallback)"
        )


def _validate_namespaced_environment(environment: Mapping[str, str]) -> None:
    recognized = {
        f"{_ENV_PREFIX}{field_name.upper()}" for field_name in _SETTINGS_FIELDS
    } | set(_CONFIG_PATH_ENV_NAMES)
    unknown = {
        name
        for name in environment
        if name.startswith(_ENV_PREFIX) and name not in recognized
    }
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ConfigSchemaError(f"environment has unknown fields: {names}")


def _read_dotenv(path: Path, *, required: bool) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        if required:
            raise ConfigSchemaError(f"dotenv file does not exist: {path}")
        return {}
    except OSError as exc:
        raise ConfigSchemaError(f"cannot read dotenv file: {path}") from exc

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigSchemaError(
                f"dotenv line {line_number} must use NAME=value syntax"
            )
        name, value = line.split("=", 1)
        name = name.strip()
        if not name:
            raise ConfigSchemaError(f"dotenv line {line_number} has an empty name")
        values[name] = value.strip().strip("\"'")
    return values


def _freeze_value(value: object) -> object:
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, dict):
        return MappingProxyType(
            {str(name): _freeze_value(item) for name, item in value.items()}
        )
    return value


def _redact_value(field_name: str, value: object) -> object:
    if field_name in _SECRET_FIELDS:
        return None if value is None else "***REDACTED***"
    if field_name in _CONNECTION_FIELDS and isinstance(value, str):
        return _redact_url(value)
    if isinstance(value, tuple):
        return list(value)
    return value


def _redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "***REDACTED_URL***"
    if not parsed.scheme or not parsed.netloc:
        return "***REDACTED_URL***"

    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = parsed.port
    except ValueError:
        return "***REDACTED_URL***"
    if port is not None:
        hostname = f"{hostname}:{port}"
    netloc = hostname
    if parsed.username is not None or parsed.password is not None:
        netloc = f"***:***@{hostname}"

    query = urlencode(
        [
            (name, "***REDACTED***")
            for name, _ in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    fragment = "***REDACTED***" if parsed.fragment else ""
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, fragment))


__all__ = [
    "ConfigError",
    "ConfigFieldSource",
    "ConfigResolver",
    "ConfigSchemaError",
    "ConfigSecurityError",
    "ConfigSource",
    "ProcessSecurityConfig",
    "ProjectSessionConfig",
    "ResolvedConfig",
    "RuntimeConfig",
]
