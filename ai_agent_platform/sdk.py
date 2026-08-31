"""Import-safe Python facade over the entrypoint-independent Query Kernel."""

from __future__ import annotations

from typing import AsyncIterator

from ai_agent_platform.core import ConfigResolver, ResolvedConfig, Settings
from ai_agent_platform.domain import (
    AgentEvent,
    QueryCommand,
    QueryParams,
    QueryResult,
)
from ai_agent_platform.runtime import (
    ApplicationFactory,
    RuntimeContainer,
    build_runtime,
)
from ai_agent_platform.services import QueryService


class AgentSDK:
    """Thin SDK facade returning the Query Kernel's public domain contracts.

    The facade never installs signal handlers or assembles dependencies by hand.
    Callers may inject an existing ``RuntimeContainer`` or use ``from_settings``
    when the SDK should own one.
    """

    def __init__(
        self,
        runtime: RuntimeContainer,
        *,
        owns_runtime: bool = False,
    ) -> None:
        service = runtime.query_service
        if service is None:
            raise ValueError("RuntimeContainer does not provide QueryService")
        self._runtime = runtime
        self._query_service: QueryService = service
        self._owns_runtime = owns_runtime

    @classmethod
    def from_settings(
        cls,
        settings: Settings | ResolvedConfig | None = None,
        *,
        application_factory: ApplicationFactory | None = None,
    ) -> "AgentSDK":
        config = (
            settings
            if settings is not None
            else ConfigResolver.from_default_locations().resolve_process()
        )
        runtime = build_runtime(
            config,
            role="cli",
            factory=application_factory,
        )
        return cls(runtime, owns_runtime=True)

    @property
    def runtime(self) -> RuntimeContainer:
        return self._runtime

    @property
    def query_service(self) -> QueryService:
        return self._query_service

    def query(
        self,
        params: QueryParams,
        *,
        cursor: int = 0,
    ) -> AsyncIterator[AgentEvent]:
        """Start one Query and stream its canonical ``AgentEvent`` values."""

        return self._query_service.query(params, cursor=cursor)

    def events(
        self,
        run_id: str,
        *,
        actor_user_id: str | None = None,
        cursor: int = 0,
    ) -> AsyncIterator[AgentEvent]:
        return self._query_service.iter_events(
            run_id,
            actor_user_id=actor_user_id,
            cursor=cursor,
        )

    def resume(
        self,
        run_id: str,
        *,
        approved: bool = True,
        message: str = "",
        actor_user_id: str | None = None,
        cursor: int | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Resume any suspended Run and stream only events after suspension."""

        before = self._query_service.get_result(
            run_id,
            actor_user_id=actor_user_id,
        )
        if before.status == "waiting_approval":
            command = QueryCommand.RESUME
        else:
            command = QueryCommand.CONTINUE
        record = self._query_service.execute(
            command,
            run_id=run_id,
            approved=approved,
            message=message,
            actor_user_id=actor_user_id,
        )
        return self._query_service.iter_events(
            record.run_id,
            actor_user_id=actor_user_id,
            cursor=before.cursor if cursor is None else cursor,
        )

    def control(
        self,
        run_id: str,
        command: QueryCommand | str,
        *,
        message: str = "",
        actor_user_id: str | None = None,
    ) -> QueryResult:
        """Apply a lifecycle control and return the canonical result snapshot."""

        resolved = QueryCommand(command)
        if resolved in {QueryCommand.START, QueryCommand.RESUME}:
            raise ValueError(
                "control accepts continue, pause, steer, compact, or cancel"
            )
        record = self._query_service.execute(
            resolved,
            run_id=run_id,
            message=message,
            actor_user_id=actor_user_id,
        )
        return self._query_service.get_result(
            record.run_id,
            actor_user_id=actor_user_id,
        )

    def result(
        self,
        run_id: str,
        *,
        actor_user_id: str | None = None,
    ) -> QueryResult:
        return self._query_service.get_result(
            run_id,
            actor_user_id=actor_user_id,
        )

    def close(self) -> None:
        if self._owns_runtime:
            self._runtime.close()

    def __enter__(self) -> "AgentSDK":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    async def __aenter__(self) -> "AgentSDK":
        return self

    async def __aexit__(self, *_: object) -> None:
        self.close()


__all__ = ["AgentSDK"]
