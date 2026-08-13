"""One event codec shared by HTTP polling/SSE and Query SDK adapters."""

from __future__ import annotations

import json
from typing import Any, Mapping, Protocol

from ai_agent_platform.domain import AgentEvent


class EventStore(Protocol):
    def list(self, run_id: str, *, after: int = 0) -> list[Any]:
        ...

    def append(self, run_id: str, event: Any) -> Any:
        ...


class RuntimeEventStore:
    """Adapter over the Agent runtime's durable AgentRunStore."""

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    def list(self, run_id: str, *, after: int = 0) -> list[Any]:
        list_events = getattr(self._runtime, "list_events", None)
        return list_events(run_id, after=after) if callable(list_events) else []

    def append(self, run_id: str, event: Any) -> Any:
        run_store = getattr(self._runtime, "_run_store", None)
        append_event = getattr(run_store, "append_event", None)
        if not callable(append_event):
            raise RuntimeError("Agent runtime EventStore is not writable")
        return append_event(run_id, event)


class AgentEventEncoder:
    def from_stored(self, run_id: str, event: Any) -> AgentEvent:
        return AgentEvent(
            sequence=int(event.sequence),
            run_id=run_id,
            status=str(event.status),
            type=str(event.type),
            summary=str(event.summary),
            output=dict(event.output or {}),
            node=str(event.node) if event.node is not None else None,
        )

    def to_payload(
        self,
        event: AgentEvent,
        *,
        include_run_id: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "sequence": event.sequence,
            "type": event.type,
            "status": event.status,
            "node": event.node,
            "summary": event.summary,
            "output": event.output_dict(),
        }
        if include_run_id:
            payload["run_id"] = event.run_id
        return payload

    def encode_json(
        self,
        event: AgentEvent,
        *,
        include_run_id: bool = True,
    ) -> str:
        return json.dumps(
            self.to_payload(event, include_run_id=include_run_id),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def encode_sse(self, event: AgentEvent) -> str:
        return (
            f"id: {event.sequence}\n"
            f"event: {event.type}\n"
            f"data: {self.encode_json(event, include_run_id=False)}\n\n"
        )

    def decode(self, payload: Mapping[str, Any], *, run_id: str | None = None) -> AgentEvent:
        resolved_run_id = str(payload.get("run_id") or run_id or "")
        if not resolved_run_id:
            raise ValueError("Agent event run_id is required")
        return AgentEvent(
            sequence=int(payload.get("sequence", 0)),
            run_id=resolved_run_id,
            status=str(payload.get("status") or ""),
            type=str(payload.get("type") or ""),
            summary=str(payload.get("summary") or ""),
            output=dict(payload.get("output") or {}),
            node=(
                str(payload["node"])
                if payload.get("node") is not None
                else None
            ),
        )


__all__ = [
    "AgentEventEncoder",
    "EventStore",
    "RuntimeEventStore",
]
