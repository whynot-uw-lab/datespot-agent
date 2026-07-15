from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from datespot_agent.api.coordinator import RunCoordinator
from datespot_agent.api.errors import CoordinatorUnavailableError
from datespot_agent.api.events import RunEventPublisher
from datespot_agent.api.models import RunJobStatus
from datespot_agent.models import RunConfig, RunReport, RunStatus
from datespot_agent.observability import RunLogManager
from datespot_agent.reporting import ReportStorageError


NOW = datetime(2026, 7, 15, 1, 2, 3, tzinfo=timezone.utc)


def make_config(location: str = "성수역") -> RunConfig:
    return RunConfig(location=location, search_keyword="일식", max_places=1)


def make_report(
    run_id: str,
    *,
    status: RunStatus = RunStatus.COMPLETED,
    config: RunConfig | None = None,
) -> RunReport:
    return RunReport(
        run_id=run_id,
        status=status,
        config=config or make_config(),
        created_at=NOW,
    )


class ControlledRunner:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.gates: dict[str, asyncio.Event] = {}
        self.status = RunStatus.COMPLETED

    async def run(
        self,
        config: RunConfig,
        *,
        run_id: str | None = None,
    ) -> RunReport:
        assert run_id is not None
        self.started.append(run_id)
        gate = self.gates.setdefault(run_id, asyncio.Event())
        await gate.wait()
        return make_report(run_id, status=self.status, config=config)


class RecordingStore:
    def __init__(self, error: Exception | None = None) -> None:
        self.attempted: list[RunReport] = []
        self.saved: list[RunReport] = []
        self.error = error

    def save(self, report: RunReport) -> Path:
        self.attempted.append(report)
        if self.error is not None:
            raise self.error
        self.saved.append(report)
        return Path("reports") / f"{report.run_id}.json"


class RecordingEventPublisher:
    def __init__(self) -> None:
        self.events: dict[str, list[tuple[str, object]]] = {}
        self.opened: list[str] = []

    def open_run(self, run_id: str) -> None:
        self.opened.append(run_id)

    def lifecycle(self, run_id, event_type, status) -> None:
        self.events.setdefault(run_id, []).append(
            (event_type.value, status.model_copy(deep=True))
        )

    def report_saved(self, run_id: str, report_url: str) -> None:
        self.events.setdefault(run_id, []).append(("report_saved", report_url))

    def terminal(self, run_id, event_type, status) -> None:
        self.events.setdefault(run_id, []).append(
            (event_type.value, status.model_copy(deep=True))
        )

    def types(self, run_id: str) -> list[str]:
        return [event_type for event_type, _ in self.events.get(run_id, [])]


class FailingEventHub:
    def open_run(self, run_id: str) -> None:
        raise RuntimeError("event unavailable")

    def publish(self, run_id: str, event_type, data):
        raise RuntimeError("event unavailable")

    def mark_terminal(self, run_id: str) -> None:
        raise RuntimeError("event unavailable")


async def wait_until(predicate, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


class RunCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_diagnostic_log_tracks_successful_run_lifecycle(self) -> None:
        class ImmediateRunner:
            async def run(self, config, *, run_id=None):
                assert run_id is not None
                return make_report(run_id, config=config)

        with tempfile.TemporaryDirectory() as directory:
            manager = RunLogManager(Path(directory))
            manager.start()
            coordinator = RunCoordinator(
                ImmediateRunner(),
                RecordingStore(),
                clock=lambda: NOW,
                run_id_factory=lambda: "run_logged_success",
            )
            await coordinator.start()
            try:
                accepted = coordinator.submit(make_config())
                await wait_until(
                    lambda: (
                        coordinator.get_status(accepted.run_id).finished_at is not None
                    )
                )
            finally:
                await coordinator.stop()
                manager.stop()

            records = [
                json.loads(line)
                for line in (Path(directory) / "run_logged_success.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]

        events = [record["event"] for record in records]
        self.assertEqual(
            events,
            [
                "run.queued",
                "run.started",
                "report.save.started",
                "report.save.completed",
                "run.completed",
            ],
        )
        self.assertTrue(all(record["runId"] == accepted.run_id for record in records))
        self.assertIsInstance(records[-1]["durationMs"], int)

    async def test_diagnostic_log_keeps_runner_exception_traceback(self) -> None:
        class FailingRunner:
            async def run(self, config, *, run_id=None):
                raise RuntimeError("graph crashed with diagnostic context")

        with tempfile.TemporaryDirectory() as directory:
            manager = RunLogManager(Path(directory))
            manager.start()
            coordinator = RunCoordinator(
                FailingRunner(),
                RecordingStore(),
                clock=lambda: NOW,
                run_id_factory=lambda: "run_logged_failure",
            )
            await coordinator.start()
            try:
                accepted = coordinator.submit(make_config())
                await wait_until(
                    lambda: (
                        coordinator.get_status(accepted.run_id).finished_at is not None
                    )
                )
            finally:
                await coordinator.stop()
                manager.stop()

            records = [
                json.loads(line)
                for line in (Path(directory) / "run_logged_failure.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]

        failure = next(record for record in records if record["event"] == "run.failed")
        self.assertEqual(failure["errorType"], "RuntimeError")
        self.assertIn("graph crashed with diagnostic context", failure["errorMessage"])
        self.assertIn("RuntimeError", failure["traceback"])

    async def test_diagnostic_log_tracks_report_storage_failure(self) -> None:
        class ImmediateRunner:
            async def run(self, config, *, run_id=None):
                assert run_id is not None
                return make_report(run_id, config=config)

        error = ReportStorageError(
            "저장 실패",
            run_id="run_report_save_failed",
            path=Path("/private/report.json"),
        )
        with tempfile.TemporaryDirectory() as directory:
            manager = RunLogManager(Path(directory))
            manager.start()
            coordinator = RunCoordinator(
                ImmediateRunner(),
                RecordingStore(error),
                clock=lambda: NOW,
                run_id_factory=lambda: "run_report_save_failed",
            )
            await coordinator.start()
            try:
                accepted = coordinator.submit(make_config())
                await wait_until(
                    lambda: (
                        coordinator.get_status(accepted.run_id).finished_at is not None
                    )
                )
            finally:
                await coordinator.stop()
                manager.stop()

            records = [
                json.loads(line)
                for line in (Path(directory) / "run_report_save_failed.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]

        failure = next(
            record for record in records if record["event"] == "report.save.failed"
        )
        self.assertEqual(failure["errorType"], "ReportStorageError")
        self.assertIsInstance(failure["durationMs"], int)

    async def asyncSetUp(self) -> None:
        self.runner = ControlledRunner()
        self.store = RecordingStore()
        self.publisher = RecordingEventPublisher()
        ids = iter(("run_first", "run_second", "run_third"))
        self.coordinator = RunCoordinator(
            self.runner,
            self.store,
            clock=lambda: NOW,
            run_id_factory=lambda: next(ids),
            event_publisher=self.publisher,
        )
        await self.coordinator.start()

    async def asyncTearDown(self) -> None:
        await self.coordinator.stop()

    async def test_submit_returns_queued_snapshot_immediately(self) -> None:
        accepted = self.coordinator.submit(make_config())

        self.assertEqual(accepted.run_id, "run_first")
        self.assertEqual(accepted.status, RunJobStatus.QUEUED)
        self.assertEqual(accepted.status_url, "/runs/run_first")
        self.assertEqual(accepted.report_url, "/runs/run_first/report")

    async def test_worker_transitions_job_from_running_to_completed(self) -> None:
        accepted = self.coordinator.submit(make_config())
        await wait_until(lambda: self.runner.started == [accepted.run_id])

        running = self.coordinator.get_status(accepted.run_id)
        self.assertIsNotNone(running)
        assert running is not None
        self.assertEqual(running.status, RunJobStatus.RUNNING)
        self.assertEqual(running.started_at, NOW)
        self.assertIsNone(running.finished_at)

        self.runner.gates[accepted.run_id].set()
        await wait_until(
            lambda: self.coordinator.get_status(accepted.run_id).finished_at is not None
        )
        completed = self.coordinator.get_status(accepted.run_id)
        assert completed is not None
        self.assertEqual(completed.status, RunJobStatus.COMPLETED)
        self.assertEqual(completed.finished_at, NOW)
        self.assertTrue(completed.report_available)
        self.assertEqual(self.store.saved[0].run_id, accepted.run_id)

    async def test_worker_publishes_saved_report_before_terminal(self) -> None:
        accepted = self.coordinator.submit(make_config())
        await wait_until(lambda: accepted.run_id in self.runner.gates)
        self.runner.gates[accepted.run_id].set()
        await wait_until(
            lambda: self.coordinator.get_status(accepted.run_id).finished_at is not None
        )

        self.assertEqual(self.publisher.opened, [accepted.run_id])
        self.assertEqual(
            self.publisher.types(accepted.run_id),
            ["queued", "running", "report_saved", "completed"],
        )
        self.assertEqual(
            self.publisher.events[accepted.run_id][-2][1],
            f"/reports/{accepted.run_id}",
        )
        terminal = self.publisher.events[accepted.run_id][-1][1]
        self.assertEqual(terminal.status, RunJobStatus.COMPLETED)
        self.assertTrue(terminal.report_available)

    async def test_worker_runs_jobs_one_at_a_time_in_fifo_order(self) -> None:
        first = self.coordinator.submit(make_config("성수역"))
        second = self.coordinator.submit(make_config("홍대입구역"))
        await wait_until(lambda: self.runner.started == [first.run_id])

        self.assertEqual(
            self.coordinator.get_status(second.run_id).status,
            RunJobStatus.QUEUED,
        )
        self.runner.gates[first.run_id].set()
        await wait_until(lambda: self.runner.started == [first.run_id, second.run_id])
        self.runner.gates[second.run_id].set()
        await wait_until(
            lambda: self.coordinator.get_status(second.run_id).finished_at is not None
        )

        self.assertEqual(
            [report.run_id for report in self.store.saved],
            [first.run_id, second.run_id],
        )

    async def test_start_is_idempotent_and_does_not_add_another_worker(self) -> None:
        await self.coordinator.start()
        first = self.coordinator.submit(make_config())
        self.coordinator.submit(make_config("홍대입구역"))
        await wait_until(lambda: self.runner.started == [first.run_id])

        await asyncio.sleep(0)
        self.assertEqual(self.runner.started, [first.run_id])

    async def test_storage_failure_marks_job_failed_without_report(self) -> None:
        private_path = Path("/private/reports/run_storage_failed.json")
        error = ReportStorageError(
            "저장 실패",
            run_id="run_storage_failed",
            path=private_path,
        )
        store = RecordingStore(error)
        coordinator = RunCoordinator(
            self.runner,
            store,
            clock=lambda: NOW,
            run_id_factory=lambda: "run_storage_failed",
            event_publisher=self.publisher,
        )
        await coordinator.start()
        try:
            accepted = coordinator.submit(make_config())
            await wait_until(lambda: accepted.run_id in self.runner.gates)
            self.runner.gates[accepted.run_id].set()
            await wait_until(
                lambda: coordinator.get_status(accepted.run_id).finished_at is not None
            )

            snapshot = coordinator.get_status(accepted.run_id)
            assert snapshot is not None
            self.assertEqual(snapshot.status, RunJobStatus.FAILED)
            self.assertIn("저장 실패", snapshot.error)
            self.assertIn(str(private_path), snapshot.error)
            self.assertFalse(snapshot.report_available)
            self.assertIsNone(coordinator.get_report(accepted.run_id))
            self.assertEqual(store.attempted[0].status, RunStatus.COMPLETED)
            self.assertEqual(
                self.publisher.types(accepted.run_id),
                ["queued", "running", "failed"],
            )
            terminal = self.publisher.events[accepted.run_id][-1][1]
            self.assertFalse(terminal.report_available)
            self.assertEqual(
                terminal.error,
                "실행 처리 중 오류가 발생함",
            )
            self.assertNotIn(str(private_path), terminal.error)
        finally:
            await coordinator.stop()

    async def test_failed_graph_report_remains_available(self) -> None:
        self.runner.status = RunStatus.FAILED
        accepted = self.coordinator.submit(make_config())
        await wait_until(lambda: accepted.run_id in self.runner.gates)
        self.runner.gates[accepted.run_id].set()
        await wait_until(
            lambda: self.coordinator.get_status(accepted.run_id).finished_at is not None
        )

        snapshot = self.coordinator.get_status(accepted.run_id)
        assert snapshot is not None
        report = self.coordinator.get_report(accepted.run_id)
        self.assertEqual(snapshot.status, RunJobStatus.FAILED)
        self.assertTrue(snapshot.report_available)
        self.assertIsNone(snapshot.error)
        self.assertIsNotNone(report)
        assert report is not None
        self.assertEqual(report.status, RunStatus.FAILED)
        self.assertEqual(self.store.saved[0].status, RunStatus.FAILED)

    async def test_failed_report_is_saved_before_failed_terminal(self) -> None:
        self.runner.status = RunStatus.FAILED
        accepted = self.coordinator.submit(make_config())
        await wait_until(lambda: accepted.run_id in self.runner.gates)
        self.runner.gates[accepted.run_id].set()
        await wait_until(
            lambda: self.coordinator.get_status(accepted.run_id).finished_at is not None
        )

        self.assertEqual(
            self.publisher.types(accepted.run_id)[-2:],
            ["report_saved", "failed"],
        )
        terminal = self.publisher.events[accepted.run_id][-1][1]
        self.assertTrue(terminal.report_available)
        self.assertIsNone(terminal.error)

    async def test_event_publisher_failure_does_not_fail_run(self) -> None:
        coordinator = RunCoordinator(
            self.runner,
            self.store,
            clock=lambda: NOW,
            run_id_factory=lambda: "run_event_failed",
            event_publisher=RunEventPublisher(FailingEventHub()),
        )
        await coordinator.start()
        try:
            with self.assertLogs(
                "datespot_agent.api.events",
                level="WARNING",
            ) as captured:
                accepted = coordinator.submit(make_config())
                await wait_until(lambda: accepted.run_id in self.runner.gates)
                self.runner.gates[accepted.run_id].set()
                await wait_until(
                    lambda: (
                        coordinator.get_status(accepted.run_id).finished_at is not None
                    )
                )

            snapshot = coordinator.get_status(accepted.run_id)
            assert snapshot is not None
            self.assertEqual(snapshot.status, RunJobStatus.COMPLETED)
            self.assertTrue(snapshot.report_available)
            self.assertEqual(len(captured.records), 5)
        finally:
            await coordinator.stop()

    async def test_submit_requires_started_accepting_worker(self) -> None:
        await self.coordinator.stop()

        with self.assertRaises(CoordinatorUnavailableError):
            self.coordinator.submit(make_config())

    async def test_runner_exception_marks_job_failed_without_report(self) -> None:
        class FailingRunner:
            async def run(
                self,
                config: RunConfig,
                *,
                run_id: str | None = None,
            ) -> RunReport:
                raise RuntimeError("graph crashed")

        coordinator = RunCoordinator(
            FailingRunner(),
            self.store,
            clock=lambda: NOW,
            run_id_factory=lambda: "run_failed",
            event_publisher=self.publisher,
        )
        await coordinator.start()
        try:
            accepted = coordinator.submit(make_config())
            await wait_until(
                lambda: coordinator.get_status(accepted.run_id).finished_at is not None
            )

            snapshot = coordinator.get_status(accepted.run_id)
            assert snapshot is not None
            self.assertEqual(snapshot.status, RunJobStatus.FAILED)
            self.assertEqual(snapshot.error, "graph crashed")
            self.assertFalse(snapshot.report_available)
            self.assertIsNone(coordinator.get_report(accepted.run_id))
            self.assertEqual(
                self.publisher.types(accepted.run_id),
                ["queued", "running", "failed"],
            )
            terminal = self.publisher.events[accepted.run_id][-1][1]
            self.assertFalse(terminal.report_available)
            self.assertEqual(
                terminal.error,
                "실행 처리 중 오류가 발생함",
            )
        finally:
            await coordinator.stop()

    async def test_worker_continues_after_a_runner_exception(self) -> None:
        class FailOnceRunner:
            def __init__(self) -> None:
                self.run_ids: list[str] = []

            async def run(
                self,
                config: RunConfig,
                *,
                run_id: str | None = None,
            ) -> RunReport:
                assert run_id is not None
                self.run_ids.append(run_id)
                if len(self.run_ids) == 1:
                    raise RuntimeError("first graph crashed")
                return make_report(run_id, config=config)

        runner = FailOnceRunner()
        ids = iter(("run_failed", "run_recovered"))
        coordinator = RunCoordinator(
            runner,
            self.store,
            clock=lambda: NOW,
            run_id_factory=lambda: next(ids),
        )
        await coordinator.start()
        try:
            first = coordinator.submit(make_config())
            second = coordinator.submit(make_config("홍대입구역"))
            await wait_until(
                lambda: coordinator.get_status(second.run_id).finished_at is not None
            )

            self.assertEqual(
                coordinator.get_status(first.run_id).status,
                RunJobStatus.FAILED,
            )
            self.assertEqual(
                coordinator.get_status(second.run_id).status,
                RunJobStatus.COMPLETED,
            )
            self.assertEqual(runner.run_ids, [first.run_id, second.run_id])
        finally:
            await coordinator.stop()

    async def test_duplicate_generated_id_is_regenerated(self) -> None:
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

    async def test_submitted_config_is_snapshotted(self) -> None:
        config = make_config()
        accepted = self.coordinator.submit(config)

        config.location = "변경됨"
        snapshot = self.coordinator.get_status(accepted.run_id)
        assert snapshot is not None
        self.assertEqual(snapshot.config.location, "성수역")

    async def test_status_and_report_are_defensive_copies(self) -> None:
        accepted = self.coordinator.submit(make_config())
        await wait_until(lambda: accepted.run_id in self.runner.gates)
        self.runner.gates[accepted.run_id].set()
        await wait_until(
            lambda: self.coordinator.get_report(accepted.run_id) is not None
        )

        status = self.coordinator.get_status(accepted.run_id)
        report = self.coordinator.get_report(accepted.run_id)
        assert status is not None
        assert report is not None
        status.config.location = "변경됨"
        report.config.location = "변경됨"

        current_status = self.coordinator.get_status(accepted.run_id)
        current_report = self.coordinator.get_report(accepted.run_id)
        assert current_status is not None
        assert current_report is not None
        self.assertEqual(current_status.config.location, "성수역")
        self.assertEqual(current_report.config.location, "성수역")

    async def test_unknown_run_has_no_status_or_report(self) -> None:
        self.assertIsNone(self.coordinator.get_status("run_unknown"))
        self.assertIsNone(self.coordinator.get_report("run_unknown"))

    async def test_health_reports_accepting_active_and_queued_runs(self) -> None:
        first = self.coordinator.submit(make_config())
        self.coordinator.submit(make_config("홍대입구역"))
        await wait_until(lambda: self.runner.started == [first.run_id])

        health = self.coordinator.health()
        self.assertEqual(health.status, "ok")
        self.assertTrue(health.accepting)
        self.assertEqual(health.active_run_id, first.run_id)
        self.assertEqual(health.queued_runs, 1)

    async def test_health_reports_not_accepting_after_stop(self) -> None:
        await self.coordinator.stop()

        health = self.coordinator.health()
        self.assertFalse(health.accepting)
        self.assertIsNone(health.active_run_id)
        self.assertEqual(health.queued_runs, 0)

    async def test_status_timestamps_are_normalized_to_utc(self) -> None:
        korea_time = datetime(
            2026,
            7,
            15,
            10,
            2,
            3,
            tzinfo=timezone(timedelta(hours=9)),
        )
        coordinator = RunCoordinator(
            self.runner,
            self.store,
            clock=lambda: korea_time,
            run_id_factory=lambda: "run_utc",
        )
        await coordinator.start()
        try:
            accepted = coordinator.submit(make_config())
            await wait_until(lambda: accepted.run_id in self.runner.gates)
            running = coordinator.get_status(accepted.run_id)
            assert running is not None
            self.assertEqual(running.created_at, NOW)
            self.assertEqual(running.started_at, NOW)

            self.runner.gates[accepted.run_id].set()
            await wait_until(
                lambda: coordinator.get_status(accepted.run_id).finished_at is not None
            )
            finished = coordinator.get_status(accepted.run_id)
            assert finished is not None
            self.assertEqual(finished.finished_at, NOW)
        finally:
            await coordinator.stop()

    async def test_stop_marks_active_run_failed(self) -> None:
        accepted = self.coordinator.submit(make_config())
        await wait_until(lambda: self.runner.started == [accepted.run_id])

        await self.coordinator.stop()

        snapshot = self.coordinator.get_status(accepted.run_id)
        assert snapshot is not None
        self.assertEqual(snapshot.status, RunJobStatus.FAILED)
        self.assertEqual(snapshot.finished_at, NOW)
        self.assertIn("서버 종료", snapshot.error)
        self.assertIsNone(self.coordinator.get_report(accepted.run_id))
        self.assertEqual(
            self.publisher.types(accepted.run_id),
            ["queued", "running", "failed"],
        )

    async def test_stop_marks_queued_run_failed_and_publishes_terminal(self) -> None:
        active = self.coordinator.submit(make_config())
        queued = self.coordinator.submit(make_config("홍대입구역"))
        await wait_until(lambda: self.runner.started == [active.run_id])

        await self.coordinator.stop()
        await self.coordinator._queue.join()

        snapshot = self.coordinator.get_status(queued.run_id)
        assert snapshot is not None
        self.assertEqual(snapshot.status, RunJobStatus.FAILED)
        self.assertEqual(snapshot.finished_at, NOW)
        self.assertEqual(snapshot.error, "서버 종료로 실행이 중단됨")
        self.assertEqual(
            self.publisher.types(queued.run_id),
            ["queued", "failed"],
        )


if __name__ == "__main__":
    unittest.main()
