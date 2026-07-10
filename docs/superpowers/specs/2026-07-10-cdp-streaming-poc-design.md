# CDP Streaming PoC Design

## Goal

1-5 CDP 스트리밍 최소 검증 PoC는 Playwright로 띄운 브라우저 화면을 Chrome DevTools Protocol(CDP) screencast 이벤트로 받아 로컬 HTML viewer에 실시간 중계할 수 있는지 확인한다.

## Scope

- 대상은 기술 리스크 검증용 최소 PoC다.
- 브라우저 화면 중계만 검증한다.
- 에이전트 로그, 리포트 실시간 갱신, 사용자 입력 UI는 3~4단계 범위다.
- 기본 타깃 URL은 `https://map.naver.com`로 두되, 실행 옵션으로 바꿀 수 있게 한다.
- viewer는 로컬 HTML 파일과 WebSocket 연결로 구성한다.

## Architecture

- `poc/1-5-cdp-streaming/stream_browser.py`가 Playwright 브라우저와 WebSocket 서버를 함께 실행한다.
- Playwright는 Chromium 페이지를 열고 `page.context.new_cdp_session(page)`로 CDP 세션을 만든다.
- CDP `Page.startScreencast`를 호출하고 `Page.screencastFrame` 이벤트의 JPEG frame을 WebSocket 클라이언트로 전달한다.
- `viewer.html`은 WebSocket으로 받은 base64 JPEG를 `<img>`에 계속 반영한다.
- 실행 결과는 `output/cdp_stream_result.json`에 저장한다.

## Output Schema

결과 파일은 다음 주요 필드를 가진다.

- `ok`: 성공 여부
- `ranAt`: 실행 시각
- `targetUrl`: 브라우저가 연 URL
- `viewerUrl`: viewer 파일 URL
- `websocketUrl`: WebSocket 주소
- `framesReceived`: CDP에서 받은 프레임 수
- `framesBroadcast`: viewer로 전송한 프레임 수
- `durationSeconds`: 측정 시간
- `averageFps`: 평균 프레임 수
- `errors`: 실패 사유 목록

## Success Criteria

- `uv run python poc/1-5-cdp-streaming/stream_browser.py --duration 5 --headless true`가 exit code `0`으로 종료한다.
- `framesReceived`가 30 이상이다.
- `framesBroadcast`가 30 이상이다.
- `output/cdp_stream_result.json`의 `ok=true`다.
- viewer HTML이 WebSocket 주소를 받아 프레임을 표시할 수 있는 구조다.

## Error Handling

- Playwright 브라우저 실행 실패는 `playwright:` 오류로 기록한다.
- CDP 세션 생성 또는 screencast 시작 실패는 `cdp:` 오류로 기록한다.
- WebSocket 서버 시작 실패는 `websocket:` 오류로 기록한다.
- duration 내 프레임 수가 기준 미달이면 `ok=false`와 threshold 오류를 기록한다.

## Testing

- CDP frame payload를 viewer message로 변환하는 pure helper를 테스트한다.
- frame counter가 수신/전송/평균 FPS를 계산하는지 테스트한다.
- 결과 검증 함수가 threshold 미달을 실패 처리하는지 테스트한다.
- 실제 브라우저/네트워크/WS 통합은 스크립트 실행으로 검증한다.
