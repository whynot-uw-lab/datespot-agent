# 1-3 사진 비전 분석 PoC

1단계(기술 리스크 검증)의 세 번째 세부 단계. 네이버지도에서 추출한 내부 사진을 Claude 비전 모델에 넣어 소개팅 장소 분위기 점수와 근거가 쓸만한지 확인한다.

## 목표 흐름

```
1-2 결과 JSON 로드
  → 첫 번째 장소의 내부 사진 URL 선택
  → Claude Messages API에 image URL block 전달
  → 사진 점수 / 근거 / 긍정·부정 시각 단서 JSON 파싱
  → output/photo_vision_result.json 저장
```

## 실행

```bash
uv run python poc/1-3-photo-vision/analyze_photos.py
```

옵션:

```bash
uv run python poc/1-3-photo-vision/analyze_photos.py \
  --place-index 0 \
  --max-photos 3 \
  --model claude-opus-4-8
```

입력 구성만 확인:

```bash
uv run python poc/1-3-photo-vision/analyze_photos.py --dry-run
```

## 입력

- 기본 입력: `poc/1-2-naver-map-flow/output/naver_map_flow_result.json`
- 기본 대상: 첫 번째 장소
- 기본 사진 수: 최대 3장

## 출력

- `output/photo_vision_result.json`
- 주요 필드:
  - `ok`
  - `model`
  - `place`
  - `photoUrls`
  - `analysis.photoScore`
  - `analysis.summary`
  - `analysis.positiveSignals`
  - `analysis.negativeSignals`
  - `errors`

## 확인 결과

- 입력 로딩, 이미지 URL message block 구성, JSON 응답 파싱 테스트 완료.
- 현재 실제 API 호출은 `invalid x-api-key`로 실패.
- 1-3 완료 처리는 유효한 `ANTHROPIC_API_KEY`로 실제 응답 JSON을 받은 뒤 진행.

## 완료 기준

- `uv run python poc/1-3-photo-vision/analyze_photos.py` exit code `0`
- `output/photo_vision_result.json`의 `ok=true`
- `analysis.photoScore`가 0~10 범위
- `summary`, `positiveSignals`, `negativeSignals`, `confidence` 포함
