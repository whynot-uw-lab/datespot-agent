"""메모리 기반 단일 FIFO 실행 coordinator."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from datespot_agent.api.errors import CoordinatorUnavailableError
from datespot_agent.api.models import (
    HealthResponse,
    RunAccepted,
    RunJobStatus,
    RunStatusResponse,
)
from datespot_agent.graph import make_run_id
from datespot_agent.models import RunConfig, RunReport, RunStatus


class RunService(Protocol):
    async def run(
        self,
        config: RunConfig,
        *,
        run_id: str | None = None,
    ) -> RunReport: ...


class ReportStore(Protocol):
    def save(self, report: RunReport) -> Path: ...


@dataclass
class _RunRecord:
    run_id: str
    status: RunJobStatus
    config: RunConfig
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    report: RunReport | None = None
    error: str | None = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class RunCoordinator:
    """실행 접수, 상태, FIFO worker lifecycle을 관리함."""

    def __init__(
        self,
        runner: RunService,
        report_store: ReportStore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        run_id_factory: Callable[[], str] = make_run_id,
    ) -> None:
        self._runner = runner
        self._report_store = report_store
        self._clock = clock
        self._run_id_factory = run_id_factory
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._records: dict[str, _RunRecord] = {}
        self._worker: asyncio.Task[None] | None = None
        self._accepting = False
        self._active_run_id: str | None = None

    async def start(self) -> None:
        if self._worker is not None and not self._worker.done():
            return
        self._accepting = True
        self._worker = asyncio.create_task(
            self._work(),
            name="datespot-run-worker",
        )

    async def stop(self) -> None:
        self._accepting = False
        if self._worker is None:
            return
        self._worker.cancel()
        with suppress(asyncio.CancelledError):
            await self._worker
        self._worker = None

    def submit(self, config: RunConfig) -> RunAccepted:
        if not self._accepting:
            raise CoordinatorUnavailableError(
                "실행 coordinator가 요청을 받지 않음"
            )
        run_id = self._new_run_id()
        self._records[run_id] = _RunRecord(
            run_id=run_id,
            status=RunJobStatus.QUEUED,
            config=config.model_copy(deep=True),
            created_at=_utc(self._clock()),
        )
        self._queue.put_nowait(run_id)
        return RunAccepted(
            run_id=run_id,
            status=RunJobStatus.QUEUED,
            status_url=f"/runs/{run_id}",
            report_url=f"/runs/{run_id}/report",
        )

    def get_status(self, run_id: str) -> RunStatusResponse | None:
        record = self._records.get(run_id)
        if record is None:
            return None
        return RunStatusResponse(
            run_id=record.run_id,
            status=record.status,
            config=record.config.model_copy(deep=True),
            created_at=record.created_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
            report_available=record.report is not None,
            error=record.error,
        )

    def get_report(self, run_id: str) -> RunReport | None:
        record = self._records.get(run_id)
        if record is None or record.report is None:
            return None
        return record.report.model_copy(deep=True)

    def health(self) -> HealthResponse:
        return HealthResponse(
            accepting=self._accepting,
            active_run_id=self._active_run_id,
            queued_runs=self._queue.qsize(),
        )

    def _new_run_id(self) -> str:
        run_id = self._run_id_factory()
        while run_id in self._records:
            run_id = self._run_id_factory()
        return run_id

    async def _work(self) -> None:
        while True:
            run_id = await self._queue.get()
            try:
                await self._execute(self._records[run_id])
            finally:
                self._queue.task_done()

    async def _execute(self, record: _RunRecord) -> None:
        self._active_run_id = record.run_id
        record.status = RunJobStatus.RUNNING
        record.started_at = _utc(self._clock())
        try:
            report = await self._runner.run(
                record.config,
                run_id=record.run_id,
            )
            self._report_store.save(report)
            record.report = report.model_copy(deep=True)
            record.status = (
                RunJobStatus.COMPLETED
                if report.status is RunStatus.COMPLETED
                else RunJobStatus.FAILED
            )
        except asyncio.CancelledError:
            record.status = RunJobStatus.FAILED
            record.error = "서버 종료로 실행이 중단됨"
            raise
        except Exception as error:
            record.status = RunJobStatus.FAILED
            record.error = str(error).strip() or type(error).__name__
        finally:
            record.finished_at = _utc(self._clock())
            self._active_run_id = None
