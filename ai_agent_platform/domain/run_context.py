"""Serializable, deeply immutable execution context captured for one Run."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping


RUN_CONTEXT_SCHEMA_VERSION = 4
SUPPORTED_RUN_CONTEXT_SCHEMA_VERSIONS = frozenset({1, 2, 3, 4})


@dataclass(frozen=True)
class ConversationMessageSnapshot:
    role: str
    content: str


@dataclass(frozen=True)
class ConversationSummarySnapshot:
    content: str
    summarized_message_count: int
    through_message_id: str
    version: int
    source_chars: int
    updated_at: str


@dataclass(frozen=True)
class ModelSelectionSnapshot:
    mode: str
    routing_policy: str
    preferred_model_id: str | None
    preferred_provider: str | None
    preferred_model: str | None
    thinking_level: str | None
    fallback_enabled: bool

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ModelSelectionSnapshot":
        return cls(
            mode=str(value.get("mode") or "auto"),
            routing_policy=str(value.get("routing_policy") or "smart"),
            preferred_model_id=_optional_string(value.get("preferred_model_id")),
            preferred_provider=_optional_string(value.get("preferred_provider")),
            preferred_model=_optional_string(value.get("preferred_model")),
            thinking_level=_optional_string(value.get("thinking_level")),
            fallback_enabled=bool(value.get("fallback_enabled", True)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "routing_policy": self.routing_policy,
            "preferred_model_id": self.preferred_model_id,
            "preferred_provider": self.preferred_provider,
            "preferred_model": self.preferred_model,
            "thinking_level": self.thinking_level,
            "fallback_enabled": self.fallback_enabled,
        }


@dataclass(frozen=True)
class IdentityContext:
    actor_user_id: str
    auth_mode: str
    workspace_role: str


@dataclass(frozen=True)
class SessionContext:
    conversation_id: str
    user_message: str
    controlled_history: tuple[ConversationMessageSnapshot, ...]
    summary: ConversationSummarySnapshot | None
    model_selection: ModelSelectionSnapshot


@dataclass(frozen=True)
class GitDirtySummary:
    is_dirty: bool
    changed_count: int
    staged_count: int
    unstaged_count: int
    untracked_count: int
    sample_paths: tuple[str, ...]
    truncated: bool


@dataclass(frozen=True)
class GitContext:
    available: bool
    is_repository: bool
    head: str | None
    branch: str | None
    dirty: GitDirtySummary
    diagnostics: tuple[str, ...]


@dataclass(frozen=True)
class ProjectContext:
    workspace_id: str
    workspace_root: str
    workspace_revision: int
    cwd: str
    git: GitContext
    _project_config_json: str

    @property
    def project_config(self) -> dict[str, object]:
        """Return an isolated JSON value; callers cannot mutate the snapshot."""
        return dict(json.loads(self._project_config_json))


@dataclass(frozen=True)
class InstructionSourceSnapshot:
    kind: str
    path: str
    start_line: int | None
    end_line: int | None
    text: str
    reason: str
    content_hash: str
    truncated: bool
    priority: int


@dataclass(frozen=True)
class InstructionContext:
    sources: tuple[InstructionSourceSnapshot, ...]
    focus_files: tuple[str, ...]
    max_chars: int
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdditionalDirectoryContext:
    workspace_id: str
    workspace_root: str
    workspace_revision: int
    workspace_role: str


@dataclass(frozen=True)
class ToolSelectionContext:
    enabled_tools: tuple[str, ...] | None
    source: str
    version: str
    catalog_version: str = "legacy:unversioned"
    catalog_hash: str = ""
    catalog_summary: str = ""
    pool_hash: str = ""
    normalized_summary: str = ""
    selection_provenance: tuple[str, ...] = ()
    exclusions: tuple[tuple[str, str], ...] = ()
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunMetadata:
    run_id: str
    created_at: str
    entrypoint_type: str
    config_version: str
    schema_version: int = RUN_CONTEXT_SCHEMA_VERSION
    _entrypoint_metadata_json: str = "{}"

    @property
    def entrypoint_metadata(self) -> dict[str, object]:
        return dict(json.loads(self._entrypoint_metadata_json))


@dataclass(frozen=True)
class ExecutionWorkspaceContext:
    run_id: str
    workspace_id: str
    source_root: str
    execution_root: str
    mode: str
    baseline: str
    base_git_head: str | None
    branch_name: str | None
    worktree_path: str | None
    cleanup_policy: str
    created_at: str
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "workspace_id": self.workspace_id,
            "source_root": self.source_root,
            "execution_root": self.execution_root,
            "mode": self.mode,
            "baseline": self.baseline,
            "base_git_head": self.base_git_head,
            "branch_name": self.branch_name,
            "worktree_path": self.worktree_path,
            "cleanup_policy": self.cleanup_policy,
            "created_at": self.created_at,
            "status": self.status,
        }


@dataclass(frozen=True)
class RunContextSnapshot:
    identity: IdentityContext
    session: SessionContext
    project: ProjectContext
    instructions: InstructionContext
    additional_directories: tuple[AdditionalDirectoryContext, ...]
    tools: ToolSelectionContext
    metadata: RunMetadata
    execution_workspace: ExecutionWorkspaceContext | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a fresh JSON-serializable representation of the snapshot."""
        return {
            "identity": {
                "actor_user_id": self.identity.actor_user_id,
                "auth_mode": self.identity.auth_mode,
                "workspace_role": self.identity.workspace_role,
            },
            "session": {
                "conversation_id": self.session.conversation_id,
                "user_message": self.session.user_message,
                "controlled_history": [
                    {"role": item.role, "content": item.content}
                    for item in self.session.controlled_history
                ],
                "summary": (
                    {
                        "content": self.session.summary.content,
                        "summarized_message_count": (
                            self.session.summary.summarized_message_count
                        ),
                        "through_message_id": self.session.summary.through_message_id,
                        "version": self.session.summary.version,
                        "source_chars": self.session.summary.source_chars,
                        "updated_at": self.session.summary.updated_at,
                    }
                    if self.session.summary is not None
                    else None
                ),
                "model_selection": self.session.model_selection.to_dict(),
            },
            "project": {
                "workspace_id": self.project.workspace_id,
                "workspace_root": self.project.workspace_root,
                "workspace_revision": self.project.workspace_revision,
                "cwd": self.project.cwd,
                "git": {
                    "available": self.project.git.available,
                    "is_repository": self.project.git.is_repository,
                    "head": self.project.git.head,
                    "branch": self.project.git.branch,
                    "dirty": {
                        "is_dirty": self.project.git.dirty.is_dirty,
                        "changed_count": self.project.git.dirty.changed_count,
                        "staged_count": self.project.git.dirty.staged_count,
                        "unstaged_count": self.project.git.dirty.unstaged_count,
                        "untracked_count": self.project.git.dirty.untracked_count,
                        "sample_paths": list(self.project.git.dirty.sample_paths),
                        "truncated": self.project.git.dirty.truncated,
                    },
                    "diagnostics": list(self.project.git.diagnostics),
                },
                "project_config": self.project.project_config,
            },
            "instructions": {
                "sources": [
                    {
                        "kind": item.kind,
                        "path": item.path,
                        "start_line": item.start_line,
                        "end_line": item.end_line,
                        "text": item.text,
                        "reason": item.reason,
                        "content_hash": item.content_hash,
                        "truncated": item.truncated,
                        "priority": item.priority,
                    }
                    for item in self.instructions.sources
                ],
                "focus_files": list(self.instructions.focus_files),
                "max_chars": self.instructions.max_chars,
                "diagnostics": list(self.instructions.diagnostics),
            },
            "additional_directories": [
                {
                    "workspace_id": item.workspace_id,
                    "workspace_root": item.workspace_root,
                    "workspace_revision": item.workspace_revision,
                    "workspace_role": item.workspace_role,
                }
                for item in self.additional_directories
            ],
            "tools": {
                "enabled_tools": (
                    list(self.tools.enabled_tools)
                    if self.tools.enabled_tools is not None
                    else None
                ),
                "source": self.tools.source,
                "version": self.tools.version,
                "catalog_version": self.tools.catalog_version,
                "catalog_hash": self.tools.catalog_hash,
                "catalog_summary": self.tools.catalog_summary,
                "pool_hash": self.tools.pool_hash,
                "normalized_summary": self.tools.normalized_summary,
                "selection_provenance": list(self.tools.selection_provenance),
                "exclusions": [
                    {"name": name, "reason": reason}
                    for name, reason in self.tools.exclusions
                ],
                "diagnostics": list(self.tools.diagnostics),
            },
            "metadata": {
                "run_id": self.metadata.run_id,
                "created_at": self.metadata.created_at,
                "entrypoint_type": self.metadata.entrypoint_type,
                "config_version": self.metadata.config_version,
                "schema_version": self.metadata.schema_version,
                "entrypoint_metadata": self.metadata.entrypoint_metadata,
            },
            "execution_workspace": (
                self.execution_workspace.to_dict()
                if self.execution_workspace is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunContextSnapshot":
        """Rehydrate a snapshot without consulting mutable external state."""
        metadata_value = _mapping(value, "metadata")
        schema_version = int(metadata_value.get("schema_version", 0))
        if schema_version not in SUPPORTED_RUN_CONTEXT_SCHEMA_VERSIONS:
            raise ValueError(
                f"unsupported Run context schema version: {schema_version}"
            )
        identity_value = _mapping(value, "identity")
        session_value = _mapping(value, "session")
        project_value = _mapping(value, "project")
        git_value = _mapping(project_value, "git")
        dirty_value = _mapping(git_value, "dirty")
        instruction_value = _mapping(value, "instructions")
        summary_value = session_value.get("summary")
        summary = None
        if summary_value is not None:
            if not isinstance(summary_value, Mapping):
                raise ValueError("Run context session.summary must be an object")
            summary = ConversationSummarySnapshot(
                content=str(summary_value.get("content") or ""),
                summarized_message_count=int(
                    summary_value.get("summarized_message_count", 0)
                ),
                through_message_id=str(
                    summary_value.get("through_message_id") or ""
                ),
                version=int(summary_value.get("version", 0)),
                source_chars=int(summary_value.get("source_chars", 0)),
                updated_at=str(summary_value.get("updated_at") or ""),
            )
        project_config = project_value.get("project_config") or {}
        if not isinstance(project_config, Mapping):
            raise ValueError("Run context project_config must be an object")
        history_values = session_value.get("controlled_history") or []
        instruction_sources = instruction_value.get("sources") or []
        additional_values = value.get("additional_directories") or []
        raw_tool_value = value.get("tools")
        if schema_version >= 2 and not isinstance(raw_tool_value, Mapping):
            raise ValueError("Run context tools must be an object")
        tool_value = raw_tool_value or {}
        if not isinstance(tool_value, Mapping):
            raise ValueError("Run context tools must be an object")
        enabled_tool_values = tool_value.get("enabled_tools")
        execution_value = value.get("execution_workspace")
        if schema_version >= 4 and not isinstance(execution_value, Mapping):
            raise ValueError("Run context execution_workspace must be an object")
        if enabled_tool_values is not None and not isinstance(
            enabled_tool_values, list
        ):
            raise ValueError("Run context tools.enabled_tools must be an array")
        if schema_version >= 2 and enabled_tool_values is None:
            raise ValueError("Run context tools.enabled_tools must be an array")
        if enabled_tool_values is not None and (
            not all(isinstance(item, str) and item for item in enabled_tool_values)
            or len(set(enabled_tool_values)) != len(enabled_tool_values)
        ):
            raise ValueError(
                "Run context tools.enabled_tools must contain unique tool names"
            )
        if not all(
            isinstance(items, list)
            for items in (history_values, instruction_sources, additional_values)
        ):
            raise ValueError("Run context collection fields must be arrays")
        if schema_version >= 2:
            expected_config_version = "sha256:" + hashlib.sha256(
                _canonical_json(project_config).encode("utf-8")
            ).hexdigest()[:16]
            if str(metadata_value.get("config_version") or "") != expected_config_version:
                raise ValueError("Run context project configuration version mismatch")
        if schema_version == 2:
            expected_tool_version = _tool_selection_version(enabled_tool_values or [])
            if str(tool_value.get("version") or "") != expected_tool_version:
                raise ValueError("Run context tool selection version mismatch")
        if schema_version >= 2:
            configured_tools = _config_snapshot_value(
                project_config,
                "project_session",
                "enabled_tools",
            )
            process_cap = _config_snapshot_value(
                project_config,
                "process_security",
                "tool_allowlist",
            )
            selected_set = set(enabled_tool_values or [])
            if schema_version == 2 and isinstance(configured_tools, list) and selected_set != set(configured_tools):
                raise ValueError(
                    "Run context tool selection conflicts with project configuration"
                )
            if schema_version >= 3 and isinstance(configured_tools, list) and not selected_set.issubset(configured_tools):
                raise ValueError(
                    "Run context tool selection exceeds project configuration"
                )
            if isinstance(process_cap, list) and not selected_set.issubset(process_cap):
                raise ValueError(
                    "Run context tool selection exceeds the frozen process cap"
                )
        if schema_version >= 3:
            _validate_tool_pool_snapshot(
                tool_value,
                enabled_tools=tuple(str(item) for item in enabled_tool_values or []),
            )
        project_workspace_root = str(project_value.get("workspace_root") or "")
        project_workspace_id = str(project_value.get("workspace_id") or "")
        if isinstance(execution_value, Mapping):
            execution_workspace = ExecutionWorkspaceContext(
                run_id=str(execution_value.get("run_id") or ""),
                workspace_id=str(execution_value.get("workspace_id") or ""),
                source_root=str(execution_value.get("source_root") or ""),
                execution_root=str(execution_value.get("execution_root") or ""),
                mode=str(execution_value.get("mode") or ""),
                baseline=str(execution_value.get("baseline") or ""),
                base_git_head=_optional_string(execution_value.get("base_git_head")),
                branch_name=_optional_string(execution_value.get("branch_name")),
                worktree_path=_optional_string(execution_value.get("worktree_path")),
                cleanup_policy=str(execution_value.get("cleanup_policy") or ""),
                created_at=str(execution_value.get("created_at") or ""),
                status=str(execution_value.get("status") or ""),
            )
            _validate_execution_workspace(
                execution_workspace,
                metadata_run_id=str(metadata_value.get("run_id") or ""),
                project_workspace_id=project_workspace_id,
                project_workspace_root=project_workspace_root,
            )
        else:
            execution_workspace = None
        return cls(
            identity=IdentityContext(
                actor_user_id=str(identity_value.get("actor_user_id") or ""),
                auth_mode=str(identity_value.get("auth_mode") or ""),
                workspace_role=str(identity_value.get("workspace_role") or ""),
            ),
            session=SessionContext(
                conversation_id=str(session_value.get("conversation_id") or ""),
                user_message=str(session_value.get("user_message") or ""),
                controlled_history=tuple(
                    ConversationMessageSnapshot(
                        role=str(_mapping(item).get("role") or ""),
                        content=str(_mapping(item).get("content") or ""),
                    )
                    for item in history_values
                ),
                summary=summary,
                model_selection=ModelSelectionSnapshot.from_mapping(
                    _mapping(session_value, "model_selection")
                ),
            ),
            project=ProjectContext(
                workspace_id=project_workspace_id,
                workspace_root=project_workspace_root,
                workspace_revision=int(project_value.get("workspace_revision", 1)),
                cwd=str(project_value.get("cwd") or ""),
                git=GitContext(
                    available=bool(git_value.get("available", False)),
                    is_repository=bool(git_value.get("is_repository", False)),
                    head=_optional_string(git_value.get("head")),
                    branch=_optional_string(git_value.get("branch")),
                    dirty=GitDirtySummary(
                        is_dirty=bool(dirty_value.get("is_dirty", False)),
                        changed_count=int(dirty_value.get("changed_count", 0)),
                        staged_count=int(dirty_value.get("staged_count", 0)),
                        unstaged_count=int(dirty_value.get("unstaged_count", 0)),
                        untracked_count=int(dirty_value.get("untracked_count", 0)),
                        sample_paths=tuple(
                            str(item) for item in dirty_value.get("sample_paths", [])
                        ),
                        truncated=bool(dirty_value.get("truncated", False)),
                    ),
                    diagnostics=tuple(
                        str(item) for item in git_value.get("diagnostics", [])
                    ),
                ),
                _project_config_json=_canonical_json(project_config),
            ),
            instructions=InstructionContext(
                sources=tuple(
                    InstructionSourceSnapshot(
                        kind=str(_mapping(item).get("kind") or ""),
                        path=str(_mapping(item).get("path") or ""),
                        start_line=_optional_int(_mapping(item).get("start_line")),
                        end_line=_optional_int(_mapping(item).get("end_line")),
                        text=str(_mapping(item).get("text") or ""),
                        reason=str(_mapping(item).get("reason") or ""),
                        content_hash=str(_mapping(item).get("content_hash") or ""),
                        truncated=bool(_mapping(item).get("truncated", False)),
                        priority=int(_mapping(item).get("priority", 0)),
                    )
                    for item in instruction_sources
                ),
                focus_files=tuple(
                    str(item) for item in instruction_value.get("focus_files", [])
                ),
                max_chars=int(instruction_value.get("max_chars", 0)),
                diagnostics=tuple(
                    str(item) for item in instruction_value.get("diagnostics", [])
                ),
            ),
            additional_directories=tuple(
                AdditionalDirectoryContext(
                    workspace_id=str(_mapping(item).get("workspace_id") or ""),
                    workspace_root=str(_mapping(item).get("workspace_root") or ""),
                    workspace_revision=int(
                        _mapping(item).get("workspace_revision", 1)
                    ),
                    workspace_role=str(
                        _mapping(item).get("workspace_role") or ""
                    ),
                )
                for item in additional_values
            ),
            tools=ToolSelectionContext(
                enabled_tools=(
                    tuple(str(item) for item in enabled_tool_values)
                    if enabled_tool_values is not None
                    else None
                ),
                source=str(tool_value.get("source") or "legacy_process_registry"),
                version=str(tool_value.get("version") or "legacy:unversioned"),
                catalog_version=str(
                    tool_value.get("catalog_version") or "legacy:unversioned"
                ),
                catalog_hash=str(tool_value.get("catalog_hash") or ""),
                catalog_summary=str(tool_value.get("catalog_summary") or ""),
                pool_hash=str(tool_value.get("pool_hash") or ""),
                normalized_summary=str(
                    tool_value.get("normalized_summary") or ""
                ),
                selection_provenance=tuple(
                    str(item)
                    for item in tool_value.get("selection_provenance", [])
                ),
                exclusions=tuple(
                    (
                        str(_mapping(item).get("name") or ""),
                        str(_mapping(item).get("reason") or ""),
                    )
                    for item in tool_value.get("exclusions", [])
                ),
                diagnostics=tuple(
                    str(item) for item in tool_value.get("diagnostics", [])
                ),
            ),
            metadata=RunMetadata(
                run_id=str(metadata_value.get("run_id") or ""),
                created_at=str(metadata_value.get("created_at") or ""),
                entrypoint_type=str(metadata_value.get("entrypoint_type") or ""),
                config_version=str(metadata_value.get("config_version") or ""),
                schema_version=schema_version,
                _entrypoint_metadata_json=_canonical_json(
                    metadata_value.get("entrypoint_metadata") or {}
                ),
            ),
            execution_workspace=execution_workspace,
        )


def _validate_execution_workspace(
    value: ExecutionWorkspaceContext,
    *,
    metadata_run_id: str,
    project_workspace_id: str,
    project_workspace_root: str,
) -> None:
    if value.mode not in {"patch_only", "direct", "worktree"}:
        raise ValueError("Run context execution workspace mode is invalid")
    if value.run_id != metadata_run_id or value.workspace_id != project_workspace_id:
        raise ValueError("Run context execution workspace binding is invalid")
    if value.source_root != project_workspace_root:
        raise ValueError("Run context execution source root is invalid")
    if not value.execution_root or not value.baseline:
        raise ValueError("Run context execution workspace is incomplete")
    if value.mode == "direct" and value.execution_root != value.source_root:
        raise ValueError("Run context direct execution root is invalid")
    if value.mode == "worktree" and value.worktree_path != value.execution_root:
        raise ValueError("Run context worktree execution root is invalid")


def canonical_project_config(value: Mapping[str, object]) -> str:
    return _canonical_json(value)


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Run context project_config must be JSON serializable") from exc


def _tool_selection_version(names: list[object]) -> str:
    encoded = json.dumps(names, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _validate_tool_pool_snapshot(
    value: Mapping[str, object],
    *,
    enabled_tools: tuple[str, ...],
) -> None:
    catalog_version = str(value.get("catalog_version") or "")
    catalog_hash = str(value.get("catalog_hash") or "")
    catalog_summary = str(value.get("catalog_summary") or "")
    pool_hash = str(value.get("pool_hash") or "")
    pool_version = str(value.get("version") or "")
    pool_summary = str(value.get("normalized_summary") or "")
    if not catalog_version.startswith("tool-catalog/"):
        raise ValueError("Run context tool catalog version is invalid")
    if not pool_version.startswith("effective-tool-pool/"):
        raise ValueError("Run context effective tool pool version is invalid")
    try:
        raw_catalog = json.loads(catalog_summary)
        raw_pool = json.loads(pool_summary)
    except (TypeError, ValueError) as exc:
        raise ValueError("Run context tool pool summary is invalid") from exc
    if not isinstance(raw_catalog, list) or not isinstance(raw_pool, list):
        raise ValueError("Run context tool summaries must be arrays")
    canonical_catalog = _canonical_json(raw_catalog)
    canonical_pool = _canonical_json(raw_pool)
    expected_catalog_hash = "sha256:" + hashlib.sha256(
        canonical_catalog.encode("utf-8")
    ).hexdigest()
    expected_pool_hash = "sha256:" + hashlib.sha256(
        canonical_pool.encode("utf-8")
    ).hexdigest()
    if canonical_catalog != catalog_summary or catalog_hash != expected_catalog_hash:
        raise ValueError("Run context tool catalog hash mismatch")
    if canonical_pool != pool_summary or pool_hash != expected_pool_hash:
        raise ValueError("Run context effective tool pool hash mismatch")
    expected_pool_version = (
        "effective-tool-pool/v1:"
        + expected_pool_hash.removeprefix("sha256:")[:16]
    )
    if pool_version != expected_pool_version:
        raise ValueError("Run context effective tool pool version mismatch")
    if not all(isinstance(item, Mapping) for item in raw_catalog + raw_pool):
        raise ValueError("Run context tool summary entries must be objects")
    catalog_by_name = {
        str(item.get("name") or ""): item for item in raw_catalog
    }
    pool_names = tuple(str(item.get("name") or "") for item in raw_pool)
    if not all(pool_names) or len(set(pool_names)) != len(pool_names):
        raise ValueError("Run context effective tool pool names are invalid")
    if pool_names != enabled_tools:
        raise ValueError(
            "Run context effective tool pool names differ from its summary"
        )
    if any(catalog_by_name.get(name) != item for name, item in zip(pool_names, raw_pool)):
        raise ValueError(
            "Run context effective tool pool differs from its catalog summary"
        )
    for field_name in ("selection_provenance", "diagnostics"):
        field_value = value.get(field_name, [])
        if not isinstance(field_value, list) or not all(
            isinstance(item, str) for item in field_value
        ):
            raise ValueError(f"Run context tools.{field_name} must be an array")
    exclusions = value.get("exclusions", [])
    if not isinstance(exclusions, list) or not all(
        isinstance(item, Mapping)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("reason"), str)
        for item in exclusions
    ):
        raise ValueError("Run context tools.exclusions must be an array")


def _config_snapshot_value(
    snapshot: Mapping[str, object],
    section: str,
    field_name: str,
) -> object:
    config = snapshot.get("config")
    if not isinstance(config, Mapping):
        return None
    section_value = config.get(section)
    if not isinstance(section_value, Mapping):
        return None
    field_value = section_value.get(field_name)
    if not isinstance(field_value, Mapping):
        return None
    return field_value.get("value")


def _mapping(
    value: Mapping[str, Any] | object,
    key: str | None = None,
) -> Mapping[str, Any]:
    selected = value.get(key) if key is not None and isinstance(value, Mapping) else value
    if not isinstance(selected, Mapping):
        label = f".{key}" if key else ""
        raise ValueError(f"Run context{label} must be an object")
    return selected


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None


def _optional_int(value: object) -> int | None:
    return int(value) if value is not None else None


__all__ = [
    "RUN_CONTEXT_SCHEMA_VERSION",
    "SUPPORTED_RUN_CONTEXT_SCHEMA_VERSIONS",
    "AdditionalDirectoryContext",
    "ConversationMessageSnapshot",
    "ConversationSummarySnapshot",
    "GitContext",
    "GitDirtySummary",
    "IdentityContext",
    "InstructionContext",
    "InstructionSourceSnapshot",
    "ModelSelectionSnapshot",
    "ProjectContext",
    "RunContextSnapshot",
    "RunMetadata",
    "SessionContext",
    "ToolSelectionContext",
    "canonical_project_config",
]
