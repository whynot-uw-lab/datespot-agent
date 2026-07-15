# 실시간 이벤트·CDP 스트림·리포트 검색 통합 설계

**작성일:** 2026-07-15

**대상:** README 3단계의 남은 백엔드 항목

- WebSocket/SSE 실행 로그·리포트 갱신
- CDP 브라우저 스트림 중계
- 저장된 리포트 목록·검색 API

**기준 문서:**

- `docs/superpowers/specs/2026-07-15-fastapi-run-api-design.md`
- `docs/superpowers/specs/2026-07-10-cdp-streaming-poc-design.md`
- `docs/superpowers/specs/2026-07-14-json-report-storage-design.md`

## 1. 목표

기존 메모리 기반 단일 FIFO 실행 API 위에 다음 세 기능을 한 번에 연결한다.

1. 실행 상태, 공개 가능한 판단 로그, 장소별 결과를 SSE로 실시간 전달
2. 현재 실행의 Chrome 화면을 WebSocket JPEG stream으로 전달
3. 서버 재시작 후에도 JSON 리포트를 목록·검색·상세 조회

세 기능은 하나의 FastAPI runtime에서 동작하지만 이벤트, 영상, 파일 조회의 소유권은
분리한다. 어느 한 기능의 consumer 실패가 graph 실행 결과를 바꾸면 안 된다.

## 2. 확정 결정

### 2.1 SSE 복구

- 실행별 최근 이벤트를 메모리에 보관한다.
- 브라우저 재접속 시 `Last-Event-ID` 이후 이벤트를 재전송한다.
- 실행별 replay buffer는 최대 1,000개다.
- 이벤트 로그는 파일로 저장하지 않는다.
- 서버 재시작 후 이벤트 복구는 범위에서 제외한다.

### 2.2 CDP 자원 정책

- 여러 viewer가 같은 실행을 볼 수 있다.
- 첫 viewer가 연결될 때만 CDP screencast를 시작한다.
- 마지막 viewer가 나가면 screencast와 CDP session을 닫는다.
- viewer가 없으면 frame을 만들거나 전송하지 않는다.

### 2.3 저장 리포트 검색

- 기존 UTC 날짜별 JSON 파일을 source of truth로 유지한다.
- DB나 별도 인덱스 파일을 추가하지 않는다.
- 최신순 cursor pagination을 제공한다.
- `status`, `location`, `searchKeyword`, `dateFrom`, `dateTo` 필터를 제공한다.
- 저장 리포트 상세 endpoint를 추가한다.

### 2.4 공개 로그 경계

SSE는 실행 단계와 결과를 설명하는 운영·도메인 이벤트만 노출한다. 모델의 숨겨진
추론 과정, raw prompt, API key, traceback, 로컬 내부 경로는 전송하지 않는다.

## 3. 접근안 비교

### 3.1 인프로세스 기능별 분리

`RunEventHub`, `CdpStreamManager`, `JsonReportCatalog`를 독립 컴포넌트로 두고 기존
runtime에서 조립한다.

- 장점: 현재 단일 프로세스 구조와 일치
- 장점: 이벤트, 영상, 파일 테스트를 독립 수행 가능
- 장점: 추후 Redis나 DB로 교체할 경계가 명확함
- 단점: runtime wiring과 lifecycle 항목이 늘어남
- **판정: 채택**

### 3.2 Coordinator 집중형

실시간 event, CDP viewer, 파일 검색을 모두 `RunCoordinator`가 소유한다.

- 장점: 초기 파일 수와 주입 지점이 적음
- 단점: coordinator가 브라우저·파일·HTTP 관심사까지 소유
- 단점: shutdown과 테스트가 서로 강하게 결합
- 판정: 제외

### 3.3 외부 인프라형

Redis pub/sub과 DB report index를 사용한다.

- 장점: 멀티프로세스와 재시작 복구에 유리
- 단점: 로컬 MVP에 불필요한 배포·운영 복잡도 추가
- 판정: 제외

## 4. 전체 아키텍처

```text
Frontend
  ├─ POST /runs ──────────────────────────────> FastAPI / RunCoordinator
  ├─ GET /runs/{id}/events (SSE) ─────────────> RunEventHub
  ├─ WS /runs/{id}/browser-stream ────────────> CdpStreamManager
  ├─ GET /reports ────────────────────────────> JsonReportCatalog
  └─ GET /reports/{id} ───────────────────────> JsonReportCatalog

RunCoordinator ─┐
GraphRunService ├─> RunEventPublisher ─> RunEventHub
BrowserService ─┘

BrowserService ── page attach/detach ──> CdpStreamManager
JsonReportCatalog ── read only ──> reports/YYYY/MM/DD/*.json
```

### 4.1 의존성 방향

- API route는 runtime 컴포넌트를 호출하고 transport 변환만 담당한다.
- event publisher는 hub의 좁은 publish interface만 사용한다.
- browser 계층은 FastAPI `WebSocket`을 알지 못한다.
- report catalog는 coordinator의 메모리 registry를 참조하지 않는다.
- catalog와 store는 같은 reports root를 공유하지만 서로 호출하지 않는다.

## 5. 컴포넌트 설계

### 5.1 `RunEventHub`

역할:

- 실행별 sequence 발급
- bounded replay 저장
- subscriber queue fan-out
- terminal run buffer LRU 관리
- shutdown 시 subscriber 종료

개념 interface:

```python
class RunEventHub:
    def open_run(self, run_id: str) -> None: ...
    def publish(self, run_id: str, event_type: RunEventType, data: object) -> RunEvent: ...
    def mark_terminal(self, run_id: str) -> None: ...
    def subscribe(self, run_id: str, last_event_id: int | None) -> RunEventSubscription: ...
    async def close(self) -> None: ...
```

모든 publish는 현재 asyncio event loop에서 수행하고 `put_nowait()`만 사용한다.
subscriber socket I/O를 기다리지 않는다.

### 5.2 `RunEventPublisher`

기존 문자열 logger와 별도의 typed event adapter다.

- coordinator: `queued`, `running`, `report_saved`, terminal 발행
- graph: 단계별 `progress`, 장소별 `place_result` 발행
- browser: 보안 확인, navigation, page attach 상태를 `progress`로 발행

기존 CLI 로그 callback은 유지한다. 이벤트 생성을 위해 `[run:...]` 문자열을 다시
파싱하지 않는다. graph와 browser가 알고 있는 `run_id`를 publisher에 직접 전달한다.

publisher는 hub 오류를 warning log로 남기되 graph·browser 예외로 전파하지 않는다.

### 5.3 `CdpStreamManager`

역할:

- run과 Playwright `Page` 연결
- viewer 등록·해제
- viewer가 있을 때 CDP session과 screencast 시작
- JPEG frame fan-out 및 backpressure 처리
- page·runtime 종료 cleanup

개념 interface:

```python
class CdpStreamManager:
    async def attach_page(self, run_id: str, page: Page) -> None: ...
    async def detach_page(self, run_id: str) -> None: ...
    def subscribe(self, run_id: str) -> BrowserStreamSubscription: ...
    async def close(self) -> None: ...
```

manager는 FastAPI `WebSocket`을 직접 받지 않는다. subscription이 control event와
binary frame을 제공하고 route가 이를 WebSocket message로 변환한다.

### 5.4 `JsonReportCatalog`

역할:

- 날짜별 JSON 파일 탐색
- `RunReport` 검증
- 필터·정렬·cursor pagination
- 저장 report 상세 조회
- 손상·중복 파일 감지

개념 interface:

```python
class JsonReportCatalog:
    def list_reports(self, query: ReportQuery) -> ReportPage: ...
    def get_report(self, run_id: str) -> RunReport | None: ...
```

catalog는 read-only다. JSON 생성·수정·삭제 기능을 제공하지 않는다.

## 6. 실행 이벤트 모델

### 6.1 Envelope

```json
{
  "runId": "run_20260715_010203_a1b2c3d4",
  "sequence": 12,
  "occurredAt": "2026-07-15T01:02:03Z",
  "type": "progress",
  "data": {
    "stage": "photo_analysis",
    "message": "사진 분석 완료",
    "placeId": "123",
    "placeName": "매장명"
  }
}
```

- `sequence`: run별 1부터 증가
- `occurredAt`: UTC
- JSON field: camelCase
- `data`: event type별 Pydantic payload

hub에 저장되는 canonical event만 새 sequence를 소비한다. route가 합성하는
`replay_reset`과 `snapshot`은 현재 최신 sequence를 본문에 표시하지만 SSE `id` field는
넣지 않고 replay buffer에도 저장하지 않는다.

### 6.2 Event type

| type | 발행 주체 | 의미 |
|---|---|---|
| `snapshot` | SSE route | 현재 `RunStatusResponse` |
| `queued` | coordinator | 실행 접수 완료 |
| `running` | coordinator | worker 실행 시작 |
| `progress` | graph/browser | 공개 가능한 단계 진행 메시지 |
| `place_result` | graph | 장소 하나의 `PlaceResult` 확정 |
| `browser_ready` | browser stream | page가 stream 가능 상태 |
| `browser_closed` | browser stream | page stream 종료 |
| `report_saved` | coordinator | JSON 저장 완료 및 URL |
| `completed` | coordinator | 성공 terminal 상태 |
| `failed` | coordinator | 실패 terminal 상태 |
| `replay_reset` | SSE route | 요청 sequence가 replay 범위를 벗어남 |

`place_result`가 실시간 report 갱신 단위다. UI는 이를 누적하고 `report_saved` 이후 최종
report endpoint를 다시 읽어 확정한다.

`progress.stage`는 `session_start`, `candidate_search`, `place_detail`,
`security_check`, `photo_analysis`, `review_analysis`, `scoring`, `report_build` 중 하나다.
상태 event data는 `status`, `reportAvailable`, 공개 가능한 `error`를 담고,
`report_saved` data는 `reportUrl`, `place_result` data는 `PlaceResult`를 담는다.

## 7. SSE API

### 7.1 Endpoint

```http
GET /runs/{run_id}/events
Accept: text/event-stream
Last-Event-ID: 11
```

FastAPI 0.139의 `fastapi.sse.EventSourceResponse`와 `ServerSentEvent`를 사용한다.
추가 SSE 라이브러리는 도입하지 않는다.

각 message는 다음 SSE field를 사용한다.

- `id`: event sequence
- `event`: event type
- `data`: `RunEvent` JSON
- `retry`: 2,000ms

15초 동안 event가 없으면 comment keep-alive를 전송한다. keep-alive에는 sequence를
부여하거나 replay buffer에 넣지 않는다.

### 7.2 연결 시작

- 알 수 없는 run: `404 run_not_found`
- `Last-Event-ID`가 정수가 아님: `422 invalid_event_id`
- header 없음: 보유 중인 event를 처음부터 replay
- header가 최신 sequence 이상: 새 event 대기
- terminal event까지 보낸 뒤 stream 종료

브라우저 `EventSource`는 끊어진 연결을 자동 재접속하므로 frontend는 `completed` 또는
`failed` 수신 즉시 `EventSource.close()`를 호출해야 한다. 서버는 terminal event를
재전송할 수 있지만 무한 재접속을 막기 위해 client close를 계약으로 둔다.

### 7.3 Replay reset

요청 sequence가 buffer의 가장 오래된 event보다 이전이면 다음 순서로 보낸다.

1. `replay_reset`
2. 현재 `RunStatusResponse`를 담은 `snapshot`
3. 현재 buffer의 event

terminal run buffer는 최근 100개 run을 LRU로 보관한다. LRU에서 제거된 terminal run은
coordinator snapshot만 보내고 stream을 종료한다. queued와 running run은 제거하지 않는다.

### 7.4 느린 subscriber

- subscriber queue 최대 128개
- overflow 시 해당 subscription만 종료
- EventSource 자동 재접속과 `Last-Event-ID` replay로 복구
- 다른 subscriber와 worker는 영향 없음

## 8. CDP WebSocket API

### 8.1 Endpoint

```text
WS /runs/{run_id}/browser-stream
```

queued 또는 running run에 연결할 수 있다. route는 연결을 accept한 뒤 다음 control JSON을
보낸다.

```json
{"type":"waiting"}
{"type":"ready","format":"jpeg","maxWidth":1280,"maxHeight":720}
{"type":"ended"}
{"type":"error","code":"stream_unavailable","message":"..."}
```

이미지 frame은 base64 JSON이 아닌 WebSocket binary message로 전송한다.

### 8.2 종료 코드

- `4404`: 알 수 없는 run
- `4409`: terminal 상태이며 연결 가능한 page가 없음
- `1011`: CDP stream 내부 오류
- `1000`: page 또는 실행 정상 종료

오류 control message를 보낼 수 있는 경우 먼저 전송하고 close code를 적용한다.

### 8.3 Screencast 설정

```json
{
  "format": "jpeg",
  "quality": 70,
  "maxWidth": 1280,
  "maxHeight": 720,
  "everyNthFrame": 2
}
```

CDP `Page.startScreencast`는 experimental API이므로 stream 실패를 graph 실패로 승격하지
않는다.

### 8.4 Frame backpressure

- CDP frame 수신 후 base64를 bytes로 변환
- 각 viewer는 최신 frame slot 하나만 보유
- 새 frame 도착 시 전송되지 않은 이전 frame 교체
- CDP `Page.screencastFrameAck`는 decode·fan-out 성공 여부와 무관하게 `finally`에서
  호출하고 viewer socket 전송을 기다리지 않음
- 한 viewer의 socket 지연이 Chrome과 다른 viewer를 막지 않음

run별 `asyncio.Lock`으로 viewer 등록, 첫 start, 마지막 stop, page detach 경쟁을
직렬화한다. screencast start와 stop은 각각 최대 한 번만 실행한다.

### 8.5 Browser lifecycle 연결

`BrowserService.start_session()`은 page 생성 직후, `navigator.open()` 전에
`attach_page()`를 호출한다. queued 상태부터 기다리던 viewer는 최초 네이버지도 진입을
볼 수 있다.

`close_session()`은 page/context를 닫기 전에 `detach_page()`를 호출한다. 시작 실패
cleanup 경로에서도 동일하게 detach한다.

## 9. 저장 리포트 API

### 9.1 목록·검색

```http
GET /reports?limit=20&status=completed&location=성수역&searchKeyword=일식&dateFrom=2026-07-01&dateTo=2026-07-15&cursor=...
```

query:

- `limit`: 기본 20, 최소 1, 최대 100
- `status`: `completed` 또는 `failed`
- `location`: trim 후 case-insensitive 부분 일치
- `searchKeyword`: trim 후 case-insensitive 부분 일치
- `dateFrom`, `dateTo`: UTC 날짜, 양 끝 포함
- `cursor`: opaque base64url 문자열

`dateFrom > dateTo` 또는 trim 후 빈 문자열 filter는 `422 invalid_filter`다.

응답:

```json
{
  "items": [
    {
      "runId": "run_...",
      "status": "completed",
      "config": {},
      "createdAt": "2026-07-15T01:02:03Z",
      "resultCount": 1,
      "errorCount": 0,
      "reportUrl": "/reports/run_..."
    }
  ],
  "nextCursor": null,
  "invalidReportCount": 0
}
```

정렬 key는 `(createdAt, runId)` 내림차순이다. cursor는 version, 마지막 정렬 key, 현재
필터 fingerprint를 포함한다. fingerprint는 정규화된 status, location, searchKeyword,
dateFrom, dateTo로 계산하고 limit은 포함하지 않는다. 다른 필터의 cursor를 재사용하면
`422 invalid_cursor`다.
cursor는 보안 token이 아니라 pagination 상태이며 변조된 값은 검증 단계에서 거절한다.

### 9.2 상세

```http
GET /reports/{run_id}
```

- 저장된 `RunReport`: `200`
- 없음: `404 report_not_found`
- 안전하지 않은 ID: `422 invalid_run_id`
- 중복 파일: `500 report_catalog_conflict`
- 손상 JSON: `500 report_corrupt`

기존 `GET /runs/{run_id}/report`는 현재 프로세스 job과 저장 성공 경계를 조회한다.
`GET /reports/{run_id}`는 서버 재시작과 무관한 저장 파일 조회다.

### 9.3 파일 탐색

- `reports/[0-9]{4}/[0-9]{2}/[0-9]{2}/*.json`만 탐색
- dot file과 `.tmp` 제외
- 모든 항목을 `RunReport`로 검증
- path의 UTC 날짜와 `createdAt` 날짜 불일치는 손상으로 처리
- root가 없으면 빈 목록 또는 상세 `404`
- permission·I/O 실패는 `500 report_catalog_unavailable`
- 목록의 손상 파일은 건너뛰고 `invalidReportCount` 증가
- 상세 대상 손상은 명시적 오류 반환

`invalidReportCount`는 적용된 UTC 날짜 범위에서 스캔했지만 정상 `RunReport`로 읽지
못한 파일 수다. status·문자열 filter와는 무관하게 계산한다.

각 목록 요청은 파일을 새로 스캔한다. 시간 복잡도는 O(N)이지만 로컬 MVP 예상 규모에
적합하며 stale cache 문제를 만들지 않는다.

## 10. 실행 상태와 이벤트 순서

```text
POST /runs
  → record 생성
  → event hub open
  → queued 발행
  → queue enqueue
  → 202

worker dequeue
  → record running 전환
  → running 발행
  → progress / place_result 반복
  → graph RunReport 반환
  → JsonReportStore.save()
  → record report 보관
  → report_saved 발행
  → report.status에 따라 completed 또는 failed 발행
  → terminal buffer 표시
```

상태 record를 먼저 변경한 뒤 해당 이벤트를 발행한다. 따라서 event를 받은 직후 상태
endpoint를 호출해도 이전 상태가 보이지 않는다.

### 10.1 실패 구분

graph가 failed report를 반환하고 저장에 성공한 경우:

```text
report_saved → failed(reportAvailable=true, error=null)
```

graph 호출 또는 저장 자체가 예외를 낸 경우:

```text
failed(reportAvailable=false, error=<공개 가능한 요약>)
```

## 11. 오류 격리

- event publish 실패: warning log, graph 계속 실행
- 개별 SSE subscriber 실패: 해당 subscriber만 제거
- CDP 시작·frame·viewer 실패: browser stream만 종료
- report catalog 조회 실패: 실행 API와 worker에 영향 없음
- JSON 저장 실패: 기존 계약대로 job failed, report 조회 불가

직접 발생시키는 HTTP 오류는 기존 `{detail: {code, message}}` 형식을 유지한다.

## 12. 시작과 종료

### 12.1 시작

1. `RunEventHub` 생성
2. `CdpStreamManager` 생성
3. `JsonReportStore`와 `JsonReportCatalog` 생성
4. event publisher를 coordinator·graph·browser에 주입
5. coordinator worker 시작

### 12.2 종료

1. coordinator 신규 접수 중단
2. queued job을 failed로 전환하고 terminal event 발행
3. active worker 취소
4. graph `finally`와 BrowserService를 통해 page detach
5. `CdpStreamManager.close()`로 남은 viewer·CDP session 종료
6. `BrowserService.close_all()` 안전 정리
7. `RunEventHub.close()`로 queued event를 drain한 뒤 SSE 종료
8. OpenAI client 종료

종료 중 한 컴포넌트 cleanup이 실패해도 뒤 컴포넌트 cleanup을 계속 시도한다.

## 13. 테스트 전략

### 13.1 Event hub 단위 테스트

- run별 sequence 증가와 UTC normalize
- event payload camelCase
- 신규 subscriber replay
- `Last-Event-ID` 이후 replay
- 오래된 ID의 `replay_reset`과 snapshot
- terminal stream 종료
- subscriber overflow 격리
- terminal 100-run LRU
- hub shutdown

### 13.2 Coordinator·Graph·Browser 테스트

- `queued → running → progress → place_result → report_saved → terminal`
- failed report와 실행·저장 예외 event 차이
- 상태 변경 후 event 발행 순서
- queued·active shutdown event
- publisher 실패가 실행 결과에 영향 없음
- page attach가 navigation보다 먼저 수행됨
- 모든 close·시작 실패 경로에서 detach 수행

### 13.3 SSE HTTP 테스트

- unknown run `404`
- invalid `Last-Event-ID` `422`
- SSE `id`, `event`, JSON `data`, `retry`
- 재접속 replay
- keep-alive comment
- terminal 이후 종료
- 느린 subscriber가 다른 subscriber에 영향 없음

### 13.4 CDP stream 테스트

- queued viewer의 `waiting`
- 첫 viewer에서 `startScreencast`
- 다중 viewer binary frame 수신
- 최신 frame 교체
- viewer 전송 전 frame ACK
- 마지막 viewer에서 `stopScreencast`
- page detach의 `ended`와 정상 close
- CDP 오류 `1011`
- stream 오류 후 graph 실행 지속
- runtime 종료 후 task·session 잔존 없음

CDP 단위 테스트는 fake Page/CDPSession을 사용한다. 기존 PoC의 실제 Chrome 검증은 최종
통합 확인에서 재사용한다.

### 13.5 Report catalog 테스트

- 최신순 및 동일 timestamp run ID 정렬
- 필터 각각과 조합
- UTC 날짜 양 끝 포함
- cursor page 간 중복·누락 없음
- cursor/filter mismatch
- root 없음
- 손상·날짜 불일치·중복 파일
- 상세 조회와 안전하지 않은 run ID
- `ReportSummary` count와 URL

### 13.6 실제 통합 확인

`성수역 / 일식 / maxPlaces=1`로 다음을 한 사이클에서 검증한다.

1. `POST /runs`가 `202`
2. SSE가 lifecycle, progress, place result, terminal 수신
3. SSE 재접속 replay 성공
4. WebSocket에서 JPEG binary frame 수신
5. terminal report가 `/reports` 검색에 노출
6. `/reports/{run_id}`와 저장 JSON 모델이 동일
7. Uvicorn 종료 후 8000 port, Chrome, CDP task, subscriber, `.tmp` 잔존 없음

## 14. 구현 순서

1. event model과 `RunEventHub`
2. coordinator lifecycle event 연결
3. graph·browser typed progress와 `place_result`
4. SSE route와 replay
5. `CdpStreamManager`와 BrowserService attach/detach
6. WebSocket route
7. `JsonReportCatalog`와 report API
8. runtime shutdown 통합
9. README·로드맵
10. 전체 자동 테스트와 실제 통합 실행

한 구현 계획에서 위 순서를 따르되 각 단계는 독립 테스트와 review checkpoint를 가진다.

## 15. 보안과 운영 경계

- 기본 bind와 문서는 `127.0.0.1` 전용
- 인증·인가·CORS 없음
- 외부 공개 금지
- WebSocket으로 브라우저 입력을 받지 않음
- frame은 메모리에서만 중계하며 저장하지 않음
- event replay와 실행 registry는 프로세스 메모리 전용
- 멀티프로세스 Uvicorn worker를 지원하지 않음

## 16. 범위 제외

- 프론트엔드 UI
- Redis, DB, 외부 message broker
- 서버 재시작 후 event replay
- 실행 취소·재시도
- 영상 녹화·오디오
- WebSocket 브라우저 원격 조작
- 저장 리포트 수정·삭제
- 인증, rate limit, queue 상한
- 멀티프로세스·멀티서버 fan-out

## 17. 완료 조건

- SSE 재접속 시 놓친 event 복구
- 공개 가능한 progress와 장소별 결과 실시간 전달
- slow client가 worker·Chrome을 막지 않음
- 여러 viewer가 같은 실행을 볼 수 있음
- stream 오류가 실행 결과에 영향 없음
- 서버 재시작 후 저장 report 목록·검색·상세 조회
- 종료 시 worker·SSE·WebSocket·CDP·Chrome 자원 정리
- 기존 테스트를 포함한 전체 suite 통과
- 실제 API·SSE·WebSocket·catalog 통합 사이클 성공

## 18. 참고 자료

- [FastAPI Server-Sent Events](https://fastapi.tiangolo.com/tutorial/server-sent-events/)
- [FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/)
- [Chrome DevTools Protocol Page domain](https://chromedevtools.github.io/devtools-protocol/tot/Page/)
- [MDN Using server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events)
