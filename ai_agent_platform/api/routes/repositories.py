from fastapi import APIRouter, HTTPException, Path, Request, Response, status

from ai_agent_platform.core import TaskQueueError
from ai_agent_platform.schemas import RepositoryIndexJobResponse, RepositoryIndexRequest
from ai_agent_platform.services import (
    RepositoryIndexConflictError,
    RepositoryIndexingError,
    RepositoryIndexJobNotFoundError,
    RepositoryIndexingService,
)


def create_repositories_router(
    repository_indexing_service: RepositoryIndexingService,
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/repositories/{repository_id}/index",
        response_model=RepositoryIndexJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def index_repository(
        request: RepositoryIndexRequest,
        http_request: Request,
        response: Response,
        repository_id: str = Path(
            min_length=1,
            max_length=128,
            pattern=r"^[a-zA-Z0-9_-]+$",
        ),
    ) -> RepositoryIndexJobResponse:
        try:
            job = repository_indexing_service.submit_index_repository(
                repository_id=repository_id,
                root_path=request.root_path,
                include_patterns=request.include_patterns,
                exclude_patterns=request.exclude_patterns,
                max_file_size=request.max_file_size,
            )
        except RepositoryIndexConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except TaskQueueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except RepositoryIndexingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        response.headers["Location"] = f"{http_request.url.path}-jobs/{job.id}"
        return RepositoryIndexJobResponse.from_domain(job)

    @router.get(
        "/repositories/{repository_id}/index-jobs/{job_id}",
        response_model=RepositoryIndexJobResponse,
    )
    def get_repository_index_job(
        job_id: str = Path(
            min_length=1,
            max_length=128,
            pattern=r"^[a-zA-Z0-9_-]+$",
        ),
        repository_id: str = Path(
            min_length=1,
            max_length=128,
            pattern=r"^[a-zA-Z0-9_-]+$",
        ),
    ) -> RepositoryIndexJobResponse:
        try:
            job = repository_indexing_service.get_index_job(
                repository_id=repository_id,
                job_id=job_id,
            )
        except RepositoryIndexJobNotFoundError as exc:
            raise HTTPException(status_code=404, detail="index job not found") from exc
        return RepositoryIndexJobResponse.from_domain(job)

    return router
