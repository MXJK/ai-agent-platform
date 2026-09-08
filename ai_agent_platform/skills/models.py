"""Immutable domain models for declarative Skills and slash commands."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class SkillSource(str, Enum):
    BUNDLED = "bundled"
    USER = "user"
    PROJECT = "project"

    @property
    def priority(self) -> int:
        return {
            SkillSource.BUNDLED: 0,
            SkillSource.USER: 1,
            SkillSource.PROJECT: 2,
        }[self]


@dataclass(frozen=True)
class SlashCommandMetadata:
    name: str
    description: str
    usage: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillDefinition:
    """A parsed Skill document; it contains no executable hooks or grants."""

    name: str
    description: str
    agents: tuple[str, ...]
    modes: tuple[str, ...]
    instructions: str
    context_budget_chars: int
    required_tools: tuple[str, ...]
    command: SlashCommandMetadata | None
    source: SkillSource
    path: str
    content_hash: str
    project_context_untrusted: bool

    @property
    def namespace(self) -> str:
        return self.source.value

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}:{self.name}"

    def applies_to(self, *, agent: str, mode: str) -> bool:
        normalized_agent = agent.strip().casefold()
        normalized_mode = mode.strip().casefold()
        return (
            "*" in self.agents or normalized_agent in self.agents
        ) and ("*" in self.modes or normalized_mode in self.modes)


@dataclass(frozen=True)
class CommandDefinition:
    """Registered command metadata pointing back to a declarative Skill."""

    name: str
    description: str
    usage: str | None
    aliases: tuple[str, ...]
    skill_name: str
    skill_qualified_name: str
    source: SkillSource

    @property
    def namespace(self) -> str:
        return self.source.value

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}:{self.name}"


@dataclass(frozen=True)
class SkillDiagnostic:
    severity: Literal["warning", "error"]
    code: str
    source: SkillSource
    path: str
    message: str
    related_path: str | None = None


@dataclass(frozen=True)
class SkillDiscoveryLimits:
    max_file_bytes: int = 64 * 1024
    max_total_chars: int = 128 * 1024
    max_discovered_skills: int = 64
    max_context_budget_chars: int = 16_000

    def __post_init__(self) -> None:
        for name, value in (
            ("max_file_bytes", self.max_file_bytes),
            ("max_total_chars", self.max_total_chars),
            ("max_discovered_skills", self.max_discovered_skills),
            ("max_context_budget_chars", self.max_context_budget_chars),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class SkillCatalog:
    skills: tuple[SkillDefinition, ...]
    commands: tuple[CommandDefinition, ...]
    diagnostics: tuple[SkillDiagnostic, ...]
    discovered_count: int
    loaded_chars: int

    def get_skill(self, name: str) -> SkillDefinition | None:
        normalized = name.strip().casefold()
        for skill in self.skills:
            if normalized in {skill.name, skill.qualified_name}:
                return skill
        return None

    def resolve_command(self, name: str) -> CommandDefinition | None:
        normalized = name.strip().removeprefix("/").casefold()
        for command in self.commands:
            if normalized in {
                command.name,
                command.qualified_name,
                *command.aliases,
            }:
                return command
        return None


@dataclass(frozen=True)
class SkillContextSource:
    kind: str
    path: str
    text: str
    reason: str
    content_hash: str
    truncated: bool


@dataclass(frozen=True)
class SkillContextSelection:
    sources: tuple[SkillContextSource, ...]
    diagnostics: tuple[SkillDiagnostic, ...]
