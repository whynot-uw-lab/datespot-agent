# 1-5 CDP 스트리밍 최소 검증 PoC

1단계(기술 리스크 검증)의 다섯 번째 세부 단계. Playwright 브라우저 화면을 CDP screencast frame으로 받아 로컬 HTML viewer에 WebSocket으로 중계할 수 있는지 확인한다.

## 목표 흐름

```text
Playwright Chromium 실행
  → CDP session 생성
  → Page.startScreencast 호출
  → Page.screencastFrame 수신
  → WebSocket viewer로 JPEG frame 전송
  → output/cdp_stream_result.json 저장
```

## 실행

```bash
uv run python poc/1-5-cdp-streaming/stream_browser.py --duration 5 --headless true
```

viewer를 직접 열려면 실행 로그의 `Viewer:` URL을 브라우저에 붙여 넣는다.

## 옵션

```bash
uv run python poc/1-5-cdp-streaming/stream_browser.py \
  --url https://map.naver.com \
  --duration 5 \
  --min-frames 30 \
  --headless true \
  --auto-client true
```

## 출력

- `output/cdp_stream_result.json`
- 주요 필드:
  - `ok`
  - `targetUrl`
  - `viewerUrl`
  - `websocketUrl`
  - `framesReceived`
  - `framesBroadcast`
  - `durationSeconds`
  - `averageFps`
  - `errors`

## 완료 기준

- exit code `0`
- `ok=true`
- `framesReceived >= 30`
- `framesBroadcast >= 30`

## 확인 결과

- `https://map.naver.com` 대상 실제 CDP screencast 실행 성공.
- `framesReceived=105`
- `framesBroadcast=105`
- `averageFps=26.28`
- 결과 파일: `output/cdp_stream_result.json`

## 참고

- 자동 검증을 위해 기본값 `--auto-client true`가 내부 WebSocket 클라이언트를 연결한다.
- 실제 UI 확인은 `--headless false`와 viewer URL을 함께 사용한다.
