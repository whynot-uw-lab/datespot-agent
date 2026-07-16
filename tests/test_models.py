from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

import datespot_agent.models as models
from datespot_agent.models import (
    AnalysisDigest,
    CandidatePlace,
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
                "weights": {"photoPercent": 60, "reviewPercent": 40},
            }
        )

        self.assertEqual(snake.location, "신사역")
        self.assertEqual(camel.search_keyword, "일식")
        payload = camel.model_dump(by_alias=True)
        self.assertEqual(payload["maxPlaces"], 3)
        self.assertEqual(payload["weights"]["photoPercent"], 60)
        self.assertNotIn("filters", payload)
        self.assertNotIn("search_keyword", payload)

    def test_rejects_out_of_range_unknown_and_removed_filter_fields(self):
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
            RunConfig.model_validate(
                {
                    "location": "신사역",
                    "searchKeyword": "음식점",
                    "filters": {"categories": ["일식"]},
                }
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

    def test_nested_scoring_defaults_are_independent(self):
        first = RunConfig(location="신사역", search_keyword="음식점")
        second = RunConfig(location="강남역", search_keyword="음식점")

        first.scoring.photo = "밝고 활기찬 분위기"

        self.assertNotEqual(first.scoring.photo, second.scoring.photo)


class PlaceAndAnalysisModelTests(unittest.TestCase):
    def test_analysis_digest_and_place_evidence_validate_and_serialize(self):
        digest_model = getattr(models, "AnalysisDigest", None)
        evidence_model = getattr(models, "PlaceEvidence", None)

        self.assertIsNotNone(digest_model)
        self.assertIsNotNone(evidence_model)

        digest = digest_model(
            summary="차분한 룸 좌석",
            strengths=["프라이빗한 좌석", "대화하기 좋은 구조"],
            cautions=["혼잡 시간대 소음 가능성"],
        )
        evidence = evidence_model(
            place_url="https://map.naver.com/p/entry/place/1720070048",
            photo_urls=["https://example.com/interior.jpg"],
            reviews=[" ", "조용해서 대화하기 좋아요"],
            source_review_count=128,
        )

        payload = evidence.model_dump(mode="json", by_alias=True)
        self.assertEqual(digest.strengths, ["프라이빗한 좌석", "대화하기 좋은 구조"])
        self.assertEqual(payload["provider"], "naver_map")
        self.assertEqual(
            payload["placeUrl"],
            "https://map.naver.com/p/entry/place/1720070048",
        )
        self.assertEqual(payload["photoUrls"], ["https://example.com/interior.jpg"])
        self.assertEqual(payload["reviews"], ["조용해서 대화하기 좋아요"])
        self.assertEqual(payload["sourceReviewCount"], 128)
        tuple_reviews = evidence_model.model_validate(
            {
                "placeUrl": "https://map.naver.com/p/entry/place/1720070048",
                "reviews": (" ", "튜플 리뷰"),
            }
        )
        self.assertEqual(tuple_reviews.reviews, ["튜플 리뷰"])

        invalid_digest_payloads = (
            {"summary": " ", "strengths": [], "cautions": []},
            {
                "summary": "요약",
                "strengths": ["1", "2", "3", "4", "5"],
                "cautions": [],
            },
            {"summary": "요약", "strengths": [" "], "cautions": []},
        )
        for invalid in invalid_digest_payloads:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    digest_model.model_validate(invalid)

        invalid_evidence_payloads = (
            {"placeUrl": "https://example.com/place/1"},
            {
                "placeUrl": "https://map.naver.com/p/entry/place/1",
                "photoUrls": [f"https://example.com/{index}.jpg" for index in range(6)],
            },
            {
                "placeUrl": "https://map.naver.com/p/entry/place/1",
                "photoUrls": ["ftp://example.com/one.jpg"],
            },
            {
                "placeUrl": "https://map.naver.com/p/entry/place/1",
                "photoUrls": ["https://"],
            },
            {
                "placeUrl": "https://map.naver.com/p/entry/place/1",
                "photoUrls": ["https:// example"],
            },
            {
                "placeUrl": "https://map.naver.com/p/entry/place/1",
                "reviews": [f"리뷰 {index}" for index in range(51)],
            },
        )
        for invalid in invalid_evidence_payloads:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    evidence_model.model_validate(invalid)

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

    def test_analysis_models_require_score_reason_and_digest(self):
        self.assertIn("digest", PhotoAnalysis.model_fields)
        self.assertIn("digest", ReviewAnalysis.model_fields)

        digest = AnalysisDigest(
            summary="차분하고 대화하기 좋은 편",
            strengths=["차분한 조명"],
            cautions=["혼잡도 확인 제한"],
        )
        photo = PhotoAnalysis(photo_score=7, reason="차분함", digest=digest)
        review = ReviewAnalysis(
            review_score=8,
            reason="소음 근거가 있음",
            digest=digest,
        )

        self.assertNotIn("matched", photo.model_dump())
        self.assertNotIn("matched", review.model_dump())

        for score in (-1, 11, 7.5):
            with self.subTest(score=score):
                with self.assertRaises(ValidationError):
                    PhotoAnalysis(photo_score=score, reason="근거", digest=digest)

        with self.assertRaises(ValidationError):
            PhotoAnalysis(photo_score=7, reason="근거")

    def test_identifying_fields_and_reasons_cannot_be_blank(self):
        with self.assertRaises(ValidationError):
            CandidatePlace(place_id=" ", name="우니도")
        with self.assertRaises(ValidationError):
            ReviewAnalysis(
                review_score=8,
                reason=" ",
                digest=AnalysisDigest(
                    summary="요약",
                    strengths=[],
                    cautions=[],
                ),
            )


class ResultAndReportModelTests(unittest.TestCase):
    def test_place_result_requires_fields_for_each_status(self):
        invalid_payloads = (
            {"status": "analyzed", "name": "우니도"},
            {"status": "not_matched", "name": "우니도"},
            {"status": "failed", "name": "우니도"},
            {"status": "excluded", "name": "우니도", "exclusionReason": "리뷰 부족"},
        )

        for payload in invalid_payloads:
            with self.subTest(status=payload["status"]):
                with self.assertRaises(ValidationError):
                    PlaceResult.model_validate(payload)

    def test_place_result_accepts_one_decimal_final_score(self):
        result = PlaceResult(
            status="analyzed",
            place_id="1720070048",
            name="우니도",
            final_score=7.5,
        )

        self.assertEqual(result.final_score, 7.5)
        with self.assertRaises(ValidationError):
            PlaceResult(status="analyzed", name="우니도", final_score=7.55)

    def test_place_result_rejects_removed_match_contract(self):
        with self.assertRaises(ValidationError):
            PlaceResult(
                status="not_matched",
                name="우니도",
                mismatch_reason="기준 미충족",
            )
        with self.assertRaises(ValidationError):
            PlaceResult(
                status="analyzed",
                name="우니도",
                final_score=6.0,
                mismatch_reason="제거된 필드",
            )

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
                PlaceResult(
                    status="analyzed",
                    name="우니도",
                    final_score=7.5,
                )
            ],
            created_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )

        payload = report.model_dump(mode="json", by_alias=True)

        self.assertEqual(payload["runId"], "run-1")
        self.assertEqual(payload["config"]["searchKeyword"], "음식점")
        self.assertEqual(payload["results"][0]["finalScore"], 7.5)
        self.assertNotIn("mismatchReason", payload["results"][0])


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

    def test_rejects_removed_filter_decision(self):
        with self.assertRaises(ValidationError):
            GraphState.model_validate(
                {
                    "runId": "run-1",
                    "config": {"location": "신사역", "searchKeyword": "음식점"},
                    "filterDecision": {"passed": True},
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
