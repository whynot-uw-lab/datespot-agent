from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from datespot_agent.models import (
    CandidatePlace,
    FilterDecision,
    GraphState,
    PhotoAnalysis,
    PlaceDetail,
    PlaceResult,
    ReviewAnalysis,
    RunConfig,
    RunReport,
    Weights,
)


class RunConfigTests(unittest.TestCase):
    def test_accepts_snake_and_camel_and_serializes_aliases(self):
        snake = RunConfig(location=" 신사역 ", search_keyword="음식점")
        camel = RunConfig.model_validate(
            {
                "location": "강남역",
                "searchKeyword": "일식",
                "maxPlaces": 3,
                "filters": {"minReviewCount": 50},
                "weights": {"photoPercent": 60, "reviewPercent": 40},
            }
        )

        self.assertEqual(snake.location, "신사역")
        self.assertEqual(camel.search_keyword, "일식")
        payload = camel.model_dump(by_alias=True)
        self.assertEqual(payload["maxPlaces"], 3)
        self.assertEqual(payload["filters"]["minReviewCount"], 50)
        self.assertEqual(payload["weights"]["photoPercent"], 60)
        self.assertNotIn("search_keyword", payload)

    def test_rejects_out_of_range_and_unknown_fields(self):
        for max_places in (0, 11):
            with self.subTest(max_places=max_places):
                with self.assertRaises(ValidationError):
                    RunConfig(
                        location="신사역",
                        search_keyword="음식점",
                        max_places=max_places,
                    )

        with self.assertRaises(ValidationError):
            RunConfig(location=" ", search_keyword="음식점")
        with self.assertRaises(ValidationError):
            RunConfig(
                location="신사역",
                search_keyword="음식점",
                filters={"min_review_count": -1},
            )
        with self.assertRaises(ValidationError):
            RunConfig.model_validate(
                {
                    "location": "신사역",
                    "searchKeyword": "음식점",
                    "unexpectedField": True,
                }
            )

    def test_weights_require_percentages_totaling_100(self):
        self.assertEqual(Weights().photo_percent, 50)
        with self.assertRaises(ValidationError):
            Weights(photo_percent=40, review_percent=40)
        with self.assertRaises(ValidationError):
            Weights(photo_percent=101, review_percent=-1)

    def test_nested_defaults_are_independent(self):
        first = RunConfig(location="신사역", search_keyword="음식점")
        second = RunConfig(location="강남역", search_keyword="음식점")

        first.filters.categories.append("일식")

        self.assertEqual(second.filters.categories, [])

    def test_distance_filter_is_not_part_of_run_config(self):
        with self.assertRaises(ValidationError):
            RunConfig.model_validate(
                {
                    "location": "신사역",
                    "searchKeyword": "일식",
                    "filters": {"maxDistanceM": 700},
                }
            )


class PlaceAndAnalysisModelTests(unittest.TestCase):
    def test_distance_is_not_part_of_place_detail(self):
        with self.assertRaises(ValidationError):
            PlaceDetail.model_validate(
                {
                    "placeId": "1720070048",
                    "name": "우니도",
                    "distanceM": 520,
                }
            )

    def test_place_detail_supports_aliases_and_independent_lists(self):
        detail = PlaceDetail.model_validate(
            {
                "placeId": "1720070048",
                "name": "우니도",
                "photoUrls": ["https://example.com/1.jpg"],
                "reviewCount": 128,
            }
        )
        other = PlaceDetail(place_id="2", name="다른 장소")

        detail.reviews.append("조용해요")

        self.assertEqual(other.reviews, [])
        self.assertEqual(
            detail.model_dump(by_alias=True)["photoUrls"],
            ["https://example.com/1.jpg"],
        )

    def test_analysis_models_require_integer_score_in_range(self):
        self.assertEqual(PhotoAnalysis(photo_score=7, reason="차분함").photo_score, 7)
        self.assertEqual(ReviewAnalysis(review_score=8, reason="조용함").review_score, 8)

        for score in (-1, 11, 7.5):
            with self.subTest(score=score):
                with self.assertRaises(ValidationError):
                    PhotoAnalysis(photo_score=score, reason="근거")

    def test_identifying_fields_and_reasons_cannot_be_blank(self):
        with self.assertRaises(ValidationError):
            CandidatePlace(place_id=" ", name="우니도")
        with self.assertRaises(ValidationError):
            ReviewAnalysis(review_score=8, reason=" ")

        decision = FilterDecision(passed=False, exclusion_reason="리뷰 부족")
        self.assertFalse(decision.passed)


class ResultAndReportModelTests(unittest.TestCase):
    def test_place_result_requires_fields_for_each_status(self):
        invalid_payloads = (
            {"status": "analyzed", "name": "우니도"},
            {"status": "excluded", "name": "우니도"},
            {"status": "failed", "name": "우니도"},
        )

        for payload in invalid_payloads:
            with self.subTest(status=payload["status"]):
                with self.assertRaises(ValidationError):
                    PlaceResult.model_validate(payload)

    def test_place_result_allows_partial_component_scores(self):
        result = PlaceResult(
            status="analyzed",
            place_id="1720070048",
            name="우니도",
            final_score=8,
        )

        self.assertIsNone(result.photo_score)
        self.assertIsNone(result.review_score)
        with self.assertRaises(ValidationError):
            PlaceResult(status="analyzed", place_id=" ", name="우니도", final_score=8)

    def test_run_report_requires_aware_datetime_and_normalizes_utc(self):
        config = RunConfig(location="신사역", search_keyword="음식점")
        with self.assertRaises(ValidationError):
            RunReport(
                run_id="run-1",
                status="completed",
                config=config,
                created_at=datetime(2026, 7, 13, 9, 0),
            )

        report = RunReport(
            run_id="run-1",
            status="completed",
            config=config,
            created_at=datetime(
                2026,
                7,
                13,
                9,
                0,
                tzinfo=timezone(timedelta(hours=9)),
            ),
        )

        self.assertEqual(report.created_at.utcoffset(), timedelta(0))
        self.assertEqual(report.created_at.hour, 0)

    def test_run_report_serializes_nested_models_with_aliases(self):
        report = RunReport(
            run_id="run-1",
            status="completed",
            config=RunConfig(location="신사역", search_keyword="음식점"),
            results=[
                PlaceResult(status="excluded", name="우니도", exclusion_reason="리뷰 부족")
            ],
            created_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )

        payload = report.model_dump(mode="json", by_alias=True)

        self.assertEqual(payload["runId"], "run-1")
        self.assertEqual(payload["config"]["searchKeyword"], "음식점")
        self.assertEqual(payload["results"][0]["exclusionReason"], "리뷰 부족")


class GraphStateModelTests(unittest.TestCase):
    def test_defaults_are_independent_and_nested_state_serializes(self):
        first = GraphState(
            run_id="run-1",
            config=RunConfig(location="신사역", search_keyword="음식점"),
        )
        second = GraphState(
            run_id="run-2",
            config=RunConfig(location="강남역", search_keyword="음식점"),
        )
        first.candidates.append(CandidatePlace(place_id="1", name="우니도"))

        payload = first.model_dump(mode="json", by_alias=True)

        self.assertEqual(second.candidates, [])
        self.assertEqual(first.status.value, "pending")
        self.assertEqual(payload["candidates"][0]["placeId"], "1")
        self.assertIsNone(payload["currentPlaceDetail"])

    def test_rejects_undeclared_live_objects(self):
        with self.assertRaises(ValidationError):
            GraphState.model_validate(
                {
                    "runId": "run-1",
                    "config": {"location": "신사역", "searchKeyword": "음식점"},
                    "page": object(),
                }
            )


class ConfigCompatibilityTests(unittest.TestCase):
    def test_search_config_is_run_config_alias(self):
        from datespot_agent.config import SearchConfig

        self.assertIs(SearchConfig, RunConfig)
        config = SearchConfig(location="신사역", search_keyword="음식점")
        self.assertEqual(config.max_places, 10)
        self.assertEqual(config.weights.photo_percent, 50)


if __name__ == "__main__":
    unittest.main()
