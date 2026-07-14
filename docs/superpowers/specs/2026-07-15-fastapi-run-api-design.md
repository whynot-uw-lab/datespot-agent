# FastAPI 실행 API 설계

**작성일:** 2026-07-15

**대상:** README 로드맵 3단계 첫 항목 `FastAPI 실행 API`

**기준 문서:**

- `docs/superpowers/specs/2026-07-14-langgraph-execution-loop-design.md`
- `docs/superpowers/specs/2026-07-14-json-report-storage-design.md`

## 1. 목표

HTTP 요청으로 `RunConfig`를 접수하고, 장시간 실행되는 네이버지도 탐색을 요청과
분리해 한 번에 하나씩 처리한다. 클라이언트는 접수 즉시 `run_id`를 받고 같은 ID로
상태와 최종 `RunReport`를 조회한다.

- `POST /runs`: 실행 접수와 `run_id` 반환
- `GET /runs/{run_id}`: 현재 상태 조회
- `GET /runs/{run_id}/report`: 저장 완료된 최종 리포트 조회
- `GET /health`: API와 worker 상태 확인
- 기존 `GraphRunService`와 `JsonReportStore` 재사용
- 기존 JSON 파일 저장 계약 유지

## 2. 확정 결정

### 2.1 실행 지속성

MVP는 메모리 기반 단일 프로세스로 구현한다.

- 서버 재시작 시 접수·실행 상태는 사라짐
- 이미 저장된 JSON 리포트 파일은 유지됨
- 재시작 후 이전 실행을 API에서 다시 찾는 기능은 제공하지 않음
- SQLite, Redis, Celery 같은 외부 상태 저장소는 도입하지 않음

### 2.2 동시성

모든 실행 요청은 메모리 FIFO queue에 접수하며 worker 하나가 순차 처리한다.

- 여러 `POST /runs` 요청은 모두 `202 Accepted`
- 먼저 접수된 실행부터 처리
- 동시에 실행되는 graph는 최대 1개
- 동일 Chrome 사용자 프로필의 동시 실행과 네이버 요청 폭증 방지

단, lifespan 종료가 시작돼 coordinator가 신규 접수를 중단한 뒤 들어온 요청은
`503 Service Unavailable`로 거절한다.

### 2.3 ID 정책

API 작업 ID와 `RunReport.run_id`는 같은 값을 사용한다.

- API 계층이 접수 시 안전한 `run_id`를 먼저 생성
- `GraphRunService.run(config, run_id=run_id)`로 전달
- 로그, 상태 조회, 저장 파일명, 리포트의 ID가 하나로 연결됨
- 기존 호출자는 `run_id`를 생략할 수 있어 하위 호환 유지

### 2.4 저장 성공 경계

graph 실행과 JSON 저장이 모두 끝나야 리포트를 조회할 수 있다.

1. worker가 `GraphRunService.run()` 호출
2. 반환된 `RunReport`를 `JsonReportStore.save()`로 저장
3. 저장 성공 후에만 `reportAvailable=true`
4. 저장 실패 시 job은 `failed`, 리포트 조회는 불가
5. 저장 실패가 `RunReport.status`를 변경하지는 않음

`RunReport.status=failed`인 정상적인 실패 리포트도 JSON 저장에 성공하면 조회할 수
있다. 이 경우 job 상태는 `failed`이고 `reportAvailable=true`다.

## 3. 접근안 비교

### 3.1 요청 안에서 동기 실행

`POST /runs`가 graph 완료까지 기다린 뒤 리포트를 반환하는 방식이다.

- 장점: 구현이 가장 단순함
- 단점: 브라우저·LLM 실행 시간이 길어 HTTP timeout과 연결 단절 위험이 큼
- 판정: 제외

### 3.2 FastAPI `BackgroundTasks`

응답 후 각 요청에 background task를 붙이는 방식이다.

- 장점: FastAPI 기본 기능만으로 빠르게 비동기 응답 가능
- 단점: 중앙 FIFO, 실행 상태 registry, 단일 동시성, shutdown 정리를 별도로 다시
  구현해야 함
- 판정: 제외

### 3.3 lifespan 기반 전용 coordinator

앱 lifespan에서 `RunCoordinator` worker를 시작하고 종료 시 정리하는 방식이다.

- 장점: FIFO·단일 동시성·상태 전이·종료 처리를 한 컴포넌트에서 보장
- 장점: API route와 graph 실행 로직 분리
- 단점: 프로세스 재시작 복구 불가
- 판정: 채택

## 4. 패키지 구조

```text
src/datespot_agent/
  api/
    __init__.py
    app.py           # create_app(), route, lifespan
    coordinator.py   # FIFO queue, 상태 registry, worker
    models.py        # API 응답 모델과 job 상태
    runtime.py       # 실제 Browser/Agent/Graph/Store 조립
```

기존 계층은 다음 변경만 허용한다.

```text
src/datespot_agent/config.py         # API 실행 경로 설정 추가
src/datespot_agent/graph/service.py  # 외부 run_id 선택 주입 지원
```

### 4.1 `api.models`

API 전용 상태와 응답 모델을 정의한다.

```python
class RunJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RunAccepted(CamelModel):
    run_id: str
    status: RunJobStatus
    status_url: str
    report_url: str


class RunStatusResponse(CamelModel):
    run_id: str
    status: RunJobStatus
    config: RunConfig
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    report_available: bool
    error: str | None


class HealthResponse(CamelModel):
    status: Literal["ok"]
    accepting: bool
    active_run_id: str | None
    queued_runs: int
```

모든 datetime은 UTC로 반환하고 JSON 필드는 기존 모델과 동일하게 camelCase를
사용한다.

### 4.2 `RunCoordinator`

`RunCoordinator`는 API job의 유일한 상태 소유자다.

```python
class RunCoordinator:
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def submit(self, config: RunConfig) -> RunAccepted: ...
    def get_status(self, run_id: str) -> RunStatusResponse | None: ...
    def get_report(self, run_id: str) -> RunReport | None: ...
    def health(self) -> HealthResponse: ...
```

`submit()`은 worker가 시작됐고 `accepting=true`일 때만 접수한다. 시작 전이나 종료
중에는 전용 `CoordinatorUnavailableError`를 발생시키고 route가 `503`으로 변환한다.

내부 구성:

- `asyncio.Queue[str]`: 접수 순서 보존
- `dict[str, RunRecord]`: 현재 프로세스의 상태 registry
- worker task 하나
- `GraphRunService`
- `JsonReportStore`
- 주입 가능한 clock과 `run_id_factory`

외부에 반환하는 config, status, report는 복사본으로 제공해 route가 내부 상태를
변경하지 못하게 한다.

### 4.3 `api.runtime`

실제 실행 의존성을 조립한다.

- `Settings` 로드
- `AsyncOpenAI` 생성
- `ChromeCdpLauncher`와 `BrowserService` 생성
- `PhotoAnalysisAgent`, `ReviewAnalysisAgent`, `PlaceScoringService` 생성
- `GraphRunService` 생성
- `JsonReportStore` 생성
- `RunCoordinator` 생성

조립 factory는 async이며, OpenAI client 생성 이후 조립이 실패하면 client를 닫고
예외를 다시 전달한다.

모듈 import 시 브라우저나 OpenAI client를 시작하지 않는다. 실제 객체 조립과 worker
시작은 FastAPI lifespan 진입 시 수행한다.

### 4.4 `api.app`

`create_app()` factory와 기본 `app` 객체를 제공한다.

- 테스트는 가짜 coordinator factory를 주입
- 운영 기본값은 `api.runtime`의 async factory 사용
- lifespan 진입 시 coordinator 생성·시작
- lifespan 종료 시 coordinator 정지 후 `BrowserService.close_all()` 보장
- route는 coordinator 호출과 HTTP 변환만 담당

## 5. Graph 실행 ID 확장

`GraphRunService.run()`을 다음처럼 확장한다.

```python
async def run(
    self,
    config: RunConfig,
    *,
    run_id: str | None = None,
) -> RunReport:
    effective_run_id = run_id or self._make_run_id()
```

- 기존 `run(config)` 호출 동작 유지
- API가 전달한 ID는 `GraphState`, 로그, `RunReport` 전체에 그대로 사용
- API ID 생성기는 기존 `run_<UTC timestamp>_<8 hex>` 형식 유지
- 중복 ID가 registry에 있으면 새 ID를 다시 생성

## 6. HTTP 계약

### 6.1 `POST /runs`

요청 본문은 기존 `RunConfig`를 그대로 사용한다.

```json
{
  "location": "성수역",
  "searchKeyword": "일식",
  "maxPlaces": 1,
  "weights": {
    "photoPercent": 50,
    "reviewPercent": 50
  },
  "scoring": {
    "photo": "어둡고 대화하기 좋은 분위기",
    "review": "음식이 맛있고 대화하기 좋음"
  }
}
```

응답: `202 Accepted`

```json
{
  "runId": "run_20260715_010203_a1b2c3d4",
  "status": "queued",
  "statusUrl": "/runs/run_20260715_010203_a1b2c3d4",
  "reportUrl": "/runs/run_20260715_010203_a1b2c3d4/report"
}
```

Pydantic 검증 실패는 FastAPI 기본 `422 Unprocessable Entity`를 사용한다.
coordinator가 시작 전이거나 종료 중이면 `503 Service Unavailable`과
`coordinator_unavailable`을 반환한다.

### 6.2 `GET /runs/{run_id}`

응답: `200 OK`

```json
{
  "runId": "run_20260715_010203_a1b2c3d4",
  "status": "running",
  "config": {
    "location": "성수역",
    "searchKeyword": "일식",
    "maxPlaces": 1
  },
  "createdAt": "2026-07-15T01:02:03Z",
  "startedAt": "2026-07-15T01:02:04Z",
  "finishedAt": null,
  "reportAvailable": false,
  "error": null
}
```

실제 응답의 `config`에는 weights와 scoring 기본값도 포함한다.
`error`는 graph 호출 자체 또는 JSON 저장 같은 coordinator 단계의 예외 메시지에만
사용한다. 저장된 failed `RunReport`의 도메인 오류는 report의 `errors`에 있고 status
응답의 `error`는 `null`이다.

### 6.3 `GET /runs/{run_id}/report`

- 저장 완료된 report: `200 OK`, 본문은 `RunReport`
- `queued` 또는 `running`: `409 Conflict`, `report_not_ready`
- 실행·저장 예외로 report 없음: `409 Conflict`, `report_unavailable`
- 알 수 없는 `run_id`: `404 Not Found`, `run_not_found`

graph가 실패 상태의 `RunReport`를 정상 반환하고 저장한 경우에는 `200 OK`로 해당
리포트를 반환한다.

### 6.4 `GET /health`

응답: `200 OK`

```json
{
  "status": "ok",
  "accepting": true,
  "activeRunId": null,
  "queuedRuns": 0
}
```

health는 외부 네이버지도나 OpenAI까지 호출하지 않는다. 현재 프로세스 worker의
생존과 접수 가능 여부만 나타낸다.

### 6.5 오류 본문

직접 발생시키는 HTTP 오류는 FastAPI의 `detail` 안에 안정적인 코드와 메시지를
넣는다.

```json
{
  "detail": {
    "code": "run_not_found",
    "message": "실행을 찾을 수 없음"
  }
}
```

## 7. 상태 전이와 데이터 흐름

```text
POST /runs
  -> run_id 생성
  -> RunRecord(queued) 저장
  -> FIFO enqueue
  -> 202 응답

worker dequeue
  -> status=running, started_at 기록
  -> GraphRunService.run(config, run_id=run_id)
  -> JsonReportStore.save(report)
  -> report 보관
  -> report.status에 따라 completed 또는 failed
  -> finished_at 기록
```

예외 흐름:

```text
graph가 failed RunReport 반환
  -> JSON 저장 성공
  -> job=failed, reportAvailable=true

graph 호출 자체가 예외 발생
  -> job=failed, reportAvailable=false, error 기록

JSON 저장 실패
  -> 원래 RunReport는 변경하지 않음
  -> job=failed, reportAvailable=false, error 기록
```

## 8. 시작과 종료

FastAPI 권장 방식인 lifespan async context manager를 사용한다.

시작:

1. 설정과 실제 의존성 생성
2. coordinator worker 시작
3. 요청 수락 시작

종료:

1. 신규 접수 중단
2. worker task 취소
3. 실행 중 graph의 `finally`로 현재 브라우저 세션 정리
4. 남은 browser session `close_all()`
5. 종료 완료

대기 중 job은 프로세스와 함께 사라지며 영속 복구하지 않는다.

## 9. 설정

`Settings`에 다음 값을 추가한다.

```text
DATESPOT_REPORTS_ROOT=reports
DATESPOT_CHROME_EXECUTABLE_PATH=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
DATESPOT_BROWSER_USER_DATA_DIR=~/.cache/datespot-agent/chrome-profile
```

- 경로는 `Path`로 파싱
- OpenAI 키가 비어 있으면 lifespan 시작을 실패시켜 요청을 받지 않음
- Chrome 실행 파일 검증은 기본 runtime 생성 시 수행
- 서버 host와 port는 uvicorn CLI 옵션으로 지정하고 앱 설정에 넣지 않음

## 10. 의존성

프로젝트 의존성에 FastAPI 실행 항목을, 개발 의존성에 테스트 항목을 추가한다.

- `fastapi`
- `uvicorn`
- 개발 전용 `httpx2` (Starlette `TestClient` backend)

구체 버전 하한은 구현 시 현재 Python 3.13 호환 공식 릴리스를 확인해 고정한다.

## 11. 테스트 전략

외부 네이버지도와 OpenAI를 호출하지 않는 자동 테스트를 기본으로 한다.

### 11.1 coordinator 단위 테스트

- submit 즉시 `queued`와 동일 `run_id` 반환
- FIFO 순서 보장
- 동시에 하나의 fake runner만 실행
- API가 만든 ID가 `GraphRunService.run()`에 전달
- `queued -> running -> completed` 전이
- failed report 저장 성공 시 `failed`, report 조회 가능
- graph 예외 시 `failed`, report 조회 불가
- 저장 예외 시 원래 report 상태 불변, report 조회 불가
- stop 시 worker 취소와 실행 자원 정리
- 외부 반환 모델 변경이 내부 record를 변경하지 않음

### 11.2 HTTP 계약 테스트

- `/health` 응답
- `POST /runs`의 `202`, camelCase 응답, `queued`
- coordinator 미시작·종료 중 접수의 `503`
- 잘못된 `RunConfig`의 `422`
- 상태 조회 `200`, 알 수 없는 ID `404`
- 준비 전 report 조회 `409`
- 저장된 completed/failed report 조회 `200`
- 저장 실패 report 조회 `409`
- TestClient context에서 lifespan start/stop 실행

### 11.3 Graph 회귀 테스트

- 외부 `run_id`가 로그와 `RunReport`에 유지
- `run_id` 생략 시 기존 자동 생성 동작 유지
- 기존 전체 테스트 통과

### 11.4 실제 통합 확인

1. uvicorn을 `127.0.0.1`에 실행
2. `POST /runs`로 `성수역 / 일식 / 1곳` 접수
3. status endpoint를 `completed` 또는 `failed`까지 polling
4. report endpoint가 저장된 JSON과 같은 `RunReport`를 반환하는지 확인
5. 프로세스 종료 후 Chrome과 Playwright 자원 정리 확인

## 12. 보안과 운영 경계

현재 API는 로컬 MVP 전용이다.

- 기본 실행 안내는 `127.0.0.1` bind
- 인증·인가 없음
- CORS 미설정
- rate limit과 queue 크기 제한 없음
- 외부 공개 배포 금지
- raw traceback은 HTTP 응답에 노출하지 않음

인증, 요청 제한, queue 상한, 다중 worker, 영속 job 저장은 실제 외부 배포 전에
별도 설계한다.

## 13. 범위 제외

- WebSocket/SSE 진행 로그
- CDP 브라우저 화면 스트리밍
- 실행 취소·재시도 API
- 실행 목록과 pagination
- 서버 재시작 후 상태 복구
- JSON 리포트 파일 역검색
- 멀티프로세스·멀티서버 worker
- 인증, CORS, rate limit
- 프론트엔드

## 14. README 반영

구현 완료 후 다음을 반영한다.

- FastAPI 실행 명령과 요청 예시 추가
- 3단계 `FastAPI 실행 API` 완료 표시
- 기존 `리포트 파일 저장 / "인박스" 저장 로직`을
  `저장된 리포트 목록·검색 인박스`로 변경
- 메모리 상태와 로컬 전용 제한 설명

## 15. 완료 조건

- `POST /runs`가 즉시 `202`와 안전한 `run_id` 반환
- 모든 실행이 단일 FIFO worker에서 순차 처리
- 동일 ID로 상태와 최종 report 조회 가능
- graph 실패 report와 실행/저장 예외가 구분됨
- JSON 저장 성공 전 report를 노출하지 않음
- lifespan 종료 시 worker와 browser 자원 정리
- 외부 호출 없는 자동 테스트와 실제 API 1회 실행 통과
- README와 3단계 로드맵 갱신

## 16. 참고 자료

- [FastAPI Lifespan Events](https://fastapi.tiangolo.com/advanced/events/)
- [FastAPI Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/)
- [FastAPI Testing Events](https://fastapi.tiangolo.com/advanced/testing-events/)
