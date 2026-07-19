from fastapi import APIRouter, HTTPException, Path, status

from ai_agent_platform.schemas import RepositoryIndexRequest, RepositoryIndexResponse
from ai_agent_platform.services import RepositoryIndexingError, RepositoryIndexingService


def create_repositories_router(
    repository_indexing_service: RepositoryIndexingService,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/repositories/{repository_id}/index",
        response_model=RepositoryIndexResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def index_repository(
        request: RepositoryIndexRequest,
        repository_id: str = Path(
            min_length=1,
            max_length=128,
            pattern=r"^[a-zA-Z0-9_-]+$",
        ),
    ) -> RepositoryIndexResponse:
        try:
            result = repository_indexing_service.index_repository(
                repository_id=repository_id,
                root_path=request.root_path,
                include_patterns=request.include_patterns,
                exclude_patterns=request.exclude_patterns,
                max_file_size=request.max_file_size,
            )
        except RepositoryIndexingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RepositoryIndexResponse.from_domain(result)

    return router
