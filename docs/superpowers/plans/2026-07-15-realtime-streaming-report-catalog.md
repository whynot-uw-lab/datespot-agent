# Realtime Streaming and Report Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 실행 이벤트 SSE, on-demand CDP WebSocket 영상, 저장 JSON 리포트 목록·검색·상세 API를 기존 단일 FIFO FastAPI runtime에 추가함.

**Architecture:** `RunEventHub`, `CdpStreamManager`, `JsonReportCatalog`를 독립 컴포넌트로 구현하고 `AppRuntime`에서 조립함. Coordinator·Graph·Browser는 typed publisher와 browser lifecycle observer만 의존하며 HTTP transport는 `api.app`에 한정함.

**Tech Stack:** Python 3.13, FastAPI 0.139 native SSE, Starlette WebSocket, Playwright CDP session, Pydantic v2, asyncio, unittest, uv

## Global Constraints

- 기준 설계: `docs/superpowers/specs/2026-07-15-realtime-streaming-report-catalog-design.md`
- 메모리 단일 프로세스, graph worker 하나, 멀티프로세스 fan-out 제외
- SSE run별 replay 1,000개, subscriber queue 128개, terminal LRU 100개
- CDP JPEG quality 70, 1280×720, everyNthFrame 2, viewer별 최신 frame 1개
- 저장 리포트는 JSON source of truth, 목록 limit 기본 20·최대 100
- raw prompt·숨겨진 추론·traceback·API key·내부 경로 노출 금지
- 기존 `/runs` 계약과 실제 Chrome 전용 profile 동작 유지
- 사용자 변경 `blind-date-recommend.iml` 제외

## 구현 완료 상태

- 완료일: 2026-07-15
- SSE: `GET /runs/{run_id}/events`, terminal 수신 후 client close, 메모리 replay 한정
- 브라우저 영상: `WS /runs/{run_id}/browser-stream`, JSON control + binary JPEG
- 저장 리포트: `GET /reports`, `GET /reports/{run_id}`, JSON 파일 O(N) scan
- 운영 범위: 인증·CORS·멀티프로세스 fan-out 없는 loopback 로컬 단일 프로세스
- 실통합: `run_20260715_033946_03682236` completed, SSE resume `1 → 2..22`,
  WebSocket JPEG 530 frame, 저장 JSON·catalog detail 일치 확인

---

### Task 1: Implement typed run events and bounded replay hub

**Files:**

- Create: `src/datespot_agent/api/events.py`
- Modify: `src/datespot_agent/api/__init__.py`
- Create: `tests/test_run_event_hub.py`

**Interfaces:**

- Produces: `RunEventType`, `ProgressStage`, `RunEvent`
- Produces: `RunEventHub.open_run()`, `publish()`, `mark_terminal()`, `subscribe()`, `close()`
- Produces: `RunEventPublisher` failure-isolating adapter
- Produces: `RunEventSubscription.replay`, async live iteration, `reset_required`, `latest_sequence`

- [x] **Step 1: Write failing model, replay, overflow, terminal and LRU tests**

```python
class RunEventHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconnect_replays_only_events_after_last_id(self):
        hub = RunEventHub(clock=lambda: NOW)
        hub.open_run("run_one")
        first = hub.publish("run_one", RunEventType.QUEUED, lifecycle("queued"))
        second = hub.publish("run_one", RunEventType.RUNNING, lifecycle("running"))
        subscription = hub.subscribe("run_one", last_event_id=first.sequence)
        self.assertEqual(subscription.replay, (second,))

    async def test_old_id_requires_reset_when_buffer_rotated(self):
        hub = RunEventHub(replay_capacity=2, clock=lambda: NOW)
        hub.open_run("run_one")
        for value in range(3):
            hub.publish("run_one", RunEventType.PROGRESS, progress(str(value)))
        subscription = hub.subscribe("run_one", last_event_id=0)
        self.assertTrue(subscription.reset_required)

    async def test_subscriber_overflow_does_not_block_publish(self):
        hub = RunEventHub(subscriber_capacity=1, clock=lambda: NOW)
        hub.open_run("run_one")
        subscription = hub.subscribe("run_one", last_event_id=None)
        hub.publish("run_one", RunEventType.QUEUED, lifecycle("queued"))
        hub.publish("run_one", RunEventType.RUNNING, lifecycle("running"))
        self.assertTrue(subscription.overflowed)
```

- [x] **Step 2: Run tests and verify RED**

Run: `uv run python -m unittest tests.test_run_event_hub -v`

Expected: `ModuleNotFoundError: datespot_agent.api.events`.

- [x] **Step 3: Implement immutable Pydantic events and event-loop-local hub**

```python
class RunEventType(str, Enum):
    SNAPSHOT = "snapshot"
    QUEUED = "queued"
    RUNNING = "running"
    PROGRESS = "progress"
    PLACE_RESULT = "place_result"
    BROWSER_READY = "browser_ready"
    BROWSER_CLOSED = "browser_closed"
    REPORT_SAVED = "report_saved"
    COMPLETED = "completed"
    FAILED = "failed"
    REPLAY_RESET = "replay_reset"


class RunEvent(CamelModel):
    run_id: str
    sequence: int = Field(ge=1)
    occurred_at: datetime
    type: RunEventType
    data: RunEventPayload
```

Use `deque(maxlen=replay_capacity)`, `asyncio.Queue(maxsize=subscriber_capacity)`, and an
`OrderedDict` for terminal LRU. On overflow, unregister the subscriber, clear its queue, and push
an internal close sentinel. `RunEventPublisher` catches hub exceptions and logs a warning.

- [x] **Step 4: Run focused tests and full regression**

Run: `uv run python -m unittest tests.test_run_event_hub -v`

Run: `uv run python -W error -m unittest discover -s tests -p 'test_*.py'`

Expected: all tests pass without warnings.

- [x] **Step 5: Commit event hub**

```bash
git add src/datespot_agent/api/events.py src/datespot_agent/api/__init__.py tests/test_run_event_hub.py
git commit -m "feat: add replayable run event hub"
```

---

### Task 2: Publish lifecycle, graph progress, browser progress and place results

**Files:**

- Modify: `src/datespot_agent/api/coordinator.py`
- Modify: `src/datespot_agent/graph/service.py`
- Modify: `src/datespot_agent/browser/service.py`
- Modify: `src/datespot_agent/browser/naver_map.py`
- Modify: `tests/test_run_coordinator.py`
- Modify: `tests/test_graph_service.py`
- Modify: `tests/test_browser_service.py`

**Interfaces:**

- Consumes: `RunEventPublisher`
- Produces: coordinator lifecycle ordering and terminal events
- Produces: graph `progress` and `place_result` typed events
- Produces: browser `security_check` and navigation progress events without log-prefix parsing

- [x] **Step 1: Write failing event-order and isolation tests**

```python
async def test_worker_publishes_saved_report_before_terminal(self):
    accepted = coordinator.submit(make_config())
    await finish(accepted.run_id)
    self.assertEqual(
        publisher.types(accepted.run_id),
        ["queued", "running", "report_saved", "completed"],
    )

async def test_failed_report_is_saved_before_failed_terminal(self):
    runner.status = RunStatus.FAILED
    accepted = coordinator.submit(make_config())
    await finish(accepted.run_id)
    self.assertEqual(publisher.types(accepted.run_id)[-2:], ["report_saved", "failed"])
    self.assertTrue(publisher.last.data.report_available)

async def test_event_publisher_failure_does_not_fail_run(self):
    publisher.error = RuntimeError("event unavailable")
    accepted = coordinator.submit(make_config())
    await finish(accepted.run_id)
    self.assertEqual(coordinator.get_status(accepted.run_id).status, RunJobStatus.COMPLETED)
```

- [x] **Step 2: Run focused tests and verify RED**

Run: `uv run python -m unittest tests.test_run_coordinator tests.test_graph_service tests.test_browser_service -v`

Expected: constructors reject `event_publisher` and expected events are absent.

- [x] **Step 3: Add optional publisher dependencies and exact ordering**

```python
class RunCoordinator:
    def __init__(..., event_publisher: RunEventPublisher | None = None) -> None: ...

    def submit(self, config: RunConfig) -> RunAccepted:
        ...
        self._events.open_run(run_id)
        self._events.lifecycle(run_id, RunEventType.QUEUED, snapshot)
        self._queue.put_nowait(run_id)

    async def _execute(self, record: _RunRecord) -> None:
        record.status = RunJobStatus.RUNNING
        self._events.lifecycle(record.run_id, RunEventType.RUNNING, self.get_status(record.run_id))
        report = await self._runner.run(record.config, run_id=record.run_id)
        self._report_store.save(report)
        record.report = report.model_copy(deep=True)
        self._events.report_saved(record.run_id, f"/reports/{record.run_id}")
        ...
        self._events.terminal(record.run_id, event_type, self.get_status(record.run_id))
```

Extend Graph and Browser constructors with an optional publisher. Keep existing `log` callbacks.
Emit `place_result` immediately after a result is appended. Pass `run_id` directly to publisher;
never recover it from formatted log text.

- [x] **Step 4: Mark queued jobs failed during shutdown and test terminal events**

Drain queued IDs with `get_nowait()`, call `task_done()`, set `finished_at`, set the public shutdown
error, publish `failed`, and mark terminal. Cancel the active worker after accepting becomes false.

- [x] **Step 5: Run focused and full tests**

Run: `uv run python -m unittest tests.test_run_coordinator tests.test_graph_service tests.test_browser_service -v`

Run: `uv run python -W error -m unittest discover -s tests -p 'test_*.py'`

- [x] **Step 6: Commit typed publishers**

```bash
git add src/datespot_agent/api/coordinator.py src/datespot_agent/graph/service.py src/datespot_agent/browser tests/test_run_coordinator.py tests/test_graph_service.py tests/test_browser_service.py
git commit -m "feat: publish typed run progress events"
```

---

### Task 3: Expose replayable SSE run events

**Files:**

- Modify: `src/datespot_agent/api/app.py`
- Modify: `src/datespot_agent/api/runtime.py`
- Create: `tests/test_api_events.py`
- Modify: `tests/test_api_runtime.py`

**Interfaces:**

- Consumes: `RunEventHub`, coordinator status lookup
- Produces: `GET /runs/{run_id}/events`

- [x] **Step 1: Write failing SSE status, replay, reset and terminal tests**

```python
def test_sse_replays_after_last_event_id(self):
    response = client.get(
        "/runs/run_api/events",
        headers={"Last-Event-ID": "1"},
    )
    self.assertEqual(response.status_code, 200)
    self.assertIn("id: 2", response.text)
    self.assertIn("event: completed", response.text)

def test_sse_rejects_invalid_last_event_id(self):
    response = client.get("/runs/run_api/events", headers={"Last-Event-ID": "bad"})
    self.assertEqual(response.status_code, 422)
    self.assertEqual(response.json()["detail"]["code"], "invalid_event_id")
```

- [x] **Step 2: Run test and verify RED**

Run: `uv run python -m unittest tests.test_api_events -v`

Expected: `404` because the route does not exist.

- [x] **Step 3: Implement native FastAPI SSE route**

Validate run and header before constructing `EventSourceResponse`. Yield synthetic
`replay_reset`/`snapshot` without SSE IDs, replay canonical events with ID/event/data/retry, send a
comment every 15 seconds, and return after a terminal event. Serialize using `by_alias=True`.

- [x] **Step 4: Wire one hub through runtime, coordinator and graph/browser publishers**

`AppRuntime` gains `event_hub`; `create_runtime()` creates exactly one hub and injects its publisher
into all producers. `stop()` closes the hub after coordinator/browser cleanup.

- [x] **Step 5: Run SSE, runtime and full tests**

Run: `uv run python -m unittest tests.test_api_events tests.test_api_runtime -v`

Run: `uv run python -W error -m unittest discover -s tests -p 'test_*.py'`

- [x] **Step 6: Commit SSE API**

```bash
git add src/datespot_agent/api/app.py src/datespot_agent/api/runtime.py tests/test_api_events.py tests/test_api_runtime.py
git commit -m "feat: stream replayable run events over SSE"
```

---

### Task 4: Implement on-demand CDP stream manager and WebSocket API

**Files:**

- Create: `src/datespot_agent/browser/stream.py`
- Modify: `src/datespot_agent/browser/__init__.py`
- Modify: `src/datespot_agent/browser/service.py`
- Modify: `src/datespot_agent/api/app.py`
- Modify: `src/datespot_agent/api/runtime.py`
- Create: `tests/test_cdp_stream_manager.py`
- Create: `tests/test_api_browser_stream.py`
- Modify: `tests/test_browser_service.py`
- Modify: `tests/test_api_runtime.py`

**Interfaces:**

- Produces: `CdpStreamManager.attach_page()`, `detach_page()`, `subscribe()`, `close()`
- Produces: `BrowserStreamControl`, `BrowserStreamSubscription`
- Produces: `WS /runs/{run_id}/browser-stream`

- [x] **Step 1: Write failing manager lifecycle and latest-frame tests**

```python
async def test_first_viewer_starts_and_last_viewer_stops_screencast(self):
    manager = CdpStreamManager()
    await manager.attach_page("run_one", page)
    first = await manager.subscribe("run_one")
    second = await manager.subscribe("run_one")
    session.send.assert_any_await("Page.startScreencast", SCREENCAST_OPTIONS)
    await first.close()
    session.send.assert_not_awaited_with("Page.stopScreencast")
    await second.close()
    session.send.assert_awaited_with("Page.stopScreencast")

async def test_frame_ack_does_not_wait_for_slow_viewer(self):
    await emit_frame("old", session_id=1)
    await emit_frame("new", session_id=2)
    self.assertEqual(await subscription.next_frame(), b"new")
    session.send.assert_any_await("Page.screencastFrameAck", {"sessionId": 2})
```

- [x] **Step 2: Run manager tests and verify RED**

Run: `uv run python -m unittest tests.test_cdp_stream_manager -v`

Expected: `ModuleNotFoundError: datespot_agent.browser.stream`.

- [x] **Step 3: Implement per-run locked manager without FastAPI dependencies**

Use a run-local `asyncio.Lock`, create one `CDPSession` on the first subscriber after page attach,
start screencast with the fixed options, and keep one latest-frame slot per subscriber. ACK every
frame in `finally`. `detach_page()` emits `ended`, stops CDP, detaches the session, and closes all
subscriptions.

- [x] **Step 4: Attach before navigation and detach before page close**

Inject an optional manager into `BrowserService`. Call `attach_page(run_id, page)` before
`navigator.open()` and call `detach_page(run_id)` on normal close plus every failed-start cleanup
path.

- [x] **Step 5: Write and implement WebSocket contract tests**

Test `waiting`, `ready`, binary JPEG, `ended`, `4404`, `4409`, and `1011`. The route owns the
FastAPI WebSocket and consumes manager subscription messages. A WebSocket disconnect closes only
that subscription.

- [x] **Step 6: Run stream, browser, API and full tests**

Run: `uv run python -m unittest tests.test_cdp_stream_manager tests.test_api_browser_stream tests.test_browser_service tests.test_api_runtime -v`

Run: `uv run python -W error -m unittest discover -s tests -p 'test_*.py'`

- [x] **Step 7: Commit CDP WebSocket stream**

```bash
git add src/datespot_agent/browser src/datespot_agent/api tests/test_cdp_stream_manager.py tests/test_api_browser_stream.py tests/test_browser_service.py tests/test_api_runtime.py
git commit -m "feat: stream browser frames over WebSocket"
```

---

### Task 5: Implement JSON report catalog and persisted report APIs

**Files:**

- Create: `src/datespot_agent/reporting/catalog.py`
- Modify: `src/datespot_agent/reporting/__init__.py`
- Modify: `src/datespot_agent/api/models.py`
- Modify: `src/datespot_agent/api/errors.py`
- Modify: `src/datespot_agent/api/app.py`
- Modify: `src/datespot_agent/api/runtime.py`
- Create: `tests/test_json_report_catalog.py`
- Create: `tests/test_api_reports.py`
- Modify: `tests/test_api_runtime.py`

**Interfaces:**

- Produces: `ReportQuery`, `ReportSummary`, `ReportPage`
- Produces: `JsonReportCatalog.list_reports()`, `get_report()`
- Produces: `GET /reports`, `GET /reports/{run_id}`

- [x] **Step 1: Write failing sort, filter, cursor and corruption tests**

```python
def test_list_reports_filters_and_paginates_newest_first(self):
    write_report("run_a", created_at=DAY_1, location="성수역")
    write_report("run_b", created_at=DAY_2, location="성수역")
    first = catalog.list_reports(ReportQuery(location="성수", limit=1))
    self.assertEqual([item.run_id for item in first.items], ["run_b"])
    second = catalog.list_reports(ReportQuery(location="성수", limit=1, cursor=first.next_cursor))
    self.assertEqual([item.run_id for item in second.items], ["run_a"])

def test_cursor_from_other_filters_is_rejected(self):
    cursor = catalog.list_reports(ReportQuery(status=RunStatus.COMPLETED, limit=1)).next_cursor
    with self.assertRaises(InvalidReportCursorError):
        catalog.list_reports(ReportQuery(status=RunStatus.FAILED, cursor=cursor))
```

- [x] **Step 2: Run catalog tests and verify RED**

Run: `uv run python -m unittest tests.test_json_report_catalog -v`

Expected: `ModuleNotFoundError: datespot_agent.reporting.catalog`.

- [x] **Step 3: Implement read-only filesystem catalog**

Scan only numeric UTC date paths and non-dot JSON files. Validate each `RunReport`, verify path date,
deduplicate run IDs, normalize substring filters with `casefold()`, sort by `(created_at, run_id)`
descending, and encode a versioned base64url cursor with SHA-256 filter fingerprint. Root absence is
an empty page; permission/I/O errors raise `ReportCatalogUnavailableError`.

- [x] **Step 4: Write failing HTTP contract tests and implement routes**

Test camelCase summary/page payloads, filter validation, persisted detail after an empty coordinator,
404, 422 cursor/ID, corrupt detail, and catalog unavailable errors. Keep `/runs/{id}/report`
unchanged.

- [x] **Step 5: Wire catalog with the same expanded reports root**

`AppRuntime` gains `report_catalog`; runtime passes the same `reports_root` to store and catalog.

- [x] **Step 6: Run catalog, API, runtime and full tests**

Run: `uv run python -m unittest tests.test_json_report_catalog tests.test_api_reports tests.test_api_runtime -v`

Run: `uv run python -W error -m unittest discover -s tests -p 'test_*.py'`

- [x] **Step 7: Commit report catalog**

```bash
git add src/datespot_agent/reporting src/datespot_agent/api tests/test_json_report_catalog.py tests/test_api_reports.py tests/test_api_runtime.py
git commit -m "feat: add persisted report catalog API"
```

---

### Task 6: Harden lifecycle, document APIs and verify the combined workflow

**Files:**

- Modify: `src/datespot_agent/api/runtime.py`
- Modify: `tests/test_api_runtime.py`
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-07-15-realtime-streaming-report-catalog.md`

**Interfaces:**

- Consumes: all Tasks 1–5
- Produces: deterministic shutdown and documented complete backend workflow

- [x] **Step 1: Write failing cleanup continuation test**

Assert cleanup order and continuation when coordinator, stream manager, browser, or event hub close
raises. Required order: coordinator → stream manager → browser → event hub → OpenAI client.

- [x] **Step 2: Implement nested cleanup guarantees and run runtime tests**

Use a small async cleanup loop that records the first exception, awaits every cleanup operation, then
re-raises the first exception. Run `uv run python -m unittest tests.test_api_runtime -v`.

- [x] **Step 3: Update README endpoints, local-only limits and roadmap**

Document `/runs/{id}/events`, `/runs/{id}/browser-stream`, `/reports`, `/reports/{id}`, SSE terminal
client close, binary JPEG frames, memory-only replay, and file-scan catalog. Mark all remaining
backend roadmap items complete.

- [x] **Step 4: Run fresh automated verification**

Run: `uv lock --check`

Run: `uv run --frozen python -W error -m unittest discover -s tests -p 'test_*.py'`

Run: `git diff --check`

Expected: lock clean, all tests pass without warnings, no diff whitespace errors.

- [x] **Step 5: Run real combined integration**

Start Uvicorn on `127.0.0.1:8000`, submit `성수역/일식/maxPlaces=1`, connect SSE and browser
WebSocket, verify at least one binary JPEG frame, reconnect SSE with `Last-Event-ID`, wait for
terminal, search `/reports`, load `/reports/{run_id}`, and compare it to the saved `RunReport`.

- [x] **Step 6: Verify cleanup**

Stop Uvicorn and verify port 8000, the dedicated Chrome profile process, CDP tasks, subscribers, and
report `.tmp` files are absent.

- [x] **Step 7: Commit docs and final lifecycle changes**

```bash
git add src/datespot_agent/api/runtime.py tests/test_api_runtime.py README.md docs/superpowers/plans/2026-07-15-realtime-streaming-report-catalog.md
git commit -m "docs: complete realtime backend workflow"
```
