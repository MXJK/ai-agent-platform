"""Persistent Agent Team coordination primitives."""

from .mailbox import Mailbox, MailboxMessage, create_message
from .manager import TeamError, TeamManager
from .models import AgentTeam, TeammateInfo
from .shared_task import SharedTask, SharedTaskStore

__all__ = [
    "AgentTeam",
    "Mailbox",
    "MailboxMessage",
    "SharedTask",
    "SharedTaskStore",
    "TeamError",
    "TeamManager",
    "TeammateInfo",
    "create_message",
]
