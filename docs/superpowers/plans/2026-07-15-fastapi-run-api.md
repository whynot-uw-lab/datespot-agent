# FastAPI Run API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** HTTP로 탐색 실행을 접수하고 단일 FIFO worker에서 처리하며 동일 `run_id`로 상태와 저장된 `RunReport`를 조회하는 FastAPI API를 구현함.

**Architecture:** FastAPI lifespan이 `AppRuntime`과 `RunCoordinator`를 시작·종료함. Coordinator는 메모리 registry와 `asyncio.Queue`를 소유하고 기존 `GraphRunService`와 `JsonReportStore`를 순차 조합함. API route는 HTTP 계약 변환만 담당함.

**Tech Stack:** Python 3.13, FastAPI 0.139+, Uvicorn 0.51+, HTTPX2 2.5+, Pydantic v2, asyncio, unittest, uv

## Global Constraints

- 메모리 단일 프로세스이며 서버 재시작 후 상태 복구 없음
- FIFO worker 하나, graph 동시 실행 최대 1개
- API job ID와 `RunReport.run_id` 동일
- JSON 저장 성공 후에만 report 조회 허용
- graph failed report와 실행·저장 예외 구분
- JSON 필드 camelCase, datetime UTC
- 인증·CORS·SSE·취소·목록 API·queue 상한 제외
- 기본 서버 bind 안내 `127.0.0.1`
- 사용자 변경 `blind-date-recommend.iml` 제외

---

### Task 1: Support caller-provided graph run IDs

**Files:**

- Create: `tests/test_graph_service.py`
- Modify: `src/datespot_agent/graph/service.py`
- Modify: `src/datespot_agent/graph/__init__.py`

**Interfaces:**

- Consumes: `GraphRunService.run(config: RunConfig) -> RunReport`
- Produces: `make_run_id(clock: Callable[[], datetime] = utc_now) -> str`
- Produces: `GraphRunService.run(config: RunConfig, *, run_id: str | None = None) -> RunReport`

- [x] **Step 1: Write failing external-ID and auto-ID tests**

```python
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from datespot_agent.graph import GraphRunService
from datespot_agent.models import RunConfig


class EmptyBrowserService:
    def __init__(self) -> None:
        self.closed_run_ids: list[str] = []

    async def start_session(self, run_id: str) -> None:
        return None

    async def search_candidates(self, run_id: str, config: RunConfig):
        return []

    async def close_session(self, run_id: str) -> None:
        self.closed_run_ids.append(run_id)


def build_service(browser: EmptyBrowserService) -> GraphRunService:
    return GraphRunService(
        browser_service=browser,
        photo_agent=object(),
        review_agent=object(),
        scoring_service=object(),
        clock=lambda: datetime(2026, 7, 15, 1, 2, 3, tzinfo=timezone.utc),
    )


class GraphRunIdTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_uses_caller_provided_run_id(self):
        browser = EmptyBrowserService()
        report = await build_service(browser).run(
            RunConfig(location="성수역", search_keyword="일식", max_places=1),
            run_id="run_20260715_010203_api00001",
        )
        self.assertEqual(report.run_id, "run_20260715_010203_api00001")
        self.assertIn(report.run_id, browser.closed_run_ids)

    async def test_run_generates_existing_safe_format_when_id_is_omitted(self):
        report = await build_service(EmptyBrowserService()).run(
            RunConfig(location="성수역", search_keyword="일식", max_places=1)
        )
        self.assertRegex(report.run_id, r"^run_20260715_010203_[0-9a-f]{8}$")
```

- [x] **Step 2: Run tests and verify RED**

Run: `uv run python -m unittest tests.test_graph_service -v`

Expected: external-ID test errors because `run()` does not accept `run_id`.

- [x] **Step 3: Extract ID creation and add optional injection**

```python
# src/datespot_agent/graph/service.py
def make_run_id(clock: Callable[[], datetime] = utc_now) -> str:
    timestamp = GraphRunService._ensure_utc(clock()).strftime("%Y%m%d_%H%M%S")
    return f"run_{timestamp}_{uuid4().hex[:8]}"


class GraphRunService:
    async def run(
        self,
        config: RunConfig,
        *,
        run_id: str | None = None,
    ) -> RunReport:
        effective_run_id = run_id or make_run_id(self._clock)
        initial_state = GraphState(run_id=effective_run_id, config=config)
        self._emit(f"[run:{effective_run_id}] 시작")

    def _make_run_id(self) -> str:
        return make_run_id(self._clock)
```

```python
# src/datespot_agent/graph/__init__.py
from datespot_agent.graph.service import GraphRunService, make_run_id

__all__ = ["GraphRunService", "make_run_id"]
```

- [x] **Step 4: Run focused and existing runner tests**

Run: `uv run python -m unittest tests.test_graph_service tests.test_run_graph_live -v`

Expected: all tests pass.

- [x] **Step 5: Commit graph ID contract**

```bash
git add src/datespot_agent/graph tests/test_graph_service.py
git commit -m "feat: allow caller-provided graph run IDs"
```

---

### Task 2: Implement the in-memory FIFO run coordinator

**Files:**

- Create: `src/datespot_agent/api/__init__.py`
- Create: `src/datespot_agent/api/models.py`
- Create: `src/datespot_agent/api/errors.py`
- Create: `src/datespot_agent/api/coordinator.py`
- Create: `tests/test_run_coordinator.py`

**Interfaces:**

- Consumes: `GraphRunService.run(config, *, run_id=None) -> RunReport`
- Consumes: `JsonReportStore.save(report) -> Path`
- Produces: `RunCoordinator.start()`, `stop()`, `submit()`, `get_status()`, `get_report()`, `health()`
- Produces: `RunAccepted`, `RunStatusResponse`, `HealthResponse`, `RunJobStatus`
- Produces: `CoordinatorUnavailableError`

- [x] **Step 1: Write failing coordinator state and FIFO tests**

```python
from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from pathlib import Path

from datespot_agent.api.coordinator import RunCoordinator
from datespot_agent.api.errors import CoordinatorUnavailableError
from datespot_agent.api.models import RunJobStatus
from datespot_agent.models import RunConfig, RunReport, RunStatus
from datespot_agent.reporting import ReportStorageError


NOW = datetime(2026, 7, 15, 1, 2, 3, tzinfo=timezone.utc)


def make_config(location: str = "성수역") -> RunConfig:
    return RunConfig(location=location, search_keyword="일식", max_places=1)


def make_report(run_id: str, *, status: RunStatus = RunStatus.COMPLETED):
    return RunReport(
        run_id=run_id,
        status=status,
        config=make_config(),
        created_at=NOW,
    )


class ControlledRunner:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.gates: dict[str, asyncio.Event] = {}
        self.status = RunStatus.COMPLETED

    async def run(self, config, *, run_id=None):
        self.started.append(run_id)
        gate = self.gates.setdefault(run_id, asyncio.Event())
        await gate.wait()
        return make_report(run_id, status=self.status)


class RecordingStore:
    def __init__(self, error: Exception | None = None) -> None:
        self.saved: list[RunReport] = []
        self.error = error

    def save(self, report: RunReport) -> Path:
        if self.error is not None:
            raise self.error
        self.saved.append(report)
        return Path("reports") / f"{report.run_id}.json"


async def wait_until(predicate, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


class RunCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runner = ControlledRunner()
        self.store = RecordingStore()
        ids = iter(("run_first", "run_second", "run_third"))
        self.coordinator = RunCoordinator(
            self.runner,
            self.store,
            clock=lambda: NOW,
            run_id_factory=lambda: next(ids),
        )
        await self.coordinator.start()

    async def asyncTearDown(self):
        await self.coordinator.stop()

    async def test_submit_returns_queued_snapshot_immediately(self):
        accepted = self.coordinator.submit(make_config())
        self.assertEqual(accepted.run_id, "run_first")
        self.assertEqual(accepted.status, RunJobStatus.QUEUED)
        self.assertEqual(accepted.status_url, "/runs/run_first")

    async def test_worker_runs_jobs_one_at_a_time_in_fifo_order(self):
        first = self.coordinator.submit(make_config("성수역"))
        second = self.coordinator.submit(make_config("홍대입구역"))
        await wait_until(lambda: self.runner.started == [first.run_id])
        self.runner.gates[first.run_id].set()
        await wait_until(lambda: self.runner.started == [first.run_id, second.run_id])
        self.runner.gates[second.run_id].set()
        await wait_until(
            lambda: self.coordinator.get_status(second.run_id).finished_at is not None
        )
        self.assertEqual(
            self.coordinator.get_status(first.run_id).status,
            RunJobStatus.COMPLETED,
        )

    async def test_storage_failure_marks_job_failed_without_report(self):
        error = ReportStorageError("저장 실패", run_id="run_first")
        coordinator = RunCoordinator(
            self.runner,
            RecordingStore(error),
            clock=lambda: NOW,
            run_id_factory=lambda: "run_first",
        )
        await coordinator.start()
        try:
            accepted = coordinator.submit(make_config())
            await wait_until(lambda: accepted.run_id in self.runner.gates)
            self.runner.gates[accepted.run_id].set()
            await wait_until(
                lambda: coordinator.get_status(accepted.run_id).finished_at is not None
            )
            self.assertEqual(
                coordinator.get_status(accepted.run_id).status,
                RunJobStatus.FAILED,
            )
            self.assertFalse(coordinator.get_status(accepted.run_id).report_available)
            self.assertIsNone(coordinator.get_report(accepted.run_id))
        finally:
            await coordinator.stop()

    async def test_failed_graph_report_remains_available(self):
        self.runner.status = RunStatus.FAILED
        accepted = self.coordinator.submit(make_config())
        await wait_until(lambda: accepted.run_id in self.runner.gates)
        self.runner.gates[accepted.run_id].set()
        await wait_until(
            lambda: self.coordinator.get_status(accepted.run_id).finished_at
            is not None
        )
        snapshot = self.coordinator.get_status(accepted.run_id)
        self.assertEqual(snapshot.status, RunJobStatus.FAILED)
        self.assertTrue(snapshot.report_available)
        self.assertIsNotNone(self.coordinator.get_report(accepted.run_id))

    async def test_submit_requires_started_accepting_worker(self):
        await self.coordinator.stop()
        with self.assertRaises(CoordinatorUnavailableError):
            self.coordinator.submit(make_config())

    async def test_runner_exception_marks_job_failed_without_report(self):
        class FailingRunner:
            async def run(self, config, *, run_id=None):
                raise RuntimeError("graph crashed")

        coordinator = RunCoordinator(
            FailingRunner(),
            self.store,
            clock=lambda: NOW,
            run_id_factory=lambda: "run_failed",
        )
        await coordinator.start()
        try:
            accepted = coordinator.submit(make_config())
            await wait_until(
                lambda: coordinator.get_status(accepted.run_id).finished_at
                is not None
            )
            snapshot = coordinator.get_status(accepted.run_id)
            self.assertEqual(snapshot.status, RunJobStatus.FAILED)
            self.assertEqual(snapshot.error, "graph crashed")
            self.assertIsNone(coordinator.get_report(accepted.run_id))
        finally:
            await coordinator.stop()

    async def test_duplicate_generated_id_is_regenerated(self):
        ids = iter(("run_duplicate", "run_duplicate", "run_unique"))
        coordinator = RunCoordinator(
            self.runner,
            self.store,
            clock=lambda: NOW,
            run_id_factory=lambda: next(ids),
        )
        await coordinator.start()
        try:
            first = coordinator.submit(make_config())
            second = coordinator.submit(make_config())
            self.assertEqual(first.run_id, "run_duplicate")
            self.assertEqual(second.run_id, "run_unique")
        finally:
            await coordinator.stop()

    async def test_status_and_report_are_defensive_copies(self):
        accepted = self.coordinator.submit(make_config())
        await wait_until(lambda: accepted.run_id in self.runner.gates)
        self.runner.gates[accepted.run_id].set()
        await wait_until(
            lambda: self.coordinator.get_report(accepted.run_id) is not None
        )
        status = self.coordinator.get_status(accepted.run_id)
        report = self.coordinator.get_report(accepted.run_id)
        status.config.location = "변경됨"
        report.config.location = "변경됨"
        self.assertEqual(
            self.coordinator.get_status(accepted.run_id).config.location,
            "성수역",
        )
        self.assertEqual(
            self.coordinator.get_report(accepted.run_id).config.location,
            "성수역",
        )

    async def test_health_reports_one_active_and_one_queued_run(self):
        first = self.coordinator.submit(make_config())
        self.coordinator.submit(make_config("홍대입구역"))
        await wait_until(lambda: self.runner.started == [first.run_id])
        health = self.coordinator.health()
        self.assertEqual(health.active_run_id, first.run_id)
        self.assertEqual(health.queued_runs, 1)

    async def test_stop_marks_active_run_failed(self):
        accepted = self.coordinator.submit(make_config())
        await wait_until(lambda: self.runner.started == [accepted.run_id])
        await self.coordinator.stop()
        snapshot = self.coordinator.get_status(accepted.run_id)
        self.assertEqual(snapshot.status, RunJobStatus.FAILED)
        self.assertIn("서버 종료", snapshot.error)
```

- [x] **Step 2: Run tests and verify RED**

Run: `uv run python -m unittest tests.test_run_coordinator -v`

Expected: import failure because `datespot_agent.api` does not exist.

- [x] **Step 3: Implement API models and coordinator errors**

```python
# src/datespot_agent/api/__init__.py
"""FastAPI 실행 계층."""
```

```python
# src/datespot_agent/api/models.py
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from datespot_agent.models import CamelModel, RunConfig


class RunJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RunAccepted(CamelModel):
    run_id: str
    status: RunJobStatus
    status_url: str
    report_url: str


class RunStatusResponse(CamelModel):
    run_id: str
    status: RunJobStatus
    config: RunConfig
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    report_available: bool = False
    error: str | None = None


class HealthResponse(CamelModel):
    status: Literal["ok"] = "ok"
    accepting: bool
    active_run_id: str | None = None
    queued_runs: int = 0
```

```python
# src/datespot_agent/api/errors.py
class CoordinatorUnavailableError(RuntimeError):
    """Coordinator가 신규 실행을 접수할 수 없음."""
```

- [x] **Step 4: Implement the coordinator worker**

```python
# src/datespot_agent/api/coordinator.py
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
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
    def save(self, report: RunReport): ...


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
        self._worker = asyncio.create_task(self._work(), name="datespot-run-worker")

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
            raise CoordinatorUnavailableError("실행 coordinator가 요청을 받지 않음")
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
            report = await self._runner.run(record.config, run_id=record.run_id)
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
```

- [x] **Step 5: Run coordinator tests and confirm GREEN**

Run: `uv run python -m unittest tests.test_run_coordinator -v`

Expected: all coordinator tests pass.

- [x] **Step 6: Commit coordinator**

```bash
git add src/datespot_agent/api tests/test_run_coordinator.py
git commit -m "feat: add FIFO run coordinator"
```

---

### Task 3: Add runtime settings and production dependency assembly

**Files:**

- Modify: `src/datespot_agent/config.py`
- Create: `src/datespot_agent/api/runtime.py`
- Create: `tests/test_api_runtime.py`

**Interfaces:**

- Consumes: `RunCoordinator`
- Produces: `AppRuntime.start() -> None`, `AppRuntime.stop() -> None`
- Produces: `async create_runtime(settings: Settings | None = None) -> AppRuntime`
- Produces: `RuntimeConfigurationError`

- [x] **Step 1: Write failing settings and runtime lifecycle tests**

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from datespot_agent.api.runtime import (
    AppRuntime,
    RuntimeConfigurationError,
    create_runtime,
)
from datespot_agent.config import Settings


class ApiRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_settings_parse_api_paths(self):
        settings = Settings.model_validate(
            {
                "OPENAI_API_KEY": "key",
                "DATESPOT_REPORTS_ROOT": "custom-reports",
                "DATESPOT_CHROME_EXECUTABLE_PATH": "/tmp/chrome",
                "DATESPOT_BROWSER_USER_DATA_DIR": "~/.cache/test-profile",
            }
        )
        self.assertEqual(settings.reports_root, Path("custom-reports"))
        self.assertEqual(settings.chrome_executable_path, Path("/tmp/chrome"))

    async def test_create_runtime_rejects_empty_api_key(self):
        with self.assertRaises(RuntimeConfigurationError):
            await create_runtime(Settings(OPENAI_API_KEY=""))

    async def test_app_runtime_stops_all_resources(self):
        coordinator = Mock(start=AsyncMock(), stop=AsyncMock())
        browser = Mock(close_all=AsyncMock())
        client = Mock(close=AsyncMock())
        runtime = AppRuntime(coordinator, browser, client)
        await runtime.start()
        await runtime.stop()
        coordinator.start.assert_awaited_once()
        coordinator.stop.assert_awaited_once()
        browser.close_all.assert_awaited_once()
        client.close.assert_awaited_once()

    async def test_create_runtime_expands_and_wires_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chrome = root / "Chrome"
            chrome.write_text("binary", encoding="utf-8")
            settings = Settings.model_validate(
                {
                    "OPENAI_API_KEY": "key",
                    "DATESPOT_CHROME_EXECUTABLE_PATH": str(chrome),
                    "DATESPOT_BROWSER_USER_DATA_DIR": str(root / "profile"),
                    "DATESPOT_REPORTS_ROOT": str(root / "reports"),
                }
            )
            with (
                patch("datespot_agent.api.runtime.AsyncOpenAI") as client_type,
                patch("datespot_agent.api.runtime.ChromeCdpLauncher") as launcher_type,
                patch("datespot_agent.api.runtime.JsonReportStore") as store_type,
            ):
                runtime = await create_runtime(settings)
            launcher_type.assert_called_once_with(
                executable_path=chrome,
                user_data_dir=root / "profile",
            )
            store_type.assert_called_once_with(root / "reports")
            self.assertIs(runtime.openai_client, client_type.return_value)
```

- [x] **Step 2: Run tests and verify RED**

Run: `uv run python -m unittest tests.test_api_runtime -v`

Expected: import failure because `api.runtime` does not exist.

- [x] **Step 3: Add settings fields**

```python
# src/datespot_agent/config.py, inside Settings
from pathlib import Path


reports_root: Path = Field(
    default=Path("reports"),
    alias="DATESPOT_REPORTS_ROOT",
)
chrome_executable_path: Path = Field(
    default=Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    alias="DATESPOT_CHROME_EXECUTABLE_PATH",
)
browser_user_data_dir: Path = Field(
    default=Path("~/.cache/datespot-agent/chrome-profile"),
    alias="DATESPOT_BROWSER_USER_DATA_DIR",
)
```

- [x] **Step 4: Implement runtime assembly and cleanup**

```python
# src/datespot_agent/api/runtime.py
from __future__ import annotations

import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

from datespot_agent.analysis import (
    PhotoAnalysisAgent,
    PlaceScoringService,
    ReviewAnalysisAgent,
)
from datespot_agent.api.coordinator import RunCoordinator
from datespot_agent.browser import BrowserService, ChromeCdpLauncher
from datespot_agent.config import Settings, get_settings
from datespot_agent.graph import GraphRunService
from datespot_agent.reporting import JsonReportStore


logger = logging.getLogger(__name__)


class RuntimeConfigurationError(RuntimeError):
    """API runtime 시작에 필요한 설정이 잘못됨."""


@dataclass
class AppRuntime:
    coordinator: RunCoordinator
    browser_service: BrowserService
    openai_client: AsyncOpenAI

    async def start(self) -> None:
        await self.coordinator.start()

    async def stop(self) -> None:
        try:
            await self.coordinator.stop()
        finally:
            try:
                await self.browser_service.close_all()
            finally:
                await self.openai_client.close()


async def create_runtime(settings: Settings | None = None) -> AppRuntime:
    effective_settings = settings or get_settings()
    api_key = effective_settings.openai_api_key.strip()
    if not api_key:
        raise RuntimeConfigurationError("OPENAI_API_KEY가 비어 있음")
    chrome_path = effective_settings.chrome_executable_path.expanduser()
    if not chrome_path.is_file():
        raise RuntimeConfigurationError(
            f"Chrome 실행 파일을 찾지 못함: {chrome_path}"
        )
    profile_path = effective_settings.browser_user_data_dir.expanduser()
    reports_root = effective_settings.reports_root.expanduser()
    client = AsyncOpenAI(api_key=api_key)
    try:
        browser = BrowserService(
            headless=False,
            cdp_launcher=ChromeCdpLauncher(
                executable_path=chrome_path,
                user_data_dir=profile_path,
            ),
            log=logger.info,
        )
        runner = GraphRunService(
            browser_service=browser,
            photo_agent=PhotoAnalysisAgent(client, model=effective_settings.model),
            review_agent=ReviewAnalysisAgent(client, model=effective_settings.model),
            scoring_service=PlaceScoringService(),
            log=logger.info,
        )
        coordinator = RunCoordinator(runner, JsonReportStore(reports_root))
        return AppRuntime(coordinator, browser, client)
    except BaseException:
        try:
            await client.close()
        except BaseException:
            logger.exception("runtime 조립 실패 후 OpenAI client 정리 실패")
        raise
```

- [x] **Step 5: Run runtime tests and confirm GREEN**

Run: `uv run python -m unittest tests.test_api_runtime -v`

Expected: all runtime tests pass.

- [x] **Step 6: Commit runtime**

```bash
git add src/datespot_agent/config.py src/datespot_agent/api/runtime.py tests/test_api_runtime.py
git commit -m "feat: assemble API runtime"
```

---

### Task 4: Expose FastAPI lifespan and HTTP contracts

**Files:**

- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/datespot_agent/api/__init__.py`
- Create: `src/datespot_agent/api/app.py`
- Create: `tests/test_api_app.py`

**Interfaces:**

- Consumes: `create_runtime() -> Awaitable[AppRuntime]`
- Consumes: `RunCoordinator` query and submit methods
- Produces: `create_app(runtime_factory=create_runtime) -> FastAPI`
- Produces: importable ASGI `datespot_agent.api.app:app`

- [x] **Step 1: Add current compatible API dependencies**

Run:

```bash
uv add 'fastapi>=0.139.0' 'uvicorn>=0.51.0'
uv add --dev 'httpx2>=2.5.0'
```

Expected: `pyproject.toml` and `uv.lock` update; Python 3.13 environment resolves.

- [x] **Step 2: Write failing HTTP and lifespan tests**

```python
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient

from datespot_agent.api.app import create_app
from datespot_agent.api.errors import CoordinatorUnavailableError
from datespot_agent.api.models import (
    HealthResponse,
    RunAccepted,
    RunJobStatus,
    RunStatusResponse,
)
from datespot_agent.models import RunConfig, RunReport, RunStatus


NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)


class FakeCoordinator:
    def __init__(self) -> None:
        self.unavailable = False
        self.report: RunReport | None = None

    def submit(self, config):
        if self.unavailable:
            raise CoordinatorUnavailableError("stopping")
        return RunAccepted(
            run_id="run_api",
            status=RunJobStatus.QUEUED,
            status_url="/runs/run_api",
            report_url="/runs/run_api/report",
        )

    def get_status(self, run_id):
        if run_id == "missing":
            return None
        job_status = RunJobStatus.QUEUED
        if self.report is not None:
            job_status = (
                RunJobStatus.FAILED
                if self.report.status is RunStatus.FAILED
                else RunJobStatus.COMPLETED
            )
        return RunStatusResponse(
            run_id=run_id,
            status=job_status,
            config=RunConfig(location="성수역", search_keyword="일식"),
            created_at=NOW,
            report_available=self.report is not None,
        )

    def get_report(self, run_id):
        return self.report

    def health(self):
        return HealthResponse(accepting=True)


class FakeRuntime:
    def __init__(self) -> None:
        self.coordinator = FakeCoordinator()
        self.start = AsyncMock()
        self.stop = AsyncMock()


class ApiAppTests(unittest.TestCase):
    def setUp(self):
        self.runtime = FakeRuntime()
        self.client_context = TestClient(
            create_app(lambda: self.runtime)
        )
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)

    def test_lifespan_starts_and_stops_runtime(self):
        self.runtime.start.assert_awaited_once()

    def test_post_runs_returns_accepted_camel_case_payload(self):
        response = self.client.post(
            "/runs",
            json={"location": "성수역", "searchKeyword": "일식"},
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["runId"], "run_api")
        self.assertEqual(response.json()["status"], "queued")

    def test_invalid_run_config_returns_422(self):
        response = self.client.post(
            "/runs",
            json={"location": "", "searchKeyword": "일식"},
        )
        self.assertEqual(response.status_code, 422)

    def test_unknown_run_returns_404_code(self):
        response = self.client.get("/runs/missing")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "run_not_found")

    def test_report_before_completion_returns_409(self):
        response = self.client.get("/runs/run_api/report")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "report_not_ready")

    def test_health_returns_worker_snapshot(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["accepting"])

    def test_unavailable_coordinator_returns_503(self):
        self.runtime.coordinator.unavailable = True
        response = self.client.post(
            "/runs",
            json={"location": "성수역", "searchKeyword": "일식"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"]["code"],
            "coordinator_unavailable",
        )

    def test_saved_report_returns_200(self):
        self.runtime.coordinator.report = RunReport(
            run_id="run_api",
            status=RunStatus.COMPLETED,
            config=RunConfig(location="성수역", search_keyword="일식"),
            created_at=NOW,
        )
        response = self.client.get("/runs/run_api/report")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["runId"], "run_api")

    def test_saved_failed_report_returns_200(self):
        self.runtime.coordinator.report = RunReport(
            run_id="run_api",
            status=RunStatus.FAILED,
            config=RunConfig(location="성수역", search_keyword="일식"),
            created_at=NOW,
        )

        status_response = self.client.get("/runs/run_api")
        report_response = self.client.get("/runs/run_api/report")

        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.json()["status"], "failed")
        self.assertTrue(status_response.json()["reportAvailable"])
        self.assertEqual(report_response.status_code, 200)
        self.assertEqual(report_response.json()["status"], "failed")

    def test_terminal_failure_without_report_returns_unavailable(self):
        status = self.runtime.coordinator.get_status("run_api")
        status.status = RunJobStatus.FAILED
        self.runtime.coordinator.get_status = Mock(return_value=status)
        response = self.client.get("/runs/run_api/report")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"]["code"],
            "report_unavailable",
        )
```

- [x] **Step 3: Run tests and verify RED**

Run: `uv run python -m unittest tests.test_api_app -v`

Expected: import failure because `api.app` does not exist.

- [x] **Step 4: Implement the FastAPI app factory and routes**

```python
# src/datespot_agent/api/app.py
from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from inspect import isawaitable

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


RuntimeFactory = Callable[[], AppRuntime | Awaitable[AppRuntime]]


def _detail(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def create_app(runtime_factory: RuntimeFactory = create_runtime) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime_or_awaitable = runtime_factory()
        runtime = (
            await runtime_or_awaitable
            if isawaitable(runtime_or_awaitable)
            else runtime_or_awaitable
        )
        app.state.runtime = runtime
        try:
            await runtime.start()
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
```

```python
# src/datespot_agent/api/__init__.py
from datespot_agent.api.app import app, create_app

__all__ = ["app", "create_app"]
```

- [x] **Step 5: Run API, coordinator, and runtime tests**

Run: `uv run python -m unittest tests.test_api_app tests.test_run_coordinator tests.test_api_runtime -v`

Expected: all focused tests pass.

- [x] **Step 6: Commit HTTP API**

```bash
git add pyproject.toml uv.lock src/datespot_agent/api tests/test_api_app.py
git commit -m "feat: expose FastAPI run endpoints"
```

---

### Task 5: Document and verify the complete API workflow

**Files:**

- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-07-15-fastapi-run-api.md`
- Verify: `reports/YYYY/MM/DD/<run_id>.json`

**Interfaces:**

- Consumes: `uvicorn datespot_agent.api.app:app`
- Produces: documented local FastAPI workflow and completed roadmap item

- [x] **Step 1: Update README**

Add:

```markdown
### FastAPI 실행 API

uv run uvicorn datespot_agent.api.app:app --host 127.0.0.1 --port 8000

POST /runs
GET /runs/{run_id}
GET /runs/{run_id}/report
GET /health
```

Document `DATESPOT_REPORTS_ROOT`, `DATESPOT_CHROME_EXECUTABLE_PATH`,
`DATESPOT_BROWSER_USER_DATA_DIR`, memory-only state, one FIFO worker, and local-only scope.
Mark `FastAPI 실행 API` complete and rename the inbox item to saved-report list/search.

- [x] **Step 2: Run the complete automated suite**

Run: `uv run python -m unittest discover -s tests -p 'test_*.py'`

Expected: all tests pass.

- [x] **Step 3: Start the real API on loopback**

Run:

```bash
uv run uvicorn datespot_agent.api.app:app --host 127.0.0.1 --port 8000
```

Expected: lifespan completes and server listens on `127.0.0.1:8000`.

- [x] **Step 4: Submit and poll one real run**

Run:

```bash
curl -sS -X POST http://127.0.0.1:8000/runs \
  -H 'content-type: application/json' \
  -d '{"location":"성수역","searchKeyword":"일식","maxPlaces":1}'
```

Poll `GET /runs/{run_id}` until `completed` or `failed`, then call
`GET /runs/{run_id}/report`.

Expected: `POST` returns `202`; status reaches a terminal state; saved report endpoint returns
`200`; JSON file exists under its UTC date path and validates with `RunReport`.

- [x] **Step 5: Stop the API and verify resource cleanup**

Send `Ctrl-C` to Uvicorn.

Expected: lifespan shutdown completes, API port closes, launched Chrome process exits, and no
report temp files remain.

- [x] **Step 6: Review scope and commit docs**

Run: `git status --short && git diff --check && git diff origin/main...HEAD --stat`

Expected: only intended API changes plus the pre-existing unstaged `.iml` change.

```bash
git add README.md docs/superpowers/plans/2026-07-15-fastapi-run-api.md
git commit -m "docs: document FastAPI run API"
```
