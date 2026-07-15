from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from datespot_agent.api.events import (
    ProgressStage,
    RunEvent,
    RunEventHub,
    RunEventPublisher,
    RunEventType,
)


NOW = datetime(2026, 7, 15, 1, 2, 3, tzinfo=timezone.utc)


def lifecycle(status: str) -> dict[str, object]:
    return {
        "status": status,
        "reportAvailable": status == "completed",
        "error": None,
    }


def progress(message: str) -> dict[str, object]:
    return {
        "stage": "candidate_search",
        "message": message,
        "placeId": None,
        "placeName": None,
    }


class RunEventModelTests(unittest.TestCase):
    def test_event_is_immutable_normalizes_utc_and_serializes_camel_case(self):
        event = RunEvent(
            run_id="run_one",
            sequence=1,
            occurred_at=datetime(
                2026,
                7,
                15,
                10,
                2,
                3,
                tzinfo=timezone(timedelta(hours=9)),
            ),
            type=RunEventType.QUEUED,
            data=lifecycle("queued"),
        )

        payload = event.model_dump(mode="json", by_alias=True)

        self.assertEqual(event.occurred_at, NOW)
        self.assertEqual(payload["runId"], "run_one")
        self.assertEqual(payload["occurredAt"], "2026-07-15T01:02:03Z")
        self.assertEqual(payload["data"]["reportAvailable"], False)
        with self.assertRaises(ValidationError):
            event.sequence = 2

    def test_event_models_reject_invalid_sequence_payload_and_naive_time(self):
        valid = {
            "run_id": "run_one",
            "sequence": 1,
            "occurred_at": NOW,
            "type": RunEventType.QUEUED,
            "data": lifecycle("queued"),
        }
        invalid_values = (
            {**valid, "sequence": 0},
            {**valid, "occurred_at": NOW.replace(tzinfo=None)},
            {**valid, "data": progress("wrong payload")},
        )

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    RunEvent.model_validate(value)

    def test_progress_stage_has_the_public_stage_values(self):
        self.assertEqual(
            {stage.value for stage in ProgressStage},
            {
                "session_start",
                "candidate_search",
                "place_detail",
                "security_check",
                "photo_analysis",
                "review_analysis",
                "scoring",
                "report_build",
            },
        )


class RunEventHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_sequence_is_per_run_and_new_subscriber_replays_buffer(self):
        hub = RunEventHub(clock=lambda: NOW)
        hub.open_run("run_one")
        hub.open_run("run_two")

        first = hub.publish(
            "run_one", RunEventType.QUEUED, lifecycle("queued")
        )
        second = hub.publish(
            "run_one", RunEventType.RUNNING, lifecycle("running")
        )
        other = hub.publish(
            "run_two", RunEventType.QUEUED, lifecycle("queued")
        )
        subscription = hub.subscribe("run_one", last_event_id=None)

        self.assertEqual((first.sequence, second.sequence), (1, 2))
        self.assertEqual(other.sequence, 1)
        self.assertEqual(subscription.replay, (first, second))
        self.assertEqual(subscription.latest_sequence, 2)

    async def test_reconnect_replays_only_events_after_last_id(self):
        hub = RunEventHub(clock=lambda: NOW)
        hub.open_run("run_one")
        first = hub.publish(
            "run_one", RunEventType.QUEUED, lifecycle("queued")
        )
        second = hub.publish(
            "run_one", RunEventType.RUNNING, lifecycle("running")
        )

        subscription = hub.subscribe(
            "run_one", last_event_id=first.sequence
        )

        self.assertEqual(subscription.replay, (second,))
        self.assertFalse(subscription.reset_required)

    async def test_live_iteration_receives_events_published_after_subscribe(self):
        hub = RunEventHub(clock=lambda: NOW)
        hub.open_run("run_one")
        subscription = hub.subscribe("run_one", last_event_id=None)

        expected = hub.publish(
            "run_one", RunEventType.QUEUED, lifecycle("queued")
        )

        self.assertEqual(await anext(subscription), expected)

    async def test_old_or_ahead_id_requires_reset_without_partial_replay(self):
        hub = RunEventHub(replay_capacity=2, clock=lambda: NOW)
        hub.open_run("run_one")
        for value in range(3):
            hub.publish(
                "run_one", RunEventType.PROGRESS, progress(str(value))
            )

        old = hub.subscribe("run_one", last_event_id=0)
        ahead = hub.subscribe("run_one", last_event_id=4)

        self.assertTrue(old.reset_required)
        self.assertEqual(old.replay, ())
        self.assertTrue(ahead.reset_required)
        self.assertEqual(ahead.latest_sequence, 3)

    async def test_subscriber_overflow_does_not_block_publish(self):
        hub = RunEventHub(subscriber_capacity=1, clock=lambda: NOW)
        hub.open_run("run_one")
        subscription = hub.subscribe("run_one", last_event_id=None)

        first = hub.publish(
            "run_one", RunEventType.QUEUED, lifecycle("queued")
        )
        second = hub.publish(
            "run_one", RunEventType.RUNNING, lifecycle("running")
        )

        self.assertEqual((first.sequence, second.sequence), (1, 2))
        self.assertTrue(subscription.overflowed)
        with self.assertRaises(StopAsyncIteration):
            await anext(subscription)

    async def test_terminal_subscription_drains_then_stops_and_replays(self):
        hub = RunEventHub(clock=lambda: NOW)
        hub.open_run("run_one")
        subscription = hub.subscribe("run_one", last_event_id=None)
        terminal = hub.publish(
            "run_one", RunEventType.COMPLETED, lifecycle("completed")
        )

        hub.mark_terminal("run_one")

        self.assertEqual(await anext(subscription), terminal)
        with self.assertRaises(StopAsyncIteration):
            await anext(subscription)
        reconnect = hub.subscribe("run_one", last_event_id=0)
        self.assertEqual(reconnect.replay, (terminal,))
        with self.assertRaises(StopAsyncIteration):
            await anext(reconnect)
        with self.assertRaises(RuntimeError):
            hub.publish(
                "run_one", RunEventType.PROGRESS, progress("too late")
            )

    async def test_terminal_lru_evicts_oldest_run(self):
        hub = RunEventHub(terminal_capacity=2, clock=lambda: NOW)
        for run_id in ("run_one", "run_two", "run_three"):
            hub.open_run(run_id)
            hub.publish(
                run_id, RunEventType.COMPLETED, lifecycle("completed")
            )
            hub.mark_terminal(run_id)

        with self.assertRaises(KeyError):
            hub.subscribe("run_one", last_event_id=None)
        self.assertEqual(
            hub.subscribe("run_two", last_event_id=None).latest_sequence,
            1,
        )
        self.assertEqual(
            hub.subscribe("run_three", last_event_id=None).latest_sequence,
            1,
        )

    async def test_close_drains_queued_events_and_rejects_new_work(self):
        hub = RunEventHub(clock=lambda: NOW)
        hub.open_run("run_one")
        subscription = hub.subscribe("run_one", last_event_id=None)
        expected = hub.publish(
            "run_one", RunEventType.QUEUED, lifecycle("queued")
        )

        await hub.close()

        self.assertEqual(await anext(subscription), expected)
        with self.assertRaises(StopAsyncIteration):
            await anext(subscription)
        with self.assertRaises(RuntimeError):
            hub.open_run("run_two")

    async def test_subscription_close_unregisters_without_affecting_others(self):
        hub = RunEventHub(clock=lambda: NOW)
        hub.open_run("run_one")
        closed = hub.subscribe("run_one", last_event_id=None)
        active = hub.subscribe("run_one", last_event_id=None)

        closed.close()
        expected = hub.publish(
            "run_one", RunEventType.QUEUED, lifecycle("queued")
        )

        with self.assertRaises(StopAsyncIteration):
            await anext(closed)
        self.assertEqual(await anext(active), expected)

    async def test_rejects_invalid_capacities_duplicate_and_unknown_runs(self):
        for arguments in (
            {"replay_capacity": 0},
            {"subscriber_capacity": 0},
            {"terminal_capacity": 0},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    RunEventHub(**arguments)

        hub = RunEventHub(clock=lambda: NOW)
        hub.open_run("run_one")
        with self.assertRaises(ValueError):
            hub.open_run("run_one")
        with self.assertRaises(KeyError):
            hub.subscribe("missing", last_event_id=None)
        with self.assertRaises(ValueError):
            hub.subscribe("run_one", last_event_id=-1)


class RunEventPublisherTests(unittest.IsolatedAsyncioTestCase):
    async def test_hub_failures_are_logged_and_not_raised(self):
        hub = RunEventHub(clock=lambda: NOW)
        await hub.close()
        publisher = RunEventPublisher(hub)

        with self.assertLogs("datespot_agent.api.events", level="WARNING"):
            result = publisher.open_run("run_one")

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
