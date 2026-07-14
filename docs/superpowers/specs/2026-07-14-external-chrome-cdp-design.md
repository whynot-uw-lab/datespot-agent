# 외부 Chrome CDP 실행 설계

## 목표

라이브 그래프 실행에서 Playwright가 Chrome을 직접 실행하지 않도록 변경함.
Python이 일반 Chrome 프로세스를 전용 프로필과 고정되지 않은 non-zero CDP 포트로
실행하고 Playwright는 해당 브라우저에 연결함. 로컬 무해 페이지 기준
`navigator.webdriver=false`인 실행 구조를 사용함.

## 접근 방식 비교

### 1. 외부 Chrome 자동 실행 후 CDP 연결 — 채택

수동 실행 없이 일반 Chrome 프로세스를 만들 수 있음. 프로세스와 프로필을 프로젝트가
소유하므로 기본 Chrome 프로필을 건드리지 않음. Playwright의 CDP 연결은 일반
Playwright 프로토콜보다 일부 기능 충실도가 낮을 수 있음.

### 2. 사용자가 Chrome을 수동 실행한 뒤 CDP 연결

구조가 단순하지만 매 실행마다 사용자 조작이 필요함. 라이브 실행 자동화 목표와 맞지 않음.

### 3. Playwright 실행 인자 변경

`--enable-automation`만 제외해도 현재 Chrome에서 `navigator.webdriver=true`가 유지됨.
페이지 속성을 덮어쓰거나 탐지 회피 플래그를 추가하지 않음.

## 구성 요소

### `ChromeCdpLauncher`

- 전용 `user_data_dir`을 생성함.
- loopback 주소의 사용 가능한 non-zero 포트를 할당함.
- 일반 Chrome 실행 파일을 `asyncio.create_subprocess_exec()`로 시작함.
- `/json/version` 응답까지 제한 시간 동안 대기함.
- 준비 실패 또는 조기 종료 시 프로세스를 정리하고 `BrowserSessionError`를 발생시킴.
- 종료 시 terminate, 제한 시간 초과 시 kill 순서로 정리함.

### `BrowserService`

- 선택적 `cdp_launcher`를 받음.
- 값이 없으면 기존 Playwright launch/persistent-context 동작을 유지함.
- 값이 있으면 launcher가 반환한 endpoint에 `connect_over_cdp()`로 연결함.
- CDP 기본 컨텍스트와 기존 첫 페이지를 재사용함.
- 세션 종료 시 page, context, browser, 외부 Chrome, Playwright 순으로 정리함.

### 네이버 지도 입력

- 역 결과와 후보 장소에서 사용한 JavaScript `element.click()`을 제거함.
- Playwright `locator.click()`을 사용해 trusted 브라우저 입력 경로를 사용함.
- CAPTCHA 자동 처리, 속성 덮어쓰기, stealth 플러그인은 추가하지 않음.

### 라이브 실행기

- macOS Google Chrome 기본 실행 경로를 사용함.
- 기존 `~/.cache/datespot-agent/chrome-profile`을 전용 CDP 프로필로 재사용함.
- 라이브 실행에서는 외부 Chrome CDP 모드를 기본 사용함.

## 오류 처리

- Chrome 실행 파일이 없으면 실행 전 명확한 세션 오류를 반환함.
- 프로필 잠금 또는 Chrome 조기 종료 시 열린 프로세스를 정리함.
- 시작 중 실패해도 Playwright와 외부 Chrome을 모두 정리함.
- 보안 확인 감지와 수동 대기 로직은 기존 동작을 유지함.

## 테스트

- launcher 명령에 non-zero 포트와 전용 프로필이 포함되는지 확인함.
- CDP 준비 성공, 조기 종료, timeout 정리를 확인함.
- BrowserService가 `connect_over_cdp(no_defaults=True, is_local=True)`를 호출하는지 확인함.
- 세션 종료 시 외부 Chrome 정리 순서를 확인함.
- 라이브 실행기가 전용 launcher를 구성하는지 확인함.
- 역/후보 클릭에서 Locator click을 사용하고 JS evaluate를 사용하지 않는지 확인함.
- 전체 단위 테스트와 로컬 Chrome 신호 검증을 실행함.

## 성공 기준

- 라이브 실행이 사용자 수동 Chrome 실행 없이 시작됨.
- 로컬 신호 페이지에서 `navigator.webdriver=false` 확인됨.
- 일반 BrowserService 사용자의 기존 동작 유지됨.
- 테스트 전체 통과함.

