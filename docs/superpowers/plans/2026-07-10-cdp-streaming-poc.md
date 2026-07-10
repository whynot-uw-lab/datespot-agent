# CDP Streaming PoC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 1-5 CDP 스트리밍 최소 검증 PoC를 WebSocket viewer와 실행 결과 JSON으로 완료한다.

**Architecture:** `poc/1-5-cdp-streaming/stream_browser.py`가 Playwright Chromium, CDP screencast, WebSocket 서버를 한 프로세스에서 실행한다. `viewer.html`은 WebSocket 메시지의 base64 JPEG를 `<img>`에 표시한다. 테스트는 프레임 메시지 변환, 카운터, 결과 검증처럼 브라우저 없는 pure helper를 검증한다.

**Tech Stack:** Python, Playwright, websockets, uv, unittest, HTML, JSON artifacts.

## Global Constraints

- 기본 타깃 URL은 `https://map.naver.com`다.
- 기본 실행 시간은 5초다.
- 성공 기준은 `framesReceived >= 30`, `framesBroadcast >= 30`, `ok=true`다.
- viewer는 로컬 HTML 파일과 WebSocket 연결만 포함한다.
- 에이전트 로그, 리포트 실시간 갱신, 사용자 입력 UI는 제외한다.

---

### Task 1: Add Stream Helper Tests And Minimal Implementation

**Files:**
- Create: `tests/test_cdp_streaming.py`
- Create: `poc/1-5-cdp-streaming/stream_browser.py`

**Interfaces:**
- Produces: `build_frame_message(frame: str, session_id: int, metadata: dict[str, Any]) -> dict[str, Any]`
- Produces: `StreamStats` dataclass with `record_received()`, `record_broadcast(count: int)`, `average_fps()`
- Produces: `build_result(...) -> dict[str, Any]`
- Produces: `validate_result(result: dict[str, Any], min_frames: int) -> dict[str, Any]`

- [ ] Write `tests/test_cdp_streaming.py` with tests for frame message, stats, and threshold failure.
- [ ] Run `uv run python -m unittest tests/test_cdp_streaming.py` and verify RED because the script is missing.
- [ ] Create `stream_browser.py` helper code until tests pass.
- [ ] Run `uv run python -m unittest tests/test_cdp_streaming.py` and verify GREEN.

### Task 2: Add WebSocket/CDP Runtime

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `poc/1-5-cdp-streaming/stream_browser.py`

**Interfaces:**
- Consumes: helpers from Task 1
- Produces: `run_stream(args: argparse.Namespace) -> dict[str, Any]`
- Produces: `main() -> int`

- [ ] Add `websockets` with `uv add websockets`.
- [ ] Implement WebSocket client registry and `broadcast_frame(...)`.
- [ ] Implement Playwright CDP flow: launch Chromium, open target URL, create CDP session, call `Page.startScreencast`, handle `Page.screencastFrame`, acknowledge each frame with `Page.screencastFrameAck`.
- [ ] Save result JSON and return exit code `0` only when thresholds pass.
- [ ] Run helper tests.

### Task 3: Add Viewer, Docs, And Real Verification

**Files:**
- Create: `poc/1-5-cdp-streaming/viewer.html`
- Create: `poc/1-5-cdp-streaming/README.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `stream_browser.py`
- Produces: local viewer and roadmap completion note

- [ ] Create `viewer.html` that reads `ws` from query string and updates an `<img>` with `data:image/jpeg;base64,...`.
- [ ] Create `poc/1-5-cdp-streaming/README.md` with goal, command, output, and caveats.
- [ ] Run dry verification: `uv run python -m unittest discover -s tests`.
- [ ] Run real PoC: `uv run python poc/1-5-cdp-streaming/stream_browser.py --duration 5 --headless true`.
- [ ] Parse `output/cdp_stream_result.json` and confirm `ok=true`, `framesReceived >= 30`, `framesBroadcast >= 30`.
- [ ] Mark root README 1-5 complete after successful run.
- [ ] Run final verification and commit/push.
