# 1-4 리뷰 LLM 분석 PoC

1단계(기술 리스크 검증)의 네 번째 세부 단계. 네이버지도에서 추출한 방문자 리뷰를 OpenAI 저가 모델에 넣어 소개팅 장소 리뷰 점수와 근거가 쓸만한지 확인한다.

## 목표 흐름

```text
1-2 결과 JSON 로드
  → 첫 번째 장소의 리뷰 최대 20개 선택
  → OpenAI Responses API에 리뷰 텍스트 전달
  → 리뷰 점수 / 요약 / 긍정·부정·데이트 적합 단서 JSON 파싱
  → output/review_llm_result.json 저장
```

## 실행

```bash
env -u OPENAI_API_KEY uv run python poc/1-4-review-llm/analyze_reviews.py
```

입력 구성만 확인:

```bash
uv run python poc/1-4-review-llm/analyze_reviews.py --dry-run
```

## 입력

- 기본 입력: `poc/1-2-naver-map-flow/output/naver_map_flow_result.json`
- 기본 대상: 첫 번째 장소
- 기본 리뷰 수: 최대 20개
- 기본 모델: `gpt-5.4-nano`

## 출력

- `output/review_llm_result.json`
- 주요 필드:
  - `ok`
  - `model`
  - `place`
  - `reviewCount`
  - `analysis.reviewScore`
  - `analysis.summary`
  - `analysis.positiveSignals`
  - `analysis.negativeSignals`
  - `analysis.dateFitSignals`
  - `analysis.concerns`
  - `analysis.confidence`
  - `errors`

## 완료 기준

- `uv run python poc/1-4-review-llm/analyze_reviews.py` exit code `0`
- `output/review_llm_result.json`의 `ok=true`
- `analysis.reviewScore`가 0~10 범위
- `summary`, `positiveSignals`, `negativeSignals`, `dateFitSignals`, `concerns`, `confidence` 포함

## 확인 결과

- 입력 로딩, OpenAI text message 구성, JSON 응답 파싱 테스트 완료.
- `gpt-5.4-nano` 실제 API 호출 성공.
- 결과: `reviewScore=8.3`, `confidence=medium`.
- 현재 Codex 셸에 기존 `OPENAI_API_KEY`가 있으면 `.env`보다 우선될 수 있으므로, 로컬 재현 시 필요하면 `env -u OPENAI_API_KEY uv run python poc/1-4-review-llm/analyze_reviews.py`로 실행한다.
