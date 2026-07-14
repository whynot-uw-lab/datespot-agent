# 분석 노드 설계

**작성일:** 2026-07-13

**대상:** README 로드맵 2-4 `분석 노드 구현`

**기준 문서:** `docs/superpowers/specs/2026-07-12-langgraph-agent-core-design.md`

## 1. 목표

네이버지도에서 추출한 장소별 사진과 리뷰를 사용자 기준으로 분석하고, 기준 충족
여부와 최종 가중 점수를 코어 모델로 반환하는 분석 계층을 구현한다.

- 내부 사진 최대 5장을 OpenAI 비전 모델로 분석
- 최신 리뷰 최대 50개를 OpenAI 텍스트 모델로 분석
- 사진·리뷰별 사용자 기준 충족 여부와 정수 점수, 근거 생성
- 모든 활성 분석이 기준을 충족하면 최종 가중 점수 계산
- 정상 분석 후 기준을 충족하지 못한 장소와 처리 오류 장소를 구분

## 2. 범위 제외

- 사전 필터링과 필터 설정
- LangGraph node와 conditional edge 연결
- 브라우저 세션과 네이버지도 데이터 추출
- 장소별 실패 기록과 다음 장소 진행 제어
- OpenAI 요청 재시도
- 최종 RunReport 정렬과 파일 출력

LangGraph 연결과 실패 결과 누적·실행 지속은 2-5, JSON 리포트 출력은 2-7에서
구현한다.

## 3. 확정 결정

### 3.1 사전 필터 제거

카테고리와 전체 리뷰 수로 장소를 미리 제외하지 않는다. 검색으로 수집된 모든 후보를
`max_places` 범위 안에서 사진과 리뷰로 분석한다.

다음 계약을 코어 모델과 문서에서 제거한다.

- `Filters`
- `FilterDecision`
- `RunConfig.filters`
- `GraphState.filter_decision`
- `PlaceResultStatus.EXCLUDED`
- `PlaceResult.exclusion_reason`
- 사전 필터로 제외된 장소와 제외 사유 리포트

`PlaceDetail.category`와 `PlaceDetail.review_count`는 장소 정보와 분석 컨텍스트이므로
유지한다. 단, 분석 대상 제외에는 사용하지 않는다.

### 3.2 분석 기준 충족 여부

사진과 리뷰 분석 결과는 점수뿐 아니라 사용자 기준을 전체적으로 충족했는지를
`matched`로 반환한다.

- `matched=true`: 제공된 근거가 사용자 기준을 전체적으로 충족함
- `matched=false`: 정상 분석됐지만 근거가 부족하거나 사용자 기준에 맞지 않음

가중치가 0보다 큰 분석 항목을 활성 항목이라고 한다. 활성 항목 하나라도
`matched=false`이면 장소 전체 상태는 `not_matched`다. 모든 활성 항목이
`matched=true`일 때만 `analyzed`가 된다.

### 3.3 점수 규격

- 사진 점수: 0부터 10까지 정수
- 리뷰 점수: 0부터 10까지 정수
- 최종 점수: 0부터 10까지, 소수점 첫째 자리
- 계산식: `(사진 점수 × 사진 비율 + 리뷰 점수 × 리뷰 비율) / 100`
- 계산 결과는 소수점 첫째 자리로 반올림

예를 들어 사진 7점, 리뷰 8점, 가중치 50:50이면 최종 점수는 7.5다.

### 3.4 자료 누락과 0% 가중치

- 활성 사진 분석에 사진 URL이 없으면 `AnalysisInputError`
- 활성 리뷰 분석에 리뷰가 없으면 `AnalysisInputError`
- 가중치가 0%인 항목은 호출과 필수 자료 검사를 생략할 수 있음
- `Weights`의 합은 100이므로 사진과 리뷰가 동시에 0%일 수 없음

자료 누락은 기준 미충족이 아니라 처리 불가능 상태이므로 `failed` 결과 대상이다.

## 4. 모듈 구조

```text
src/datespot_agent/analysis/
├── __init__.py   # 공개 Agent, Service, 예외 재노출
├── errors.py     # 분석 예외 계층
├── photo.py      # PhotoAnalysisAgent
├── review.py     # ReviewAnalysisAgent
└── scoring.py    # PlaceScoringService
```

역할 구분:

- `PhotoAnalysisAgent`: 장소 정보, 사진 URL, 사진 기준을 구조화된 사진 분석으로 변환
- `ReviewAnalysisAgent`: 장소 정보, 리뷰 목록, 리뷰 기준을 구조화된 리뷰 분석으로 변환
- `PlaceScoringService`: 분석 결과 상태 판정, 최종 점수 계산, `PlaceResult` 생성
- `errors.py`: 입력, OpenAI 요청, 구조화 응답 오류를 호출자가 구분하도록 정의

분석 계층은 LangGraph, Playwright, 파일 입출력에 의존하지 않는다.

## 5. 데이터 모델 변경

### 5.1 RunConfig

필드:

- `location: str`
- `search_keyword: str`
- `max_places: int = 10`
- `weights: Weights = Weights()`
- `scoring: ScoringCriteria = ScoringCriteria()`

`filters` 입력은 알 수 없는 필드로 거부한다.

### 5.2 PhotoAnalysis

필드:

- `photo_score: int`: 0부터 10
- `matched: bool`: 사진 근거가 사진 기준을 전체적으로 충족하는지 여부
- `reason: str`: 점수와 충족 여부를 설명하는 근거

### 5.3 ReviewAnalysis

필드:

- `review_score: int`: 0부터 10
- `matched: bool`: 리뷰 근거가 리뷰 기준을 전체적으로 충족하는지 여부
- `reason: str`: 점수와 충족 여부를 설명하는 근거

### 5.4 PlaceResult

`PlaceResultStatus`:

- `analyzed`: 모든 활성 분석이 기준을 충족하고 최종 점수 계산 완료
- `not_matched`: 정상 분석됐지만 활성 분석 중 하나 이상이 기준 미충족
- `failed`: 입력, API, 응답 파싱, 추출 등 처리 오류

변경 필드:

- `final_score: float | None`: 0부터 10, 소수점 첫째 자리
- `mismatch_reason: str | None`: `not_matched`일 때 필수
- `failure_reason: str | None`: `failed`일 때 필수

제거 필드:

- `exclusion_reason`

상태별 검증:

- `analyzed`: `final_score` 필수, `mismatch_reason` 없음
- `not_matched`: `mismatch_reason` 필수, `final_score` 없음
- `failed`: `failure_reason` 필수

`not_matched` 결과에도 완료된 사진·리뷰 점수와 근거를 보존한다.

### 5.5 GraphState

`filter_decision`을 제거한다. `photo_analysis`, `review_analysis`, `place_results`는
유지한다.

## 6. 공개 인터페이스

```python
class PhotoAnalysisAgent:
    async def analyze(
        self,
        detail: PlaceDetail,
        criteria: str,
    ) -> PhotoAnalysis: ...


class ReviewAnalysisAgent:
    async def analyze(
        self,
        detail: PlaceDetail,
        criteria: str,
    ) -> ReviewAnalysis: ...


class PlaceScoringService:
    def calculate(
        self,
        detail: PlaceDetail,
        weights: Weights,
        photo_analysis: PhotoAnalysis | None,
        review_analysis: ReviewAnalysis | None,
    ) -> PlaceResult: ...
```

각 Agent는 생성 시 `AsyncOpenAI`, 모델 이름, 최대 출력 토큰을 받는다. 기본 모델은
`Settings.model`에서 조합 계층이 전달한다. Agent가 전역 설정을 직접 읽지 않게 해
테스트와 실행 환경을 분리한다.

## 7. 사진 분석 흐름

1. `detail.photo_urls`가 비어 있지 않은지 확인
2. 장소명, 카테고리, 주소, 사진 기준으로 텍스트 프롬프트 구성
3. DOM 순서로 받은 내부 사진 URL 최대 5개를 `input_image` block으로 구성
4. `AsyncOpenAI.responses.parse()`를 `PhotoAnalysis` 형식으로 호출
5. `response.output_parsed`를 확인하고 반환

프롬프트는 조명, 좌석 배치, 공간감, 혼잡 신호, 대화 적합성을 사진에서 확인하고,
보이지 않는 사실을 단정하지 않도록 지시한다. `matched`는 사용자 사진 기준을
전체적으로 충족하는 경우에만 `true`로 판단한다.

## 8. 리뷰 분석 흐름

1. `detail.reviews`가 비어 있지 않은지 확인
2. 장소명, 카테고리, 주소, 리뷰 기준으로 텍스트 프롬프트 구성
3. 최신 리뷰 최대 50개를 순서와 함께 하나의 `input_text` block으로 구성
4. `AsyncOpenAI.responses.parse()`를 `ReviewAnalysis` 형식으로 호출
5. `response.output_parsed`를 확인하고 반환

프롬프트는 조용함, 대화 적합성, 친절함, 청결함, 대기·혼잡, 데이트 적합성을
리뷰 근거에서 확인한다. 직접 근거가 부족하면 이를 이유에 명시하고, 사용자 리뷰
기준을 전체적으로 충족하지 못하면 `matched=false`로 판단한다.

## 9. 상태 판정과 점수 계산

`PlaceScoringService.calculate()`는 다음 순서를 고정한다.

1. 사진 가중치가 0보다 큰데 `photo_analysis`가 없으면 `AnalysisInputError`
2. 리뷰 가중치가 0보다 큰데 `review_analysis`가 없으면 `AnalysisInputError`
3. 활성 분석 중 `matched=false`가 있으면 `PlaceResult(status="not_matched")` 반환
4. 모든 활성 분석이 충족되면 가중합 계산
5. 소수점 첫째 자리로 반올림
6. `PlaceResult(status="analyzed")` 반환

0% 가중치인 항목의 분석 결과가 전달되더라도 점수 계산과 충족 여부 판정에서
사용하지 않는다.

`mismatch_reason`은 충족하지 못한 활성 분석의 종류와 각 `reason`을 결정적인
순서인 사진, 리뷰 순으로 결합한다.

## 10. 오류 계약

```text
AnalysisError
├── AnalysisInputError
├── AnalysisRequestError
└── AnalysisResponseError
```

- `AnalysisInputError`: 활성 분석 자료 누락, 필수 분석 결과 누락
- `AnalysisRequestError`: OpenAI 요청 실패
- `AnalysisResponseError`: 구조화된 `output_parsed` 누락

OpenAI 원본 예외는 `AnalysisRequestError`의 원인으로 연결한다. 2-4에서는 자동
재시도하지 않는다. 실행 루프에서 예외를 `PlaceResult(status="failed")`로 변환하고
다음 장소로 진행한다.

## 11. 테스트 전략

기본 테스트는 실제 OpenAI API를 호출하지 않는다. 비동기 가짜 client를 주입해
요청 payload와 반환 계약을 검증한다.

### 모델 테스트

- `RunConfig`가 `filters` 입력을 거부함
- `PhotoAnalysis`와 `ReviewAnalysis`가 `matched`를 요구함
- `PlaceResult.final_score`가 소수점 첫째 자리 값을 허용함
- `not_matched`가 `mismatch_reason`을 요구하고 `final_score`를 거부함
- `excluded` 상태와 `exclusion_reason` 입력을 거부함
- `GraphState`에 `filter_decision`을 넣을 수 없음

### Agent 테스트

- 사진 최대 5장과 사용자 기준이 요청에 포함됨
- 리뷰 최대 50개와 사용자 기준이 요청에 포함됨
- 사진 또는 리뷰 자료가 비어 있으면 `AnalysisInputError`
- `output_parsed`가 없으면 `AnalysisResponseError`
- OpenAI 호출 실패를 `AnalysisRequestError`로 변환함

### 점수 테스트

- 사진 7, 리뷰 8, 가중치 50:50의 최종 점수가 7.5임
- 0% 항목은 결과가 없어도 정상 계산함
- 활성 항목 분석 결과가 없으면 `AnalysisInputError`
- 사진 또는 리뷰가 `matched=false`이면 `not_matched`임
- `not_matched` 결과에 완료된 점수, 근거, 불일치 이유가 보존됨

### 회귀 검증

- `uv run python -m unittest discover -s tests -v`
- `uv run python poc/1-1-env/smoke_test.py`

## 12. 문서 정리 범위

다음 문서에서 사전 필터, 제외 결과, 필터 설정을 제거하고 분석 기준 충족 여부와
`not_matched` 상태를 반영한다.

- `README.md`
- `idea.md`
- `poc/00-planning.md`
- `poc/1-2-naver-map-flow/README.md`
- `docs/superpowers/specs/2026-07-12-langgraph-agent-core-design.md`
- `docs/superpowers/specs/2026-07-13-browser-service-design.md`

완료된 2-2 구현 계획은 과거 실행 기록을 전면 재작성하지 않고, 문서 상단에 2-4에서
필터 계약과 최종 점수 규격이 변경됐다는 안내를 추가한다. 신규 2-4 구현 계획은 이
설계를 현재 계약으로 사용한다.
