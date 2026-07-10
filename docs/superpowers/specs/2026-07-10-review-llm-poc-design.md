# Review LLM PoC Design

## Goal

1-4 리뷰 LLM 분석 PoC는 1-2 네이버지도 탐색 결과의 방문자 리뷰 텍스트를 OpenAI 저가 모델로 분석해, 소개팅 장소 판단에 쓸 수 있는 리뷰 점수와 근거가 나오는지 검증한다.

## Scope

- 대상 입력은 `poc/1-2-naver-map-flow/output/naver_map_flow_result.json`이다.
- 기본 분석 대상은 첫 번째 장소(`place_index=0`)다.
- 기본 리뷰 수는 최대 20개다.
- 기본 모델은 기존 1-3과 같은 `gpt-5.4-nano`다.
- 1-4는 리뷰 분석만 다룬다. 사진 점수와 최종 가중합은 이후 에이전트 코어 단계에서 다룬다.

## Data Flow

1. 1-2 결과 JSON을 읽는다.
2. 선택한 장소의 `reviews` 배열을 읽는다.
3. 리뷰 텍스트가 비어 있으면 실패 JSON을 저장한다.
4. 최대 20개 리뷰를 OpenAI Responses API에 전달한다.
5. 모델 응답 JSON을 파싱하고 검증한다.
6. `poc/1-4-review-llm/output/review_llm_result.json`에 결과를 저장한다.

## Output Schema

결과 파일은 다음 주요 필드를 가진다.

- `ok`: 성공 여부
- `model`: 사용 모델
- `criteria`: 리뷰 평가 기준
- `inputPath`: 입력 파일 경로
- `place`: 장소 이름, ID, 카테고리, 주소
- `reviewCount`: 분석에 사용한 리뷰 수
- `reviews`: 분석에 사용한 리뷰 텍스트
- `analysis.reviewScore`: 0~10점, 소수점 1자리 허용
- `analysis.summary`: 리뷰 기반 요약
- `analysis.positiveSignals`: 소개팅에 유리한 단서
- `analysis.negativeSignals`: 불리하거나 불확실한 단서
- `analysis.dateFitSignals`: 데이트/소개팅 적합성 단서
- `analysis.concerns`: 대기, 혼잡, 소음, 가격, 서비스 등 우려
- `analysis.confidence`: `low|medium|high`
- `rawText`: 모델 원문 응답
- `errors`: 실패 사유 목록

## Scoring Criteria

리뷰 점수는 소개팅 적합성 관점으로 산출한다.

- 긍정: 조용함, 대화하기 좋음, 친절함, 깔끔함, 데이트/연인 방문, 분위기 좋음, 예약/입장 안정성
- 부정: 긴 대기, 혼잡함, 시끄러움, 불친절, 좌석 불편, 가격 대비 불만, 데이트와 무관한 단서만 많음
- 불확실: 음식 맛 칭찬 위주이고 공간/대화/분위기 단서가 적은 경우

## Error Handling

- 입력 파일이 없거나 JSON 구조가 맞지 않으면 `ok=false`와 `input:` 오류를 기록한다.
- 선택 장소에 리뷰가 없으면 `ok=false`와 리뷰 없음 오류를 기록한다.
- `OPENAI_API_KEY`가 비어 있으면 `ok=false`를 기록하고 API 호출을 생략한다.
- 모델 응답이 JSON이 아니거나 필수 필드가 없으면 `ok=false`와 파싱/검증 오류를 기록한다.
- 성공 기준은 exit code `0`, `ok=true`, `reviewScore` 0~10 범위, 필수 필드 존재다.

## Testing

- 샘플 1-2 결과에서 장소와 리뷰를 선택하고 최대 리뷰 수를 제한하는 테스트를 둔다.
- OpenAI Responses API 입력 포맷을 테스트한다.
- strict JSON과 fenced JSON 응답 파싱을 테스트한다.
- 점수 범위와 필수 필드 검증 실패를 테스트한다.
- API 호출은 mock으로 검증하고 실제 호출은 PoC 스크립트 실행으로 확인한다.
