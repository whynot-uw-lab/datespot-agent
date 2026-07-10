# 1-1 개발 환경 구축

1단계(기술 리스크 검증)의 첫 세부 단계. 개발 환경을 세팅하고 정상 동작을 검증한다.

> 상세한 설치·실행·트러블슈팅은 [GUIDE.md](GUIDE.md) 참고.

## 목표

- uv 프로젝트 + src 레이아웃 패키지
- 핵심 의존성 설치 (Playwright / OpenAI / Anthropic / LangGraph / pydantic)
- 설정 모델(`datespot_agent.config`) 로드 확인
- Playwright Chromium 헤드리스 실행 확인

## 실행

```bash
uv run python poc/1-1-env/smoke_test.py
```

## 산출물

- `smoke_test.py` — 환경 스모크 테스트
- `output/smoke_result.json` — 마지막 실행 결과 (자동 생성)

## 상태

✅ 완료 — 전체 검증 통과
