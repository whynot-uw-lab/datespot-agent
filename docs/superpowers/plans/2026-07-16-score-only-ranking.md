# Score-Only Place Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 정상 분석된 모든 장소를 이진 통과 판정 없이 가중 점수로 계산하고 웹에서 점수순으로 표시함.

**Architecture:** 사진·리뷰 분석 출력에서 `matched`를 제거하고 점수·근거만 도메인 모델에 보존한다. 점수 서비스는 활성 분석 결과가 있으면 항상 `analyzed` 결과를 만들며, 그래프·SSE·프런트 계약은 `analyzed/failed`만 처리한다.

**Tech Stack:** Python 3.13, Pydantic v2, LangGraph, unittest, React 19, TypeScript 7, Vitest

## Global Constraints

- 정상 분석 장소는 0점을 포함해 모두 `analyzed`로 처리함.
- 정렬 기준은 `finalScore` 내림차순 하나이며 동점은 입력 순서를 유지함.
- `matched`, `not_matched`, `mismatchReason`을 백엔드·SSE·프런트 계약에서 제거함.
- 실제 수집·분석·점수 계산 실패만 `failed`로 처리함.
- `reports/`의 기존 JSON은 삭제하되 `.gitkeep`, 실행 로그, 브라우저 산출물은 유지함.
- 사용자 소유 변경 `blind-date-recommend.iml`, `output/`은 수정하거나 커밋하지 않음.

---

### Task 1: 분석 모델과 점수 계산을 점수 전용으로 변경

**Files:**
- Modify: `tests/test_models.py`
- Modify: `tests/test_photo_analysis_agent.py`
- Modify: `tests/test_review_analysis_agent.py`
- Modify: `tests/test_place_scoring_service.py`
- Modify: `src/datespot_agent/models.py`
- Modify: `src/datespot_agent/analysis/photo.py`
- Modify: `src/datespot_agent/analysis/review.py`
- Modify: `src/datespot_agent/analysis/scoring.py`

**Interfaces:**
- Produces: `PhotoAnalysis(photo_score: int, reason: str)`
- Produces: `ReviewAnalysis(review_score: int, reason: str)`
- Produces: `PlaceResultStatus` with `ANALYZED`, `FAILED`
- Produces: `PlaceScoringService.calculate(...) -> PlaceResult(status="analyzed")`

- [ ] **Step 1: 점수 전용 계약 실패 테스트 작성**

```python
photo = PhotoAnalysis(photo_score=0, reason="근거 부족")
review = ReviewAnalysis(review_score=2, reason="소음 언급")
result = service.calculate(detail, Weights(), photo, review)
self.assertEqual(result.status, PlaceResultStatus.ANALYZED)
self.assertEqual(result.final_score, 1.0)
self.assertNotIn("matched", photo.model_dump())
```

프롬프트 테스트는 `matched`가 없고 점수·근거 지시가 존재하는지 검증한다.

- [ ] **Step 2: RED 확인**

Run:

```bash
uv run python -m unittest tests.test_models tests.test_photo_analysis_agent tests.test_review_analysis_agent tests.test_place_scoring_service -v
```

Expected: 기존 모델이 `matched`를 요구하고 기존 프롬프트가 `matched`를 포함하여 FAIL.

- [ ] **Step 3: 모델·프롬프트·점수 서비스 최소 구현**

```python
class PhotoAnalysis(CamelModel):
    photo_score: int = Field(ge=0, le=10)
    reason: str = Field(min_length=1)

class ReviewAnalysis(CamelModel):
    review_score: int = Field(ge=0, le=10)
    reason: str = Field(min_length=1)

class PlaceResultStatus(str, Enum):
    ANALYZED = "analyzed"
    FAILED = "failed"
```

`PlaceScoringService.calculate`에서 mismatch 분기 없이 기존 가중 합계와 반올림을 실행한다.

- [ ] **Step 4: GREEN 확인**

Run: Step 2와 동일

Expected: 대상 테스트 전체 PASS.

- [ ] **Step 5: 변경 범위 커밋**

```bash
git add tests/test_models.py tests/test_photo_analysis_agent.py tests/test_review_analysis_agent.py tests/test_place_scoring_service.py src/datespot_agent/models.py src/datespot_agent/analysis/photo.py src/datespot_agent/analysis/review.py src/datespot_agent/analysis/scoring.py
git commit -m "refactor: score every analyzed place"
```

### Task 2: 그래프·이벤트에서 이진 판정 제거

**Files:**
- Modify: `tests/test_graph_service.py`
- Modify: `tests/test_run_event_hub.py`
- Modify: `src/datespot_agent/graph/service.py`
- Modify: `src/datespot_agent/api/events.py`

**Interfaces:**
- Consumes: Task 1의 점수 전용 분석·결과 모델
- Produces: `RunProgressData` without `matched`
- Produces: `RunEventPublisher.progress(..., score: int | None, photo_urls: tuple[str, ...] | None)`

- [ ] **Step 1: 이벤트 계약 실패 테스트 작성**

```python
self.assertEqual(photo_events[2]["score"], 8)
self.assertNotIn("matched", photo_events[2])
self.assertEqual(review_events[2]["score"], 9)
self.assertNotIn("matched", review_events[2])
```

place result snapshot은 `analyzed`와 `failed`만 직렬화하고 `mismatchReason`이 없음을 검증한다.

- [ ] **Step 2: RED 확인**

Run:

```bash
uv run python -m unittest tests.test_graph_service tests.test_run_event_hub -v
```

Expected: 이벤트 payload와 graph log가 기존 `matched/not_matched` 계약을 사용하여 FAIL.

- [ ] **Step 3: 그래프·이벤트 최소 구현**

```python
class RunProgressData(_FrozenCamelModel):
    # 기존 필드 유지
    score: int | None = Field(default=None, ge=0, le=10)
    photo_urls: tuple[str, ...] | None = None
```

`RunEventPublisher.progress`, graph `_progress`, 분석 완료 emit에서 `matched` 인자를 제거한다.
리포트 완료 로그는 `analyzed`, `failed`만 집계한다.

- [ ] **Step 4: GREEN 확인**

Run: Step 2와 동일

Expected: 대상 테스트 전체 PASS.

- [ ] **Step 5: 변경 범위 커밋**

```bash
git add tests/test_graph_service.py tests/test_run_event_hub.py src/datespot_agent/graph/service.py src/datespot_agent/api/events.py
git commit -m "refactor: remove match status from run events"
```

### Task 3: 프런트 리포트를 전체 점수순으로 변경

**Files:**
- Modify: `frontend/src/api/contracts.ts`
- Modify: `frontend/src/realtime/runEventReducer.ts`
- Modify: `frontend/src/features/run-progress/RunProgressPage.tsx`
- Modify: `frontend/src/features/run-progress/RunProgressPage.test.tsx`
- Modify: `frontend/src/features/reports/ReportView.tsx`
- Modify: `frontend/src/features/reports/ReportView.test.tsx`

**Interfaces:**
- Consumes: Task 2의 `matched` 없는 SSE payload
- Produces: `PlaceResultStatus = "analyzed" | "failed"`
- Produces: `ReportView`의 `점수순 장소`, `평가 완료`, `확인 실패` UI

- [ ] **Step 1: 점수순·용어 실패 테스트 작성**

```tsx
const results = [
  { status: "analyzed", name: "0점 장소", finalScore: 0 },
  { status: "analyzed", name: "고득점 장소", finalScore: 9 },
  { status: "failed", name: "수집 실패", failureReason: "상세 수집 실패" },
];
expect(within(screen.getByLabelText("점수순 장소")).getAllByRole("heading")[0]).toHaveTextContent("고득점 장소");
expect(screen.getByText("0점 장소")).toBeInTheDocument();
expect(screen.getByText("평가 완료")).toBeInTheDocument();
expect(screen.queryByText(/충족|추천/)).not.toBeInTheDocument();
```

진행 화면 테스트는 `9점`만 보이고 충족 문구가 없음을 검증한다.

- [ ] **Step 2: RED 확인**

Run:

```bash
npm test -- --run src/features/reports/ReportView.test.tsx src/features/run-progress/RunProgressPage.test.tsx
```

Expected: 기존 추천·미충족 UI와 `matched` 렌더링 때문에 FAIL.

- [ ] **Step 3: 프런트 계약·UI 최소 구현**

```ts
export type PlaceResultStatus = "analyzed" | "failed";
```

`mismatchReason`, reducer의 `matched`, 진행 화면의 충족 문구를 제거한다. `ReportView`는
`analyzed` 전체를 안정적인 내림차순으로 정렬하고 통계를 `평가 완료/확인 실패`로 표시한다.

- [ ] **Step 4: GREEN 및 타입 확인**

Run:

```bash
npm test -- --run
npm run build
```

Expected: 프런트 테스트와 프로덕션 빌드 PASS.

- [ ] **Step 5: 변경 범위 커밋**

```bash
git add frontend/src/api/contracts.ts frontend/src/realtime/runEventReducer.ts frontend/src/features/run-progress/RunProgressPage.tsx frontend/src/features/run-progress/RunProgressPage.test.tsx frontend/src/features/reports/ReportView.tsx frontend/src/features/reports/ReportView.test.tsx
git commit -m "feat: rank all analyzed places by score"
```

### Task 4: 잔존 계약 제거와 전체·실사용 검증

**Files:**
- Delete: `reports/*.json` if present
- Modify: backend/frontend tests only when stale fixtures still use removed contract

**Interfaces:**
- Consumes: Tasks 1–3의 신규 계약
- Produces: 신규 스키마 리포트와 검증 증거

- [ ] **Step 1: 잔존 용어 검사와 stale fixture 수정**

Run:

```bash
rg -n 'matched|not_matched|mismatchReason|mismatch_reason|기준 충족|기준 미충족' src tests frontend/src --glob '!**/node_modules/**'
```

Expected: 브라우저 parser의 일반 지역변수와 테스트 fake를 제외한 도메인 계약 검색 결과 없음.

- [ ] **Step 2: 전체 자동 검증**

Run:

```bash
uv run python -m unittest discover -s tests -p 'test_*.py'
cd frontend && npm test -- --run && npm run build
```

Expected: Python, Vitest, TypeScript/Vite 모두 PASS.

- [ ] **Step 3: 기존 JSON 정리 후 실사용 실행**

`reports/.gitkeep`을 제외한 기존 JSON을 삭제한다. FastAPI 20003, 프런트 10003을 실행하고
웹에서 `신사역 / 일식 / 5개`를 실행한다.

Expected: 정상 분석 장소 전체가 `finalScore`를 가지며 점수순으로 표시되고, 이진 충족 문구가 없음.

- [ ] **Step 4: 최종 상태와 diff 검증**

```bash
git diff --check
git status --short
```

Expected: 사용자 소유 `.iml`, `output/`을 제외하고 계획된 변경만 존재함.

- [ ] **Step 5: 검증 보완 변경 처리**

Task 4에서 stale fixture를 추가 수정했다면 해당 테스트 파일 경로만 명시해 stage하고
`test: verify score-only ranking flow`로 커밋한다. 추적 파일 변경이 없으면 커밋을 생략한다.
