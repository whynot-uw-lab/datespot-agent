# BrowserService 네이버지도 연동 설계

**작성일:** 2026-07-13

**대상:** README 로드맵 2-3 `BrowserService 연동`

**기준 문서:** `docs/superpowers/plans/2026-07-13-agent-core-models.md`

## 1. 목표

Playwright로 네이버지도 우측 패널을 직접 조작하는 브라우저 계층을 구현한다.

- 실행별 브라우저 세션 시작과 종료
- 기준 위치 검색, 역 선택, 줌 15 고정
- 키워드 검색 결과를 `CandidatePlace` 목록으로 변환
- 특정 장소의 기본 정보, 내부 사진 최대 5장, 최신 리뷰 최대 50개 추출
- 브라우저 객체를 LangGraph 직렬화 상태와 분리
- 고정 UI 경로 실패 시 1회 재시도 후 타입이 있는 예외 발생

## 2. 범위 제외

- 후보 장소 수 제한과 후보 선택
- 사진 유효성 판정
- 리뷰 분석과 점수 계산
- 거리 계산, 거리 필드, 거리 기반 필터
- CAPTCHA, 로그인, 차단 우회
- LLM 기반 내비게이션 복구
- 네이버 내부 API 직접 호출

후보 수 제한과 사진 유효성 판정은 이후 Graph 계층에서 처리한다. 2-3은
결정적인 UI 조작과 원시 데이터 추출만 담당한다.

## 3. 확정 결정

### 3.1 검색 범위

실제 네이버지도 화면에서 신사역을 기준으로 비교한 결과를 사용한다.

| 줌 | 화면 범위 근사치 | 판단 |
|---:|---:|---|
| 14 | 약 2.5 km × 2.6 km | 검색 범위가 넓음 |
| 15 | 약 1.26 km × 1.28 km | 역 중심 약 650~700 m 범위로 적합 |
| 16 | 약 0.63 km × 0.64 km | 검색 범위가 좁음 |

`search_candidates()`는 위치 검색과 역 선택 후 줌을 15로 고정한다. 검색 범위는
이 화면 영역으로만 제한하며 별도 거리 계산은 하지 않는다.

### 3.2 브라우저 수명주기

한 `run_id`마다 Browser, BrowserContext, Page를 하나씩 사용한다.
`BrowserService`가 실행 중 객체를 메모리 레지스트리에 보관하며 `GraphState`에는
`run_id`와 직렬화 가능한 모델만 저장한다.

### 3.3 상세 화면 진입 방식

상세 URL을 조합하거나 최상위 Page를 `pcmap.place.naver.com`으로 직접 이동하지
않는다. 네이버지도 검색 목록을 클릭해 우측 패널을 열고 `entryIframe` 안에서
사진과 리뷰 탭을 조작한다. iframe 자체 URL이 `pcmap.place.naver.com`인 것은
정상 동작이다.

## 4. 모듈 구조

```text
src/datespot_agent/browser/
├── __init__.py       # 공개 타입과 예외 재노출
├── service.py        # BrowserService, 실행별 세션 레지스트리
├── naver_map.py      # NaverMapPage, 네이버지도 UI 조작
├── parsers.py        # DOM 문자열을 모델 값으로 변환하는 순수 함수
└── errors.py         # BrowserService 예외 계층
```

역할 구분:

- `BrowserService`: 공개 API, 세션 소유권, 재시도, 정리 보장
- `NaverMapPage`: frame 탐색, 클릭, 입력, 대기, 목록 복원
- `parsers.py`: 장소 ID, 리뷰 수, 텍스트, URL 파싱과 정규화
- `errors.py`: 호출자가 분기 가능한 실패 유형 정의

## 5. 공개 인터페이스

```python
class BrowserService:
    async def start_session(self, run_id: str) -> None: ...

    async def search_candidates(
        self,
        run_id: str,
        config: RunConfig,
    ) -> list[CandidatePlace]: ...

    async def extract_place_detail(
        self,
        run_id: str,
        candidate: CandidatePlace,
    ) -> PlaceDetail: ...

    async def close_session(self, run_id: str) -> None: ...

    async def close_all(self) -> None: ...
```

- `start_session`: 브라우저, 컨텍스트, 페이지를 만들고
  `https://map.naver.com`을 연다.
- `search_candidates`: 위치와 키워드에 맞는 전체 후보 목록을 반환한다.
- `extract_place_detail`: 검색 목록에서 후보를 다시 찾아 우측 패널 상세 정보를
  추출한다.
- `close_session`: 해당 실행 자원을 닫고 레지스트리에서 제거한다.
- `close_all`: 남은 모든 실행 자원을 닫는다.

`close_session`과 `close_all`은 여러 번 호출해도 실패하지 않는 멱등 동작으로
정의한다.

## 6. 내부 세션 상태

`BrowserSession`은 외부에 노출하지 않는 내부 타입이다.

```python
@dataclass
class BrowserSession:
    browser: Browser
    context: BrowserContext
    page: Page
    candidate_targets: dict[str, CandidateTarget]
```

`CandidateTarget`에는 `place_id`, 표시 이름, 검색 결과를 다시 찾기 위한 안정적인
DOM 식별 정보만 저장한다. Playwright 객체와 DOM 핸들은 모델이나 GraphState에
넣지 않는다.

## 7. 후보 검색 흐름

`search_candidates(run_id, config)`는 다음 순서를 고정한다.

1. `run_id`에 해당하는 세션 확인
2. 네이버지도 최상위 Page가 열린 상태 확인
3. `search_location(config.location)` 실행
4. 검색 결과에서 `select_station(config.location)` 실행
5. 지도 줌을 15로 조정하고 실제 줌 값 확인
6. `search_keyword(config.search_keyword)` 실행
7. `searchIframe` 로드 대기
8. 목록 항목에서 장소 ID와 이름 추출
9. 광고, 중복, 장소 ID 없는 항목 제거
10. 장소 ID 기준으로 `candidate_targets` 갱신
11. 제한 없는 `list[CandidatePlace]` 반환

후보 수는 `RunConfig.max_places`로 자르지 않는다. Graph가 반환 목록에서 처리할
후보 수를 결정한다.

## 8. 장소 상세 추출 흐름

`extract_place_detail(run_id, candidate)`는 다음 순서를 고정한다.

1. 세션과 캐시된 후보 target 확인
2. `searchIframe`에서 해당 검색 결과 클릭
3. `entryIframe` 로드 대기
4. 장소 ID 일치 확인
5. 홈 화면에서 카테고리, 주소, 전체 리뷰 수 추출
6. 사진 탭 클릭
7. 사진 분류에서 `내부` 클릭
8. `INTERIOR_*` 이미지 중 DOM 순서 기준 첫 5개 URL 추출
9. 리뷰 탭 클릭
10. `최신순` 클릭
11. `펼쳐서 더보기`를 반복해 최대 50개까지 로드
12. 리뷰 본문만 DOM 순서대로 추출
13. `PlaceDetail` 생성
14. `finally`에서 우측 패널을 닫고 검색 목록 상태 복원

반환 모델:

```python
PlaceDetail(
    place_id=candidate.place_id,
    name=candidate.name,
    category=category,
    address=address,
    photo_urls=photo_urls[:5],
    reviews=reviews[:50],
    review_count=review_count,
)
```

`review_count`는 화면에 표시된 전체 방문자 리뷰 수다. `reviews`는 이번 실행에서
실제로 로드한 최신 리뷰 본문이며 작성자, 날짜, 평점 메타데이터는 포함하지 않는다.

사진이 5장보다 적거나 리뷰가 50개보다 적으면 존재하는 데이터만 반환한다.
내부 사진 또는 리뷰가 없는 것은 정상적인 빈 목록으로 처리한다.

## 9. UI 탐색 원칙

- 최상위 Page URL은 네이버지도에 유지
- 검색 목록은 `searchIframe`, 장소 상세는 `entryIframe` 기준으로 탐색
- iframe은 이름과 URL 패턴을 함께 확인하고 로드 완료 후 사용
- 텍스트, 접근성 역할, 의미 있는 속성을 우선 사용
- 난독화된 CSS 클래스 단독 의존 금지
- 사진은 `alt="INTERIOR_<index>"` 패턴으로 식별
- 리뷰 더보기는 버튼 표시 여부와 현재 리뷰 개수를 함께 검사
- 클릭 뒤 URL 파라미터만 신뢰하지 않고 목표 탭의 실제 DOM 표시 확인
- 모든 반복은 목표 개수와 최대 반복 횟수를 둬 무한 루프 방지

네이버지도 DOM 변경에 대응할 선택자 후보와 대기 조건은 `NaverMapPage`에만
집중시킨다.

## 10. 재시도와 오류 계약

예외 계층:

```text
BrowserServiceError
├── BrowserSessionError
├── BrowserNavigationError
└── BrowserExtractionError
```

- `BrowserSessionError`: 세션 누락, 중복 시작, 닫힌 세션 사용
- `BrowserNavigationError`: 위치·역·탭 탐색 실패, frame 누락, 목록 복원 실패
- `BrowserExtractionError`: 장소 ID, 필수 장소 정보, 리뷰 수 등 파싱 실패

각 공개 검색·추출 작업은 동일한 결정적 경로를 최대 2회 실행한다. 첫 실패 뒤
현재 frame과 패널 상태를 복원하고 한 번만 재시도한다. 두 번째 실패 시 원래
예외를 위 계층의 타입으로 감싸 `run_id`, 단계, `place_id`가 있으면 함께 기록한다.
`BrowserSessionError`는 재시도해도 상태가 바뀌지 않으므로 즉시 반환한다.

다음은 빈 결과로 대체하지 않고 타입이 있는 예외로 보고할 대상이다.

- 존재하지 않는 `run_id`
- 기준 위치 또는 역 선택 실패
- 줌 15 조정 또는 확인 실패
- `searchIframe` 또는 `entryIframe` 확보 실패
- 후보 장소 ID 누락 또는 상세 장소 ID 불일치
- 상세 홈 화면 진입 실패
- 작업 후 검색 목록 복원 실패

`NavigationRecoveryAgent` 호출은 2-6 실패 처리 범위로 미룬다.

## 11. 자원 정리

- 세션 종료 순서: Page → BrowserContext → Browser
- 중간 단계가 실패해도 나머지 자원 종료 계속 시도
- 종료 후 레지스트리에서 세션 제거
- `extract_place_detail` 성공 여부와 무관하게 목록 복원 시도
- 애플리케이션 종료 시 `close_all` 호출

목록 복원 실패는 다음 후보 처리를 신뢰할 수 없으므로 정상 결과와 함께 숨기지
않고 `BrowserNavigationError`로 보고한다.

## 12. 테스트 전략

### 12.1 단위 테스트

- 장소 URL과 DOM 속성에서 장소 ID 추출
- 쉼표가 포함된 전체 리뷰 수 파싱
- 공백과 중복이 포함된 리뷰 본문 정규화
- 내부 사진 URL 최대 5개 제한과 순서 보존
- 최신 리뷰 최대 50개 제한과 순서 보존
- 광고, ID 없는 후보, 중복 장소 제거
- 빈 사진과 빈 리뷰 정상 처리
- 예외 타입과 오류 컨텍스트 확인
- 첫 실패 후 정확히 1회만 재시도
- 세션 시작, 조회, 멱등 종료, 전체 종료

### 12.2 로컬 브라우저 통합 테스트

고정 HTML fixture에 `searchIframe`과 `entryIframe`을 구성해 다음을 검증한다.

- 위치 검색 → 역 선택 → 줌 15 → 키워드 검색 순서
- 검색 결과 클릭 후 상세 frame 전환
- 사진 탭 → 내부 사진 5장 추출
- 리뷰 탭 → 최신순 → 더보기 반복 → 리뷰 50개 추출
- 상세 종료 후 검색 목록 복원
- 중간 실패 시 재시도와 자원 정리

### 12.3 실사이트 스모크 테스트

기본 시나리오:

1. 신사역 검색과 역 결과 선택
2. 줌 15 확인
3. 일식 검색
4. 후보 하나 선택
5. 내부 사진 최대 5장 추출
6. 최신 리뷰 최대 50개와 전체 리뷰 수 추출
7. 검색 목록으로 복귀
8. 세션 종료

확인 항목:

- 최상위 Page가 `map.naver.com`에 유지됨
- 상세 조작이 `entryIframe`을 통해 이뤄짐
- 지도 상태에서 줌 15가 확인됨
- `PlaceDetail` 모델 검증 통과
- 브라우저와 컨텍스트가 종료 후 남지 않음

실사이트 테스트는 네이버 UI와 네트워크에 의존하므로 기본 단위 테스트와 분리해
명시적으로 실행한다.

## 13. 완료 조건

- 공개 `BrowserService` API 구현 및 타입 검사 통과
- 후보 목록이 장소 ID 기준으로 정제되어 반환됨
- 우측 패널 직접 조작으로 `PlaceDetail` 생성됨
- 내부 사진 최대 5장, 최신 리뷰 최대 50개, 전체 리뷰 수가 구분됨
- 모든 공개 작업이 최대 2회 시도 후 타입이 있는 예외를 반환함
- 세션 종료가 멱등이며 실패 경로에서도 자원이 정리됨
- 단위 테스트와 로컬 브라우저 통합 테스트 통과
- 신사역·일식 실사이트 스모크 시나리오 통과
