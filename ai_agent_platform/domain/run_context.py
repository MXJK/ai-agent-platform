"""Serializable, deeply immutable execution context captured for one Run."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping


RUN_CONTEXT_SCHEMA_VERSION = 1


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


@dataclass(frozen=True)
class InstructionContext:
    sources: tuple[InstructionSourceSnapshot, ...]
    focus_files: tuple[str, ...]
    max_chars: int


@dataclass(frozen=True)
class AdditionalDirectoryContext:
    workspace_id: str
    workspace_root: str
    workspace_revision: int
    workspace_role: str


@dataclass(frozen=True)
class RunMetadata:
    run_id: str
    created_at: str
    entrypoint_type: str
    config_version: str
    schema_version: int = RUN_CONTEXT_SCHEMA_VERSION


@dataclass(frozen=True)
class RunContextSnapshot:
    identity: IdentityContext
    session: SessionContext
    project: ProjectContext
    instructions: InstructionContext
    additional_directories: tuple[AdditionalDirectoryContext, ...]
    metadata: RunMetadata

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
                    }
                    for item in self.instructions.sources
                ],
                "focus_files": list(self.instructions.focus_files),
                "max_chars": self.instructions.max_chars,
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
            "metadata": {
                "run_id": self.metadata.run_id,
                "created_at": self.metadata.created_at,
                "entrypoint_type": self.metadata.entrypoint_type,
                "config_version": self.metadata.config_version,
                "schema_version": self.metadata.schema_version,
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunContextSnapshot":
        """Rehydrate a snapshot without consulting mutable external state."""
        metadata_value = _mapping(value, "metadata")
        schema_version = int(metadata_value.get("schema_version", 0))
        if schema_version != RUN_CONTEXT_SCHEMA_VERSION:
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
        if not all(
            isinstance(items, list)
            for items in (history_values, instruction_sources, additional_values)
        ):
            raise ValueError("Run context collection fields must be arrays")
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
                workspace_id=str(project_value.get("workspace_id") or ""),
                workspace_root=str(project_value.get("workspace_root") or ""),
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
                    )
                    for item in instruction_sources
                ),
                focus_files=tuple(
                    str(item) for item in instruction_value.get("focus_files", [])
                ),
                max_chars=int(instruction_value.get("max_chars", 0)),
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
            metadata=RunMetadata(
                run_id=str(metadata_value.get("run_id") or ""),
                created_at=str(metadata_value.get("created_at") or ""),
                entrypoint_type=str(metadata_value.get("entrypoint_type") or ""),
                config_version=str(metadata_value.get("config_version") or ""),
                schema_version=schema_version,
            ),
        )


def canonical_project_config(value: Mapping[str, object]) -> str:
    return _canonical_json(value)


def _canonical_json(value: Mapping[str, object]) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Run context project_config must be JSON serializable") from exc


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
    "canonical_project_config",
]
