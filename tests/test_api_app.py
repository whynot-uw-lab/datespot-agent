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
        self.client_context = TestClient(create_app(lambda: self.runtime))
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.runtime.stop.assert_awaited_once()

    def test_lifespan_starts_and_stops_runtime(self):
        self.runtime.start.assert_awaited_once()

    def test_lifespan_accepts_async_runtime_factory(self):
        runtime = FakeRuntime()

        async def runtime_factory():
            return runtime

        with TestClient(create_app(runtime_factory)):
            runtime.start.assert_awaited_once()

        runtime.stop.assert_awaited_once()

    def test_lifespan_stops_runtime_when_startup_fails(self):
        runtime = FakeRuntime()
        runtime.start.side_effect = RuntimeError("startup failed")

        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            with TestClient(create_app(lambda: runtime)):
                self.fail("startup failure must prevent context entry")

        runtime.start.assert_awaited_once()
        runtime.stop.assert_awaited_once()

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
