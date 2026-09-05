from __future__ import annotations

from typing import Any

from .parser import SkillDef, SkillParseError, substitute_arguments


class SkillExecutor:
    def __init__(self, agent: Any, client: Any = None, protocol: str = "") -> None:
        self.agent = agent

    def execute_inline(self, skill: SkillDef, args: str) -> None:
        if skill.mode != "inline" or skill.context == "fork":
            raise SkillParseError("unsupported_skill_execution: only inline Skills are supported")
        self.agent.activate_skill(skill.name, substitute_arguments(skill.prompt_body, args))

    async def execute_fork(self, skill: SkillDef, args: str) -> str:
        raise SkillParseError("unsupported_skill_execution: fork Skills are not supported")
