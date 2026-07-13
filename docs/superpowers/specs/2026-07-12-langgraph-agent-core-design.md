# LangGraph + Agent Core Design

## 개요

datespot-agent의 전체 실행은 LangGraph workflow가 관리한다.

큰 흐름은 사용자의 탐색 설정을 검증한 뒤, 네이버지도에서 후보 장소를 찾고, 장소를 하나씩 순차 분석하며, 결과를 리포트에 누적하는 구조다.

Agent는 전체 흐름을 자율적으로 결정하지 않는다. 사진 분석, 리뷰 분석, 브라우저 복구처럼 판단이 필요한 일부 지점에서만 단발성으로 호출한다. 실행 상태와 누적 컨텍스트는 Agent memory가 아니라 LangGraph state가 관리한다.

핵심 방향:

- LangGraph가 실행 순서와 루프를 통제함
- Playwright fixed router가 기본 브라우저 이동을 담당함
- Navigation recovery agent는 실패 시에만 제한적으로 호출함
- Photo/Review analyzer agent는 장소별 단발 호출함
- 필터링, 점수 계산, 다음 장소 선택은 코드로 고정함
- 장소 하나가 실패해도 전체 실행은 계속함

## LangGraph node 흐름

아래 다이어그램은 LangGraph 구현 관점의 node 흐름이다.

- 사각형: `add_node` 대상
- 마름모: `add_conditional_edges`에서 사용하는 routing 함수
- Agent는 node 안에서 단발 호출됨
- `route_after_loop`가 장소 순차 처리 루프를 통제함

```mermaid
flowchart TD
    START([START])
    END([END])

    validate["validate_run_config<br/>사용자 설정 검증<br/>max_places / filters / weights"]
    init["init_run_state<br/>run_id 생성<br/>report / errors 초기화"]
    openBrowser["open_browser<br/>Playwright browser/context/page 준비"]

    search["search_candidates<br/>후보 장소 검색<br/>fixed navigator 우선"]
    normalize["normalize_candidates<br/>광고 제외 / 중복 제거<br/>placeId 보강"]
    routeSearch{"route_after_search<br/>후보 있음?"}

    select["select_next_place<br/>current_place 지정<br/>index 증가"]
    extract["extract_place_detail<br/>home/photo/review 추출<br/>category / address / photos / reviews"]
    routeExtract{"route_after_extract<br/>성공 / 복구 / 실패?"}

    buildRecovery["build_recovery_context<br/>URL / frame URLs / error 수집"]
    recoverAgent["navigation_recovery_agent<br/>허용 action 중 하나 선택"]
    applyRecovery["apply_navigation_action<br/>escape / close / reload / retry route"]
    routeRecovery{"route_after_recovery<br/>복구됨?"}

    recordFailure["record_place_failure<br/>실패 사유 저장"]
    appendFailed["append_failed_result<br/>실패 장소 리포트 반영"]

    preFilter["pre_filter_place<br/>카테고리 / 리뷰 수 / 거리 검사"]
    routeFilter{"route_after_filter<br/>통과?"}
    recordExcluded["record_place_exclusion<br/>제외 사유 저장"]
    appendExcluded["append_excluded_result<br/>제외 장소 리포트 반영"]

    analyzePhotos["analyze_photos<br/>PhotoAnalyzerAgent 단발 호출"]
    analyzeReviews["analyze_reviews<br/>ReviewAnalyzerAgent 단발 호출"]
    calculate["calculate_final_score<br/>사진/리뷰 가중합 계산"]
    appendAnalyzed["append_analyzed_result<br/>분석 완료 장소 리포트 반영"]

    throttle["throttle_between_places<br/>delay / rate limit 적용"]
    routeLoop{"route_after_loop<br/>다음 장소 있음?"}

    finalize["finalize_report<br/>점수순 정렬<br/>최종 payload 확정"]
    closeBrowser["close_browser<br/>browser/context 정리"]

    START --> validate --> init --> openBrowser --> search --> normalize --> routeSearch

    routeSearch -- "empty" --> finalize
    routeSearch -- "ready" --> routeLoop

    routeLoop -- "next" --> select --> extract --> routeExtract
    routeLoop -- "done" --> finalize

    routeExtract -- "success" --> preFilter --> routeFilter
    routeExtract -- "recover" --> buildRecovery --> recoverAgent --> applyRecovery --> routeRecovery
    routeExtract -- "fail" --> recordFailure --> appendFailed --> throttle --> routeLoop

    routeRecovery -- "retry_extract" --> extract
    routeRecovery -- "give_up" --> recordFailure

    routeFilter -- "exclude" --> recordExcluded --> appendExcluded --> throttle
    routeFilter -- "analyze" --> analyzePhotos --> analyzeReviews --> calculate --> appendAnalyzed --> throttle

    throttle --> routeLoop
    finalize --> closeBrowser --> END
```

## 데이터 규격

2-2 단계에서는 실행 설정, 장소 후보, 상세 추출값, 분석 결과, 리포트, LangGraph state를 Pydantic `BaseModel`로 구현한다.

공통 규칙:

- Python 내부 필드는 `snake_case`를 사용한다.
- JSON/API 입출력은 `camelCase` alias를 지원한다.
- 입력은 `snake_case`와 `camelCase`를 모두 허용한다.
- 출력 JSON은 `camelCase`로 직렬화할 수 있어야 한다.
- 점수는 `0`부터 `10`까지의 정수다.
- 리스트 필드는 `None` 대신 빈 배열을 기본값으로 쓴다.
- 선택 필드는 값이 없으면 `None`으로 둔다.
- 시간 필드는 UTC `datetime`으로 저장하고 JSON에서는 ISO 문자열로 직렬화한다.
- `GraphState`에는 Playwright live object를 절대 넣지 않는다.

### 공통 enum

`RunStatus`:

- `pending`: 실행 준비 상태
- `running`: 실행 중
- `completed`: 정상 완료
- `failed`: 실행 전체 실패

`PlaceResultStatus`:

- `analyzed`: 분석 완료
- `excluded`: 사전 필터로 제외
- `failed`: 상세 추출 또는 분석 실패

`ConfidenceLevel`:

- `low`
- `medium`
- `high`

### RunConfig

사용자가 한 번의 탐색 실행에서 지정하는 설정이다. 2단계 MVP에서는 분석 수를 최대 10개로 제한한다.

필드:

- `location: str`: 기준 지역 또는 역
- `search_keyword: str`: 검색 키워드 또는 카테고리
- `max_places: int = 10`: 최소 `1`, 최대 `10`
- `filters: Filters = Filters()`
- `weights: Weights = Weights()`
- `scoring: ScoringCriteria = ScoringCriteria()`

`Filters`:

- `categories: list[str] = []`
- `min_review_count: int = 0`
- `max_distance_m: int | None = None`

`Weights`:

- `photo_percent: int = 50`
- `review_percent: int = 50`
- 합계는 반드시 `100`
- 각 값은 `0`부터 `100`까지 허용

`ScoringCriteria`:

- `photo: str`: 사진 평가 기준
- `review: str`: 리뷰 평가 기준

예시:

```json
{
  "location": "신사역",
  "searchKeyword": "음식점",
  "maxPlaces": 10,
  "filters": {
    "categories": ["일식", "양식"],
    "minReviewCount": 50,
    "maxDistanceM": 700
  },
  "weights": {
    "photoPercent": 50,
    "reviewPercent": 50
  },
  "scoring": {
    "photo": "어둡고 차분한 분위기, 넓은 좌석 간격, 대화하기 좋은 구조",
    "review": "깔끔함, 조용함, 대화하기 좋음 등 긍정 표현"
  }
}
```

### CandidatePlace

검색 목록에서 얻은 후보 장소다. 상세 분석 전 단계에서 사용한다. 목록 단계에서는 `place_id`가 없을 수 있다.

필드:

- `place_id: str | None = None`
- `name: str`
- `list_rank: int | None = None`
- `category_hint: str | None = None`
- `is_ad: bool = False`
- `raw_text: str | None = None`
- `detail_url_hint: str | None = None`

예시:

```json
{
  "placeId": null,
  "name": "카이센동 우니도 본점",
  "listRank": 1,
  "categoryHint": "일식당",
  "isAd": false,
  "rawText": "카이센동 우니도 본점 일식당",
  "detailUrlHint": "https://pcmap.place.naver.com/restaurant/1720070048/home"
}
```

### PlaceDetail

상세 페이지에서 추출한 분석 재료다. 사진/리뷰 분석 node의 입력이 된다. 상세 단계부터 `place_id`는 필수다.

필드:

- `place_id: str`
- `name: str`
- `category: str | None = None`
- `address: str | None = None`
- `distance_hint: str | None = None`
- `home_url: str | None = None`
- `photo_url: str | None = None`
- `review_url: str | None = None`
- `photo_urls: list[str] = []`
- `reviews: list[str] = []`
- `review_count: int = 0`
- `extracted_at: datetime`
- `extraction_errors: list[str] = []`

예시:

```json
{
  "placeId": "1720070048",
  "name": "카이센동 우니도 본점",
  "category": "일식당",
  "address": "서울 강남구 압구정로2길 15",
  "distanceHint": "신사역 700m 이내",
  "homeUrl": "https://pcmap.place.naver.com/restaurant/1720070048/home",
  "photoUrl": "https://pcmap.place.naver.com/restaurant/1720070048/photo",
  "reviewUrl": "https://pcmap.place.naver.com/restaurant/1720070048/review/visitor",
  "photoUrls": ["https://example.com/photo-1.jpg"],
  "reviews": ["조용하고 대화하기 좋았어요."],
  "reviewCount": 128,
  "extractedAt": "2026-07-12T00:00:00Z",
  "extractionErrors": []
}
```

### PhotoAnalysis

사진 기반 소개팅 적합도 분석 결과다.

필드:

- `photo_score: int`: `0`부터 `10`
- `summary: str`
- `mood_signals: list[str] = []`
- `space_signals: list[str] = []`
- `lighting_signals: list[str] = []`
- `negative_signals: list[str] = []`
- `representative_photo_url: str | None = None`
- `confidence: ConfidenceLevel = "medium"`

예시:

```json
{
  "photoScore": 7,
  "summary": "차분한 조명과 정돈된 좌석 구성이 보임",
  "moodSignals": ["차분한 조명"],
  "spaceSignals": ["테이블 간격이 비교적 넓음"],
  "lightingSignals": ["어두운 톤"],
  "negativeSignals": ["일부 사진에서 혼잡 가능성"],
  "representativePhotoUrl": "https://example.com/photo-1.jpg",
  "confidence": "medium"
}
```

### ReviewAnalysis

리뷰 기반 소개팅 적합도 분석 결과다.

필드:

- `review_score: int`: `0`부터 `10`
- `summary: str`
- `positive_signals: list[str] = []`
- `negative_signals: list[str] = []`
- `date_fit_signals: list[str] = []`
- `concerns: list[str] = []`
- `used_review_count: int = 0`
- `confidence: ConfidenceLevel = "medium"`

예시:

```json
{
  "reviewScore": 8,
  "summary": "조용함, 친절함, 데이트 방문 언급이 반복됨",
  "positiveSignals": ["친절함", "깔끔함"],
  "negativeSignals": ["웨이팅 가능성"],
  "dateFitSignals": ["데이트 방문"],
  "concerns": ["피크 시간 혼잡도 확인 필요"],
  "usedReviewCount": 50,
  "confidence": "medium"
}
```

### FilterDecision

사전 필터 node의 판단 결과다.

필드:

- `passed: bool`
- `exclusion_reason: str | None = None`
- `applied_filters: list[str] = []`
- `detail_summary: str | None = None`

예시:

```json
{
  "passed": false,
  "exclusionReason": "리뷰 수가 최소 기준 50개보다 적음",
  "appliedFilters": ["minReviewCount"],
  "detailSummary": "리뷰 수 18개"
}
```

### RecoveryDecision

navigation 복구 agent의 판단 결과다.

필드:

- `diagnosis: str`
- `action: str`
- `reason: str`
- `can_retry: bool = False`

예시:

```json
{
  "diagnosis": "목록 iframe 로딩 실패",
  "action": "direct_route_retry",
  "reason": "pcmap 직접 URL 접근이 가능함",
  "canRetry": true
}
```

### PlaceResult

리포트에 누적되는 장소 단위 결과다. 분석 완료, 제외, 실패를 하나의 모델에서 상태값으로 구분한다.

필드:

- `status: PlaceResultStatus`
- `place_id: str | None = None`
- `name: str`
- `category: str | None = None`
- `address: str | None = None`
- `photo_score: int | None = None`
- `review_score: int | None = None`
- `final_score: int | None = None`
- `photo_summary: str | None = None`
- `review_summary: str | None = None`
- `key_reasons: list[str] = []`
- `concerns: list[str] = []`
- `representative_photo_url: str | None = None`
- `sample_reviews: list[str] = []`
- `exclusion_reason: str | None = None`
- `failure_reason: str | None = None`
- `errors: list[str] = []`

상태별 검증:

- `analyzed`: `final_score` 필수
- `excluded`: `exclusion_reason` 필수
- `failed`: `failure_reason` 필수

예시:

```json
{
  "status": "analyzed",
  "placeId": "1720070048",
  "name": "카이센동 우니도 본점",
  "category": "일식당",
  "address": "서울 강남구 압구정로2길 15",
  "photoScore": 7,
  "reviewScore": 8,
  "finalScore": 8,
  "photoSummary": "차분한 조명과 정돈된 좌석 구성이 보임",
  "reviewSummary": "조용함과 친절함 언급이 반복됨",
  "keyReasons": ["조용함", "깔끔함", "데이트 방문 언급"],
  "concerns": ["피크 시간 웨이팅 가능성"],
  "representativePhotoUrl": "https://example.com/photo-1.jpg",
  "sampleReviews": ["조용하고 대화하기 좋았어요."],
  "exclusionReason": null,
  "failureReason": null,
  "errors": []
}
```

### RunReport

최종 JSON 리포트 payload다. `finalize_report` node가 만든다.

필드:

- `run_id: str`
- `status: RunStatus`
- `config: RunConfig`
- `results: list[PlaceResult] = []`
- `analyzed_count: int = 0`
- `excluded_count: int = 0`
- `failed_count: int = 0`
- `errors: list[str] = []`
- `created_at: datetime`

예시:

```json
{
  "runId": "run_20260712_000001",
  "status": "completed",
  "config": {
    "location": "신사역",
    "searchKeyword": "음식점",
    "maxPlaces": 10,
    "filters": {
      "categories": ["일식", "양식"],
      "minReviewCount": 50,
      "maxDistanceM": 700
    },
    "weights": {
      "photoPercent": 50,
      "reviewPercent": 50
    },
    "scoring": {
      "photo": "어둡고 차분한 분위기, 넓은 좌석 간격, 대화하기 좋은 구조",
      "review": "깔끔함, 조용함, 대화하기 좋음 등 긍정 표현"
    }
  },
  "results": [],
  "analyzedCount": 0,
  "excludedCount": 0,
  "failedCount": 0,
  "errors": [],
  "createdAt": "2026-07-12T00:00:00Z"
}
```

### GraphState

LangGraph node 사이를 이동하는 최소 실행 state다. 2-2에서는 Pydantic `BaseModel`로 구현한다.

필드:

- `run_id: str`
- `config: RunConfig`
- `status: RunStatus = "pending"`
- `candidates: list[CandidatePlace] = []`
- `current_place_index: int = 0`
- `current_place: CandidatePlace | None = None`
- `current_place_detail: PlaceDetail | None = None`
- `filter_decision: FilterDecision | None = None`
- `photo_analysis: PhotoAnalysis | None = None`
- `review_analysis: ReviewAnalysis | None = None`
- `recovery_decision: RecoveryDecision | None = None`
- `place_results: list[PlaceResult] = []`
- `final_report: RunReport | None = None`
- `last_error: str | None = None`

제외 정보:

- Playwright `Browser`, `BrowserContext`, `Page`, `Locator`
- 전체 이벤트 히스토리
- node별 상태 히스토리
- screenshot path
- DOM snapshot
- 전체 retry history

예시:

```json
{
  "runId": "run_20260712_000001",
  "config": {
    "location": "신사역",
    "searchKeyword": "음식점",
    "maxPlaces": 10,
    "filters": {
      "categories": ["일식", "양식"],
      "minReviewCount": 50,
      "maxDistanceM": 700
    },
    "weights": {
      "photoPercent": 50,
      "reviewPercent": 50
    },
    "scoring": {
      "photo": "어둡고 차분한 분위기, 넓은 좌석 간격, 대화하기 좋은 구조",
      "review": "깔끔함, 조용함, 대화하기 좋음 등 긍정 표현"
    }
  },
  "status": "running",
  "candidates": [
    {
      "placeId": null,
      "name": "카이센동 우니도 본점",
      "listRank": 1,
      "categoryHint": "일식당",
      "isAd": false,
      "rawText": "카이센동 우니도 본점 일식당",
      "detailUrlHint": "https://pcmap.place.naver.com/restaurant/1720070048/home"
    }
  ],
  "currentPlaceIndex": 0,
  "currentPlace": null,
  "currentPlaceDetail": null,
  "filterDecision": null,
  "photoAnalysis": null,
  "reviewAnalysis": null,
  "recoveryDecision": null,
  "placeResults": [],
  "finalReport": null,
  "lastError": null
}
```

라우팅 결정은 별도 모델로 만들지 않는다. `route_after_*` 함수의 문자열 반환값으로 처리한다.

## 인터페이스 초안

구체 함수 시그니처는 아직 정하지 않는다. node가 의존할 외부 능력의 경계만 정한다.

### BrowserRuntime

Playwright live object를 관리한다. LangGraph state에는 `run_id`만 저장하고, 실제 browser/context/page는 runtime registry 또는 dependency injection으로 접근한다.

책임:

- browser/context/page 생성
- run_id 기준 세션 조회
- 세션 종료

### FixedNavigator

네이버지도 이동과 데이터 추출의 기본 경로다. LLM을 사용하지 않는다.

책임:

- 후보 장소 검색
- 후보 정규화에 필요한 원천 정보 제공
- 장소 상세 route 진입
- 사진 URL 추출
- 리뷰 텍스트 추출

### NavigationRecoveryAgent

fixed navigator 실패 시에만 호출되는 제한적 agent다.

입력 정보:

- 현재 목표
- 현재 URL
- frame URL 목록
- 최근 오류
- 허용된 action 목록

출력 정보:

- 복구 진단
- 선택한 action
- 이유

### NavigationActionExecutor

agent가 선택한 복구 action을 같은 Playwright page에 적용한다.

책임:

- escape
- popup close
- reload
- direct route retry
- 실패 확정

### PhotoAnalyzer

사진 분석 agent 호출을 감싼 인터페이스다.

입력 정보:

- 장소 기본 정보
- 사진 URL 목록
- 사진 평가 기준

출력 정보:

- `PhotoAnalysis`

### ReviewAnalyzer

리뷰 분석 agent 호출을 감싼 인터페이스다.

입력 정보:

- 장소 기본 정보
- 리뷰 목록
- 리뷰 평가 기준

출력 정보:

- `ReviewAnalysis`

### ScoreCalculator

LLM을 사용하지 않는 순수 계산 로직이다.

책임:

- 사진 점수와 리뷰 점수 가중합 계산
- 점수 없음/부분 분석 상황 처리
