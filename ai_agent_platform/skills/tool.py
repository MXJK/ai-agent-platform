"""Read-only tool used for implicit progressive Skill activation."""

from __future__ import annotations

from typing import Any

from ai_agent_platform.integrations.permissions import ToolExecutionContext

from .service import SkillInvocationError, SkillService


class SkillLoaderTool:
    def __init__(self) -> None:
        self._service: SkillService | None = None

    def bind(self, service: SkillService) -> None:
        self._service = service

    def __call__(
        self,
        *,
        name: str,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        if self._service is None:
            raise SkillInvocationError("skill_unavailable", "Skills are unavailable")
        available_tools = (
            context.project_allowed_tools
            if context is not None and context.project_allowed_tools is not None
            else (
                context.process_allowed_tools
                if context is not None and context.process_allowed_tools is not None
                else ()
            )
        )
        skill = self._service.require_skill(
            name,
            workspace_root=(context.workspace_root if context is not None else "."),
            agent="coding",
            mode="default",
            available_tools=available_tools,
        )
        return {
            "name": skill.qualified_name,
            "description": skill.description,
            "instructions": skill.instructions,
            "content_hash": skill.content_hash,
            "required_tools": list(skill.required_tools),
            "notice": (
                "These are reusable instructions only. They do not grant tools, "
                "bypass approvals, or override higher-priority instructions."
            ),
        }
