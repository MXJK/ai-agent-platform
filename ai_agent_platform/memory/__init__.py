"""Cross-session L0 search and user-scoped L3 memory contracts."""

from .models import (
    USER_MEMORY_KINDS,
    USER_MEMORY_MODES,
    USER_MEMORY_STATUSES,
    ConversationMemoryHit,
    UserMemory,
    UserMemoryEvidence,
    UserMemorySettings,
    UserProfileSnapshot,
)
from .service import (
    UserMemoryConflictError,
    UserMemoryNotFoundError,
    UserMemoryService,
    UserMemoryValidationError,
)

__all__ = [
    "USER_MEMORY_KINDS",
    "USER_MEMORY_MODES",
    "USER_MEMORY_STATUSES",
    "ConversationMemoryHit",
    "UserMemory",
    "UserMemoryEvidence",
    "UserMemorySettings",
    "UserMemoryConflictError",
    "UserMemoryNotFoundError",
    "UserMemoryService",
    "UserMemoryValidationError",
    "UserProfileSnapshot",
]
