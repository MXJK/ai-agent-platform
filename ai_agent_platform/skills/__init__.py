"""Safe declarative Skill discovery and slash command registration."""

from .discovery import SkillDiscovery, SkillDocumentError
from .models import (
    CommandDefinition,
    SkillCatalog,
    SkillContextSelection,
    SkillContextSource,
    SkillDefinition,
    SkillDiagnostic,
    SkillDiscoveryLimits,
    SkillSource,
    SlashCommandMetadata,
)
from .registry import CommandRegistry
from .service import SkillInvocationError, SkillService

__all__ = [
    "CommandDefinition",
    "CommandRegistry",
    "SkillCatalog",
    "SkillContextSelection",
    "SkillContextSource",
    "SkillDefinition",
    "SkillDiagnostic",
    "SkillDiscovery",
    "SkillDiscoveryLimits",
    "SkillDocumentError",
    "SkillService",
    "SkillInvocationError",
    "SkillSource",
    "SlashCommandMetadata",
]
