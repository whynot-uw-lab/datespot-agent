from __future__ import annotations

import unittest

from datespot_agent.analysis import AnalysisInputError, PlaceScoringService
from datespot_agent.models import PhotoAnalysis, PlaceDetail, ReviewAnalysis, Weights


class PlaceScoringServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = PlaceScoringService()
        self.detail = PlaceDetail(
            place_id="1720070048",
            name="우니도",
            category="일식당",
            address="서울 강남구",
        )

    def test_calculates_one_decimal_weighted_score(self):
        result = self.service.calculate(
            self.detail,
            Weights(photo_percent=50, review_percent=50),
            PhotoAnalysis(photo_score=7, reason="차분함"),
            ReviewAnalysis(review_score=8, reason="조용함"),
        )

        self.assertEqual(result.status.value, "analyzed")
        self.assertEqual(result.final_score, 7.5)
        self.assertEqual(result.photo_score, 7)
        self.assertEqual(result.review_score, 8)

    def test_rounds_half_up_to_one_decimal(self):
        result = self.service.calculate(
            self.detail,
            Weights(photo_percent=55, review_percent=45),
            PhotoAnalysis(photo_score=7, reason="차분함"),
            ReviewAnalysis(review_score=8, reason="조용함"),
        )

        self.assertEqual(result.final_score, 7.5)

    def test_zero_weight_component_is_not_required_or_scored(self):
        result = self.service.calculate(
            self.detail,
            Weights(photo_percent=0, review_percent=100),
            None,
            ReviewAnalysis(review_score=8, reason="조용함"),
        )

        self.assertEqual(result.status.value, "analyzed")
        self.assertEqual(result.final_score, 8.0)
        self.assertIsNone(result.photo_score)

    def test_missing_active_analysis_raises_input_error(self):
        with self.assertRaises(AnalysisInputError):
            self.service.calculate(
                self.detail,
                Weights(photo_percent=50, review_percent=50),
                None,
                ReviewAnalysis(review_score=8, reason="조용함"),
            )

    def test_low_scores_are_still_analyzed_and_weighted(self):
        result = self.service.calculate(
            self.detail,
            Weights(photo_percent=50, review_percent=50),
            PhotoAnalysis(photo_score=0, reason="좌석 간격 확인 불가"),
            ReviewAnalysis(review_score=2, reason="소음 우려"),
        )

        self.assertEqual(result.status.value, "analyzed")
        self.assertEqual(result.final_score, 1.0)
        self.assertEqual(result.photo_score, 0)
        self.assertEqual(result.review_score, 2)


if __name__ == "__main__":
    unittest.main()
