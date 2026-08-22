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
from ai_agent_platform.core import ConfigResolver, ResolvedConfig
from ai_agent_platform.domain import (
    AdditionalDirectoryContext,
    ConversationMessageSnapshot,
    ConversationSummarySnapshot,
    ExecutionWorkspaceContext,
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
    ToolSelectionContext,
    canonical_project_config,
)
from ai_agent_platform.integrations.permissions import ToolUseContext
from ai_agent_platform.integrations.tool_pool import (
    EffectiveToolPool,
    SandboxCapabilities,
    ToolCatalog,
    ToolPoolBuilder,
)
from ai_agent_platform.integrations.tools import ToolRegistry
from ai_agent_platform.model_registry import ModelSelection
from ai_agent_platform.integrations.execution_workspace import (
    EXECUTION_WORKSPACE_MODES,
    ExecutionWorkspaceRuntime,
)


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
        skill_service: Any = None,
        process_config: ResolvedConfig | None = None,
        tool_registry: ToolRegistry | None = None,
        tool_pool_builder: ToolPoolBuilder | None = None,
        model_registry: Any = None,
        execution_workspace_runtime: ExecutionWorkspaceRuntime | None = None,
        user_memory_service: Any = None,
        llm_client: Any = None,
        max_context_messages_ceiling: int = 0,
    ) -> None:
        if entrypoint_type not in {"api", "worker", "cli", "sdk", "agent_loop"}:
            raise ValueError(f"unsupported Run entrypoint type: {entrypoint_type}")
        self._session_service = session_service
        self._workspace_service = workspace_service
        self._workspace_authorizer = workspace_authorizer
        self._auth_mode = auth_mode
        self._entrypoint_type = entrypoint_type
        self._max_context_messages = max_context_messages
        self._max_context_messages_ceiling = max_context_messages_ceiling
        self._max_instruction_chars = max_instruction_chars
        self._llm_client = llm_client
        self._skill_service = skill_service
        self._process_config = process_config
        self._tool_registry = tool_registry or ToolRegistry()
        self._tool_pool_builder = tool_pool_builder or ToolPoolBuilder(
            self._tool_registry
        )
        self._model_registry = model_registry
        self._execution_workspace_runtime = (
            execution_workspace_runtime or ExecutionWorkspaceRuntime()
        )
        self._user_memory_service = user_memory_service
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
        entrypoint_type: str | None = None,
        entrypoint_metadata: Mapping[str, object] | None = None,
        skill_name: str | None = None,
        skill_arguments: Sequence[str] = (),
        preferred_tool_name: str | None = None,
        prepare_execution_workspace: bool = True,
    ) -> RunContextSnapshot:
        session = self._session_service.get_session(session_id=conversation_id)
        if actor_user_id is not None and session.user_id != actor_user_id:
            raise PermissionError("conversation access denied")
        actor = actor_user_id or session.user_id
        resolved_run_id = run_id or f"run_{uuid4().hex[:12]}"

        workspace, workspace_root = self._resolve_workspace(workspace_id)
        workspace_role = self._resolve_workspace_role(workspace_id, actor)
        effective_config, config_json, config_version = self._workspace_config(
            workspace_root
        )
        git_context = _capture_git_context(workspace_root)
        mode_capabilities = self.workspace_mode_capabilities(
            workspace_id=workspace_id,
            actor_user_id=actor,
            workspace_root=workspace_root,
            workspace_role=workspace_role,
            effective_config=effective_config,
            git_context=git_context,
        )
        effective_workspace_mode = _select_default_workspace_mode(mode_capabilities)
        if prepare_execution_workspace:
            execution_record = self._execution_workspace_runtime.prepare(
                run_id=resolved_run_id,
                workspace_id=workspace_id,
                source_root=workspace_root,
                mode=effective_workspace_mode,
            )
        else:
            execution_record = _preview_execution_workspace(
                run_id=resolved_run_id,
                workspace_id=workspace_id,
                source_root=workspace_root,
                mode=effective_workspace_mode,
                git_context=git_context,
            )
        execution_root = str(execution_record.execution_root)
        agent_type = "coding"
        run_mode = "default"
        skill_enabled = bool(
            self._skill_service is not None and self._skill_service.enabled
        )
        enabled_skills = (
            self._skill_service.enabled_skills
            if self._skill_service is not None
            else None
        )
        skill_catalog = (
            self._skill_service.discover(
                workspace_root=execution_root,
                enabled=skill_enabled,
            )
            if self._skill_service is not None
            else None
        )
        tool_selection, effective_pool = self._tool_selection(
            effective_config,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            workspace_root=workspace_root,
            execution_root=execution_root,
            execution_workspace_mode=effective_workspace_mode,
            run_id=resolved_run_id,
            actor_user_id=actor,
            workspace_role=workspace_role,
            model_selection=model_selection,
            skills=(skill_catalog.skills if skill_catalog is not None else ()),
            enabled_skill_names=enabled_skills,
            agent_type=agent_type,
            run_mode=run_mode,
        )
        context_message_limit = (
            int(effective_config.llm_max_context_messages)
            if effective_config is not None
            else self._max_context_messages
        )
        instruction_char_limit = (
            int(effective_config.agent_max_instruction_chars)
            if effective_config is not None
            else self._max_instruction_chars
        )
        resolved_cwd = _resolve_cwd(workspace_root, cwd)
        _resolve_execution_cwd(workspace_root, execution_root, cwd)
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
                    max_context_messages=context_message_limit,
                    max_context_tokens=self._context_budget_tokens(
                        model_selection
                    ),
                    max_context_messages_ceiling=(
                        self._max_context_messages_ceiling
                    ),
                    record_injection=False,
                )
            except TypeError:
                raw_history = build_agent_context(
                    session_id=conversation_id,
                    max_context_messages=context_message_limit,
                )
        else:
            messages = self._session_service.list_messages(
                session_id=conversation_id
            )
            raw_history = [
                {"role": item.role, "content": item.content}
                for item in messages[-context_message_limit:]
            ]
        if self._user_memory_service is not None:
            profile_context = self._user_memory_service.context_for_user(
                user_id=actor
            )
            if profile_context:
                raw_history = [
                    {"role": "system", "content": profile_context},
                    *raw_history,
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
        if execution_root != workspace_root:
            # Preparation happens first, but unsafe instruction nodes in the source
            # checkout still fail closed instead of disappearing during a filtered
            # patch-only copy or Git worktree checkout.
            load_project_instructions(
                workspace_root=workspace_root,
                focus_files=unique(instruction_focus),
                max_chars=1,
            )
        file_instructions = load_project_instructions(
            workspace_root=execution_root,
            focus_files=unique(instruction_focus),
            max_chars=instruction_char_limit,
        )
        instruction_snapshots = [
            InstructionSourceSnapshot(
                kind=item.kind,
                path=item.path,
                start_line=item.start_line,
                end_line=item.end_line,
                text=item.text,
                reason=item.reason,
                content_hash=item.content_hash,
                truncated=item.truncated,
                priority=_instruction_file_priority(item.path),
            )
            for item in file_instructions
        ]
        configured_instructions = (
            tuple(effective_config.project_session.project_instructions)
            if effective_config is not None
            else ()
        )
        remaining_instruction_chars = max(
            0,
            instruction_char_limit
            - sum(len(item.text) for item in instruction_snapshots),
        )
        instruction_provenance = (
            effective_config.provenance_for("project_instructions").detail
            if effective_config is not None
            else "static process snapshot"
        )
        for index, text in enumerate(configured_instructions):
            clipped = text[:remaining_instruction_chars]
            instruction_snapshots.append(
                InstructionSourceSnapshot(
                    kind="config_instruction",
                    path=(
                        "config://project_session/project_instructions/"
                        f"{index}"
                    ),
                    start_line=1,
                    end_line=clipped.count("\n") + 1,
                    text=clipped,
                    reason=(
                        "lower-priority project configuration instruction; "
                        f"source={instruction_provenance}"
                    ),
                    content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    truncated=len(clipped) < len(text),
                    priority=100,
                )
            )
            remaining_instruction_chars = max(
                0,
                remaining_instruction_chars - len(clipped),
            )
        preferred_tool = None
        if preferred_tool_name:
            if preferred_tool_name not in (tool_selection.enabled_tools or ()):
                raise ValueError(
                    f"preferred tool is unavailable: {preferred_tool_name}"
                )
            preferred_tool = (
                effective_pool.get_spec(preferred_tool_name)
                if effective_pool is not None
                else self._tool_registry.get_spec(preferred_tool_name)
            )
            if preferred_tool is None:
                raise ValueError(
                    f"preferred tool is unavailable: {preferred_tool_name}"
                )
            tool_preference_text = (
                "[User-selected tool preference]\n"
                f"The user selected `{preferred_tool.name}` for this request. "
                "Prefer this tool when it is relevant and its input schema fits "
                "the task. This preference does not grant permission, bypass "
                "approval, or require an inappropriate tool call.\n"
                f"Tool description: {preferred_tool.description}"
            )
            clipped = tool_preference_text[:remaining_instruction_chars]
            if clipped:
                instruction_snapshots.append(
                    InstructionSourceSnapshot(
                        kind="user_tool_preference",
                        path=f"tool://{preferred_tool.name}",
                        start_line=1,
                        end_line=clipped.count("\n") + 1,
                        text=clipped,
                        reason="tool explicitly selected in the conversation composer",
                        content_hash=hashlib.sha256(
                            tool_preference_text.encode("utf-8")
                        ).hexdigest(),
                        truncated=len(clipped) < len(tool_preference_text),
                        priority=60,
                    )
                )
                remaining_instruction_chars = max(
                    0,
                    remaining_instruction_chars - len(clipped),
                )
        selected_skill_names: tuple[str, ...] | None = None
        invoked_skill = None
        if skill_name:
            if self._skill_service is None:
                raise ValueError("Skills are unavailable")
            invoked_skill = self._skill_service.require_skill(
                skill_name,
                workspace_root=execution_root,
                agent=agent_type,
                mode=run_mode,
                available_tools=tool_selection.enabled_tools or (),
            )
            selected_skill_names = (invoked_skill.qualified_name,)
        skill_diagnostics: tuple[str, ...] = tool_selection.diagnostics
        if (
            self._skill_service is not None
            and selected_skill_names is None
            and remaining_instruction_chars > 0
            and "agent.load_skill" in (tool_selection.enabled_tools or ())
        ):
            catalog_options: dict[str, object] = {}
            catalog_options["available_tools"] = tool_selection.enabled_tools or ()
            implicit_catalog = self._skill_service.effective_catalog(
                workspace_root=execution_root,
                agent=agent_type,
                mode=run_mode,
                **catalog_options,
            )
            if implicit_catalog.skills:
                catalog_lines = [
                    "[Global Skill catalog]",
                    "The following reusable Skills are available in every Workspace. ",
                    "When exactly one Skill clearly matches the user's request, call ",
                    "`agent.load_skill` with its qualified name before continuing. ",
                    "Do not load a Skill merely because of a weak keyword match.",
                ]
                catalog_lines.extend(
                    f"- {skill.qualified_name}: {skill.description}"
                    for skill in implicit_catalog.skills
                )
                catalog_text = "\n".join(catalog_lines)
                clipped = catalog_text[:remaining_instruction_chars]
                instruction_snapshots.append(
                    InstructionSourceSnapshot(
                        kind="skill_catalog",
                        path="skill://catalog",
                        start_line=1,
                        end_line=clipped.count("\n") + 1,
                        text=clipped,
                        reason=(
                            "global Skill metadata for conservative implicit activation"
                        ),
                        content_hash=hashlib.sha256(
                            catalog_text.encode("utf-8")
                        ).hexdigest(),
                        truncated=len(clipped) < len(catalog_text),
                        priority=40,
                    )
                )
                remaining_instruction_chars = max(
                    0,
                    remaining_instruction_chars - len(clipped),
                )
        if self._skill_service is not None and remaining_instruction_chars > 0:
            skill_options: dict[str, object] = {}
            if self._tool_registry is not None:
                skill_options["available_tools"] = (
                    tool_selection.enabled_tools or ()
                )
            if selected_skill_names is not None:
                skill_options["selected_skill_names"] = selected_skill_names
            selection = self._skill_service.build_context(
                workspace_root=execution_root,
                agent=agent_type,
                mode=run_mode,
                max_chars=remaining_instruction_chars,
                **skill_options,
            )
            skill_diagnostics = tuple(
                _skill_diagnostic_message(item) for item in selection.diagnostics
            )
            for item in selection.sources:
                instruction_snapshots.append(
                    InstructionSourceSnapshot(
                        kind=item.kind,
                        path=item.path,
                        start_line=1,
                        end_line=item.text.count("\n") + 1,
                        text=item.text,
                        reason=item.reason,
                        content_hash=item.content_hash,
                        truncated=item.truncated,
                        priority=50,
                    )
                )
                remaining_instruction_chars = max(
                    0,
                    remaining_instruction_chars - len(item.text),
                )
        selection_values = (
            asdict(model_selection)
            if isinstance(model_selection, ModelSelection)
            else dict(model_selection)
        )
        timestamp = created_at or datetime.now(timezone.utc)
        resolved_entrypoint = entrypoint_type or self._entrypoint_type
        if resolved_entrypoint not in {"api", "worker", "cli", "sdk", "agent_loop"}:
            raise ValueError(f"unsupported Run entrypoint type: {resolved_entrypoint}")
        resolved_entrypoint_metadata = dict(entrypoint_metadata or {})
        if invoked_skill is not None:
            resolved_entrypoint_metadata["skill_invocation"] = {
                "skill_name": invoked_skill.qualified_name,
                "arguments": [str(item) for item in skill_arguments],
            }
        if preferred_tool is not None:
            resolved_entrypoint_metadata["tool_preference"] = {
                "name": preferred_tool.name,
                "provider": preferred_tool.provider,
            }
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
                git=git_context,
                _project_config_json=config_json,
            ),
            instructions=InstructionContext(
                sources=tuple(instruction_snapshots),
                focus_files=normalized_focus,
                max_chars=instruction_char_limit,
                diagnostics=skill_diagnostics,
            ),
            additional_directories=additional,
            tools=tool_selection,
            metadata=RunMetadata(
                run_id=resolved_run_id,
                created_at=timestamp.astimezone(timezone.utc).isoformat(),
                entrypoint_type=resolved_entrypoint,
                config_version=config_version,
                _entrypoint_metadata_json=canonical_project_config(
                    resolved_entrypoint_metadata
                ),
            ),
            execution_workspace=ExecutionWorkspaceContext(
                **execution_record.to_dict(),
            ),
        )

    def _context_budget_tokens(self, model_selection: Any = None) -> int:
        """Resolve the model-derived input budget, or 0 when unavailable."""
        resolve = getattr(self._llm_client, "resolve_context_budget", None)
        if not callable(resolve):
            return 0
        manual = getattr(model_selection, "mode", None) == "manual"
        return int(
            resolve(
                provider=(
                    getattr(model_selection, "preferred_provider", None)
                    if manual
                    else None
                ),
                model=(
                    getattr(model_selection, "preferred_model", None)
                    if manual
                    else None
                ),
            ).input_tokens
        )

    def _workspace_config(
        self,
        workspace_root: str,
    ) -> tuple[ResolvedConfig | None, str, str]:
        if self._process_config is None:
            return None, self._config_json, self._config_version
        resolved = ConfigResolver.resolve_workspace(
            self._process_config,
            workspace_root=workspace_root,
        )
        config_json = canonical_project_config(resolved.safe_snapshot())
        version = "sha256:" + hashlib.sha256(
            config_json.encode("utf-8")
        ).hexdigest()[:16]
        return resolved, config_json, version

    def preview(
        self,
        *,
        conversation_id: str,
        workspace_id: str,
        actor_user_id: str | None = None,
        model_selection: ModelSelection | Mapping[str, object] | None = None,
    ) -> RunContextSnapshot:
        """Build a read-only effective-context preview through the normal factory."""

        return self.create(
            conversation_id=conversation_id,
            user_message="",
            workspace_id=workspace_id,
            model_selection=model_selection or ModelSelection(),
            actor_user_id=actor_user_id,
            entrypoint_type=self._entrypoint_type,
            entrypoint_metadata={"adapter": "effective_context_preview"},
            prepare_execution_workspace=False,
        )

    def workspace_mode_capabilities(
        self,
        *,
        workspace_id: str,
        actor_user_id: str,
        workspace_root: str,
        workspace_role: str,
        effective_config: ResolvedConfig | None = None,
        git_context: GitContext | None = None,
    ) -> dict[str, object]:
        config = effective_config
        allowed = tuple(
            config.agent_workspace_allowed_modes
            if config is not None
            else ("patch_only",)
        )
        configured_default = str(
            config.agent_workspace_default_mode if config is not None else "patch_only"
        )
        git = git_context or _capture_git_context(workspace_root)
        reasons: dict[str, str | None] = {}
        for mode in sorted(EXECUTION_WORKSPACE_MODES):
            reason: str | None = None
            if mode not in allowed:
                reason = "mode is outside the server allowlist"
            elif mode in {"direct", "worktree"}:
                if self._auth_mode == "disabled":
                    reason = "authenticated identity is required for live workspaces"
                elif config is None or not config.live_workspace_writes_enabled:
                    reason = "LIVE_WORKSPACE_WRITES_ENABLED is false"
                elif workspace_role not in {"editor", "admin"}:
                    reason = "Workspace role editor or admin is required"
                elif mode == "worktree" and not git.is_repository:
                    reason = "the registered Workspace is not a Git repository"
                elif mode == "worktree" and git.dirty.is_dirty:
                    reason = (
                        "the Git checkout is dirty; choose direct or patch_only"
                    )
            reasons[mode] = reason
        default_mode = configured_default
        if reasons.get(default_mode) is not None:
            default_mode = next(
                (mode for mode in allowed if reasons.get(mode) is None),
                configured_default,
            )
        return {
            "workspace_id": workspace_id,
            "allowed_modes": list(allowed),
            "configured_default_mode": configured_default,
            "default_mode": default_mode,
            "unavailable_reasons": reasons,
        }

    def restore_tool_access(self, snapshot: RunContextSnapshot):
        if snapshot.metadata.schema_version >= 3:
            return self._tool_pool_builder.restore(snapshot.tools)
        selected = snapshot.tools.enabled_tools
        if selected is None:
            selected = tuple(
                spec.name for spec in self._tool_registry.list_specs()
            )
        return self._tool_registry.select(tuple(selected))

    def effective_skills(self, snapshot: RunContextSnapshot):
        if self._skill_service is None:
            return None
        return self._skill_service.effective_catalog(
            workspace_root=(
                snapshot.execution_workspace.execution_root
                if snapshot.execution_workspace is not None
                else snapshot.project.workspace_root
            ),
            agent="coding",
            mode="default",
            available_tools=snapshot.tools.enabled_tools or (),
        )

    def describe_permissions(self, snapshot: RunContextSnapshot) -> dict[str, object]:
        config = snapshot.project.project_config
        process_cap = _config_snapshot_value(
            config, "process_security", "tool_allowlist"
        )
        project_selection = _config_snapshot_value(
            config, "project_session", "enabled_tools"
        )
        approval_policy = _config_snapshot_value(
            config, "runtime", "agent_approval_policy"
        )
        return {
            "process_tool_allowlist": process_cap,
            "workspace_role": snapshot.identity.workspace_role,
            "project_enabled_tools": project_selection,
            "approval_policy": approval_policy or "on_request",
            "effective_denies": [
                {"name": name, "reason": reason}
                for name, reason in snapshot.tools.exclusions
            ],
        }

    def _tool_selection(
        self,
        config: ResolvedConfig | None,
        *,
        conversation_id: str,
        workspace_id: str,
        workspace_root: str,
        execution_root: str,
        execution_workspace_mode: str,
        run_id: str,
        actor_user_id: str,
        workspace_role: str,
        model_selection: ModelSelection | Mapping[str, object],
        skills: Sequence[object],
        enabled_skill_names: Sequence[str] | None,
        agent_type: str,
        run_mode: str,
    ) -> tuple[ToolSelectionContext, EffectiveToolPool | None]:
        catalog = ToolCatalog.from_registry(self._tool_registry)
        available = tuple(entry.name for entry in catalog.entries)
        configured = config.enabled_tools if config is not None else None
        selected = tuple(configured) if configured is not None else available
        if config is not None and configured is not None:
            provenance = config.provenance_for("enabled_tools")
            source = f"{provenance.source.value}:{provenance.detail}"
        elif config is not None and config.tool_allowlist is not None:
            provenance = config.provenance_for("tool_allowlist")
            source = f"process_cap:{provenance.source.value}:{provenance.detail}"
        else:
            source = "process_registry"
        approval_policy = (
            config.agent_approval_policy if config is not None else "on_request"
        )
        context = ToolUseContext(
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            workspace_root=workspace_root,
            authorized_workspace_root=workspace_root,
            execution_root=execution_root,
            execution_workspace_mode=execution_workspace_mode,
            run_id=run_id,
            actor_user_id=actor_user_id,
            workspace_role=workspace_role,
            approval_policy=approval_policy,
            process_allowed_tools=available,
            project_allowed_tools=selected,
        )
        model_capabilities = self._model_capabilities(model_selection)
        sandbox = self._sandbox_capabilities(config, catalog)
        selection_provenance = (
            source,
            f"agent:{agent_type}",
            f"mode:{run_mode}",
            f"workspace_role:{workspace_role}",
            "model_capabilities:"
            + ",".join(
                f"{name}={str(value).lower()}"
                for name, value in sorted(model_capabilities.items())
            ),
            f"sandbox:{sandbox.mode}",
        )
        pool = self._tool_pool_builder.build(
            catalog=catalog,
            skills=skills,
            enabled_skill_names=enabled_skill_names,
            agent_type=agent_type,
            run_mode=run_mode,
            model_capabilities=model_capabilities,
            tool_use_context=context,
            sandbox_capabilities=sandbox,
            requested_names=selected,
            selection_provenance=selection_provenance,
        )
        return ToolSelectionContext(
            enabled_tools=pool.allowed_names,
            source=source,
            version=pool.pool_version,
            catalog_version=pool.catalog_version,
            catalog_hash=pool.catalog_hash,
            catalog_summary=pool.catalog_summary,
            pool_hash=pool.pool_hash,
            normalized_summary=pool.normalized_summary,
            selection_provenance=pool.selection_provenance,
            exclusions=tuple(
                (item.name, item.reason) for item in pool.exclusions
            ),
            diagnostics=pool.diagnostics,
        ), pool

    def _model_capabilities(
        self,
        model_selection: ModelSelection | Mapping[str, object],
    ) -> dict[str, bool]:
        if self._model_registry is None:
            return {"tool_calling": True, "structured_output": True}
        values = (
            asdict(model_selection)
            if isinstance(model_selection, ModelSelection)
            else dict(model_selection)
        )
        configs = tuple(self._model_registry.model_configs())
        preferred_id = values.get("preferred_model_id")
        preferred_provider = values.get("preferred_provider")
        preferred_model = values.get("preferred_model")
        if preferred_id:
            view = next(
                (
                    item
                    for item in self._model_registry.list_models()
                    if item.get("id") == preferred_id
                ),
                None,
            )
            if view is not None:
                preferred_provider = view.get("provider")
                preferred_model = view.get("model")
        if preferred_provider or preferred_model:
            candidates = tuple(
                item
                for item in configs
                if (not preferred_provider or item.provider == preferred_provider)
                and (not preferred_model or item.model == preferred_model)
                and item.enabled
            )
        else:
            candidates = tuple(
                item for item in configs if item.enabled and item.auto_eligible
            )
        return {
            "tool_calling": any(
                item.capabilities.tool_calling or item.provider == "fake"
                for item in candidates
            ),
            "structured_output": any(
                item.capabilities.structured_output for item in candidates
            ),
        }

    @staticmethod
    def _sandbox_capabilities(
        config: ResolvedConfig | None,
        catalog: ToolCatalog,
    ) -> SandboxCapabilities:
        sandbox_entries = tuple(
            entry for entry in catalog.entries if entry.name.startswith("sandbox.")
        )
        names = tuple(entry.name for entry in sandbox_entries)
        return SandboxCapabilities(
            available=bool(names),
            mode=config.sandbox_mode if config is not None else "local",
            readable=any(
                entry.spec.permission_level == "read_only"
                for entry in sandbox_entries
            ),
            writable=any(
                entry.spec.permission_level != "read_only"
                for entry in sandbox_entries
            ),
            command_execution="sandbox.run_command" in names,
            supported_tools=names,
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


def _skill_diagnostic_message(value: Any) -> str:
    code = str(getattr(value, "code", "skill_diagnostic"))
    path = str(getattr(value, "path", "SKILL.md"))
    message = str(getattr(value, "message", "Skill could not be loaded"))
    return f"skill[{code}] {path}: {message}"[:1000]


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


def _resolve_execution_cwd(
    source_root: str,
    execution_root: str,
    cwd: str | None,
) -> str:
    source = Path(source_root).resolve()
    source_cwd = Path(_resolve_cwd(source_root, cwd))
    relative = source_cwd.relative_to(source)
    candidate = (Path(execution_root).resolve() / relative).resolve(strict=True)
    if not candidate.is_dir():
        raise ValueError("cwd is unavailable in the execution workspace")
    return str(candidate)


def _select_default_workspace_mode(capabilities: Mapping[str, object]) -> str:
    mode = str(capabilities["default_mode"])
    if mode not in EXECUTION_WORKSPACE_MODES:
        raise ValueError(f"unsupported default workspace mode: {mode}")
    allowed = tuple(str(item) for item in capabilities.get("allowed_modes", []))
    if mode not in allowed:
        raise PermissionError("default workspace mode is outside the server allowlist")
    reasons = capabilities.get("unavailable_reasons")
    reason = reasons.get(mode) if isinstance(reasons, Mapping) else None
    if reason:
        raise PermissionError(str(reason))
    return mode


def _preview_execution_workspace(
    *,
    run_id: str,
    workspace_id: str,
    source_root: str,
    mode: str,
    git_context: GitContext,
):
    from ai_agent_platform.integrations.execution_workspace import (
        ExecutionWorkspaceRecord,
    )

    source = Path(source_root).resolve()
    return ExecutionWorkspaceRecord(
        run_id=run_id,
        workspace_id=workspace_id,
        source_root=source,
        execution_root=source,
        mode=mode,
        baseline={},
        baseline_digest="sha256:preview",
        base_git_head=git_context.head,
        branch_name=None,
        worktree_path=(source if mode == "worktree" else None),
        cleanup_policy="preview",
        created_at=datetime.now(timezone.utc).isoformat(),
        status="preview",
    )


def _instruction_file_priority(path: str) -> int:
    name = Path(path).name
    if name == "AGENTS.override.md":
        return 400
    if name == "AGENTS.md":
        return 300
    return 200


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
