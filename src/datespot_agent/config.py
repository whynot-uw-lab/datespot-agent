"""앱 레벨 설정 (환경변수) 및 탐색 실행 설정 모델.

- Settings: .env / 환경변수에서 로드하는 앱 전역 설정 (API 키, 모델 등).
- SearchConfig: 1회 탐색 실행 단위의 사용자 설정 (poc/00-planning.md 스키마 반영).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경변수 기반 앱 전역 설정."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        extra="ignore",
    )

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    model: str = Field(default="claude-opus-4-8", alias="DATESPOT_MODEL")
    headless: bool = Field(default=True, alias="DATESPOT_HEADLESS")


@lru_cache
def get_settings() -> Settings:
    """캐시된 Settings 인스턴스."""
    return Settings()


# --- 탐색 실행 단위 설정 (poc/00-planning.md 참고) ---


class Filters(BaseModel):
    """사전 필터링 조건 (심층 분석 전 제외 판단)."""

    categories: list[str] = Field(default_factory=list)
    min_review_count: int = 0
    max_distance_m: int | None = None


class Weights(BaseModel):
    """최종 점수 가중치. 합은 1.0 이어야 한다."""

    photo: float = 0.5
    review: float = 0.5

    @model_validator(mode="after")
    def _check_sum(self) -> "Weights":
        total = self.photo + self.review
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"가중치 합은 1.0 이어야 합니다 (현재 {total}).")
        return self


class ScoringCriteria(BaseModel):
    """점수 기준 (프롬프트에 반영되는 자연어 기준)."""

    photo: str = "어둡고 차분한 분위기, 넓은 좌석 간격, 대화하기 좋은 구조"
    review: str = "깔끔함, 조용함, 대화하기 좋음 등 긍정 표현"


class Throttle(BaseModel):
    """약관/봇 차단 리스크 대응: 요청 간 딜레이 + 속도 제한."""

    delay_ms: tuple[int, int] = (2000, 5000)  # 요청 간 랜덤 딜레이 범위(ms)
    rate_limit_per_min: int = 10  # 분당 최대 요청 수


class SearchConfig(BaseModel):
    """1회 탐색 실행 단위 설정."""

    location: str  # 기준 역/지역
    max_places: int = 30  # 1회 최대 분석 장소 수 (병렬 금지, 순차 처리)
    filters: Filters = Field(default_factory=Filters)
    weights: Weights = Field(default_factory=Weights)
    scoring: ScoringCriteria = Field(default_factory=ScoringCriteria)
    throttle: Throttle = Field(default_factory=Throttle)
