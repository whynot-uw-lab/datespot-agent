# 에이전트 코어 데이터 모델 구현 계획

> **에이전트 작업자 필수 사항:** 이 계획을 작업 단위로 실행할 때 `superpowers:executing-plans` 스킬을 사용한다. 사용자가 명시적으로 요청하지 않는 한 저장소 지침에 따라 하위 에이전트 위임을 금지한다.

**목표:** 기존 `SearchConfig` import 호환성을 유지하면서 실행 설정, 장소 데이터, 분석 결과, 리포트, LangGraph 상태에 필요한 2-2 Pydantic 데이터 규격을 구현한다.

**아키텍처:** 직렬화 가능한 모든 에이전트 코어 모델을 공통 camelCase Pydantic 기반 클래스와 함께 `datespot_agent.models` 단일 모듈에 둔다. 환경변수 기반 `Settings`는 `config.py`에 유지하고 실행 설정 모델을 해당 모듈에서 다시 export한다. `SearchConfig`는 `RunConfig`의 동일 객체 별칭으로 만들어 데이터 규격의 단일 출처를 유지한다.

**기술 스택:** Python 3.13, Pydantic 2.13+, pydantic-settings, 표준 라이브러리 `unittest`

## 전체 제약사항

- Python 필드는 `snake_case`, JSON/API 별칭은 `camelCase`를 사용한다.
- 입력은 `snake_case`와 `camelCase`를 모두 허용하고, `model_dump(by_alias=True)`는 `camelCase`로 출력한다.
- 정의되지 않은 모델 필드는 거부하고 문자열 입력의 앞뒤 공백을 제거한다.
- 점수는 `0`부터 `10`까지의 정수다.
- `RunConfig.max_places`는 `1`부터 `10`까지의 정수다.
- 거리 기반 필터와 장소 거리 필드는 모델 계약에 포함하지 않는다.
- 가중치 비율은 `0`부터 `100`까지의 정수이며 합계는 반드시 `100`이다.
- 리스트 필드는 독립적인 `default_factory=list` 기본값을 사용한다.
- `RunReport`에 저장되는 datetime은 timezone을 포함하며 UTC로 정규화한다.
- `GraphState`에는 Playwright `Browser`, `BrowserContext`, `Page`, `Locator` 객체를 넣지 않는다.
- 기존 사용자 변경인 `README.md`와 `.playwright-cli/`는 범위 밖이며 stage하지 않는다.
- 모든 프로덕션 변경은 `uv run python -m unittest`를 사용해 RED → GREEN → REFACTOR 순서로 진행한다.

---

### 작업 1: 공통 모델 기반 클래스와 실행 설정

**파일:**
- 생성: `src/datespot_agent/models.py`
- 생성: `tests/test_models.py`

**인터페이스:**
- 생성 결과: `CamelModel`, `Filters`, `Weights`, `ScoringCriteria`, `RunConfig`
- `RunConfig(location: str, search_keyword: str, max_places: int = 10, filters: Filters = Filters(), weights: Weights = Weights(), scoring: ScoringCriteria = ScoringCriteria())`

- [ ] **1단계: 실패하는 실행 설정 테스트 작성**

`tests/test_models.py` 생성:

```python
from __future__ import annotations

import unittest

from pydantic import ValidationError

from datespot_agent.models import RunConfig, Weights


class RunConfigTests(unittest.TestCase):
    def test_accepts_snake_and_camel_and_serializes_aliases(self):
        snake = RunConfig(location=" 신사역 ", search_keyword="음식점")
        camel = RunConfig.model_validate(
            {
                "location": "강남역",
                "searchKeyword": "일식",
                "maxPlaces": 3,
                "filters": {"minReviewCount": 50},
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

    def test_distance_filter_is_not_part_of_run_config(self):
        with self.assertRaises(ValidationError):
            RunConfig.model_validate(
                {
                    "location": "신사역",
                    "searchKeyword": "일식",
                    "filters": {"maxDistanceM": 700},
                }
            )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **2단계: 대상 테스트를 실행해 RED 확인**

실행:

```bash
uv run python -m unittest discover -s tests -p 'test_models.py' -v
```

예상 결과: `ModuleNotFoundError: No module named 'datespot_agent.models'`와 함께 실패함.

- [ ] **3단계: 공통 기반 클래스와 실행 설정 구현**

`src/datespot_agent/models.py` 생성:

```python
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
```

- [ ] **4단계: 대상 테스트를 실행해 GREEN 확인**

실행:

```bash
uv run python -m unittest discover -s tests -p 'test_models.py' -v
```

예상 결과: `Ran 5 tests`, `OK`.

- [ ] **5단계: 실행 설정 규격 커밋**

```bash
git add src/datespot_agent/models.py tests/test_models.py
git commit -m "feat: add agent run configuration models"
```

---

### 작업 2: 장소 및 분석 모델

**파일:**
- 수정: `src/datespot_agent/models.py`
- 수정: `tests/test_models.py`

**인터페이스:**
- 입력 의존성: `CamelModel`
- 생성 결과: `CandidatePlace`, `PlaceDetail`, `PhotoAnalysis`, `ReviewAnalysis`, `FilterDecision`
- `PlaceDetail`은 사전 필터, 사진 분석, 리뷰 분석 노드의 타입 지정 입력값이다.

- [ ] **1단계: 실패하는 장소 및 분석 테스트 작성**

`tests/test_models.py`의 import 확장:

```python
from datespot_agent.models import (
    CandidatePlace,
    FilterDecision,
    PhotoAnalysis,
    PlaceDetail,
    ReviewAnalysis,
    RunConfig,
    Weights,
)
```

`if __name__ == "__main__"` 블록 앞에 삽입:

```python
class PlaceAndAnalysisModelTests(unittest.TestCase):
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
```

- [ ] **2단계: 대상 테스트를 실행해 RED 확인**

실행:

```bash
uv run python -m unittest discover -s tests -p 'test_models.py' -v
```

예상 결과: 신규 모델 5개를 import할 수 없어 실패함.

- [ ] **3단계: 장소 및 분석 모델 구현**

`src/datespot_agent/models.py`에 추가:

```python

class CandidatePlace(CamelModel):
    place_id: str = Field(min_length=1)
    name: str = Field(min_length=1)


class PlaceDetail(CamelModel):
    place_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: str | None = None
    address: str | None = None
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
```

- [ ] **4단계: 대상 테스트를 실행해 GREEN 확인**

실행:

```bash
uv run python -m unittest discover -s tests -p 'test_models.py' -v
```

예상 결과: `Ran 9 tests`, `OK`.

- [ ] **5단계: 타입이 지정된 장소 및 분석 데이터 커밋**

```bash
git add src/datespot_agent/models.py tests/test_models.py
git commit -m "feat: add place analysis data models"
```

---

### 작업 3: 장소 결과 및 실행 리포트

**파일:**
- 수정: `src/datespot_agent/models.py`
- 수정: `tests/test_models.py`

**인터페이스:**
- 입력 의존성: `CamelModel`, `RunConfig`
- 생성 결과: `RunStatus`, `PlaceResultStatus`, `PlaceResult`, `RunReport`
- `RunReport.created_at`은 timezone이 있는 datetime만 허용하고 UTC로 저장한다.

- [ ] **1단계: 실패하는 결과 및 리포트 테스트 작성**

`tests/test_models.py`의 표준 라이브러리 import에 추가:

```python
from datetime import datetime, timedelta, timezone
```

`datespot_agent.models` import에 추가:

```python
    PlaceResult,
    RunReport,
```

모듈의 마지막 `if __name__ == "__main__"` 블록 앞에 삽입:

```python
class ResultAndReportModelTests(unittest.TestCase):
    def test_place_result_requires_fields_for_each_status(self):
        invalid_payloads = (
            {"status": "analyzed", "name": "우니도"},
            {"status": "excluded", "name": "우니도"},
            {"status": "failed", "name": "우니도"},
        )

        for payload in invalid_payloads:
            with self.subTest(status=payload["status"]):
                with self.assertRaises(ValidationError):
                    PlaceResult.model_validate(payload)

    def test_place_result_allows_partial_component_scores(self):
        result = PlaceResult(
            status="analyzed",
            place_id="1720070048",
            name="우니도",
            final_score=8,
        )

        self.assertIsNone(result.photo_score)
        self.assertIsNone(result.review_score)
        with self.assertRaises(ValidationError):
            PlaceResult(status="analyzed", place_id=" ", name="우니도", final_score=8)

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
                PlaceResult(status="excluded", name="우니도", exclusion_reason="리뷰 부족")
            ],
            created_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )

        payload = report.model_dump(mode="json", by_alias=True)

        self.assertEqual(payload["runId"], "run-1")
        self.assertEqual(payload["config"]["searchKeyword"], "음식점")
        self.assertEqual(payload["results"][0]["exclusionReason"], "리뷰 부족")
```

- [ ] **2단계: 대상 테스트를 실행해 RED 확인**

실행:

```bash
uv run python -m unittest discover -s tests -p 'test_models.py' -v
```

예상 결과: `PlaceResult`와 `RunReport`를 import할 수 없어 실패함.

- [ ] **3단계: enum, 결과 검증, UTC 정규화 구현**

`src/datespot_agent/models.py` 상단에 다음 import 추가:

```python
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
```

아래 내용 추가:

```python

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
```

- [ ] **4단계: 대상 테스트를 실행해 GREEN 확인**

실행:

```bash
uv run python -m unittest discover -s tests -p 'test_models.py' -v
```

예상 결과: `Ran 13 tests`, `OK`.

- [ ] **5단계: 결과 및 리포트 규격 커밋**

```bash
git add src/datespot_agent/models.py tests/test_models.py
git commit -m "feat: add place result and run report models"
```

---

### 작업 4: LangGraph 상태 모델

**파일:**
- 수정: `src/datespot_agent/models.py`
- 수정: `tests/test_models.py`

**인터페이스:**
- 입력 의존성: 작업 1~3에서 생성한 모든 모델
- 생성 결과: `GraphState`
- `GraphState`는 직렬화 가능하며 실행 식별자와 타입 지정 데이터만 보유하고 Playwright live object는 저장하지 않는다.

- [ ] **1단계: 실패하는 GraphState 테스트 작성**

`datespot_agent.models` import에 추가:

```python
    CandidatePlace,
    GraphState,
```

작업 2 이후에는 `CandidatePlace`가 이미 import되어 있으므로 중복된 경우 `GraphState`만 추가한다.

모듈의 마지막 `if __name__ == "__main__"` 블록 앞에 삽입:

```python
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
```

- [ ] **2단계: 대상 테스트를 실행해 RED 확인**

실행:

```bash
uv run python -m unittest discover -s tests -p 'test_models.py' -v
```

예상 결과: `GraphState`를 import할 수 없어 실패함.

- [ ] **3단계: GraphState 구현**

`src/datespot_agent/models.py`에 추가:

```python

class GraphState(CamelModel):
    run_id: str = Field(min_length=1)
    config: RunConfig
    status: RunStatus = RunStatus.PENDING
    candidates: list[CandidatePlace] = Field(default_factory=list)
    current_place_index: int = Field(default=0, ge=0)
    current_place: CandidatePlace | None = None
    current_place_detail: PlaceDetail | None = None
    filter_decision: FilterDecision | None = None
    photo_analysis: PhotoAnalysis | None = None
    review_analysis: ReviewAnalysis | None = None
    place_results: list[PlaceResult] = Field(default_factory=list)
    final_report: RunReport | None = None
    last_error: str | None = None
```

- [ ] **4단계: 대상 테스트를 실행해 GREEN 확인**

실행:

```bash
uv run python -m unittest discover -s tests -p 'test_models.py' -v
```

예상 결과: `Ran 15 tests`, `OK`.

- [ ] **5단계: 직렬화 가능한 그래프 상태 커밋**

```bash
git add src/datespot_agent/models.py tests/test_models.py
git commit -m "feat: add serializable graph state model"
```

---

### 작업 5: 설정 호환성과 회귀 검증

**파일:**
- 수정: `src/datespot_agent/config.py`
- 수정: `poc/1-1-env/smoke_test.py`
- 수정: `poc/1-1-env/GUIDE.md`
- 수정: `tests/test_models.py`

**인터페이스:**
- 입력 의존성: `Filters`, `Weights`, `ScoringCriteria`, `RunConfig`
- 생성 결과: `datespot_agent.config.SearchConfig is datespot_agent.models.RunConfig`
- 유지 대상: `Settings`, `get_settings()` 동작

- [ ] **1단계: 실패하는 호환성 테스트 작성**

`tests/test_models.py`의 마지막 `if __name__ == "__main__"` 블록 앞에 삽입:

```python
class ConfigCompatibilityTests(unittest.TestCase):
    def test_search_config_is_run_config_alias(self):
        from datespot_agent.config import SearchConfig

        self.assertIs(SearchConfig, RunConfig)
        config = SearchConfig(location="신사역", search_keyword="음식점")
        self.assertEqual(config.max_places, 10)
        self.assertEqual(config.weights.photo_percent, 50)
```

- [ ] **2단계: 대상 테스트를 실행해 RED 확인**

실행:

```bash
uv run python -m unittest discover -s tests -p 'test_models.py' -v
```

예상 결과: 현재 `SearchConfig`가 `RunConfig`와 다른 클래스이므로 실패함.

- [ ] **3단계: 중복 실행 모델을 호환용 re-export로 교체**

`src/datespot_agent/config.py`의 기존 `Settings`, `get_settings()` 정의는 유지한다. 모듈 설명과 import, `get_settings()` 이후 내용을 교체해 다음 구조로 만든다:

```python
"""환경변수 기반 앱 설정과 실행 설정 호환 export."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from datespot_agent.models import Filters, RunConfig, ScoringCriteria, Weights


class Settings(BaseSettings):
    """환경변수 기반 앱 전역 설정."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        extra="ignore",
    )

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    model: str = Field(default="gpt-5.4-nano", alias="DATESPOT_MODEL")
    headless: bool = Field(default=True, alias="DATESPOT_HEADLESS")


@lru_cache
def get_settings() -> Settings:
    """캐시된 Settings 인스턴스."""
    return Settings()


SearchConfig = RunConfig

__all__ = [
    "Filters",
    "RunConfig",
    "ScoringCriteria",
    "SearchConfig",
    "Settings",
    "Weights",
    "get_settings",
]
```

- [ ] **4단계: 1-1 스모크 검증을 새 호환 규격에 맞게 수정**

`poc/1-1-env/smoke_test.py`의 `check_config()`를 다음 내용으로 교체:

```python
def check_config() -> str:
    from datespot_agent.config import SearchConfig, get_settings

    settings = get_settings()
    cfg = SearchConfig(location="강남역", search_keyword="음식점")
    assert cfg.max_places == 10
    assert cfg.weights.photo_percent + cfg.weights.review_percent == 100
    return (
        f"설정 로드 (model={settings.model}, headless={settings.headless}, "
        f"max_places={cfg.max_places}, "
        f"weights={cfg.weights.photo_percent}/{cfg.weights.review_percent})"
    )
```

`poc/1-1-env/GUIDE.md`의 설정 설명을 다음과 같이 변경:

```markdown
2. **config** — `datespot_agent.config`의 `Settings` / `SearchConfig` 호환 별칭 로드 및 2단계 기본값(가중치 합=100, max_places=10) 확인
```

- [ ] **5단계: 대상 테스트를 실행해 GREEN 확인**

실행:

```bash
uv run python -m unittest discover -s tests -p 'test_models.py' -v
```

예상 결과: `Ran 16 tests`, `OK`.

- [ ] **6단계: 전체 단위 회귀 테스트 실행**

실행:

```bash
uv run python -m unittest discover -s tests -v
```

예상 결과: `Ran 48 tests`, `OK`.

- [ ] **7단계: 환경 스모크 테스트 실행**

실행:

```bash
uv run python poc/1-1-env/smoke_test.py
```

예상 결과: 종료 코드 `0`, 설정 출력 `max_places=10`, `weights=50/50`, Playwright Chromium 검증 통과.

- [ ] **8단계: 변경 범위 확인 및 호환성 마이그레이션 커밋**

실행:

```bash
git diff --check
git status --short
```

예상 결과: `config.py`, `smoke_test.py`, `GUIDE.md`, `models.py`, `test_models.py`만 2-2 작업에 포함됨. 기존 `README.md`, `.playwright-cli/`는 stage되지 않은 상태로 유지됨.

```bash
git add src/datespot_agent/config.py poc/1-1-env/smoke_test.py poc/1-1-env/GUIDE.md tests/test_models.py
git commit -m "refactor: align config with agent core models"
```

---

## 완료 조건

- [ ] `tests/test_models.py`의 모델 규격 테스트 16개 통과.
- [ ] 전체 단위 테스트 48개 통과, 실패 0개.
- [ ] 환경 스모크 테스트 종료 코드 `0`.
- [ ] `git diff --check` 공백 오류 없음.
- [ ] 실행 및 워크플로 모델 정의 위치는 `src/datespot_agent/models.py` 하나뿐임.
- [ ] `SearchConfig is RunConfig` 평가 결과 `True`.
- [ ] `GraphState`에 Playwright live object 필드 없음.
- [ ] 이 계획으로 `README.md`, `.playwright-cli/`를 stage하지 않음.
