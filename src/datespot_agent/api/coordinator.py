"""메모리 기반 단일 FIFO 실행 coordinator."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Protocol

from datespot_agent.api.errors import CoordinatorUnavailableError
from datespot_agent.api.events import RunEventPublisher, RunEventType
from datespot_agent.api.models import (
    HealthResponse,
    RunAccepted,
    RunJobStatus,
    RunStatusResponse,
)
from datespot_agent.graph import make_run_id
from datespot_agent.models import RunConfig, RunReport, RunStatus
from datespot_agent.observability import bind_log_context, log_event


_PUBLIC_EXECUTION_ERROR = "실행 처리 중 오류가 발생함"
_SHUTDOWN_ERROR = "서버 종료로 실행이 중단됨"
LOGGER = logging.getLogger(__name__)


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
        event_publisher: RunEventPublisher | None = None,
    ) -> None:
        self._runner = runner
        self._report_store = report_store
        self._clock = clock
        self._run_id_factory = run_id_factory
        self._events = event_publisher
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
        self._fail_queued_runs_for_shutdown()
        if self._worker is None:
            return
        self._worker.cancel()
        with suppress(asyncio.CancelledError):
            await self._worker
        self._worker = None

    def submit(self, config: RunConfig) -> RunAccepted:
        if not self._accepting:
            raise CoordinatorUnavailableError("실행 coordinator가 요청을 받지 않음")
        run_id = self._new_run_id()
        self._records[run_id] = _RunRecord(
            run_id=run_id,
            status=RunJobStatus.QUEUED,
            config=config.model_copy(deep=True),
            created_at=_utc(self._clock()),
        )
        if self._events is not None:
            self._events.open_run(run_id)
            snapshot = self.get_status(run_id)
            assert snapshot is not None
            self._events.lifecycle(run_id, RunEventType.QUEUED, snapshot)
        self._queue.put_nowait(run_id)
        log_event(
            LOGGER,
            "run.queued",
            "실행 대기열 등록",
            run_id=run_id,
            component="coordinator",
            status=RunJobStatus.QUEUED,
            queue_size=self._queue.qsize(),
            location=config.location,
            search_keyword=config.search_keyword,
            max_places=config.max_places,
        )
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
        started_at = monotonic()
        terminal_logged = False
        with bind_log_context(
            run_id=record.run_id,
            component="coordinator",
        ):
            self._active_run_id = record.run_id
            record.status = RunJobStatus.RUNNING
            record.started_at = _utc(self._clock())
            log_event(
                LOGGER,
                "run.started",
                "실행 처리 시작",
                status=record.status,
                queue_size=self._queue.qsize(),
                queue_wait_ms=self._datetime_duration_ms(
                    record.created_at,
                    record.started_at,
                ),
            )
            if self._events is not None:
                snapshot = self.get_status(record.run_id)
                assert snapshot is not None
                self._events.lifecycle(
                    record.run_id,
                    RunEventType.RUNNING,
                    snapshot,
                )
            try:
                report = await self._runner.run(
                    record.config,
                    run_id=record.run_id,
                )
                log_event(
                    LOGGER,
                    "report.save.started",
                    "리포트 저장 시작",
                    report_status=report.status,
                    result_count=len(report.results),
                    error_count=len(report.errors),
                )
                save_started_at = monotonic()
                try:
                    report_path = self._report_store.save(report)
                except Exception:
                    log_event(
                        LOGGER,
                        "report.save.failed",
                        "리포트 저장 실패",
                        level=logging.ERROR,
                        exc_info=True,
                        report_status=report.status,
                        duration_ms=self._elapsed_ms(save_started_at),
                    )
                    raise
                log_event(
                    LOGGER,
                    "report.save.completed",
                    "리포트 저장 완료",
                    report_status=report.status,
                    report_path=report_path,
                    duration_ms=self._elapsed_ms(save_started_at),
                )
                record.report = report.model_copy(deep=True)
                if self._events is not None:
                    self._events.report_saved(
                        record.run_id,
                        f"/reports/{record.run_id}",
                    )
                record.status = (
                    RunJobStatus.COMPLETED
                    if report.status is RunStatus.COMPLETED
                    else RunJobStatus.FAILED
                )
            except asyncio.CancelledError:
                record.status = RunJobStatus.FAILED
                record.error = _SHUTDOWN_ERROR
                log_event(
                    LOGGER,
                    "run.failed",
                    "서버 종료로 실행 중단",
                    level=logging.ERROR,
                    exc_info=True,
                    status=record.status,
                    duration_ms=self._elapsed_ms(started_at),
                )
                terminal_logged = True
                raise
            except Exception as error:
                record.status = RunJobStatus.FAILED
                record.error = str(error).strip() or type(error).__name__
                log_event(
                    LOGGER,
                    "run.failed",
                    "실행 처리 실패",
                    level=logging.ERROR,
                    exc_info=True,
                    status=record.status,
                    duration_ms=self._elapsed_ms(started_at),
                )
                terminal_logged = True
            finally:
                record.finished_at = _utc(self._clock())
                self._active_run_id = None
                if not terminal_logged:
                    log_event(
                        LOGGER,
                        (
                            "run.completed"
                            if record.status is RunJobStatus.COMPLETED
                            else "run.failed"
                        ),
                        (
                            "실행 처리 완료"
                            if record.status is RunJobStatus.COMPLETED
                            else "실패 리포트 처리 완료"
                        ),
                        level=(
                            logging.INFO
                            if record.status is RunJobStatus.COMPLETED
                            else logging.WARNING
                        ),
                        status=record.status,
                        report_available=record.report is not None,
                        result_count=(
                            len(record.report.results)
                            if record.report is not None
                            else 0
                        ),
                        duration_ms=self._elapsed_ms(started_at),
                    )
                self._publish_terminal(record)

    def _fail_queued_runs_for_shutdown(self) -> None:
        while True:
            try:
                run_id = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                record = self._records[run_id]
                if record.status is not RunJobStatus.QUEUED:
                    continue
                record.status = RunJobStatus.FAILED
                record.finished_at = _utc(self._clock())
                record.error = _SHUTDOWN_ERROR
                log_event(
                    LOGGER,
                    "run.failed",
                    "서버 종료로 대기 실행 취소",
                    run_id=record.run_id,
                    component="coordinator",
                    level=logging.WARNING,
                    status=record.status,
                    duration_ms=0,
                )
                self._publish_terminal(record)
            finally:
                self._queue.task_done()

    def _publish_terminal(self, record: _RunRecord) -> None:
        if self._events is None:
            return
        snapshot = self.get_status(record.run_id)
        assert snapshot is not None
        if snapshot.error is not None and snapshot.error != _SHUTDOWN_ERROR:
            snapshot.error = _PUBLIC_EXECUTION_ERROR
        event_type = (
            RunEventType.COMPLETED
            if record.status is RunJobStatus.COMPLETED
            else RunEventType.FAILED
        )
        self._events.terminal(record.run_id, event_type, snapshot)

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, int((monotonic() - started_at) * 1_000))

    @staticmethod
    def _datetime_duration_ms(started_at: datetime, ended_at: datetime) -> int:
        return max(0, int((ended_at - started_at).total_seconds() * 1_000))
