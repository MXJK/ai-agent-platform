"""Agent-run storage implementations used by the coding runtime."""

from threading import RLock

from ai_agent_platform.agents.coding.models import (
    AgentRunEvent,
    AgentRunRecord,
    AgentToolExecution,
)
from ai_agent_platform.domain import QueryLifecycle


class InMemoryAgentRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, AgentRunRecord] = {}
        self._events: dict[str, list[AgentRunEvent]] = {}
        self._event_keys: dict[str, set[str]] = {}
        self._tool_executions: dict[tuple[str, str], AgentToolExecution] = {}
        self._lock = RLock()

    def save(self, record: AgentRunRecord) -> None:
        with self._lock:
            current = self._runs.get(record.run_id)
            if (
                current is not None
                and current.status in QueryLifecycle.TERMINAL_STATUSES
            ):
                return
            self._runs[record.run_id] = record
            events = self._events.setdefault(record.run_id, [])
            keys = self._event_keys.setdefault(record.run_id, set())
            for key, event in events_for_record(record):
                if key in keys:
                    continue
                keys.add(key)
                events.append(
                    AgentRunEvent(
                        sequence=len(events) + 1,
                        type=event.type,
                        status=event.status,
                        node=event.node,
                        summary=event.summary,
                        output=event.output,
                    )
                )

    def get(self, run_id: str) -> AgentRunRecord:
        with self._lock:
            return self._runs[run_id]

    def get_latest_for_conversation(
        self,
        conversation_id: str,
    ) -> AgentRunRecord | None:
        with self._lock:
            return next(
                (
                    record
                    for record in reversed(self._runs.values())
                    if record.conversation_id == conversation_id
                ),
                None,
            )

    def list_events(self, run_id: str, *, after: int = 0) -> list[AgentRunEvent]:
        with self._lock:
            if run_id not in self._runs:
                raise KeyError(run_id)
            return [
                event
                for event in self._events.get(run_id, [])
                if event.sequence > after
            ]

    def append_event(self, run_id: str, event: AgentRunEvent) -> AgentRunEvent:
        with self._lock:
            if run_id not in self._runs:
                raise KeyError(run_id)
            events = self._events.setdefault(run_id, [])
            stored = AgentRunEvent(
                sequence=len(events) + 1,
                type=event.type,
                status=event.status,
                node=event.node,
                summary=event.summary,
                output=event.output,
            )
            events.append(stored)
            return stored

    def get_tool_execution(
        self, run_id: str, call_id: str
    ) -> AgentToolExecution | None:
        with self._lock:
            return self._tool_executions.get((run_id, call_id))

    def save_tool_execution(self, execution: AgentToolExecution) -> None:
        with self._lock:
            self._tool_executions[(execution.run_id, execution.call_id)] = execution


def events_for_record(
    record: AgentRunRecord,
) -> list[tuple[str, AgentRunEvent]]:
    """Return deterministic append-only events derivable from a run snapshot."""
    candidates: list[tuple[str, AgentRunEvent]] = [
        (
            "run:queued",
            AgentRunEvent(
                sequence=0,
                type="run_queued",
                status="queued",
                node=None,
                summary="Agent run accepted and queued for background execution.",
                output={
                    "run_id": record.run_id,
                    "conversation_id": record.conversation_id,
                    "workspace_id": record.workspace_id,
                },
            ),
        )
    ]
    if record.status != "queued" or record.trace:
        candidates.append(
            (
                "run:started",
                AgentRunEvent(
                    sequence=0,
                    type="run_started",
                    status="running",
                    node="setup_workspace",
                    summary="Background worker started executing the Agent graph.",
                    output={"thread_id": record.thread_id},
                ),
            )
        )
    for item in record.trace:
        step = int(item.get("step", len(candidates)))
        candidates.append(
            (
                f"trace:{step}",
                AgentRunEvent(
                    sequence=0,
                    type="node_completed",
                    status="running",
                    node=str(item.get("node") or "") or None,
                    summary=str(item.get("summary") or ""),
                    output=dict(item.get("output") or {}),
                ),
            )
        )
    terminal = QueryLifecycle.status_event(record.status)
    if terminal is not None:
        event_type, summary = terminal
        transition_identity = record.checkpoint_id or f"trace-{len(record.trace)}"
        candidates.append(
            (
                f"status:{record.status}:{transition_identity}",
                AgentRunEvent(
                    sequence=0,
                    type=event_type,
                    status=record.status,
                    node=record.latest_node,
                    summary=summary,
                    output={
                        "error": record.error,
                        "pending": record.pending_approval,
                        "answer_chars": len(
                            record.result.answer if record.result is not None else ""
                        ),
                    },
                ),
            )
        )
    return candidates
