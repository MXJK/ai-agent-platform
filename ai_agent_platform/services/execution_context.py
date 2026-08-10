"""Build the authoritative context snapshot before a Run is queued."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any
from uuid import uuid4

from ai_agent_platform.agents.coding.context import load_project_instructions
from ai_agent_platform.agents.coding.text import extract_paths, unique
from ai_agent_platform.domain import (
    AdditionalDirectoryContext,
    ConversationMessageSnapshot,
    ConversationSummarySnapshot,
    GitContext,
    GitDirtySummary,
    IdentityContext,
    InstructionContext,
    InstructionSourceSnapshot,
    ModelSelectionSnapshot,
    ProjectContext,
    RunContextSnapshot,
    RunMetadata,
    SessionContext,
    canonical_project_config,
)
from ai_agent_platform.model_registry import ModelSelection


_SENSITIVE_FIELD_PARTS = frozenset(
    {
        "api_key",
        "authorization",
        "backend_url",
        "connection_string",
        "database_url",
        "dsn",
        "password",
        "redis_url",
        "secret",
        "token",
    }
)
_GIT_STATUS_SAMPLE_LIMIT = 20


class ExecutionContextFactory:
    """Capture identity, session, project and instruction state exactly once."""

    def __init__(
        self,
        *,
        session_service: Any,
        workspace_service: Any,
        workspace_authorizer: Any = None,
        auth_mode: str = "disabled",
        entrypoint_type: str = "api",
        max_context_messages: int = 12,
        max_instruction_chars: int = 16000,
        config_snapshot: Mapping[str, object] | None = None,
    ) -> None:
        if entrypoint_type not in {"api", "worker", "cli", "agent_loop"}:
            raise ValueError(f"unsupported Run entrypoint type: {entrypoint_type}")
        self._session_service = session_service
        self._workspace_service = workspace_service
        self._workspace_authorizer = workspace_authorizer
        self._auth_mode = auth_mode
        self._entrypoint_type = entrypoint_type
        self._max_context_messages = max_context_messages
        self._max_instruction_chars = max_instruction_chars
        safe_config = _redact_config(config_snapshot or {})
        self._config_json = canonical_project_config(safe_config)
        self._config_version = "sha256:" + hashlib.sha256(
            self._config_json.encode("utf-8")
        ).hexdigest()[:16]

    @property
    def entrypoint_type(self) -> str:
        return self._entrypoint_type

    @property
    def config_version(self) -> str:
        return self._config_version

    def create(
        self,
        *,
        conversation_id: str,
        user_message: str,
        workspace_id: str,
        model_selection: ModelSelection | Mapping[str, object],
        actor_user_id: str | None = None,
        focus_files: Sequence[str] = (),
        cwd: str | None = None,
        additional_workspace_ids: Sequence[str] = (),
        run_id: str | None = None,
        created_at: datetime | None = None,
    ) -> RunContextSnapshot:
        session = self._session_service.get_session(session_id=conversation_id)
        if actor_user_id is not None and session.user_id != actor_user_id:
            raise PermissionError("conversation access denied")
        actor = actor_user_id or session.user_id

        workspace, workspace_root = self._resolve_workspace(workspace_id)
        workspace_role = self._resolve_workspace_role(workspace_id, actor)
        resolved_cwd = _resolve_cwd(workspace_root, cwd)
        normalized_focus = tuple(
            _validate_focus_path(workspace_root, item) for item in focus_files
        )
        additional = self._resolve_additional_directories(
            primary_workspace_id=workspace_id,
            workspace_ids=additional_workspace_ids,
            actor_user_id=actor,
        )

        build_agent_context = getattr(
            self._session_service,
            "build_agent_context",
            None,
        )
        if callable(build_agent_context):
            try:
                raw_history = build_agent_context(
                    session_id=conversation_id,
                    max_context_messages=self._max_context_messages,
                    record_injection=False,
                )
            except TypeError:
                raw_history = build_agent_context(
                    session_id=conversation_id,
                    max_context_messages=self._max_context_messages,
                )
        else:
            messages = self._session_service.list_messages(
                session_id=conversation_id
            )
            raw_history = [
                {"role": item.role, "content": item.content}
                for item in messages[-self._max_context_messages :]
            ]
        history = tuple(
            ConversationMessageSnapshot(
                role=str(item.get("role") or ""),
                content=str(item.get("content") or ""),
            )
            for item in raw_history
            if isinstance(item, Mapping)
        )

        summary = None
        get_summary = getattr(
            self._session_service,
            "get_conversation_summary",
            None,
        )
        raw_summary = get_summary(conversation_id) if callable(get_summary) else None
        if raw_summary is not None:
            summary = ConversationSummarySnapshot(
                content=raw_summary.content,
                summarized_message_count=raw_summary.summarized_message_count,
                through_message_id=raw_summary.through_message_id,
                version=raw_summary.version,
                source_chars=raw_summary.source_chars,
                updated_at=raw_summary.updated_at.isoformat(),
            )

        instruction_focus = list(normalized_focus)
        for inferred in extract_paths(user_message):
            try:
                instruction_focus.append(
                    _validate_focus_path(workspace_root, inferred)
                )
            except ValueError:
                continue
        instructions = load_project_instructions(
            workspace_root=workspace_root,
            focus_files=unique(instruction_focus),
            max_chars=self._max_instruction_chars,
        )
        selection_values = (
            asdict(model_selection)
            if isinstance(model_selection, ModelSelection)
            else dict(model_selection)
        )
        resolved_run_id = run_id or f"run_{uuid4().hex[:12]}"
        timestamp = created_at or datetime.now(timezone.utc)
        return RunContextSnapshot(
            identity=IdentityContext(
                actor_user_id=actor,
                auth_mode=self._auth_mode,
                workspace_role=workspace_role,
            ),
            session=SessionContext(
                conversation_id=conversation_id,
                user_message=user_message,
                controlled_history=history,
                summary=summary,
                model_selection=ModelSelectionSnapshot.from_mapping(
                    selection_values
                ),
            ),
            project=ProjectContext(
                workspace_id=workspace.id,
                workspace_root=workspace_root,
                workspace_revision=int(workspace.revision),
                cwd=resolved_cwd,
                git=_capture_git_context(workspace_root),
                _project_config_json=self._config_json,
            ),
            instructions=InstructionContext(
                sources=tuple(
                    InstructionSourceSnapshot(
                        kind=item.kind,
                        path=item.path,
                        start_line=item.start_line,
                        end_line=item.end_line,
                        text=item.text,
                        reason=item.reason,
                        content_hash=item.content_hash,
                        truncated=item.truncated,
                    )
                    for item in instructions
                ),
                focus_files=normalized_focus,
                max_chars=self._max_instruction_chars,
            ),
            additional_directories=additional,
            metadata=RunMetadata(
                run_id=resolved_run_id,
                created_at=timestamp.astimezone(timezone.utc).isoformat(),
                entrypoint_type=self._entrypoint_type,
                config_version=self._config_version,
            ),
        )

    def _resolve_workspace(self, workspace_id: str) -> tuple[Any, str]:
        workspace = self._workspace_service.get(workspace_id)
        root = self._workspace_service.resolve_for_run(workspace_id)
        return workspace, str(Path(root).resolve())

    def _resolve_workspace_role(self, workspace_id: str, actor_user_id: str) -> str:
        if self._auth_mode == "disabled":
            return "admin"
        if self._workspace_authorizer is None:
            raise PermissionError("workspace authorization is unavailable")
        self._workspace_authorizer.authorize(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            required_role="viewer",
        )
        role_for = getattr(self._workspace_authorizer, "role_for", None)
        role = (
            role_for(workspace_id=workspace_id, actor_user_id=actor_user_id)
            if callable(role_for)
            else None
        )
        return str(role or "viewer")

    def _resolve_additional_directories(
        self,
        *,
        primary_workspace_id: str,
        workspace_ids: Sequence[str],
        actor_user_id: str,
    ) -> tuple[AdditionalDirectoryContext, ...]:
        resolved: list[AdditionalDirectoryContext] = []
        seen = {primary_workspace_id}
        for workspace_id in workspace_ids:
            normalized_id = str(workspace_id).strip()
            if not normalized_id or normalized_id in seen:
                continue
            seen.add(normalized_id)
            workspace, root = self._resolve_workspace(normalized_id)
            role = self._resolve_workspace_role(normalized_id, actor_user_id)
            resolved.append(
                AdditionalDirectoryContext(
                    workspace_id=workspace.id,
                    workspace_root=root,
                    workspace_revision=int(workspace.revision),
                    workspace_role=role,
                )
            )
        return tuple(resolved)


def _resolve_cwd(workspace_root: str, cwd: str | None) -> str:
    root = Path(workspace_root).resolve()
    candidate = Path(cwd).expanduser() if cwd else root
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError("cwd must be an existing workspace directory") from exc
    if not resolved.is_dir():
        raise ValueError("cwd must be an existing workspace directory")
    if resolved != root and root not in resolved.parents:
        raise ValueError("cwd escapes the registered workspace root")
    return str(resolved)


def _validate_focus_path(workspace_root: str, value: str) -> str:
    root = Path(workspace_root).resolve()
    relative = Path(str(value))
    if relative.is_absolute():
        raise ValueError("focus files must be relative to the workspace root")
    resolved = (root / relative).resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise ValueError("focus file escapes the registered workspace root")
    return resolved.relative_to(root).as_posix()


def _capture_git_context(workspace_root: str) -> GitContext:
    empty = GitDirtySummary(False, 0, 0, 0, 0, (), False)
    try:
        inside = _git(workspace_root, "rev-parse", "--is-inside-work-tree")
    except FileNotFoundError:
        return GitContext(False, False, None, None, empty, ("git_unavailable",))
    except (OSError, subprocess.TimeoutExpired):
        return GitContext(False, False, None, None, empty, ("git_probe_failed",))
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return GitContext(True, False, None, None, empty, ("not_a_git_repository",))

    diagnostics: list[str] = []
    try:
        head_result = _git(workspace_root, "rev-parse", "--verify", "HEAD")
        head = head_result.stdout.strip() if head_result.returncode == 0 else None
        if head is None:
            diagnostics.append("git_head_unavailable")
        branch_result = _git(
            workspace_root,
            "symbolic-ref",
            "--short",
            "-q",
            "HEAD",
        )
        branch = (
            branch_result.stdout.strip() if branch_result.returncode == 0 else None
        )
        if branch is None and head is not None:
            diagnostics.append("detached_head")
        status_result = _git(
            workspace_root,
            "status",
            "--short",
            "--untracked-files=normal",
        )
    except FileNotFoundError:
        return GitContext(False, True, None, None, empty, ("git_unavailable",))
    except (OSError, subprocess.TimeoutExpired):
        return GitContext(True, True, None, None, empty, ("git_probe_failed",))
    if status_result.returncode != 0:
        diagnostics.append("git_status_unavailable")
        dirty = empty
    else:
        lines = [line for line in status_result.stdout.splitlines() if line]
        staged = sum(1 for line in lines if line[:1] not in {" ", "?"})
        unstaged = sum(1 for line in lines if line[1:2] not in {" ", "?"})
        untracked = sum(1 for line in lines if line.startswith("??"))
        dirty = GitDirtySummary(
            is_dirty=bool(lines),
            changed_count=len(lines),
            staged_count=staged,
            unstaged_count=unstaged,
            untracked_count=untracked,
            sample_paths=tuple(line[3:] for line in lines[:_GIT_STATUS_SAMPLE_LIMIT]),
            truncated=len(lines) > _GIT_STATUS_SAMPLE_LIMIT,
        )
    return GitContext(
        available=True,
        is_repository=True,
        head=head,
        branch=branch,
        dirty=dirty,
        diagnostics=tuple(diagnostics),
    )


def _git(workspace_root: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", workspace_root, *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )


def _redact_config(value: Mapping[str, object]) -> dict[str, object]:
    return {
        str(name): _redact_config_value(str(name), item)
        for name, item in value.items()
    }


def _redact_config_value(name: str, value: object) -> object:
    normalized = name.casefold()
    if any(part in normalized for part in _SENSITIVE_FIELD_PARTS):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return _redact_config(value)
    if isinstance(value, (list, tuple)):
        return [_redact_config_value(name, item) for item in value]
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return str(value)
    return value


__all__ = ["ExecutionContextFactory"]
