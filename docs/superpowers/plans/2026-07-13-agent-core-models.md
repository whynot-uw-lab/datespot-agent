# Agent Core Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Repository instructions prohibit subagent delegation unless the user explicitly requests it.

**Goal:** Implement the 2-2 Pydantic data contract for run configuration, place data, analysis results, reports, and LangGraph state while preserving the legacy `SearchConfig` import.

**Architecture:** Put every serializable agent-core model in one `datespot_agent.models` module built on a shared camel-case Pydantic base. Keep environment-backed `Settings` in `config.py`, re-export the run configuration models there, and make `SearchConfig` an exact alias of `RunConfig` so there is only one source of truth.

**Tech Stack:** Python 3.13, Pydantic 2.13+, pydantic-settings, standard-library `unittest`

## Global Constraints

- Python fields use `snake_case`; JSON/API aliases use `camelCase`.
- Inputs accept both `snake_case` and `camelCase`; `model_dump(by_alias=True)` emits `camelCase`.
- Unknown model fields are rejected and string inputs are stripped.
- Scores are integers from `0` through `10`.
- `RunConfig.max_places` is an integer from `1` through `10`.
- Weight percentages are integers from `0` through `100` and must sum to `100`.
- List fields use independent `default_factory=list` defaults.
- Datetimes stored by `RunReport` are timezone-aware and normalized to UTC.
- `GraphState` contains no Playwright `Browser`, `BrowserContext`, `Page`, or `Locator` objects.
- Existing user changes in `README.md` and `.playwright-cli/` are out of scope and must not be staged.
- Every production change follows RED → GREEN → REFACTOR using `uv run python -m unittest`.

---

### Task 1: Shared model base and run configuration

**Files:**
- Create: `src/datespot_agent/models.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Produces: `CamelModel`, `Filters`, `Weights`, `ScoringCriteria`, `RunConfig`
- `RunConfig(location: str, search_keyword: str, max_places: int = 10, filters: Filters = Filters(), weights: Weights = Weights(), scoring: ScoringCriteria = ScoringCriteria())`

- [ ] **Step 1: Write the failing run-configuration tests**

Create `tests/test_models.py`:

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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused test to verify RED**

Run:

```bash
uv run python -m unittest discover -s tests -p 'test_models.py' -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'datespot_agent.models'`.

- [ ] **Step 3: Implement the shared base and run configuration**

Create `src/datespot_agent/models.py`:

```python
"""Serializable data contracts for the agent-core workflow."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


def to_camel(field_name: str) -> str:
    """Convert a snake_case model field name to lower camelCase."""
    first, *rest = field_name.split("_")
    return first + "".join(part.capitalize() for part in rest)


class CamelModel(BaseModel):
    """Base model accepting snake_case and camelCase without extra fields."""

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
            raise ValueError("weight percentages must sum to 100")
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

- [ ] **Step 4: Run the focused test to verify GREEN**

Run:

```bash
uv run python -m unittest discover -s tests -p 'test_models.py' -v
```

Expected: `Ran 4 tests` and `OK`.

- [ ] **Step 5: Commit the run-configuration contract**

```bash
git add src/datespot_agent/models.py tests/test_models.py
git commit -m "feat: add agent run configuration models"
```

---

### Task 2: Place and analysis models

**Files:**
- Modify: `src/datespot_agent/models.py`
- Modify: `tests/test_models.py`

**Interfaces:**
- Consumes: `CamelModel`
- Produces: `CandidatePlace`, `PlaceDetail`, `PhotoAnalysis`, `ReviewAnalysis`, `FilterDecision`
- `PlaceDetail` is the typed input for pre-filter, photo-analysis, and review-analysis nodes.

- [ ] **Step 1: Write failing place and analysis tests**

Extend the import in `tests/test_models.py`:

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

Insert before the `if __name__ == "__main__"` block:

```python
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
```

- [ ] **Step 2: Run the focused test to verify RED**

Run:

```bash
uv run python -m unittest discover -s tests -p 'test_models.py' -v
```

Expected: FAIL because the five new model names cannot be imported.

- [ ] **Step 3: Implement place and analysis models**

Append to `src/datespot_agent/models.py`:

```python

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
```

- [ ] **Step 4: Run the focused test to verify GREEN**

Run:

```bash
uv run python -m unittest discover -s tests -p 'test_models.py' -v
```

Expected: `Ran 7 tests` and `OK`.

- [ ] **Step 5: Commit typed place and analysis data**

```bash
git add src/datespot_agent/models.py tests/test_models.py
git commit -m "feat: add place analysis data models"
```

---

### Task 3: Place results and run report

**Files:**
- Modify: `src/datespot_agent/models.py`
- Modify: `tests/test_models.py`

**Interfaces:**
- Consumes: `CamelModel`, `RunConfig`
- Produces: `RunStatus`, `PlaceResultStatus`, `PlaceResult`, `RunReport`
- `RunReport.created_at` accepts aware datetimes only and stores them in UTC.

- [ ] **Step 1: Write failing result and report tests**

Add to the standard-library imports in `tests/test_models.py`:

```python
from datetime import datetime, timedelta, timezone
```

Extend the `datespot_agent.models` import with:

```python
    PlaceResult,
    RunReport,
```

Insert before the module’s final `if __name__ == "__main__"` block:

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

- [ ] **Step 2: Run the focused test to verify RED**

Run:

```bash
uv run python -m unittest discover -s tests -p 'test_models.py' -v
```

Expected: FAIL because `PlaceResult` and `RunReport` cannot be imported.

- [ ] **Step 3: Implement enums, result validation, and UTC normalization**

Add these imports at the top of `src/datespot_agent/models.py`:

```python
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
```

Append:

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
            raise ValueError("analyzed result requires final_score")
        if self.status is PlaceResultStatus.EXCLUDED and not self.exclusion_reason:
            raise ValueError("excluded result requires exclusion_reason")
        if self.status is PlaceResultStatus.FAILED and not self.failure_reason:
            raise ValueError("failed result requires failure_reason")
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
            raise ValueError("created_at must be timezone-aware")
        return value.astimezone(timezone.utc)
```

- [ ] **Step 4: Run the focused test to verify GREEN**

Run:

```bash
uv run python -m unittest discover -s tests -p 'test_models.py' -v
```

Expected: `Ran 11 tests` and `OK`.

- [ ] **Step 5: Commit result and report contracts**

```bash
git add src/datespot_agent/models.py tests/test_models.py
git commit -m "feat: add place result and run report models"
```

---

### Task 4: LangGraph state model

**Files:**
- Modify: `src/datespot_agent/models.py`
- Modify: `tests/test_models.py`

**Interfaces:**
- Consumes: every model produced by Tasks 1–3
- Produces: `GraphState`
- `GraphState` is serializable and holds only run identifiers and typed data, never Playwright live objects.

- [ ] **Step 1: Write failing GraphState tests**

Extend the `datespot_agent.models` import with:

```python
    CandidatePlace,
    GraphState,
```

`CandidatePlace` is already imported after Task 2; add only `GraphState` if present.

Insert before the module’s final `if __name__ == "__main__"` block:

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

- [ ] **Step 2: Run the focused test to verify RED**

Run:

```bash
uv run python -m unittest discover -s tests -p 'test_models.py' -v
```

Expected: FAIL because `GraphState` cannot be imported.

- [ ] **Step 3: Implement GraphState**

Append to `src/datespot_agent/models.py`:

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

- [ ] **Step 4: Run the focused test to verify GREEN**

Run:

```bash
uv run python -m unittest discover -s tests -p 'test_models.py' -v
```

Expected: `Ran 13 tests` and `OK`.

- [ ] **Step 5: Commit the serializable graph state**

```bash
git add src/datespot_agent/models.py tests/test_models.py
git commit -m "feat: add serializable graph state model"
```

---

### Task 5: Config compatibility and regression verification

**Files:**
- Modify: `src/datespot_agent/config.py`
- Modify: `poc/1-1-env/smoke_test.py`
- Modify: `poc/1-1-env/GUIDE.md`
- Modify: `tests/test_models.py`

**Interfaces:**
- Consumes: `Filters`, `Weights`, `ScoringCriteria`, `RunConfig`
- Produces: `datespot_agent.config.SearchConfig is datespot_agent.models.RunConfig`
- Preserves: `Settings` and `get_settings()` behavior

- [ ] **Step 1: Write the failing compatibility test**

Insert before the module’s final `if __name__ == "__main__"` block in `tests/test_models.py`:

```python
class ConfigCompatibilityTests(unittest.TestCase):
    def test_search_config_is_run_config_alias(self):
        from datespot_agent.config import SearchConfig

        self.assertIs(SearchConfig, RunConfig)
        config = SearchConfig(location="신사역", search_keyword="음식점")
        self.assertEqual(config.max_places, 10)
        self.assertEqual(config.weights.photo_percent, 50)
```

- [ ] **Step 2: Run the focused test to verify RED**

Run:

```bash
uv run python -m unittest discover -s tests -p 'test_models.py' -v
```

Expected: FAIL because the current `SearchConfig` is a different class from `RunConfig`.

- [ ] **Step 3: Replace duplicate run models with compatibility re-exports**

Keep the existing `Settings` and `get_settings()` definitions in `src/datespot_agent/config.py`. Replace its module description, imports, and everything after `get_settings()` so the file has this structure:

```python
"""Environment-backed app settings and run-config compatibility exports."""

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

- [ ] **Step 4: Update the 1-1 smoke check to the new compatibility contract**

Replace `check_config()` in `poc/1-1-env/smoke_test.py` with:

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

Change the config description in `poc/1-1-env/GUIDE.md` to:

```markdown
2. **config** — `datespot_agent.config`의 `Settings` / `SearchConfig` 호환 별칭 로드 및 2단계 기본값(가중치 합=100, max_places=10) 확인
```

- [ ] **Step 5: Run focused tests to verify GREEN**

Run:

```bash
uv run python -m unittest discover -s tests -p 'test_models.py' -v
```

Expected: `Ran 14 tests` and `OK`.

- [ ] **Step 6: Run the full unit regression suite**

Run:

```bash
uv run python -m unittest discover -s tests -v
```

Expected: `Ran 46 tests` and `OK`.

- [ ] **Step 7: Run the environment smoke test**

Run:

```bash
uv run python poc/1-1-env/smoke_test.py
```

Expected: exit code `0`, config reports `max_places=10` and `weights=50/50`, and Playwright Chromium check passes.

- [ ] **Step 8: Check scope and commit compatibility migration**

Run:

```bash
git diff --check
git status --short
```

Expected: only `config.py`, `smoke_test.py`, `GUIDE.md`, `models.py`, and `test_models.py` are part of the 2-2 work; pre-existing `README.md` and `.playwright-cli/` remain unstaged.

```bash
git add src/datespot_agent/config.py poc/1-1-env/smoke_test.py poc/1-1-env/GUIDE.md tests/test_models.py
git commit -m "refactor: align config with agent core models"
```

---

## Completion Gate

- [ ] `tests/test_models.py` contains 14 passing model-contract tests.
- [ ] Full unit suite reports 46 passing tests and zero failures.
- [ ] Environment smoke test exits `0`.
- [ ] `git diff --check` reports no whitespace errors.
- [ ] `src/datespot_agent/models.py` is the only definition site for run and workflow models.
- [ ] `SearchConfig is RunConfig` evaluates to `True`.
- [ ] No Playwright live object field exists in `GraphState`.
- [ ] `README.md` and `.playwright-cli/` are not staged by this plan.
