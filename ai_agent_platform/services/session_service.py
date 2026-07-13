from __future__ import annotations

from ai_agent_platform.agents.game_agent import GameAgentRuntime
from ai_agent_platform.domain import Message, Session, SessionSummary, TokenUsageRecord
from ai_agent_platform.repositories import InMemorySessionRepository


class SessionService:
    """Coordinates session use cases without depending on HTTP details."""

    def __init__(
        self,
        repository: InMemorySessionRepository,
        agent_runtime: GameAgentRuntime,
    ) -> None:
        self._repository = repository
        self._agent_runtime = agent_runtime

    def create_session(self, user_id: str) -> Session:
        return self._repository.create_session(user_id=user_id)

    def list_sessions(self) -> list[Session]:
        return self._repository.list_sessions()

    def get_session(self, session_id: str) -> Session:
        return self._repository.get_session(session_id=session_id)

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        run_agent: bool = False,
    ) -> list[Message]:
        messages = [
            self._repository.add_message(
                session_id=session_id,
                role=role,
                content=content,
            )
        ]

        if run_agent and role == "user":
            decision = self._agent_runtime.decide(content)
            messages.append(
                self._repository.add_message(
                    session_id=session_id,
                    role="assistant",
                    content=(
                        f"agent_action={decision.kind}; "
                        f"confidence={decision.confidence:.2f}; "
                        f"reason={decision.reason}"
                    ),
                )
            )

        return messages

    def list_messages(self, session_id: str) -> list[Message]:
        return self._repository.list_messages(session_id=session_id)

    def build_chat_context(
        self,
        session_id: str,
        user_message: str,
        max_context_messages: int,
    ) -> list[dict[str, str]]:
        messages = self._repository.list_messages(session_id=session_id)
        recent_messages = messages[-max_context_messages:] if max_context_messages else []
        context = [
            {"role": message.role, "content": message.content}
            for message in recent_messages
            if message.role in {"system", "user", "assistant"}
        ]
        context.append({"role": "user", "content": user_message})
        return context

    def record_token_usage(
        self,
        session_id: str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> TokenUsageRecord:
        return self._repository.add_token_usage(
            session_id=session_id,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def list_token_usage(self, session_id: str) -> list[TokenUsageRecord]:
        return self._repository.list_token_usage(session_id=session_id)

    def get_session_summary(self, session_id: str) -> SessionSummary:
        messages = self._repository.list_messages(session_id=session_id)
        last_message = messages[-1].content if messages else None
        return SessionSummary(
            session_id=session_id,
            message_count=len(messages),
            last_message=last_message,
        )
