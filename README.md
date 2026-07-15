# 소개팅 장소 자동 탐색 에이전트 (datespot-agent)

사용자 취향에 맞는 소개팅 장소를 네이버지도에서 자동으로 탐색하고, 후보 장소를 점수화해 리포트로 정리해주는 AI 에이전트.

> 아이디어 원문은 [idea.md](idea.md) 참고.

## 한 줄 소개

사용자가 조건을 입력하면 에이전트가 네이버지도를 직접 탐색하며 장소를 분석하고, 사진·리뷰를 기반으로 소개팅 적합도를 점수화해 실시간 리포트로 정리한다.

## 기술 스택

- **client**: React + TypeScript + Vite
- **backend/agent**: Python + FastAPI
- **agent framework**: LangGraph (+ LangChain)
- **browser automation**: Playwright
- **browser streaming**: Playwright CDP 기반 스트리밍
- **실시간 채널**: WebSocket / SSE

## 시작하기

```bash
# 1. 의존성 설치 (uv 필요: brew install uv)
uv sync

# 2. Playwright 브라우저 설치
uv run playwright install chromium

# 3. 환경변수 설정
cp .env.example .env   # OPENAI_API_KEY 등 채우기

# 4. 환경 검증
uv run python poc/1-1-env/smoke_test.py
```

### 프로젝트 구조

```
src/datespot_agent/
  analysis/           # 사진·리뷰 분석 Agent와 점수 계산
  browser/            # 네이버지도 탐색과 브라우저 세션 관리
  graph/              # LangGraph 실행 루프
  reporting/          # 실행 리포트 JSON 저장
  config.py           # 환경 설정
  models.py           # 실행 설정·상태·리포트 모델
frontend/
  src/                # React 화면·API client·SSE/WebSocket adapter
  e2e/                # 고정 fixture 기반 Playwright E2E
tests/
  test_*.py           # 외부 호출 없는 자동 테스트
  run_graph_live.py   # 네이버지도·OpenAI 수동 통합 실행기
poc/                  # 1단계 리스크 검증 — 단계별 스크립트·결과
reports/              # 생성된 리포트 (gitignore)
docs/                 # 설계·구현 계획 문서
```

> 1단계의 각 세부 단계(1-1, 1-2, …)는 `poc/<단계>/` 디렉토리로 독립 관리한다.
> 스크립트·설명(README)·실행 결과(`output/`)를 그 안에 모은다.

### LangGraph 실행 루프 수동 확인

1. `tests/run_graph_live.py` 상단의 검색 지역·키워드·최대 장소 수·가중치·평가 기준을 수정한다.
2. 필요하면 `MODEL_OVERRIDE`, `CHROME_EXECUTABLE_PATH`, `HEADED`, `REPORTS_ROOT`를 설정한다.
3. `.env`에 `OPENAI_API_KEY`가 설정됐는지 확인한 뒤 실행한다.
   수동 통합 실행기는 상속된 셸 환경변수보다 프로젝트 `.env`의 키를 우선 사용한다.

```bash
uv run python tests/run_graph_live.py
```

기본값은 macOS Google Chrome을 전용 프로필과 non-zero loopback CDP 포트로 자동 실행한
뒤 Playwright가 연결한다. Chrome 설치 위치가 다르면 `CHROME_EXECUTABLE_PATH`를 수정한다.
네이버 보안 확인 화면이 표시되면
`artifacts/browser/<run_id>/`에 스크린샷과 HTML을 남기고, 사용자가 브라우저에서
확인을 완료할 때까지 10초 간격으로 대기한 뒤 작업을 재개한다.

실행 리포트는 `REPORTS_ROOT/YYYY/MM/DD/<run_id>.json`에 자동 저장된다.
날짜는 리포트 생성 시각의 UTC 날짜 기준이며, 저장된 경로를 stdout에 출력한다.
종료 코드는 실행 완료 `0`, 그래프 실패 `2`, 리포트 저장 실패 `3`이다.
이 스크립트는 실제 네이버지도와 OpenAI API를 호출하므로 자동 테스트에는 포함하지 않는다.

### FastAPI 실행 API

`.env`에 `OPENAI_API_KEY`를 설정한 뒤 로컬 loopback에서 실행한다.

```bash
uv run uvicorn datespot_agent.api.app:app --host 127.0.0.1 --port 20003
```

개발용 frontend는 별도 terminal에서 실행한다.

```bash
cd frontend
npm install
npm run dev
```

브라우저에서 `http://127.0.0.1:10003/app/`을 연다. Vite가 HTTP·SSE·WebSocket을
`127.0.0.1:20003`으로 proxy한다.

production build는 FastAPI가 동일 origin의 `/app/`에서 제공한다.

```bash
cd frontend && npm run build && cd ..
uv run uvicorn datespot_agent.api.app:app --host 127.0.0.1 --port 20003
```

제공 엔드포인트:

- `POST /runs`: 탐색 실행 접수 (`202 Accepted`)
- `GET /runs/{run_id}`: queue 및 실행 상태 조회
- `GET /runs/{run_id}/events`: 실행 lifecycle·진행 단계·장소 결과 SSE
- `WS /runs/{run_id}/browser-stream`: 실행 중 Chrome 화면 JPEG stream
- `GET /runs/{run_id}/report`: 저장 완료된 리포트 조회
- `GET /reports`: 저장된 JSON 리포트 목록·검색
- `GET /reports/{run_id}`: 서버 재시작 후에도 가능한 저장 리포트 상세 조회
- `GET /health`: coordinator 상태 조회

실행 접수 예시:

```bash
curl -sS -X POST http://127.0.0.1:20003/runs \
  -H 'content-type: application/json' \
  -d '{"location":"성수역","searchKeyword":"일식","maxPlaces":1}'
```

`POST /runs` 응답의 `runId`로 SSE를 연결한다. canonical SSE event의 `id`는 실행별로
증가하며, 재접속할 때 `Last-Event-ID`를 보내면 그 이후 event를 replay한다. `completed`
또는 `failed` terminal event를 받은 client는 SSE fetch stream을 abort해야 한다.

```bash
curl -N http://127.0.0.1:20003/runs/<run_id>/events \
  -H 'Accept: text/event-stream' \
  -H 'Last-Event-ID: 12'
```

browser stream은 먼저 JSON control message `waiting`, `ready`, `ended`, `error` 중 하나를
보내고, `ready` 이후 화면 frame을 WebSocket binary JPEG message로 전송한다. 첫 viewer가
연결될 때 CDP screencast를 시작하고 마지막 viewer가 나가면 종료한다. 영상은 JPEG quality
70, 최대 1280×720, 매 두 번째 frame 설정이며, viewer의 키보드·마우스 입력 중계는
제공하지 않는다.

저장 리포트 목록은 최신순 cursor pagination을 사용한다. `limit`은 기본 20, 최대 100이며
`status`, `location`, `searchKeyword`, `dateFrom`, `dateTo`, `cursor` query를 지원한다.

```bash
curl -sS 'http://127.0.0.1:20003/reports?location=성수&searchKeyword=일식&limit=20'
curl -sS http://127.0.0.1:20003/reports/<run_id>
```

경로는 환경변수로 변경할 수 있다.

- `DATESPOT_REPORTS_ROOT`: JSON 리포트 루트, 기본 `reports`
- `DATESPOT_DIAGNOSTIC_LOGS_ROOT`: 실행별 JSONL 진단 로그 루트, 기본 `artifacts/logs`
- `DATESPOT_CHROME_EXECUTABLE_PATH`: Google Chrome 실행 파일 경로
- `DATESPOT_BROWSER_USER_DATA_DIR`: API 전용 Chrome 프로필 경로

실행 상태와 SSE event는 프로세스 메모리에만 유지되므로 서버 재시작 시 초기화된다.
SSE replay는 실행당 최근 1,000개, subscriber queue는 128개, 종료 실행 LRU는 100개로
제한된다. 저장 리포트 카탈로그는 `reports/YYYY/MM/DD/*.json`을 source of truth로 삼아
요청마다 O(N) 파일 scan하며 DB나 별도 index를 사용하지 않는다.

서버는 콘솔 로그와 함께 `artifacts/logs/<run_id>.jsonl`에 실행별 진단 로그를 남긴다.
API, 브라우저 재시도, 사진·리뷰 모델 요청, 점수 계산, 리포트 저장, 오류 traceback을
`runId`로 연결한다. API key·인증 정보·전체 prompt·리뷰 원문·사진 URL은 진단 파일에서
마스킹한다.

작업은 단일 프로세스의 FIFO worker 하나가 순차 처리한다. 멀티프로세스 event fan-out,
실행 취소, browser 원격 입력은 제공하지 않는다. 현재 API는 인증·CORS가 없는 로컬
전용이며 외부에 노출하지 않는다. SSE에는 공개 가능한 lifecycle·진행·장소 결과만 담고,
raw prompt·숨겨진 추론·API key·traceback·로컬 내부 경로는 전송하지 않는다.

---

## 개발 로드맵

이 프로젝트의 핵심 난이도는 에이전트 로직보다 **네이버지도 자동화의 안정성**과 **실시간 스트리밍 UX**에 있다. 따라서 리스크가 큰 것부터 검증하는 순서로 단계를 구성한다.

### 0단계: 기획 확정 (~0.5주) ✅ 완료

문서에 없는 결정들을 먼저 확정해 개발이 흔들리지 않게 한다. → [docs/00-planning.md](poc/00-planning.md)

- [x] **점수 체계 정의**: 0~10점, 최종 = 사진 × w + 리뷰 × w (가중치 UI 조정, 기본 50:50)
- [x] **탐색 범위 제한**: 순차 처리(병렬 금지), 최대 10개(설정 가능), 딜레이 + 속도 제한
- [x] **"실시간"의 수준 결정**: CDP 영상 스트리밍

### 1단계: 기술 리스크 검증 (PoC, 1~2주) ✅ 완료

여기서 막히면 아이디어 전체가 바뀔 수 있으므로 가장 먼저 진행한다. 목표는 "예쁜 코드"가 아니라 **"이 방식이 되긴 하는가"** 를 증명하는 것. 세부 단계로 나눠 진행한다.

- [x] **1-1 개발 환경 구축**: uv 프로젝트, 의존성(Playwright/OpenAI/Anthropic/LangGraph), 설정 모델, 스모크 테스트
- [x] **1-2 네이버지도 탐색 PoC**: "역 + 카테고리" 검색 → 목록 → 상세(사진/리뷰) 추출
  - 네이버지도는 iframe·동적로딩·봇 차단이 까다로움 — **진짜 병목 지점**
- [x] **1-3 사진 비전 분석 PoC**: 사진을 OpenAI 최저가 비전 모델에 넣어 "분위기 점수 + 근거"가 쓸만한지 확인
  - `gpt-5.4-nano` 실제 호출 성공, `photoScore=6.2`, `confidence=medium`
- [x] **1-4 리뷰 LLM 분석 PoC**: 리뷰 텍스트로 "점수 + 근거" 산출 확인
  - `gpt-5.4-nano` 실제 호출 성공, `reviewScore=8.3`, `confidence=medium`
- [x] **1-5 CDP 스트리밍 최소 검증**: 브라우저 화면이 프론트에 뜨는지 확인
  - CDP screencast → WebSocket → HTML viewer 경로 성공, 105프레임, 평균 26.28 FPS
- [ ] (플랜B) 자동화가 막힐 경우: 미리 크롤링한 데이터로 데모하는 방식 검토

### 2단계: LangGraph 에이전트 코어 구현 (~3~4주)

설계 초안: [LangGraph + Agent Core Design](docs/superpowers/specs/2026-07-12-langgraph-agent-core-design.md)

실행 루프 설계: [LangGraph 실행 루프 설계](docs/superpowers/specs/2026-07-14-langgraph-execution-loop-design.md)

- [x] **2-1 설계 확정**: LangGraph node 흐름, state, 데이터 모델, 인터페이스 정의
- [x] **2-2 데이터 모델 구현**: RunConfig, CandidatePlace, PlaceDetail, PhotoAnalysis, ReviewAnalysis, PlaceResult, RunReport, GraphState
- [x] **2-3 BrowserService 연동**: 1-2 PoC의 네이버지도 검색/상세 추출 로직을 `BrowserService`로 정리
- [x] **2-4 분석 계층 구현**: 사진 분석, 리뷰 분석, 기준 충족 판정, 점수 계산
- [x] **2-5 LangGraph 실행 루프 구현**: 후보 검색 → 장소 순회 → 분석 → `analyzed`/`not_matched`/`failed` 리포트 반영
- [x] **2-7 JSON 리포트 출력**: 분석/기준 미충족/실패 장소를 UTC 날짜별 JSON 결과로 저장

### 3단계: 백엔드 로직 구현 (1~2주) ✅ 완료

- [x] FastAPI 실행 API: 탐색 설정 입력 → 에이전트 실행
- [x] SSE로 공개 실행 단계·장소 결과·리포트 갱신 실시간 push 및 replay
- [x] CDP 브라우저 스트림을 WebSocket binary JPEG로 중계
- [x] 저장된 JSON 리포트 목록·검색·상세 API

### 4단계: 프론트엔드 (~2주) ✅ 완료

- [x] 취향 설정 폼 (탐색 조건 + 사진·리뷰 평가 기준 + 점수 가중치)
- [x] 진행 화면: 브라우저 스트림 + 공개 실행 단계 로그 + 실시간 리포트
- [x] 최종 리포트 뷰 (점수순 정렬, 기준 미충족 사유 포함)
- [x] 저장 리포트 목록·filter·cursor pagination·상세 화면

### 5단계: 다듬기 (1주+)

- [ ] 에러 복구 고도화 (navigation recovery, 재시도 정책)
- [ ] 비용/속도 최적화, 캐싱

---

## 핵심 원칙

1. **1단계 PoC를 절대 건너뛰지 않는다.** 네이버지도 자동화가 프로젝트 성패를 가른다. 막히면 즉시 플랜B로 선회한다.
2. **MVP는 실시간 스트리밍을 빼고 시작한다.** "실행 → 끝나면 리포트"를 먼저 완성하고, 실시간 UX는 이후에 얹어 리스크를 분산한다.
3. 워크플로가 정형화된 루프이므로 **LangGraph 중심**으로 구성한다.

---

## 사용자 취향 설정

### 점수 조건 (분석·점수화 기준)

- 내부 사진: 어둡고 차분한 분위기
- 좌석: 테이블 간격이 넓고 대화하기 좋은 구조
- 리뷰: 깔끔함, 조용함, 대화하기 좋음 등의 표현

## 리포트 포함 내용

- 장소명 / 위치 / 카테고리 / 최종 점수
- 사진 기반 점수와 판단 근거 + 대표 내부 사진
- 리뷰 기반 점수와 판단 근거 + 주요 리뷰 요약
- 사용자 기준을 충족하지 못한 장소와 미충족 사유
- 분석 처리에 실패한 장소와 실패 사유

## 에이전트 구조

- **메인 에이전트**: 전체 탐색 흐름 관리. 검색 결과를 순회하고 각 장소 분석을 서브 에이전트에 위임, 결과를 리포트에 반영.
- **서브 에이전트**: 개별 장소의 사진과 리뷰를 분석해 점수와 기준 충족 여부를 산출.
