# Playwright 영구 브라우저 프로필 설계

## 목표

네이버지도 라이브 실행에서 매번 초기화되는 브라우저 상태를 전용 프로필에 보존한다.
최초 보안 확인을 사용자가 직접 완료한 뒤 쿠키와 로컬 저장소를 다음 실행에서 재사용해
반복 확인 가능성을 낮춘다. 보안 확인 우회나 자동 풀이는 범위에 포함하지 않는다.

## 접근 방식 비교

### 1. 전용 영구 프로필 사용 — 채택

`chromium.launch_persistent_context()`에 프로젝트 전용 `user_data_dir`를 전달한다.
쿠키, 로컬 저장소와 브라우저 프로필 상태가 함께 유지된다. 같은 프로필의 동시 실행은
허용하지 않는다.

### 2. `storage_state` 저장·복원

현재 `browser.new_context()` 구조를 유지할 수 있지만 저장 대상이 쿠키와 일부 웹 저장소로
제한된다. 네이버 보안 확인 상태 유지에는 전용 영구 프로필보다 불확실성이 크다.

### 3. 사용 중인 Chrome에 CDP로 연결

일반 브라우징 상태를 가장 많이 재사용할 수 있지만 실행 중인 Chrome과 충돌할 수 있고,
Chrome의 기본 프로필 원격 제어 정책 제약을 받는다. 사용자 기본 프로필 보호를 위해
채택하지 않는다.

## 설계

### `BrowserService`

- 선택적 `user_data_dir: Path | None` 생성자 인자를 추가한다.
- `user_data_dir`가 없으면 기존 `launch()` + `new_context()` 격리 모드를 유지한다.
- 값이 있으면 `launch_persistent_context()`로 전용 프로필을 실행한다.
- 두 경로 모두 기존 `headless`, `browser_channel`, locale, timezone, viewport 설정을 유지한다.
- 영구 컨텍스트가 이미 만든 첫 페이지를 재사용하고, 없을 때만 새 페이지를 만든다.
- 컨텍스트 종료로 영구 브라우저 프로세스도 종료되도록 기존 정리 흐름을 유지한다.

### 라이브 실행기

`tests/run_graph_live.py`만 기본적으로 아래 전용 프로필을 사용한다.

```text
~/.cache/datespot-agent/chrome-profile
```

일반 자동 테스트와 다른 `BrowserService` 사용자는 인자를 전달하지 않으므로 기존 격리
동작을 유지한다. 같은 프로필을 사용하는 라이브 실행은 한 번에 하나만 실행한다.

## 오류 처리

- 프로필 잠금이나 Chrome 시작 실패는 기존 `BrowserSessionError` 변환 흐름을 따른다.
- 네이버 보안 확인 감지 시 기존 수동 해제 대기 동작을 유지한다.
- Playwright 탐지 회피용 브라우저 플래그, User-Agent 위조, CAPTCHA 자동 풀이는 추가하지
  않는다.

## 테스트

- `user_data_dir=None`: `launch()`와 `new_context()` 사용 확인
- `user_data_dir` 지정: `launch_persistent_context()`와 동일 브라우저/컨텍스트 옵션 확인
- 영구 컨텍스트의 기존 페이지 재사용 확인
- 라이브 실행기가 프로젝트 전용 프로필 경로를 전달하는지 확인
- 관련 단위 테스트와 전체 테스트 실행

## 성공 기준

- 라이브 실행 종료 후 전용 프로필 디렉터리가 유지됨
- 다음 실행에서 같은 프로필이 재사용됨
- 보안 확인이 발생하면 사용자가 완료할 때까지 기존처럼 대기 후 재개함
- 기존 비영구 `BrowserService` 호출자의 동작과 테스트가 유지됨
