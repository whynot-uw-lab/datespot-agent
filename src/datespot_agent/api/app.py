"""FastAPI 애플리케이션 조립과 실행 HTTP 계약."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status

from datespot_agent.api.coordinator import RunCoordinator
from datespot_agent.api.errors import CoordinatorUnavailableError
from datespot_agent.api.models import (
    HealthResponse,
    RunAccepted,
    RunJobStatus,
    RunStatusResponse,
)
from datespot_agent.api.runtime import AppRuntime, create_runtime
from datespot_agent.models import RunConfig, RunReport


RuntimeFactory = Callable[[], AppRuntime]


def _detail(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def create_app(runtime_factory: RuntimeFactory = create_runtime) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime = runtime_factory()
        app.state.runtime = runtime
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(title="datespot-agent", lifespan=lifespan)

    def coordinator(request: Request) -> RunCoordinator:
        return request.app.state.runtime.coordinator

    @app.get("/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        return coordinator(request).health()

    @app.post(
        "/runs",
        response_model=RunAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_run(config: RunConfig, request: Request) -> RunAccepted:
        try:
            return coordinator(request).submit(config)
        except CoordinatorUnavailableError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=_detail("coordinator_unavailable", str(error)),
            ) from error

    @app.get("/runs/{run_id}", response_model=RunStatusResponse)
    async def get_run(run_id: str, request: Request) -> RunStatusResponse:
        snapshot = coordinator(request).get_status(run_id)
        if snapshot is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_detail("run_not_found", "실행을 찾을 수 없음"),
            )
        return snapshot

    @app.get("/runs/{run_id}/report", response_model=RunReport)
    async def get_report(run_id: str, request: Request) -> RunReport:
        run_coordinator = coordinator(request)
        snapshot = run_coordinator.get_status(run_id)
        if snapshot is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_detail("run_not_found", "실행을 찾을 수 없음"),
            )
        report = run_coordinator.get_report(run_id)
        if report is not None:
            return report
        if snapshot.status in (RunJobStatus.QUEUED, RunJobStatus.RUNNING):
            code = "report_not_ready"
            message = "리포트가 아직 준비되지 않음"
        else:
            code = "report_unavailable"
            message = "실행 또는 저장 실패로 리포트를 사용할 수 없음"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_detail(code, message),
        )

    return app


app = create_app()
