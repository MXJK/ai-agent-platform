"""Stable, entrypoint-independent Query Kernel contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
from types import MappingProxyType
from typing import Any, Mapping


class QueryCommand(str, Enum):
    START = "start"
    RESUME = "resume"
    CONTINUE = "continue"
    STEER = "steer"
    PAUSE = "pause"
    CANCEL = "cancel"
    COMPACT = "compact"


@dataclass(frozen=True)
class QueryParams:
    """Everything an entrypoint may supply to start one Agent query."""

    conversation_id: str
    message: str
    workspace_id: str | None = None
    focus_files: tuple[str, ...] = ()
    provider: str | None = None
    model: str | None = None
    thinking_level: str | None = None
    routing_policy: str | None = None
    mode: str | None = None
    cwd: str | None = None
    additional_workspace_ids: tuple[str, ...] = ()
    actor_user_id: str | None = None
    skill_name: str | None = None
    skill_arguments: tuple[str, ...] = ()
    preferred_tool_name: str | None = None
    evaluation: bool = False
    evaluation_knowledge_base_ids: tuple[str, ...] = ()
    entrypoint: str = "sdk"
    entrypoint_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "focus_files",
            tuple(str(item) for item in self.focus_files),
        )
        object.__setattr__(
            self,
            "additional_workspace_ids",
            tuple(str(item) for item in self.additional_workspace_ids),
        )
        object.__setattr__(
            self,
            "skill_arguments",
            tuple(str(item) for item in self.skill_arguments),
        )
        object.__setattr__(
            self,
            "evaluation_knowledge_base_ids",
            tuple(str(item) for item in self.evaluation_knowledge_base_ids),
        )
        object.__setattr__(
            self,
            "entrypoint_metadata",
            _freeze_json(dict(self.entrypoint_metadata)),
        )

    def metadata_dict(self) -> dict[str, Any]:
        return dict(_thaw_json(self.entrypoint_metadata))


@dataclass(frozen=True)
class AgentEvent:
    sequence: int
    run_id: str
    status: str
    type: str
    summary: str
    output: Mapping[str, Any] = field(default_factory=dict)
    node: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "output", _freeze_json(dict(self.output)))

    def output_dict(self) -> dict[str, Any]:
        return dict(_thaw_json(self.output))


@dataclass(frozen=True)
class QueryResult:
    run_id: str
    status: str
    cursor: int
    output: Mapping[str, Any] = field(default_factory=dict)
    resumable: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "output", _freeze_json(dict(self.output)))

    def output_dict(self) -> dict[str, Any]:
        return dict(_thaw_json(self.output))


class QueryLifecycle:
    """Single source of truth for commands and durable Run status transitions."""

    ACTIVE_STATUSES = frozenset({"queued", "running"})
    SUSPENDED_STATUSES = frozenset(
        {"waiting_approval", "waiting_input", "paused"}
    )
    TERMINAL_STATUSES = frozenset(
        {"completed", "partial", "blocked", "cancelled", "failed"}
    )
    ALL_STATUSES = ACTIVE_STATUSES | SUSPENDED_STATUSES | TERMINAL_STATUSES
    STREAM_STOP_STATUSES = SUSPENDED_STATUSES | TERMINAL_STATUSES

    _COMMAND_STATUSES = {
        QueryCommand.RESUME: frozenset({"waiting_approval"}),
        QueryCommand.CONTINUE: frozenset({"waiting_input", "paused"}),
        QueryCommand.STEER: ACTIVE_STATUSES | SUSPENDED_STATUSES,
        QueryCommand.PAUSE: frozenset({"running"}),
        QueryCommand.CANCEL: ACTIVE_STATUSES | SUSPENDED_STATUSES,
        QueryCommand.COMPACT: frozenset({"running", "paused"}),
    }
    _TRANSITIONS = {
        "queued": frozenset({"queued", "running", "cancelled", "failed"}),
        "running": frozenset(
            {
                "running",
                "waiting_approval",
                "waiting_input",
                "paused",
                "completed",
                "partial",
                "blocked",
                "cancelled",
                "failed",
            }
        ),
        "waiting_approval": frozenset(
            {"waiting_approval", "running", "cancelled", "failed"}
        ),
        "waiting_input": frozenset(
            {"waiting_input", "running", "cancelled", "failed"}
        ),
        "paused": frozenset({"paused", "running", "cancelled", "failed"}),
        "completed": frozenset({"completed"}),
        "partial": frozenset({"partial"}),
        "blocked": frozenset({"blocked"}),
        "cancelled": frozenset({"cancelled"}),
        "failed": frozenset({"failed"}),
    }
    _STATUS_EVENTS = {
        "waiting_approval": (
            "approval_required",
            "Agent run is waiting for approval.",
        ),
        "waiting_input": ("input_required", "Agent run is waiting for user input."),
        "paused": ("run_paused", "Agent run paused at a safe boundary."),
        "completed": ("run_completed", "Agent run completed."),
        "partial": ("run_partial", "Agent run stopped with a partial result."),
        "blocked": ("run_blocked", "Agent run is blocked."),
        "cancelled": ("run_cancelled", "Agent run was cancelled."),
        "failed": ("run_failed", "Agent run failed."),
    }

    @classmethod
    def assert_command(cls, command: QueryCommand | str, status: str) -> None:
        resolved = QueryCommand(command)
        if resolved is QueryCommand.START:
            raise ValueError("start does not target an existing Run")
        if status not in cls._COMMAND_STATUSES[resolved]:
            raise QueryStateError(resolved, status)

    @classmethod
    def assert_transition(cls, previous: str | None, current: str) -> None:
        if current not in cls.ALL_STATUSES:
            raise ValueError(f"unsupported Query lifecycle status: {current}")
        if previous is None:
            if current != "queued":
                raise QueryTransitionError(previous, current)
            return
        if current not in cls._TRANSITIONS.get(previous, frozenset()):
            raise QueryTransitionError(previous, current)

    @classmethod
    def status_event(cls, status: str) -> tuple[str, str] | None:
        return cls._STATUS_EVENTS.get(status)

    @classmethod
    def is_resumable(cls, status: str) -> bool:
        return status in cls.SUSPENDED_STATUSES


class QueryStateError(RuntimeError):
    def __init__(self, command: QueryCommand, status: str) -> None:
        super().__init__(
            f"Query command {command.value} is invalid while Run status is {status}"
        )
        self.command = command
        self.status = status


class QueryTransitionError(RuntimeError):
    def __init__(self, previous: str | None, current: str) -> None:
        super().__init__(f"invalid Query lifecycle transition: {previous} -> {current}")
        self.previous = previous
        self.current = current


def _freeze_json(value: Any) -> Any:
    try:
        copied = json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("Query metadata and output must be JSON serializable") from exc
    return _freeze_copied_json(copied)


def _freeze_copied_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {str(key): _freeze_copied_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_copied_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


__all__ = [
    "AgentEvent",
    "QueryCommand",
    "QueryLifecycle",
    "QueryParams",
    "QueryResult",
    "QueryStateError",
    "QueryTransitionError",
]
