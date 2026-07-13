"""방문자 리뷰 기반 장소 분석 Agent."""

from __future__ import annotations

from openai import AsyncOpenAI

from datespot_agent.analysis.errors import (
    AnalysisInputError,
    AnalysisRequestError,
    AnalysisResponseError,
)
from datespot_agent.models import PlaceDetail, ReviewAnalysis

MAX_REVIEWS = 50
DEFAULT_MAX_OUTPUT_TOKENS = 700

SYSTEM_PROMPT = (
    "너는 소개팅 장소를 방문자 리뷰로 평가하는 분석가다. "
    "리뷰에 직접 나타난 근거와 추정을 구분하고 구조화된 결과만 반환한다."
)


def build_review_prompt(detail: PlaceDetail, criteria: str, reviews: list[str]) -> str:
    """장소 정보와 최신 리뷰를 사용자 기준 평가 프롬프트로 만든다."""
    numbered = "\n".join(
        f"{index}. {review}" for index, review in enumerate(reviews, start=1)
    )
    return (
        f"장소명: {detail.name}\n"
        f"카테고리: {detail.category or '확인 불가'}\n"
        f"주소: {detail.address or '확인 불가'}\n"
        f"전체 리뷰 수: {detail.review_count}\n"
        f"리뷰 평가 기준: {criteria}\n\n"
        "조용함, 대화 적합성, 친절함, 청결함, 대기·혼잡, "
        "데이트 적합성을 리뷰 근거로 평가한다. "
        "review_score는 0~10 정수다. "
        "리뷰 근거가 평가 기준을 전체적으로 충족할 때만 matched=true로 판단한다. "
        "직접 근거가 부족하면 충족한 것으로 간주하지 말고 reason에 명시한다.\n\n"
        f"리뷰 목록:\n{numbered}"
    )


class ReviewAnalysisAgent:
    """OpenAI 구조화 출력을 사용해 방문자 리뷰를 분석한다."""

    def __init__(
        self,
        client: AsyncOpenAI,
        *,
        model: str,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> None:
        self._client = client
        self._model = model
        self._max_output_tokens = max_output_tokens

    async def analyze(self, detail: PlaceDetail, criteria: str) -> ReviewAnalysis:
        """최신 리뷰 최대 50개를 사용자 기준으로 분석한다."""
        reviews = [review for review in detail.reviews if review][:MAX_REVIEWS]
        if not reviews:
            raise AnalysisInputError(f"리뷰 분석 자료가 없음: {detail.name}")

        try:
            response = await self._client.responses.parse(
                model=self._model,
                instructions=SYSTEM_PROMPT,
                max_output_tokens=self._max_output_tokens,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": build_review_prompt(detail, criteria, reviews),
                            }
                        ],
                    }
                ],
                text_format=ReviewAnalysis,
            )
        except Exception as exc:
            raise AnalysisRequestError(f"리뷰 분석 요청 실패: {detail.name}") from exc

        parsed = response.output_parsed
        if not isinstance(parsed, ReviewAnalysis):
            raise AnalysisResponseError(f"리뷰 분석 구조화 응답 없음: {detail.name}")
        return parsed
