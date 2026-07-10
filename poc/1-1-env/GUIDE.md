# 1-1 개발 환경 구축 — 실행 및 가이드

이 문서는 1-1 단계를 처음부터 재현하고 검증하는 방법을 단계별로 설명한다.
(요약은 [README.md](README.md) 참고)

---

## 사전 요구사항

| 항목 | 버전/비고 |
|------|-----------|
| macOS | Apple Silicon 기준 (arm64) |
| Python | 3.13+ (`.python-version` 로 고정) |
| uv | 패키지 매니저 — 없으면 `brew install uv` |
| Homebrew | uv 설치에 사용 |

확인:

```bash
python3 --version   # Python 3.13.x
uv --version        # uv 0.11.x
```

---

## 설치 절차 (처음부터 재현)

프로젝트 루트(`datespot-agent/`)에서 실행한다.

### 1. uv 설치 (최초 1회)

```bash
brew install uv
```

### 2. 의존성 설치

`pyproject.toml` / `uv.lock` 기준으로 가상환경(`.venv`)을 만들고 패키지를 설치한다.

```bash
uv sync
```

설치되는 핵심 패키지:

- `playwright` — 브라우저 자동화
- `openai` — 사진 비전 분석
- `anthropic` — 향후 리뷰 LLM 분석 등 필요 시 사용
- `langgraph`, `langchain-core` — 에이전트 그래프
- `pydantic`, `pydantic-settings` — 설정 모델
- `python-dotenv` — .env 로드

### 3. Playwright 브라우저 설치

Chromium 바이너리를 내려받는다. (`uv sync` 로는 설치되지 않음 — 별도 필요)

```bash
uv run playwright install chromium
```

### 4. 환경변수 설정

```bash
cp .env.example .env
```

`.env` 를 열어 값을 채운다.

| 키 | 설명 | 예시 |
|----|------|------|
| `OPENAI_API_KEY` | OpenAI API 키 | `sk-...` |
| `DATESPOT_MODEL` | 사용할 비전 지원 모델 | `gpt-5.4-nano` |
| `DATESPOT_HEADLESS` | 브라우저 헤드리스 여부 | `true` / `false` |

> 1-1 스모크 테스트는 API 키 없이도 통과한다(실호출 없음).
> 키는 1-3(사진 비전) 단계부터 필요하다.

---

## 실행

```bash
uv run python poc/1-1-env/smoke_test.py
```

### 검증 항목

1. **imports** — 핵심 패키지 import 가능 여부
2. **config** — `datespot_agent.config` 의 `Settings` / `SearchConfig` 로드 및 기본값(가중치 합=1.0, max_places=30) 확인
3. **playwright** — Chromium 헤드리스 기동 + 페이지 렌더링

### 기대 출력

```
=== 1-1 환경 스모크 테스트 ===
  [OK] 핵심 패키지 import
  [OK] 설정 로드 (model=gpt-5.4-nano, headless=True, max_places=30, weights=0.5/0.5)
  [OK] Playwright Chromium 헤드리스 실행
=== 전체 통과 ✅ (결과: .../poc/1-1-env/output/smoke_result.json) ===
```

### 결과 파일

`output/smoke_result.json` 에 실행 시각과 항목별 결과가 저장된다.

```json
{
  "ran_at": "2026-07-09T12:28:35+00:00",
  "passed": true,
  "checks": [
    { "name": "imports", "ok": true, "detail": "핵심 패키지 import" },
    { "name": "config", "ok": true, "detail": "설정 로드 (...)" },
    { "name": "playwright", "ok": true, "detail": "Playwright Chromium 헤드리스 실행" }
  ]
}
```

이 파일은 `.gitignore` 로 git 추적에서 제외된다(디렉토리는 유지).

---

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| `ModuleNotFoundError: datespot_agent` | 패키지 미설치 | `uv sync` 재실행 |
| `[FAIL] playwright: ... Executable doesn't exist` | Chromium 미설치 | `uv run playwright install chromium` |
| `pip install` 시 `externally-managed-environment` | 시스템 파이썬 보호(PEP 668) | 시스템 pip 대신 `uv` 사용 |
| `.env` 값이 반영 안 됨 | 실행 위치가 루트가 아님 | 프로젝트 루트에서 실행 |

---

## 완료 기준 (Definition of Done)

- [x] `uv sync` 성공
- [x] `uv run playwright install chromium` 성공
- [x] `smoke_test.py` 전체 통과 (`passed: true`)
- [x] `output/smoke_result.json` 생성 확인

**상태: ✅ 완료** — 다음 단계는 `poc/1-2-*`(네이버지도 탐색 PoC).
