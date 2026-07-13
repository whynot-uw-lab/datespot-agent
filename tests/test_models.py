from __future__ import annotations

import unittest

from pydantic import ValidationError

from datespot_agent.models import (
    CandidatePlace,
    FilterDecision,
    PhotoAnalysis,
    PlaceDetail,
    ReviewAnalysis,
    RunConfig,
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
                "filters": {"minReviewCount": 50, "maxDistanceM": 700},
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


class PlaceAndAnalysisModelTests(unittest.TestCase):
    def test_place_detail_supports_aliases_and_independent_lists(self):
        detail = PlaceDetail.model_validate(
            {
                "placeId": "1720070048",
                "name": "우니도",
                "distanceM": 520,
                "photoUrls": ["https://example.com/1.jpg"],
                "reviewCount": 128,
            }
        )
        other = PlaceDetail(place_id="2", name="다른 장소")

        detail.reviews.append("조용해요")

        self.assertEqual(detail.distance_m, 520)
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


if __name__ == "__main__":
    unittest.main()
