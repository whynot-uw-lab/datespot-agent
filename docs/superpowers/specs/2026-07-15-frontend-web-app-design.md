# DateSpot Desktop Web App Design

**작성일:** 2026-07-15

**상태:** 승인·구현 완료
**대상:** 로컬 전용 DateSpot FastAPI backend를 사용하는 React desktop web app

## 1. 목표

사용자가 탐색 조건을 입력하고, 에이전트의 네이버지도 탐색 화면과 공개 진행 단계를 실시간으로 확인한 뒤, 최종 장소 추천과 저장 리포트를 조회할 수 있는 desktop web app을 제공함.

이번 범위의 완료 조건은 다음 네 흐름이 한 앱에서 연결되는 것임.

1. 새 탐색 조건 입력
2. SSE와 WebSocket을 이용한 실시간 진행 확인
3. 실행 종료 후 최종 리포트 확인
4. 저장 리포트 목록 검색과 상세 재조회

## 2. 확정 결정

- 기술: React, TypeScript, Vite
- 개발 포트: Vite `10003`, FastAPI `20003`
- 상태 구조: route-driven UI + TanStack Query + 실시간 전용 hooks
- 시각 방향: `A. Cinematic Split`
- 최적 화면: 1440px desktop
- 최소 지원 폭: 1024px
- 모바일 전용 UI: 제외
- 인증, 관리자 기능, 실행 취소, browser 입력 제어: 제외
- 개발 시 Vite proxy, build 후 FastAPI 동일 origin 정적 제공

## 3. 비목표

- 로그인·회원·공유 링크
- 모바일 전용 navigation과 touch 최적화
- 실행 취소 또는 재시도 제어 UI
- browser stream에 키보드·마우스 입력 전달
- 여러 FastAPI process 사이 event 공유
- report 편집·삭제
- raw prompt, 숨겨진 추론, 내부 traceback 표시

## 4. 사용자 흐름과 routes

frontend route는 backend API와 충돌하지 않도록 모두 `/app` 아래에 둠. `/`는 `/app/`로 redirect함.

### `/app/` — 새 탐색

- `location`: 장소 또는 역 이름
- `searchKeyword`: 음식 종류 또는 검색어
- `maxPlaces`: 1~10
- `photoPercent`: slider
- `reviewPercent`: `100 - photoPercent`로 자동 계산
- `scoring.photo`: 사진 평가 기준
- `scoring.review`: 리뷰 평가 기준

submit 성공 시 `POST /runs` 응답의 `runId`로 `/app/runs/:runId`에 이동함. 전송 중 중복 submit을 막고, backend validation 오류는 해당 필드에 연결함.

### `/app/runs/:runId` — 실시간 진행

- 진입 직후 `GET /runs/:runId`로 현재 snapshot 조회
- `GET /runs/:runId/events` SSE 연결
- `WS /runs/:runId/browser-stream` WebSocket 연결
- terminal 상태가 아니면 두 transport를 독립적으로 유지
- `completed` 또는 `failed` 수신 시 SSE를 닫고 report query 실행
- report가 있으면 최종 결과 CTA와 inline 결과를 표시
- report가 없으면 실패 안내와 새 탐색 CTA 표시

새로고침과 직접 URL 접근을 지원함. 마지막 canonical SSE ID는 `sessionStorage`의 run별 key에 저장하고 재연결 header 의미를 유지하는 transport adapter에서 사용함.

### `/app/reports` — 저장 리포트 목록

- `status`, `location`, `searchKeyword`, `dateFrom`, `dateTo`를 URL query와 동기화
- backend cursor는 URL에 노출하지 않고 현재 화면의 page chain에서 관리
- 첫 요청은 `limit=20`
- `nextCursor`가 있으면 `더 보기`
- 빈 결과, 일부 손상 파일 수, 조회 실패를 구분해 표시

### `/app/reports/:runId` — 저장 리포트 상세

- `GET /reports/:runId` 사용
- 실행 process가 재시작되어도 조회 가능
- analyzed 결과는 `finalScore` 내림차순
- `not_matched`, `failed` 결과는 별도 section

## 5. Visual system

### 방향

`Cinematic Split`은 browser 자동화의 현장감과 데이트 추천 서비스의 따뜻한 인상을 함께 유지함.

- 배경: warm cream
- 주요 색: deep forest green
- 강조 색: coral
- surface: 낮은 대비의 cream/white card
- score: coral large numeral
- body: 읽기 쉬운 sans-serif
- 주요 heading: 제한적으로 editorial serif 사용 가능

색상은 상태의 유일한 신호로 사용하지 않음. 아이콘과 label을 함께 제공함.

### 공통 shell

- 좌측 brand
- 중앙 navigation: `새 탐색`, `진행 중`, `저장 리포트`
- 우측 현재 실행 상태 또는 CTA
- 최대 콘텐츠 폭 1440px

### 진행 화면 배치

- 1440px: browser 65%, 진행·결과 rail 35%
- 1024~1279px: browser 60%, rail 40%
- rail 내부는 `실행 조건 → 친화적 진행 단계 → 최신 장소 결과` 순서
- 1024px 아래는 지원 대상이 아니며, 깨진 layout 대신 최소 폭을 유지하고 안내함

## 6. Frontend architecture

```text
frontend/
  src/
    app/             router, query client, app shell
    api/             HTTP client, contract types, public error mapping
    realtime/        SSE adapter, WebSocket adapter, run event reducer
    features/
      new-run/       입력 form과 mutation
      run-progress/  Cinematic Split 진행 화면
      reports/       저장 목록·filter·상세
    components/      button, field, status, empty/error surface
    styles/          tokens, global, responsive rules
  tests/
  e2e/
```

### 서버 상태

TanStack Query가 다음을 소유함.

- run snapshot
- terminal report
- report list pages
- persisted report detail

Query key는 route와 filter에서 결정함. SSE event를 받은 뒤 query cache를 직접 변형하기보다 run event reducer가 진행 화면용 projection을 관리하고, terminal에서 authoritative HTTP report를 다시 조회함.

### 실시간 상태

`useRunEvents(runId)`는 fetch 기반 SSE adapter를 사용함. native `EventSource`는 custom `Last-Event-ID` header를 지정할 수 없으므로 사용하지 않음. adapter는 `fetch`, `ReadableStream`, `eventsource-parser`, `AbortController`로 구성해 새로고침 후에도 `sessionStorage`의 마지막 ID를 header로 전송함.

hook은 다음을 반환함.

- connection state
- latest sequence
- public progress items
- accumulated place results
- terminal status
- reset count

canonical event는 sequence로 중복 제거함. `replay_reset` 이후 `snapshot`을 받으면 local projection을 교체하고 이어지는 retained canonical event를 다시 적용함.

`useBrowserStream(runId)`는 다음을 반환함.

- `waiting | ready | ended | error`
- 현재 JPEG Object URL
- 공개 오류 message

새 binary frame을 받으면 이전 Object URL을 revoke함. unmount, runId 변경, socket close에서도 마지막 URL과 socket을 정리함.

### Transport adapters

browser transport를 얇은 adapter로 감싸 test에서 fake를 주입할 수 있게 함.

- SSE adapter: fetch stream parsing, `Last-Event-ID` header, reconnect cursor, terminal abort
- WebSocket adapter: JSON control과 binary frame 분기
- HTTP client: JSON error envelope을 `AppError`로 변환

component는 native `EventSource`나 `WebSocket`을 직접 생성하지 않음.

## 7. Backend integration

### 개발

Vite server는 `10003`을 사용함.

```text
/runs/**   → http://127.0.0.1:20003  (HTTP, SSE, WebSocket)
/reports/**→ http://127.0.0.1:20003
/health    → http://127.0.0.1:20003
```

WebSocket proxy를 활성화하고 SSE buffering을 추가하지 않음.

### build

FastAPI는 frontend를 `/app` 아래에서 제공함. `frontend/dist`가 없는 개발·test 환경에서는 API-only로 정상 기동함.

- Vite build base는 `/app/`
- `/app/assets`는 `frontend/dist/assets` 정적 mount
- `/app/{path:path}`는 존재하는 root 정적 파일 또는 `index.html` fallback
- `/`는 `/app/`로 redirect
- `/runs`, `/reports`, `/health`, `/openapi.json`, `/docs`는 기존 backend API 그대로 유지
- frontend는 relative URL만 사용
- CORS는 추가하지 않음

## 8. Event projection

사용자에게 raw log prefix를 표시하지 않음. event를 다음 label로 변환함.

| Event | UI 표현 |
|---|---|
| `queued` | 탐색 대기 중 |
| `running` | 탐색 시작 |
| `browser_ready` | 브라우저 준비 완료 |
| `progress:candidate_search` | 후보 검색 |
| `progress:place_detail` | 장소 정보 확인 |
| `progress:security_check` | 보안 확인 대기 |
| `progress:photo_analysis` | 사진 분위기 분석 |
| `progress:review_analysis` | 리뷰 분석 |
| `progress:scoring` | 소개팅 적합도 계산 |
| `place_result` | 장소 결과 card 추가 |
| `browser_closed` | 브라우저 정리 완료 |
| `report_saved` | 리포트 저장 완료 |
| `completed` | 탐색 완료 |
| `failed` | 탐색 실패 |

message는 backend가 제공한 공개 문구만 text로 렌더링하며 HTML로 삽입하지 않음.

## 9. Report presentation

### Summary

- 검색 위치·키워드
- 사진·리뷰 비중
- 생성 시각
- analyzed, not matched, failed 개수

### Analyzed place card

- 장소명, category, address
- final score
- photo score·reason
- review score·reason
- 현재 기준을 만족했다는 상태 label

### Non-matched and failed

- 기본적으로 접힌 section
- 장소명과 mismatch/failure reason
- score가 없을 수 있음을 명시적으로 처리

## 10. Error and recovery

- `422`: form/filter field 오류
- `404 run_not_found`: 실행을 찾을 수 없음 + 새 탐색 CTA
- `404 report_not_found`: 저장 리포트를 찾을 수 없음 + 목록 CTA
- `409 report_not_ready`: 진행 화면 유지
- `409 report_unavailable`: 실패 안내
- `500 report_corrupt/conflict/unavailable`: 저장 리포트 문제 안내
- SSE disconnected: 마지막 sequence 표시와 자동 재연결 상태
- SSE replay reset: snapshot으로 projection 재구성
- WebSocket `4404`: 실행 없음
- WebSocket `4409`: terminal 실행이라 영상 사용 불가
- WebSocket `1011` 또는 `error`: 영상 대체 surface만 표시

영상 실패는 run/report UI를 실패시키지 않음. HTTP report를 최종 source of truth로 사용함.

## 11. Accessibility

- 모든 input에 visible label 연결
- field 오류는 `aria-describedby`
- 실행 단계 갱신은 과도한 낭독을 막는 `aria-live="polite"`
- 상태는 색상 + icon + text로 표현
- keyboard focus ring 유지
- `prefers-reduced-motion`에서 transition 최소화
- 1024px 이상에서 200% zoom 핵심 흐름 사용 가능

## 12. Testing strategy

### Unit and component

- Vitest
- React Testing Library
- MSW for HTTP
- injected fake SSE/WebSocket adapters

필수 test:

- form defaults, weight complement, validation, submit routing
- API error envelope mapping
- run event sequence dedupe
- replay reset + snapshot replacement
- terminal close and report refetch
- WebSocket waiting/ready/ended/error
- Object URL revoke on replacement and unmount
- report result grouping and score sorting
- report filter URL synchronization and cursor load-more
- 404/409/500 recovery surfaces

### Browser E2E

Playwright desktop viewport에서 다음을 검증함.

1. 새 탐색 form submit
2. `/app/runs/:runId` 이동
3. SSE progress와 place card 표시
4. WebSocket JPEG 표시
5. terminal 후 final report 표시
6. 저장 목록에서 detail 이동

E2E backend는 deterministic fixture server를 기본으로 사용하고, 실제 Naver/OpenAI live run은 기존 수동 통합 검증으로 분리함.

### Required commands

```bash
npm run typecheck
npm test -- --run
npm run build
npm run e2e
```

backend 전체 unittest도 함께 통과해야 함.

## 13. Security and privacy

- backend의 공개 event와 report field만 표시
- user/backend text는 React text node로 렌더링
- `dangerouslySetInnerHTML` 사용 금지
- local API key를 frontend env나 bundle에 넣지 않음
- browser JPEG Object URL을 수명 종료 시 revoke
- local-only 경계를 문서화하며 외부 host bind를 기본 제공하지 않음

## 14. Acceptance criteria

- `10003` Vite에서 `20003` FastAPI의 HTTP·SSE·WebSocket 사용 가능
- frontend route는 `/app/...`, backend API route는 기존 `/runs`, `/reports`로 충돌 없이 공존
- 1440px에서 승인된 Cinematic Split과 같은 정보 hierarchy 제공
- 1024px에서 browser와 rail을 동시에 사용할 수 있음
- 새 탐색부터 terminal report까지 새로고침 가능한 route로 연결
- SSE resume/reset/terminal 계약 준수
- WebSocket binary JPEG와 control message 처리
- 저장 report filter·pagination·detail 동작
- 영상 장애가 실행·report 확인을 차단하지 않음
- frontend typecheck, unit, build, E2E와 backend unittest 통과
