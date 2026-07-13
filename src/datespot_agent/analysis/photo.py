"""내부 사진 기반 장소 분석 Agent."""

from __future__ import annotations

from openai import AsyncOpenAI

from datespot_agent.analysis.errors import (
    AnalysisInputError,
    AnalysisRequestError,
    AnalysisResponseError,
)
from datespot_agent.models import PhotoAnalysis, PlaceDetail

MAX_PHOTOS = 5
DEFAULT_MAX_OUTPUT_TOKENS = 700

SYSTEM_PROMPT = (
    "너는 소개팅 장소를 내부 사진으로 평가하는 공간 분석가다. "
    "사진에서 직접 확인되는 근거와 추정을 구분하고 구조화된 결과만 반환한다."
)


def build_photo_prompt(detail: PlaceDetail, criteria: str) -> str:
    """장소 정보와 사용자 사진 기준을 평가 프롬프트로 만든다."""
    return (
        f"장소명: {detail.name}\n"
        f"카테고리: {detail.category or '확인 불가'}\n"
        f"주소: {detail.address or '확인 불가'}\n"
        f"사진 평가 기준: {criteria}\n\n"
        "조명, 좌석 배치, 공간감, 혼잡 신호, 대화 적합성을 평가한다. "
        "photo_score는 0~10 정수다. "
        "사진 근거가 평가 기준을 전체적으로 충족할 때만 matched=true로 판단한다. "
        "확인할 수 없는 조건은 충족한 것으로 간주하지 말고 reason에 명시한다."
    )


class PhotoAnalysisAgent:
    """OpenAI 구조화 출력을 사용해 내부 사진을 분석한다."""

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

    async def analyze(self, detail: PlaceDetail, criteria: str) -> PhotoAnalysis:
        """내부 사진 최대 5장을 사용자 기준으로 분석한다."""
        photo_urls = [url for url in detail.photo_urls if url][:MAX_PHOTOS]
        if not photo_urls:
            raise AnalysisInputError(f"사진 분석 자료가 없음: {detail.name}")

        content = [{"type": "input_text", "text": build_photo_prompt(detail, criteria)}]
        content.extend(
            {"type": "input_image", "image_url": url, "detail": "low"}
            for url in photo_urls
        )

        try:
            response = await self._client.responses.parse(
                model=self._model,
                instructions=SYSTEM_PROMPT,
                max_output_tokens=self._max_output_tokens,
                input=[{"role": "user", "content": content}],
                text_format=PhotoAnalysis,
            )
        except Exception as exc:
            raise AnalysisRequestError(f"사진 분석 요청 실패: {detail.name}") from exc

        parsed = response.output_parsed
        if not isinstance(parsed, PhotoAnalysis):
            raise AnalysisResponseError(f"사진 분석 구조화 응답 없음: {detail.name}")
        return parsed
