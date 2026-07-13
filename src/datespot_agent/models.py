"""에이전트 코어 워크플로의 직렬화 가능한 데이터 규격."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def to_camel(field_name: str) -> str:
    """snake_case 모델 필드명을 lower camelCase로 변환한다."""
    first, *rest = field_name.split("_")
    return first + "".join(part.capitalize() for part in rest)


class CamelModel(BaseModel):
    """추가 필드 없이 snake_case와 camelCase를 허용하는 기반 모델."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class Filters(CamelModel):
    categories: list[str] = Field(default_factory=list)
    min_review_count: int = Field(default=0, ge=0)
    max_distance_m: int | None = Field(default=None, ge=0)


class Weights(CamelModel):
    photo_percent: int = Field(default=50, ge=0, le=100)
    review_percent: int = Field(default=50, ge=0, le=100)

    @model_validator(mode="after")
    def validate_total(self) -> "Weights":
        if self.photo_percent + self.review_percent != 100:
            raise ValueError("가중치 비율의 합은 100이어야 한다")
        return self


class ScoringCriteria(CamelModel):
    photo: str = Field(
        default="어둡고 차분한 분위기, 넓은 좌석 간격, 대화하기 좋은 구조",
        min_length=1,
    )
    review: str = Field(
        default="깔끔함, 조용함, 대화하기 좋음 등 긍정 표현",
        min_length=1,
    )


class RunConfig(CamelModel):
    location: str = Field(min_length=1)
    search_keyword: str = Field(min_length=1)
    max_places: int = Field(default=10, ge=1, le=10)
    filters: Filters = Field(default_factory=Filters)
    weights: Weights = Field(default_factory=Weights)
    scoring: ScoringCriteria = Field(default_factory=ScoringCriteria)


class CandidatePlace(CamelModel):
    place_id: str = Field(min_length=1)
    name: str = Field(min_length=1)


class PlaceDetail(CamelModel):
    place_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: str | None = None
    address: str | None = None
    distance_m: int | None = Field(default=None, ge=0)
    photo_urls: list[str] = Field(default_factory=list)
    reviews: list[str] = Field(default_factory=list)
    review_count: int = Field(default=0, ge=0)


class PhotoAnalysis(CamelModel):
    photo_score: int = Field(ge=0, le=10)
    reason: str = Field(min_length=1)


class ReviewAnalysis(CamelModel):
    review_score: int = Field(ge=0, le=10)
    reason: str = Field(min_length=1)


class FilterDecision(CamelModel):
    passed: bool
    exclusion_reason: str | None = None


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class PlaceResultStatus(str, Enum):
    ANALYZED = "analyzed"
    EXCLUDED = "excluded"
    FAILED = "failed"


class PlaceResult(CamelModel):
    status: PlaceResultStatus
    place_id: str | None = Field(default=None, min_length=1)
    name: str = Field(min_length=1)
    category: str | None = None
    address: str | None = None
    photo_score: int | None = Field(default=None, ge=0, le=10)
    review_score: int | None = Field(default=None, ge=0, le=10)
    final_score: int | None = Field(default=None, ge=0, le=10)
    photo_reason: str | None = None
    review_reason: str | None = None
    exclusion_reason: str | None = None
    failure_reason: str | None = None

    @model_validator(mode="after")
    def validate_status_fields(self) -> "PlaceResult":
        if self.status is PlaceResultStatus.ANALYZED and self.final_score is None:
            raise ValueError("분석 완료 결과에는 final_score가 필요하다")
        if self.status is PlaceResultStatus.EXCLUDED and not self.exclusion_reason:
            raise ValueError("제외 결과에는 exclusion_reason이 필요하다")
        if self.status is PlaceResultStatus.FAILED and not self.failure_reason:
            raise ValueError("실패 결과에는 failure_reason이 필요하다")
        return self


class RunReport(CamelModel):
    run_id: str = Field(min_length=1)
    status: RunStatus
    config: RunConfig
    results: list[PlaceResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at에는 timezone 정보가 필요하다")
        return value.astimezone(timezone.utc)
