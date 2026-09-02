"""Checkpoint lookup and LangGraph resume coordination."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from langgraph.checkpoint.base import create_checkpoint
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from ai_agent_platform.agents.coding.models import AgentRunRecord, CodingAgentState
from ai_agent_platform.integrations.llm import collect_llm_usage


class CheckpointResumeCoordinator:
    """Keep LangGraph state and resume commands behind the runtime facade."""

    def __init__(
        self,
        *,
        graph: Any,
        recursion_limit: int,
        checkpointer: Any,
        continue_on_recursion: bool = False,
    ) -> None:
        self._graph = graph
        self._recursion_limit = recursion_limit
        self._checkpointer = checkpointer
        self._continue_on_recursion = continue_on_recursion

    def config(self, thread_id: str) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": self._recursion_limit,
        }

    def invoke(
        self,
        initial_state: CodingAgentState,
        config: dict[str, Any],
    ) -> tuple[CodingAgentState, Any]:
        with collect_llm_usage() as usage:
            try:
                state, slice_count = self._invoke_slices(initial_state, config)
            except Exception as exc:
                setattr(exc, "llm_usage", usage)
                raise
        state = {**state, "graph_slice_count": slice_count}
        return state, usage

    def resume(
        self,
        record: AgentRunRecord,
        *,
        approved: bool,
        feedback: Optional[str],
        approved_by: str | None = None,
    ) -> tuple[CodingAgentState, Any, dict[str, Any]]:
        config = self.config(record.thread_id)
        with collect_llm_usage() as usage:
            try:
                state, slice_count = self._invoke_slices(
                    Command(
                        resume={
                            "approved": approved,
                            "feedback": feedback or "",
                            "message": feedback or "",
                            "action": "continue",
                            "approved_by": approved_by or "",
                        }
                    ),
                    config,
                )
            except Exception as exc:
                setattr(exc, "llm_usage", usage)
                raise
        state = {**state, "graph_slice_count": slice_count}
        return state, usage, config

    def snapshot_for(self, config: dict[str, Any]):
        try:
            return self._graph.get_state(config)
        except Exception:
            return None

    def history(self, thread_id: str, *, limit: int = 100) -> list[Any]:
        return list(
            self._graph.get_state_history(
                self.config(thread_id),
                limit=max(1, min(limit, 200)),
            )
        )

    def snapshot_by_id(self, thread_id: str, checkpoint_id: str) -> Any:
        for snapshot in self.history(thread_id, limit=200):
            configurable = snapshot.config.get("configurable", {})
            if str(configurable.get("checkpoint_id") or "") == checkpoint_id:
                return snapshot
        raise KeyError(checkpoint_id)

    def clone_checkpoint(
        self,
        snapshot: Any,
        *,
        thread_id: str,
        state_overrides: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Copy one durable graph boundary into an independent thread."""

        if self._checkpointer is None:
            raise RuntimeError("checkpoint restoration requires a checkpointer")
        stored = self._checkpointer.get_tuple(snapshot.config)
        if stored is None:
            raise KeyError("checkpoint state is unavailable")
        checkpoint = create_checkpoint(stored.checkpoint, None, 0)
        checkpoint["channel_values"].update(state_overrides)
        checkpoint_ns = str(
            snapshot.config.get("configurable", {}).get("checkpoint_ns") or ""
        )
        checkpoint_metadata = dict(stored.metadata or {})
        checkpoint_metadata.update(metadata)
        checkpoint_metadata["parents"] = {}
        new_config = self._checkpointer.put(
            {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                }
            },
            checkpoint,
            checkpoint_metadata,
            dict(checkpoint["channel_versions"]),
        )

        writes_by_task: dict[str, list[tuple[str, Any]]] = defaultdict(list)
        overridden = set(state_overrides)
        for task_id, channel, value in stored.pending_writes or []:
            if channel in {"__interrupt__", "__resume__"} or channel in overridden:
                continue
            writes_by_task[str(task_id)].append((str(channel), value))
        for task_id, writes in writes_by_task.items():
            self._checkpointer.put_writes(new_config, writes, task_id)
        return new_config

    def invoke_from_checkpoint(
        self,
        config: dict[str, Any],
    ) -> tuple[CodingAgentState, Any]:
        with collect_llm_usage() as usage:
            try:
                state, slice_count = self._invoke_slices(None, config)
            except Exception as exc:
                setattr(exc, "llm_usage", usage)
                raise
        state = {**state, "graph_slice_count": slice_count}
        return state, usage

    def _invoke_slices(
        self,
        value: Any,
        config: dict[str, Any],
    ) -> tuple[CodingAgentState, int]:
        """Continue a checkpointed graph after each finite recursion slice.

        LangGraph requires a finite ``recursion_limit``. In unbounded Run mode
        that value is an execution slice size, not a semantic task limit: a
        recursion exception is resumable only when the durable snapshot exposes
        pending nodes. Other exceptions and non-resumable snapshots still fail.
        """

        slice_count = 0
        current = value
        while True:
            slice_count += 1
            try:
                return self._graph.invoke(current, config), slice_count
            except GraphRecursionError:
                if not self._continue_on_recursion:
                    raise
                snapshot = self.snapshot_for(config)
                if snapshot is None or not tuple(snapshot.next or ()):
                    raise
                current = None
