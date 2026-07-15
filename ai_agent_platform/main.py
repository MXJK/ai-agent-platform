from __future__ import annotations

from fastapi import FastAPI

from ai_agent_platform.agents import CodingAgentRuntime, GameAgentRuntime
from ai_agent_platform.api import create_api_router
from ai_agent_platform.core import Settings
from ai_agent_platform.integrations import LLMClient, RAGService, create_rag_service
from ai_agent_platform.repositories import (
    InMemoryRepositoryIndexRepository,
    InMemorySessionRepository,
    PostgresAgentRunRepository,
    PostgresDocumentRepository,
    PostgresRepositoryIndexRepository,
    PostgresSessionRepository,
)
from ai_agent_platform.services import RepositoryIndexingService, SessionService


def create_app(
    settings: Settings | None = None,
    llm_client: LLMClient | None = None,
    rag_service: RAGService | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    repository = _create_session_repository(settings)
    agent_runtime = GameAgentRuntime()
    llm_client = llm_client or LLMClient(settings)
    rag_service = rag_service or create_rag_service(
        settings,
        document_store=_create_document_store(settings),
    )
    coding_agent_runtime = CodingAgentRuntime(
        rag_service=rag_service,
        run_store=_create_agent_run_store(settings),
        checkpointer=_create_langgraph_checkpointer(settings),
    )
    repository_indexing_service = RepositoryIndexingService(
        rag_service=rag_service,
        index_store=_create_repository_index_store(settings),
    )
    session_service = SessionService(
        repository=repository,
        agent_runtime=agent_runtime,
    )

    app = FastAPI(title=settings.app_name)
    app.include_router(
        create_api_router(
            session_service=session_service,
            llm_client=llm_client,
            rag_service=rag_service,
            coding_agent_runtime=coding_agent_runtime,
            repository_indexing_service=repository_indexing_service,
            settings=settings,
        ),
        prefix=settings.api_prefix,
    )
    return app


def _create_session_repository(settings: Settings):
    if settings.session_repository == "memory":
        return InMemorySessionRepository()
    if settings.session_repository == "postgres":
        return PostgresSessionRepository(database_url=settings.database_url)
    raise ValueError(f"unsupported session repository: {settings.session_repository}")


def _create_agent_run_store(settings: Settings):
    if settings.agent_run_store == "memory":
        return None
    if settings.agent_run_store == "postgres":
        return PostgresAgentRunRepository(database_url=settings.database_url)
    raise ValueError(f"unsupported agent run store: {settings.agent_run_store}")


def _create_document_store(settings: Settings):
    if settings.document_store == "memory":
        return None
    if settings.document_store == "postgres":
        return PostgresDocumentRepository(database_url=settings.database_url)
    raise ValueError(f"unsupported document store: {settings.document_store}")


def _create_repository_index_store(settings: Settings):
    if settings.repository_index_store == "memory":
        return InMemoryRepositoryIndexRepository()
    if settings.repository_index_store == "postgres":
        return PostgresRepositoryIndexRepository(database_url=settings.database_url)
    raise ValueError(
        f"unsupported repository index store: {settings.repository_index_store}"
    )


def _create_langgraph_checkpointer(settings: Settings):
    if settings.langgraph_checkpointer == "memory":
        return None
    if settings.langgraph_checkpointer == "postgres":
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg_pool import ConnectionPool
        except ImportError as exc:
            raise RuntimeError(
                "langgraph-checkpoint-postgres and psycopg-pool are required "
                "for LANGGRAPH_CHECKPOINTER=postgres"
            ) from exc

        pool = ConnectionPool(
            conninfo=settings.database_url,
            kwargs={"autocommit": True},
        )
        checkpointer = PostgresSaver(pool)
        checkpointer.setup()
        return checkpointer
    raise ValueError(
        f"unsupported LangGraph checkpointer: {settings.langgraph_checkpointer}"
    )


app = create_app()
