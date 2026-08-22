"""Trajectory eval endpoints.

Runs are started by a person and execute in the background, because a run
against a real provider spends money and takes minutes. The page polls the run
detail while it is in progress.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from ai_agent_platform.evaluation.service import (
    EvalProviderUnavailableError,
    EvalRunInProgressError,
    EvalRunNotFoundError,
    EvalService,
)
from ai_agent_platform.schemas.evals import (
    EvalBaselineResponse,
    EvalCatalogueResponse,
    EvalProviderResponse,
    EvalRunDetailResponse,
    EvalRunListResponse,
    EvalRunStartRequest,
    EvalRunSummaryResponse,
)


SUPPORTED_PROVIDERS = frozenset(
    {"anthropic", "deepseek", "fake", "google", "openai"}
)


def create_evals_router(
    eval_service: EvalService | None,
    model_registry: object | None = None,
) -> APIRouter:
    router = APIRouter()

    def _service() -> EvalService:
        if eval_service is None:
            raise HTTPException(
                status_code=503,
                detail="evaluation is not available in this runtime",
            )
        return eval_service

    @router.get("/evals/catalogue", response_model=EvalCatalogueResponse)
    def catalogue() -> EvalCatalogueResponse:
        service = _service()
        payload = service.catalogue()
        baselines = [
            EvalBaselineResponse.from_domain(baseline)
            for provider in sorted(SUPPORTED_PROVIDERS)
            if (baseline := service.get_baseline(provider)) is not None
        ]
        return EvalCatalogueResponse(
            **payload,
            metric_directions=_metric_directions(),
            active_run_id=service.active_run_id,
            baselines=baselines,
            providers=[
                EvalProviderResponse(**item)
                for item in service.available_providers()
            ],
        )

    @router.post(
        "/evals/runs",
        response_model=EvalRunSummaryResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def start_run(request: EvalRunStartRequest) -> EvalRunSummaryResponse:
        service = _service()
        provider = request.provider.strip().lower()
        if provider not in SUPPORTED_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported provider: {request.provider}",
            )
        try:
            record = service.start_run(
                provider=provider,
                model=request.model.strip(),
            )
        except EvalRunInProgressError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except EvalProviderUnavailableError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return EvalRunSummaryResponse.from_domain(record)

    @router.get("/evals/runs", response_model=EvalRunListResponse)
    def list_runs(
        provider: str | None = None,
        limit: int = Query(default=20, ge=1, le=100),
    ) -> EvalRunListResponse:
        service = _service()
        records = service.list_runs(provider=provider, limit=limit)
        return EvalRunListResponse(
            runs=[EvalRunSummaryResponse.from_domain(item) for item in records],
            active_run_id=service.active_run_id,
        )

    @router.get("/evals/runs/{run_id}", response_model=EvalRunDetailResponse)
    def get_run(run_id: str) -> EvalRunDetailResponse:
        service = _service()
        try:
            record = service.get_run(run_id)
        except EvalRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="eval run not found") from exc
        return EvalRunDetailResponse.from_domain(
            record,
            service.get_baseline(record.provider),
        )

    @router.post(
        "/evals/runs/{run_id}/baseline",
        response_model=EvalBaselineResponse,
    )
    def pin_baseline(run_id: str) -> EvalBaselineResponse:
        service = _service()
        try:
            baseline = service.pin_baseline(run_id)
        except EvalRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="eval run not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return EvalBaselineResponse.from_domain(baseline)

    return router


def _metric_directions() -> dict[str, str]:
    from ai_agent_platform.evaluation.models import METRIC_DIRECTIONS

    return dict(METRIC_DIRECTIONS)
