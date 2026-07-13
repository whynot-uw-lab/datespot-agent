"""장소 분석 기준 충족 판정과 최종 점수 계산."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from datespot_agent.analysis.errors import AnalysisInputError
from datespot_agent.models import (
    PhotoAnalysis,
    PlaceDetail,
    PlaceResult,
    ReviewAnalysis,
    Weights,
)

ONE_DECIMAL = Decimal("0.1")


class PlaceScoringService:
    """사진·리뷰 분석 결과를 장소 단위 결과로 결합한다."""

    def calculate(
        self,
        detail: PlaceDetail,
        weights: Weights,
        photo_analysis: PhotoAnalysis | None,
        review_analysis: ReviewAnalysis | None,
    ) -> PlaceResult:
        """활성 분석의 기준 충족 여부를 판정하고 가중 점수를 계산한다."""
        photo_active = weights.photo_percent > 0
        review_active = weights.review_percent > 0

        if photo_active and photo_analysis is None:
            raise AnalysisInputError(f"사진 분석 결과가 없음: {detail.name}")
        if review_active and review_analysis is None:
            raise AnalysisInputError(f"리뷰 분석 결과가 없음: {detail.name}")

        photo = photo_analysis if photo_active else None
        review = review_analysis if review_active else None
        common = {
            "place_id": detail.place_id,
            "name": detail.name,
            "category": detail.category,
            "address": detail.address,
            "photo_score": photo.photo_score if photo else None,
            "review_score": review.review_score if review else None,
            "photo_reason": photo.reason if photo else None,
            "review_reason": review.reason if review else None,
        }

        mismatches: list[str] = []
        if photo is not None and not photo.matched:
            mismatches.append(f"사진 기준 미충족: {photo.reason}")
        if review is not None and not review.matched:
            mismatches.append(f"리뷰 기준 미충족: {review.reason}")
        if mismatches:
            return PlaceResult(
                status="not_matched",
                mismatch_reason="; ".join(mismatches),
                **common,
            )

        weighted_total = Decimal(0)
        if photo is not None:
            weighted_total += Decimal(photo.photo_score * weights.photo_percent)
        if review is not None:
            weighted_total += Decimal(review.review_score * weights.review_percent)
        final_score = float(
            (weighted_total / Decimal(100)).quantize(
                ONE_DECIMAL,
                rounding=ROUND_HALF_UP,
            )
        )

        return PlaceResult(status="analyzed", final_score=final_score, **common)
