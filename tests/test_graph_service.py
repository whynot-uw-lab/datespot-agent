from __future__ import annotations

import unittest
from datetime import datetime, timezone

from datespot_agent.graph import GraphRunService
from datespot_agent.models import (
    CandidatePlace,
    PhotoAnalysis,
    PlaceDetail,
    PlaceResult,
    PlaceResultStatus,
    ReviewAnalysis,
    RunConfig,
)


class EmptyBrowserService:
    def __init__(self) -> None:
        self.closed_run_ids: list[str] = []

    async def start_session(self, run_id: str) -> None:
        return None

    async def search_candidates(self, run_id: str, config: RunConfig):
        return []

    async def close_session(self, run_id: str) -> None:
        self.closed_run_ids.append(run_id)


class SuccessfulBrowserService(EmptyBrowserService):
    async def search_candidates(self, run_id: str, config: RunConfig):
        return [CandidatePlace(place_id="place-1", name="우니도")]

    async def extract_place_detail(self, run_id: str, candidate):
        return PlaceDetail(
            place_id=candidate.place_id,
            name=candidate.name,
            photo_urls=["https://example.com/photo.jpg"],
            reviews=["조용해요"],
        )


class PhotoAgent:
    async def analyze(self, detail, criteria):
        return PhotoAnalysis(photo_score=8, matched=True, reason="차분함")


class ReviewAgent:
    async def analyze(self, detail, criteria):
        return ReviewAnalysis(review_score=9, matched=True, reason="조용함")


class ScoringService:
    def calculate(self, detail, weights, photo_analysis, review_analysis):
        return PlaceResult(
            status=PlaceResultStatus.ANALYZED,
            place_id=detail.place_id,
            name=detail.name,
            photo_score=photo_analysis.photo_score,
            review_score=review_analysis.review_score,
            final_score=8.5,
        )


class RecordingEventPublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, object]] = []

    def progress(
        self,
        run_id,
        stage,
        message,
        *,
        place_id=None,
        place_name=None,
    ) -> None:
        self.events.append(
            (
                run_id,
                "progress",
                (stage.value, message, place_id, place_name),
            )
        )

    def place_result(self, run_id, result) -> None:
        self.events.append(
            (run_id, "place_result", result.model_copy(deep=True))
        )


def build_service(
    browser: EmptyBrowserService,
    *,
    event_publisher=None,
    log=None,
) -> GraphRunService:
    return GraphRunService(
        browser_service=browser,
        photo_agent=PhotoAgent(),
        review_agent=ReviewAgent(),
        scoring_service=ScoringService(),
        clock=lambda: datetime(2026, 7, 15, 1, 2, 3, tzinfo=timezone.utc),
        event_publisher=event_publisher,
        log=log,
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

    async def test_run_publishes_typed_progress_and_place_result(self):
        publisher = RecordingEventPublisher()
        logs: list[str] = []
        run_id = "run_direct_event_id"

        report = await build_service(
            SuccessfulBrowserService(),
            event_publisher=publisher,
            log=logs.append,
        ).run(
            RunConfig(location="성수역", search_keyword="일식", max_places=1),
            run_id=run_id,
        )

        self.assertEqual(report.status.value, "completed")
        self.assertTrue(logs)
        self.assertTrue(all(event[0] == run_id for event in publisher.events))
        progress_stages = [
            event[2][0]
            for event in publisher.events
            if event[1] == "progress"
        ]
        self.assertEqual(
            progress_stages,
            [
                "session_start",
                "session_start",
                "candidate_search",
                "candidate_search",
                "place_detail",
                "place_detail",
                "photo_analysis",
                "photo_analysis",
                "review_analysis",
                "review_analysis",
                "scoring",
                "scoring",
                "report_build",
            ],
        )
        place_event_index = next(
            index
            for index, event in enumerate(publisher.events)
            if event[1] == "place_result"
        )
        report_event_index = next(
            index
            for index, event in enumerate(publisher.events)
            if event[1] == "progress"
            and event[2][0] == "report_build"
        )
        self.assertLess(place_event_index, report_event_index)
        place_result = publisher.events[place_event_index][2]
        self.assertEqual(place_result.place_id, "place-1")
        self.assertEqual(place_result.final_score, 8.5)

    async def test_failed_place_is_published_after_append(self):
        publisher = RecordingEventPublisher()
        service = build_service(
            EmptyBrowserService(),
            event_publisher=publisher,
        )
        from datespot_agent.models import GraphState

        state = GraphState(
            run_id="run_failed_place",
            config=RunConfig(
                location="성수역",
                search_keyword="일식",
                max_places=1,
            ),
            current_place=CandidatePlace(place_id="place-f", name="실패 장소"),
            last_error="상세 추출 실패",
        )

        next_state = service._append_failed_place(state)

        self.assertEqual(len(next_state.place_results), 1)
        self.assertEqual(publisher.events[-1][1], "place_result")
        published = publisher.events[-1][2]
        self.assertEqual(published.status, PlaceResultStatus.FAILED)
        self.assertEqual(published.failure_reason, "상세 추출 실패")
