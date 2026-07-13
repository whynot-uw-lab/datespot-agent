"""에이전트 코어 워크플로의 직렬화 가능한 데이터 규격."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
