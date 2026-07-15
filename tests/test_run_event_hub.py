from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from datespot_agent.api.events import (
    ProgressStage,
    ProgressStatus,
    RunEvent,
    RunEventHub,
    RunEventPublisher,
    RunProgressData,
    RunEventType,
)
from datespot_agent.api.models import RunJobStatus, RunStatusResponse
from datespot_agent.models import PlaceResult, RunConfig


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


def status_response(status: RunJobStatus) -> RunStatusResponse:
    return RunStatusResponse(
        run_id="run_one",
        status=status,
        config=RunConfig(location="신사역", search_keyword="음식점"),
        created_at=NOW,
        report_available=status is RunJobStatus.COMPLETED,
    )


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

    def test_progress_data_serializes_analysis_details_and_thumbnails(self):
        progress_data = RunProgressData(
            stage=ProgressStage.PHOTO_ANALYSIS,
            message="사진 2장 분석 시작",
            status=ProgressStatus.STARTED,
            place_id="place-1",
            place_name="우니도",
            input_count=2,
            duration_ms=123,
            score=8,
            photo_urls=(
                "https://images.example/one.jpg",
                "https://images.example/two.jpg",
            ),
        )

        self.assertEqual(
            progress_data.model_dump(mode="json", by_alias=True),
            {
                "stage": "photo_analysis",
                "message": "사진 2장 분석 시작",
                "status": "started",
                "placeId": "place-1",
                "placeName": "우니도",
                "current": None,
                "total": None,
                "inputCount": 2,
                "durationMs": 123,
                "score": 8,
                "photoUrls": [
                    "https://images.example/one.jpg",
                    "https://images.example/two.jpg",
                ],
            },
        )

    def test_progress_data_rejects_invalid_counts_scores_and_photo_urls(self):
        invalid_values = (
            {"current": 2, "total": 1},
            {"input_count": -1},
            {"duration_ms": -1},
            {"score": 11},
            {"photo_urls": tuple(f"https://x/{index}" for index in range(6))},
            {"photo_urls": ("ftp://images.example/one.jpg",)},
            {
                "stage": "review_analysis",
                "photo_urls": ("https://images.example/one.jpg",),
            },
        )

        for overrides in invalid_values:
            with self.subTest(overrides=overrides):
                value = {
                    "stage": "photo_analysis",
                    "message": "분석",
                    **overrides,
                }
                with self.assertRaises(ValidationError):
                    RunProgressData.model_validate(value)


class RunEventHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_sequence_is_per_run_and_new_subscriber_replays_buffer(self):
        hub = RunEventHub(clock=lambda: NOW)
        hub.open_run("run_one")
        hub.open_run("run_two")

        first = hub.publish("run_one", RunEventType.QUEUED, lifecycle("queued"))
        second = hub.publish("run_one", RunEventType.RUNNING, lifecycle("running"))
        other = hub.publish("run_two", RunEventType.QUEUED, lifecycle("queued"))
        subscription = hub.subscribe("run_one", last_event_id=None)

        self.assertEqual((first.sequence, second.sequence), (1, 2))
        self.assertEqual(other.sequence, 1)
        self.assertEqual(subscription.replay, (first, second))
        self.assertEqual(subscription.latest_sequence, 2)

    async def test_reconnect_replays_only_events_after_last_id(self):
        hub = RunEventHub(clock=lambda: NOW)
        hub.open_run("run_one")
        first = hub.publish("run_one", RunEventType.QUEUED, lifecycle("queued"))
        second = hub.publish("run_one", RunEventType.RUNNING, lifecycle("running"))

        subscription = hub.subscribe("run_one", last_event_id=first.sequence)

        self.assertEqual(subscription.replay, (second,))
        self.assertFalse(subscription.reset_required)

    async def test_live_iteration_receives_events_published_after_subscribe(self):
        hub = RunEventHub(clock=lambda: NOW)
        hub.open_run("run_one")
        subscription = hub.subscribe("run_one", last_event_id=None)

        expected = hub.publish("run_one", RunEventType.QUEUED, lifecycle("queued"))

        self.assertEqual(await anext(subscription), expected)

    async def test_old_id_requires_reset_and_replays_retained_events(self):
        hub = RunEventHub(replay_capacity=2, clock=lambda: NOW)
        hub.open_run("run_one")
        events = [
            hub.publish("run_one", RunEventType.PROGRESS, progress(str(value)))
            for value in range(3)
        ]

        old = hub.subscribe("run_one", last_event_id=0)

        self.assertTrue(old.reset_required)
        self.assertEqual(old.replay, tuple(events[-2:]))

    async def test_ahead_id_waits_for_events_after_id_without_reset(self):
        hub = RunEventHub(clock=lambda: NOW)
        hub.open_run("run_one")
        for value in range(3):
            hub.publish("run_one", RunEventType.PROGRESS, progress(str(value)))

        ahead = hub.subscribe("run_one", last_event_id=4)

        self.assertFalse(ahead.reset_required)
        self.assertEqual(ahead.replay, ())
        self.assertEqual(ahead.latest_sequence, 3)
        hub.publish("run_one", RunEventType.PROGRESS, progress("four"))
        expected = hub.publish("run_one", RunEventType.PROGRESS, progress("five"))
        self.assertEqual(await anext(ahead), expected)

    async def test_place_result_replay_isolated_from_input_and_delivery(self):
        hub = RunEventHub(clock=lambda: NOW)
        hub.open_run("run_one")
        live = hub.subscribe("run_one", last_event_id=None)
        result = PlaceResult(
            status="analyzed",
            name="원본 장소",
            final_score=7.5,
        )

        published = hub.publish("run_one", RunEventType.PLACE_RESULT, result)
        delivered = await anext(live)
        result.name = "입력 변경"
        published.data.name = "반환값 변경"
        delivered.data.name = "구독값 변경"

        replayed = hub.subscribe("run_one", last_event_id=None).replay[0]
        self.assertEqual(replayed.data.name, "원본 장소")
        self.assertEqual(
            replayed.model_dump(mode="json", by_alias=True)["data"],
            {
                "status": "analyzed",
                "placeId": None,
                "name": "원본 장소",
                "category": None,
                "address": None,
                "photoScore": None,
                "reviewScore": None,
                "finalScore": 7.5,
                "photoReason": None,
                "reviewReason": None,
                "failureReason": None,
            },
        )

    async def test_snapshot_replay_isolated_from_input_and_subscription(self):
        hub = RunEventHub(clock=lambda: NOW)
        hub.open_run("run_one")
        snapshot = status_response(RunJobStatus.QUEUED)

        hub.publish("run_one", RunEventType.SNAPSHOT, snapshot)
        snapshot.config.location = "입력 변경"
        delivered = hub.subscribe("run_one", last_event_id=None).replay[0]
        delivered.data.config.location = "구독값 변경"

        replayed = hub.subscribe("run_one", last_event_id=None).replay[0]
        self.assertEqual(replayed.data.config.location, "신사역")
        self.assertEqual(
            replayed.model_dump(mode="json", by_alias=True)["data"]["runId"],
            "run_one",
        )

    async def test_subscriber_overflow_does_not_block_publish(self):
        hub = RunEventHub(subscriber_capacity=1, clock=lambda: NOW)
        hub.open_run("run_one")
        subscription = hub.subscribe("run_one", last_event_id=None)

        first = hub.publish("run_one", RunEventType.QUEUED, lifecycle("queued"))
        with self.assertLogs(
            "datespot_agent.api.events",
            level="WARNING",
        ) as captured:
            second = hub.publish("run_one", RunEventType.RUNNING, lifecycle("running"))

        self.assertEqual((first.sequence, second.sequence), (1, 2))
        self.assertTrue(subscription.overflowed)
        self.assertEqual(
            captured.records[-1].datespot_event,
            "sse.subscriber.overflowed",
        )
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
            hub.publish("run_one", RunEventType.PROGRESS, progress("too late"))

    async def test_terminal_lru_evicts_oldest_run(self):
        hub = RunEventHub(terminal_capacity=2, clock=lambda: NOW)
        for run_id in ("run_one", "run_two"):
            hub.open_run(run_id)
            hub.publish(run_id, RunEventType.COMPLETED, lifecycle("completed"))
            hub.mark_terminal(run_id)

        hub.subscribe("run_one", last_event_id=None)
        hub.open_run("run_three")
        hub.publish("run_three", RunEventType.COMPLETED, lifecycle("completed"))
        hub.mark_terminal("run_three")

        with self.assertRaises(KeyError):
            hub.subscribe("run_two", last_event_id=None)
        self.assertEqual(
            hub.subscribe("run_one", last_event_id=None).latest_sequence,
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
        expected = hub.publish("run_one", RunEventType.QUEUED, lifecycle("queued"))

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
        expected = hub.publish("run_one", RunEventType.QUEUED, lifecycle("queued"))

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

    async def test_run_id_is_normalized_before_keying_and_event_creation(self):
        hub = RunEventHub(clock=lambda: NOW)

        hub.open_run("  run_one  ")
        event = hub.publish("run_one", RunEventType.QUEUED, lifecycle("queued"))

        self.assertEqual(event.run_id, "run_one")
        self.assertEqual(
            hub.subscribe(" run_one ", last_event_id=None).replay,
            (event,),
        )
        with self.assertRaises(ValueError):
            hub.open_run("run_one")


class RunEventPublisherTests(unittest.IsolatedAsyncioTestCase):
    async def test_progress_publisher_keeps_structured_analysis_fields(self):
        hub = RunEventHub(clock=lambda: NOW)
        hub.open_run("run_one")
        publisher = RunEventPublisher(hub)

        event = publisher.progress(
            "run_one",
            ProgressStage.REVIEW_ANALYSIS,
            "리뷰 분석 완료",
            status=ProgressStatus.COMPLETED,
            place_id="place-1",
            place_name="우니도",
            input_count=12,
            duration_ms=456,
            score=9,
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.data.status, ProgressStatus.COMPLETED)
        self.assertEqual(event.data.input_count, 12)
        self.assertEqual(event.data.duration_ms, 456)
        self.assertEqual(event.data.score, 9)
        self.assertNotIn("matched", event.data.model_dump())

    async def test_hub_failures_are_logged_and_not_raised(self):
        hub = RunEventHub(clock=lambda: NOW)
        await hub.close()
        publisher = RunEventPublisher(hub)

        with self.assertLogs("datespot_agent.api.events", level="WARNING"):
            result = publisher.open_run("run_one")

        self.assertIsNone(result)

    async def test_terminal_publish_failure_does_not_mark_run_terminal(self):
        hub = RunEventHub(clock=lambda: NOW)
        hub.open_run("run_one")
        publisher = RunEventPublisher(hub)

        with self.assertLogs("datespot_agent.api.events", level="WARNING"):
            event = publisher.terminal(
                "run_one",
                RunEventType.COMPLETED,
                status_response(RunJobStatus.QUEUED),
            )

        self.assertIsNone(event)
        queued = hub.publish("run_one", RunEventType.QUEUED, lifecycle("queued"))
        self.assertEqual(queued.sequence, 1)

    async def test_invalid_publisher_payloads_are_isolated_and_logged(self):
        hub = RunEventHub(clock=lambda: NOW)
        hub.open_run("run_one")
        publisher = RunEventPublisher(hub)
        invalid_status = status_response(RunJobStatus.QUEUED)
        invalid_status.status = "invalid"

        calls = (
            lambda: publisher.lifecycle("run_one", RunEventType.QUEUED, invalid_status),
            lambda: publisher.progress("run_one", ProgressStage.CANDIDATE_SEARCH, " "),
            lambda: publisher.report_saved("run_one", " "),
        )
        for call in calls:
            with self.subTest(call=call):
                with self.assertLogs("datespot_agent.api.events", level="WARNING"):
                    self.assertIsNone(call())

        self.assertEqual(
            hub.subscribe("run_one", last_event_id=None).latest_sequence,
            0,
        )


if __name__ == "__main__":
    unittest.main()
