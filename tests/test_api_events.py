from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from datespot_agent.api.app import create_app
from datespot_agent.api.events import RunEvent, RunEventHub, RunEventType
from datespot_agent.api.models import RunJobStatus, RunStatusResponse
from datespot_agent.models import RunConfig


NOW = datetime(2026, 7, 15, 1, 2, 3, tzinfo=timezone.utc)


class _Coordinator:
    def __init__(self) -> None:
        self.errors: dict[str, str] = {}

    def get_status(self, run_id: str) -> RunStatusResponse | None:
        if run_id == "missing":
            return None
        return RunStatusResponse(
            run_id=run_id,
            status=RunJobStatus.COMPLETED,
            config=RunConfig(location="성수역", search_keyword="일식"),
            created_at=NOW,
            finished_at=NOW,
            report_available=True,
            error=self.errors.get(run_id),
        )


class _Runtime:
    def __init__(self, event_hub=None) -> None:
        self.coordinator = _Coordinator()
        self.event_hub = event_hub or RunEventHub(clock=lambda: NOW)
        self.start = AsyncMock()
        self.stop = AsyncMock()


class _DelayedSubscription:
    def __init__(self) -> None:
        self.replay = ()
        self.reset_required = False
        self.latest_sequence = 3
        self.closed = False
        self._sent = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._sent:
            raise StopAsyncIteration
        self._sent = True
        await asyncio.sleep(0.03)
        return RunEvent(
            run_id="run_live",
            sequence=6,
            occurred_at=NOW,
            type=RunEventType.COMPLETED,
            data={
                "status": "completed",
                "reportAvailable": True,
                "error": None,
            },
        )

    def close(self) -> None:
        self.closed = True


class _DelayedHub:
    def __init__(self) -> None:
        self.subscription = _DelayedSubscription()
        self.last_event_id = None

    def subscribe(self, run_id: str, last_event_id: int | None):
        self.last_event_id = last_event_id
        return self.subscription


class ApiEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = _Runtime()
        self.runtime.event_hub.open_run("run_api")
        self.runtime.event_hub.publish(
            "run_api",
            RunEventType.QUEUED,
            {"status": "queued", "reportAvailable": False, "error": None},
        )
        self.runtime.event_hub.publish(
            "run_api",
            RunEventType.COMPLETED,
            {"status": "completed", "reportAvailable": True, "error": None},
        )
        self.runtime.event_hub.mark_terminal("run_api")
        self.context = TestClient(create_app(lambda: self.runtime))
        self.client = self.context.__enter__()

    def tearDown(self) -> None:
        self.context.__exit__(None, None, None)

    def test_sse_replays_after_last_event_id(self) -> None:
        response = self.client.get(
            "/runs/run_api/events",
            headers={"Last-Event-ID": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["content-type"],
            "text/event-stream; charset=utf-8",
        )
        self.assertIn("id: 2", response.text)
        self.assertIn("event: completed", response.text)
        self.assertIn("retry: 2000", response.text)
        data_line = next(
            line
            for line in response.text.splitlines()
            if line.startswith("data: ")
        )
        payload = json.loads(data_line.removeprefix("data: "))
        self.assertEqual(payload["runId"], "run_api")
        self.assertNotIn("run_id", payload)

    def test_sse_rejects_invalid_last_event_id_before_streaming(self) -> None:
        for value in ("bad", "-1"):
            with self.subTest(value=value):
                response = self.client.get(
                    "/runs/run_api/events",
                    headers={"Last-Event-ID": value},
                )

                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.json()["detail"]["code"],
                    "invalid_event_id",
                )

    def test_sse_rejects_unknown_run_before_streaming(self) -> None:
        response = self.client.get("/runs/missing/events")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"],
            {
                "code": "run_not_found",
                "message": "실행을 찾을 수 없음",
            },
        )

    def test_stale_id_sends_reset_sanitized_snapshot_then_retained_replay(
        self,
    ) -> None:
        hub = RunEventHub(replay_capacity=2, clock=lambda: NOW)
        hub.open_run("run_stale")
        for sequence in range(3):
            hub.publish(
                "run_stale",
                RunEventType.PROGRESS,
                {
                    "stage": "candidate_search",
                    "message": f"단계 {sequence}",
                },
            )
        hub.publish(
            "run_stale",
            RunEventType.COMPLETED,
            {"status": "completed", "reportAvailable": True, "error": None},
        )
        hub.mark_terminal("run_stale")
        runtime = _Runtime(hub)
        runtime.coordinator.errors["run_stale"] = (
            "path=/private/tmp/report.json sk-secret Traceback"
        )

        with TestClient(create_app(lambda: runtime)) as client:
            response = client.get(
                "/runs/run_stale/events",
                headers={"Last-Event-ID": "0"},
            )

        self.assertEqual(response.status_code, 200)
        blocks = response.text.strip().split("\n\n")
        self.assertIn("event: replay_reset", blocks[0])
        self.assertNotIn("id:", blocks[0])
        self.assertIn("event: snapshot", blocks[1])
        self.assertNotIn("id:", blocks[1])
        reset = json.loads(
            next(
                line.removeprefix("data: ")
                for line in blocks[0].splitlines()
                if line.startswith("data: ")
            )
        )
        snapshot = json.loads(
            next(
                line.removeprefix("data: ")
                for line in blocks[1].splitlines()
                if line.startswith("data: ")
            )
        )
        self.assertEqual(reset["sequence"], 4)
        self.assertEqual(reset["data"]["latestSequence"], 4)
        self.assertEqual(snapshot["sequence"], 4)
        self.assertEqual(snapshot["data"]["runId"], "run_stale")
        self.assertEqual(
            snapshot["data"]["error"],
            "실행 처리 중 오류가 발생함",
        )
        self.assertNotIn("/private", response.text)
        self.assertNotIn("sk-secret", response.text)
        self.assertNotIn("Traceback", response.text)
        self.assertIn("id: 3", blocks[2])
        self.assertIn("id: 4", blocks[3])

    def test_evicted_terminal_run_sends_snapshot_without_event_id(self) -> None:
        hub = RunEventHub(terminal_capacity=1, clock=lambda: NOW)
        for run_id in ("run_evicted", "run_retained"):
            hub.open_run(run_id)
            hub.publish(
                run_id,
                RunEventType.COMPLETED,
                {
                    "status": "completed",
                    "reportAvailable": True,
                    "error": None,
                },
            )
            hub.mark_terminal(run_id)
        runtime = _Runtime(hub)

        with TestClient(create_app(lambda: runtime)) as client:
            response = client.get("/runs/run_evicted/events")

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: snapshot", response.text)
        self.assertNotIn("id:", response.text)
        self.assertEqual(response.text.count("event:"), 1)

    def test_ahead_id_waits_for_live_event_and_native_keepalive(self) -> None:
        hub = _DelayedHub()
        runtime = _Runtime(hub)

        with patch("fastapi.routing._PING_INTERVAL", 0.01):
            with TestClient(create_app(lambda: runtime)) as client:
                response = client.get(
                    "/runs/run_live/events",
                    headers={"Last-Event-ID": "5"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(hub.last_event_id, 5)
        self.assertIn(": ping", response.text)
        self.assertIn("id: 6", response.text)
        self.assertTrue(hub.subscription.closed)


if __name__ == "__main__":
    unittest.main()
