"""Agent-run storage implementations used by the coding runtime."""

from threading import Lock

from ai_agent_platform.agents.coding.models import AgentRunRecord


class InMemoryAgentRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, AgentRunRecord] = {}
        self._lock = Lock()

    def save(self, record: AgentRunRecord) -> None:
        with self._lock:
            self._runs[record.run_id] = record

    def get(self, run_id: str) -> AgentRunRecord:
        with self._lock:
            return self._runs[run_id]
