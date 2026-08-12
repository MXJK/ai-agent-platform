"""Deterministic slash command registration for discovered Skills."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, Sequence

from .models import (
    CommandDefinition,
    SkillDefinition,
    SkillDiagnostic,
)


class CommandRegistry:
    """An immutable name/alias index; registration never installs tools."""

    def __init__(self, commands: Sequence[CommandDefinition] = ()) -> None:
        ordered = tuple(sorted(commands, key=_command_sort_key))
        self._commands = ordered
        by_name: dict[str, CommandDefinition] = {}
        for command in ordered:
            by_name[command.name] = command
            by_name[command.qualified_name] = command
            for alias in command.aliases:
                by_name.setdefault(alias, command)
        self._by_name: Mapping[str, CommandDefinition] = MappingProxyType(by_name)

    @property
    def commands(self) -> tuple[CommandDefinition, ...]:
        return self._commands

    def resolve(self, value: str) -> CommandDefinition | None:
        normalized = value.strip().removeprefix("/").casefold()
        return self._by_name.get(normalized)

    @classmethod
    def from_skills(
        cls,
        skills: Sequence[SkillDefinition],
    ) -> tuple["CommandRegistry", tuple[SkillDiagnostic, ...]]:
        candidates: list[tuple[SkillDefinition, CommandDefinition]] = []
        skills_by_qualified_name = {skill.qualified_name: skill for skill in skills}
        for skill in skills:
            metadata = skill.command
            if metadata is None:
                continue
            candidates.append(
                (
                    skill,
                    CommandDefinition(
                        name=metadata.name,
                        description=metadata.description,
                        usage=metadata.usage,
                        aliases=metadata.aliases,
                        skill_name=skill.name,
                        skill_qualified_name=skill.qualified_name,
                        source=skill.source,
                    ),
                )
            )

        diagnostics: list[SkillDiagnostic] = []
        winners: list[CommandDefinition] = []
        for command_name in sorted({item.name for _, item in candidates}):
            group = [item for item in candidates if item[1].name == command_name]
            group.sort(key=lambda item: _candidate_rank(item[0], item[1]))
            winner_skill, winner = group[0]
            winners.append(winner)
            for loser_skill, _ in group[1:]:
                diagnostics.append(
                    SkillDiagnostic(
                        severity="warning",
                        code="command_conflict",
                        source=loser_skill.source,
                        path=loser_skill.path,
                        related_path=winner_skill.path,
                        message=(
                            f"slash command /{command_name} from "
                            f"{loser_skill.qualified_name} is shadowed by "
                            f"{winner_skill.qualified_name}"
                        ),
                    )
                )

        canonical = {command.name: command for command in winners}
        qualified = {command.qualified_name: command for command in winners}
        claimed: dict[str, CommandDefinition] = {**canonical, **qualified}
        resolved: list[CommandDefinition] = []
        alias_claim_order = sorted(
            winners,
            key=lambda command: _candidate_rank(
                skills_by_qualified_name[command.skill_qualified_name],
                command,
            ),
        )
        for command in alias_claim_order:
            aliases: list[str] = []
            for alias in command.aliases:
                existing = claimed.get(alias)
                if existing is None:
                    claimed[alias] = command
                    aliases.append(alias)
                    continue
                diagnostics.append(
                    SkillDiagnostic(
                        severity="warning",
                        code="command_alias_conflict",
                        source=command.source,
                        path=skills_by_qualified_name[
                            command.skill_qualified_name
                        ].path,
                        related_path=skills_by_qualified_name[
                            existing.skill_qualified_name
                        ].path,
                        message=(
                            f"slash command alias /{alias} for "
                            f"{command.skill_qualified_name} conflicts with "
                            f"{existing.skill_qualified_name}"
                        ),
                    )
                )
            resolved.append(
                CommandDefinition(
                    name=command.name,
                    description=command.description,
                    usage=command.usage,
                    aliases=tuple(aliases),
                    skill_name=command.skill_name,
                    skill_qualified_name=command.skill_qualified_name,
                    source=command.source,
                )
            )
        return cls(sorted(resolved, key=_command_sort_key)), tuple(diagnostics)


def _candidate_rank(
    skill: SkillDefinition,
    command: CommandDefinition,
) -> tuple[int, str, str, str]:
    return (-skill.source.priority, skill.name, skill.path, command.name)


def _command_sort_key(command: CommandDefinition) -> tuple[str, str, str]:
    return (command.name, command.skill_qualified_name, command.qualified_name)
