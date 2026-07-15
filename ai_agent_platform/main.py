from __future__ import annotations

from fastapi import FastAPI

from ai_agent_platform.agents import CodingAgentRuntime, GameAgentRuntime
from ai_agent_platform.api import create_api_router
from ai_agent_platform.core import Settings
from ai_agent_platform.integrations import LLMClient, RAGService, create_rag_service
from ai_agent_platform.repositories import InMemorySessionRepository
from ai_agent_platform.services import SessionService


def create_app(
    settings: Settings | None = None,
    llm_client: LLMClient | None = None,
    rag_service: RAGService | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    repository = InMemorySessionRepository()
    agent_runtime = GameAgentRuntime()
    llm_client = llm_client or LLMClient(settings)
    rag_service = rag_service or create_rag_service(settings)
    coding_agent_runtime = CodingAgentRuntime(rag_service=rag_service)
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
            settings=settings,
        ),
        prefix=settings.api_prefix,
    )
    return app


app = create_app()
