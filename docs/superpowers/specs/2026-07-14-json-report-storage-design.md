# JSON 리포트 저장 설계

**작성일:** 2026-07-14

**대상:** README 로드맵 2-7 `JSON 리포트 출력`

**기준 문서:**

- `docs/superpowers/specs/2026-07-12-langgraph-agent-core-design.md`
- `docs/superpowers/specs/2026-07-14-langgraph-execution-loop-design.md`

## 1. 목표

`GraphRunService`가 반환하는 메모리상 `RunReport`를 재사용 가능한 파일 저장 계층을
통해 JSON 파일로 영속화한다.

- `analyzed`, `not_matched`, `failed` 결과를 한 파일에 보존
- UTC 날짜별 디렉터리와 `run_id` 기반 파일명 사용
- 중간 상태가 노출되지 않는 원자적 파일 저장
- 동일 리포트 재저장을 허용하는 멱등 계약
- 저장 실패를 호출자에게 명시적으로 전달
- 수동 실행기와 이후 FastAPI 계층에서 같은 저장 기능 재사용

## 2. 확정 결정

### 2.1 저장 책임 분리

`GraphRunService`는 현재처럼 `RunReport` 생성과 반환만 담당한다. 파일 시스템 접근은
별도 `JsonReportStore`가 담당하며, 실행기나 API 같은 호출자가 명시적으로 저장을
요청한다.

이 경계는 다음 효과가 있다.

- 그래프 테스트가 파일 시스템에 의존하지 않음
- 파일 저장 없이 메모리 결과만 사용하는 호출자 지원
- 수동 실행기와 향후 FastAPI가 같은 저장 구현 재사용
- 분석 결과 상태와 저장 성공 여부를 분리

### 2.2 기본 경로

기본 저장 루트는 프로젝트의 `reports/`다. `RunReport.created_at`을 UTC로 정규화한
날짜를 사용해 다음 경로를 만든다.

```text
reports/YYYY/MM/DD/<run_id>.json
```

예시:

```text
reports/2026/07/14/run_20260714_145020_b91196f7.json
```

날짜는 실행한 컴퓨터의 로컬 시간대가 아니라 `RunReport.created_at`의 UTC 날짜를
기준으로 한다. 저장 루트는 생성자 인자로 바꿀 수 있어 테스트와 배포 환경을
분리한다.

### 2.3 저장 실패 의미

저장 실패는 `ReportStorageError`로 호출자에게 전달한다. 이미 생성된
`RunReport.status`는 변경하지 않는다.

- 그래프 실행 결과: `RunReport.status`
- 영속화 결과: `JsonReportStore.save()`의 성공 또는 예외

수동 실행기는 저장 실패 시 오류를 stderr에 기록하고 0이 아닌 종료 코드를
반환한다. 저장 실패 전용 종료 코드는 `3`으로 고정한다. 향후 FastAPI는 같은
예외를 서버 오류 응답으로 변환할 수 있다.

## 3. 범위

### 3.1 포함

- JSON 파일 저장 계층과 공개 export
- 날짜별 경로 생성
- JSON 직렬화 규격
- 원자적 저장
- 동일 내용 재저장과 충돌 처리
- 수동 라이브 실행기 연동
- README 실행 방법과 로드맵 갱신
- 외부 호출 없는 단위·통합 테스트

### 3.2 제외

- 데이터베이스 저장
- 리포트 목록 조회와 검색 API
- 보존 기간과 자동 삭제
- 압축, 암호화, 클라우드 업로드
- WebSocket/SSE 전송
- 브라우저·OpenAI 실패 재시도 정책
- 동일 `run_id`를 여러 프로세스가 동시에 저장하는 시나리오

## 4. 구성 요소

새 패키지는 다음 구조를 사용한다.

```text
src/datespot_agent/reporting/
  __init__.py
  errors.py
  json_store.py
```

### 4.1 `JsonReportStore`

공개 계약:

```python
class JsonReportStore:
    def __init__(self, root: Path = Path("reports")) -> None: ...
    def save(self, report: RunReport) -> Path: ...
```

책임:

- 안전한 `run_id` 검증
- UTC 날짜 기반 대상 경로 계산
- `RunReport` 직렬화
- 상위 디렉터리 생성
- 기존 파일의 멱등성·충돌 검사
- 임시 파일과 원자적 교체를 통한 저장
- 저장 경로 반환

`run_id`는 파일명으로 직접 사용하므로 영문자, 숫자, `_`, `-`만 허용한다. 경로
구분자나 상위 경로 이동 문자가 포함되면 `ReportStorageError`를 발생시킨다.

### 4.2 `ReportStorageError`

리포트 저장 계층의 공개 예외다. 오류 메시지에는 `run_id`와 대상 경로를 포함하고,
원본 `OSError`는 예외 원인으로 유지한다. 호출자는 운영체제별 예외를 직접 해석하지
않고 이 타입 하나를 처리한다.

## 5. JSON 계약

저장 payload는 다음 방식으로 만든다.

```python
report.model_dump(mode="json", by_alias=True)
```

직렬화 규격:

- 필드명: lower camelCase
- 문자 인코딩: UTF-8
- 한글: `ensure_ascii=False`
- 들여쓰기: 공백 2칸
- 파일 끝: 개행 1개
- datetime: 기존 Pydantic JSON 변환 결과 사용

추가 envelope나 저장 전용 필드는 넣지 않는다. 파일을 다시
`RunReport.model_validate_json()`으로 검증할 수 있어야 한다.

## 6. 저장 알고리즘

1. `run_id`가 안전한 파일명인지 검증
2. `created_at`의 UTC 날짜로 대상 디렉터리 계산
3. JSON 문자열을 한 번 직렬화하고 UTF-8 bytes로 변환
4. 대상 디렉터리를 `parents=True`로 생성
5. 대상 파일이 있으면 현재 bytes와 비교
   - 동일: 기존 경로를 반환
   - 다름: `ReportStorageError` 발생
6. 같은 디렉터리에 임시 파일 생성
7. 전체 bytes 기록 후 flush와 `fsync` 수행
8. `os.replace()`로 대상 파일에 원자적으로 반영
9. 성공 여부와 무관하게 남은 임시 파일 정리
10. 대상 `Path` 반환

`run_id`는 실행마다 고유하므로 서로 다른 실행 간 정상적인 덮어쓰기는 발생하지
않는다. 동일 ID에 다른 내용이 들어오면 데이터 손상 가능성이 있으므로 덮어쓰지
않는다.

## 7. 호출 흐름

```text
RunConfig
  -> GraphRunService.run()
  -> RunReport
  -> JsonReportStore.save()
  -> reports/YYYY/MM/DD/<run_id>.json
```

`tests/run_graph_live.py`는 다음처럼 바뀐다.

- `OUTPUT_PATH` 제거
- `REPORTS_ROOT = Path("reports")` 추가
- `JsonReportStore(REPORTS_ROOT)` 생성
- 그래프 실행 후 `save(report)` 호출
- stdout에는 저장된 파일 경로 출력
- 저장 실패 시 stderr에 사유 출력 후 별도 비정상 종료 코드 반환

향후 FastAPI는 같은 순서를 사용하되 HTTP 응답에는 `RunReport` 또는 저장된 리포트
식별자를 반환한다.

## 8. 오류 처리

다음 오류는 `ReportStorageError`로 통일한다.

- 안전하지 않은 `run_id`
- 디렉터리 생성 실패
- 기존 파일 읽기 실패
- 동일 `run_id`의 다른 내용 충돌
- 임시 파일 생성·쓰기·동기화 실패
- 원자적 교체 실패

저장 실패는 그래프를 다시 실행하지 않는다. 이미 완료된 브라우저 탐색과 OpenAI
호출을 반복하면 비용과 결과가 달라질 수 있기 때문이다.

## 9. 테스트 전략

### 9.1 `JsonReportStore` 단위 테스트

- UTC `created_at`에서 날짜별 경로 생성
- 다른 시간대 입력도 UTC 날짜로 변환
- camelCase, 한글, 들여쓰기, 마지막 개행 확인
- 저장 파일을 `RunReport`로 역직렬화
- 상위 디렉터리 자동 생성
- 동일 내용 재저장 시 같은 경로 반환
- 동일 `run_id`의 다른 내용 저장 거부
- 안전하지 않은 `run_id` 거부
- 파일 시스템 오류를 `ReportStorageError`로 래핑
- 성공·실패 후 임시 파일 잔존 여부 확인

### 9.2 수동 실행기 계약 테스트

- 기본 `REPORTS_ROOT`로 저장소 생성
- 저장 성공 시 경로 출력
- 저장 실패 시 비정상 종료 코드와 stderr 출력
- `GraphRunService`의 report 상태를 저장 실패 때문에 변경하지 않음

### 9.3 전체 회귀

기존 외부 호출 없는 테스트 전체를 실행한다. 실제 네이버지도와 OpenAI 호출은
자동 테스트에 포함하지 않으며, 구현 완료 후 `max_places=1` 수동 라이브 실행으로
파일 생성과 역직렬화를 한 번 확인한다.

## 10. 문서 변경

- README 로드맵에서 실패 복구 고도화 항목 제거
- README 수동 실행 설명을 날짜별 자동 저장 방식으로 갱신
- 기존 현행 설계의 실패 복구 미래 확장 참조 제거
- 구현 완료 후 README의 2-7 체크박스를 완료로 변경

## 11. 완료 조건

- `JsonReportStore`가 명시된 경로와 JSON 규격으로 저장
- 저장 중 부분 파일이 최종 경로에 노출되지 않음
- 동일 내용 재저장은 성공하고 다른 내용 충돌은 실패
- 수동 실행기가 항상 저장소를 통해 report를 저장
- 저장 실패가 명시적인 오류와 비정상 종료 코드로 전달
- 파일을 `RunReport`로 다시 검증 가능
- 전체 자동 테스트 통과
- 라이브 실행으로 실제 report 파일 생성 확인
