"""Safe declarative Skill discovery and slash command registration."""

from .discovery import SkillDiscovery, SkillDocumentError, parse_skill_document
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
from .management import (
    SkillRegistryError,
    SkillRegistryNotFoundError,
    SkillRegistryService,
)
from .service import SkillInvocationError, SkillService
from .tool import SkillLoaderTool

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
    "parse_skill_document",
    "SkillService",
    "SkillInvocationError",
    "SkillRegistryError",
    "SkillRegistryNotFoundError",
    "SkillRegistryService",
    "SkillSource",
    "SkillLoaderTool",
    "SlashCommandMetadata",
]
