from __future__ import annotations

import unittest

from datespot_agent.analysis import AnalysisInputError, PlaceScoringService
from datespot_agent.models import (
    AnalysisDigest,
    PhotoAnalysis,
    PlaceDetail,
    ReviewAnalysis,
    Weights,
)


def digest(summary: str) -> AnalysisDigest:
    return AnalysisDigest(
        summary=summary,
        strengths=["장점"],
        cautions=["고려사항"],
    )


class PlaceScoringServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = PlaceScoringService()
        self.detail = PlaceDetail(
            place_id="1720070048",
            name="우니도",
            category="일식당",
            address="서울 강남구",
            photo_urls=[f"https://example.com/{index}.jpg" for index in range(7)],
            reviews=[f"리뷰 {index}" for index in range(55)],
            review_count=128,
        )

    def test_calculates_one_decimal_weighted_score(self):
        result = self.service.calculate(
            self.detail,
            Weights(photo_percent=50, review_percent=50),
            PhotoAnalysis(photo_score=7, reason="차분함", digest=digest("사진 요약")),
            ReviewAnalysis(review_score=8, reason="조용함", digest=digest("리뷰 요약")),
        )

        self.assertEqual(result.status.value, "analyzed")
        self.assertEqual(result.final_score, 7.5)
        self.assertEqual(result.photo_score, 7)
        self.assertEqual(result.review_score, 8)

    def test_rounds_half_up_to_one_decimal(self):
        result = self.service.calculate(
            self.detail,
            Weights(photo_percent=55, review_percent=45),
            PhotoAnalysis(photo_score=7, reason="차분함", digest=digest("사진 요약")),
            ReviewAnalysis(review_score=8, reason="조용함", digest=digest("리뷰 요약")),
        )

        self.assertEqual(result.final_score, 7.5)

    def test_zero_weight_component_is_not_required_or_scored(self):
        result = self.service.calculate(
            self.detail,
            Weights(photo_percent=0, review_percent=100),
            None,
            ReviewAnalysis(review_score=8, reason="조용함", digest=digest("리뷰 요약")),
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
                ReviewAnalysis(review_score=8, reason="조용함", digest=digest("리뷰 요약")),
            )

    def test_low_scores_are_still_analyzed_and_weighted(self):
        result = self.service.calculate(
            self.detail,
            Weights(photo_percent=50, review_percent=50),
            PhotoAnalysis(
                photo_score=0,
                reason="좌석 간격 확인 불가",
                digest=digest("사진 요약"),
            ),
            ReviewAnalysis(
                review_score=2,
                reason="소음 우려",
                digest=digest("리뷰 요약"),
            ),
        )

        self.assertEqual(result.status.value, "analyzed")
        self.assertEqual(result.final_score, 1.0)
        self.assertEqual(result.photo_score, 0)
        self.assertEqual(result.review_score, 2)

    def test_copies_analysis_digests_and_exact_limited_evidence(self):
        photo = PhotoAnalysis(
            photo_score=7,
            reason="차분함",
            digest=digest("사진 요약"),
        )
        review = ReviewAnalysis(
            review_score=8,
            reason="조용함",
            digest=digest("리뷰 요약"),
        )

        result = self.service.calculate(
            self.detail,
            Weights(photo_percent=50, review_percent=50),
            photo,
            review,
        )

        self.assertEqual(result.photo_digest, photo.digest)
        self.assertEqual(result.review_digest, review.digest)
        self.assertIsNotNone(result.evidence)
        assert result.evidence is not None
        self.assertEqual(
            result.evidence.place_url,
            "https://map.naver.com/p/entry/place/1720070048",
        )
        self.assertEqual(result.evidence.photo_urls, self.detail.photo_urls[:5])
        self.assertEqual(result.evidence.reviews, self.detail.reviews[:50])
        self.assertEqual(result.evidence.source_review_count, 128)


if __name__ == "__main__":
    unittest.main()
