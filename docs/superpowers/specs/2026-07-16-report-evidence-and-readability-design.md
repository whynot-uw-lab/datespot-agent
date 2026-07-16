# DateSpot 리포트 증거자료·가독성 개선 설계

**작성일:** 2026-07-16

**상태:** 사용자 승인
**대상:** 신규 생성 리포트의 백엔드 저장 모델, API 계약, 웹 리포트 상세 화면

## 1. 목표

현재 리포트는 사진·리뷰 점수와 긴 분석문만 제공한다. 사용자는 결론을 빠르게 이해하기 어렵고, 분석에 사용된 실제 사진과 리뷰를 최종 리포트에서 다시 검증할 수 없다.

이번 변경의 목표는 다음과 같다.

1. 장소별 핵심 결론, 좋은 점, 고려할 점을 먼저 보여준다.
2. 분석에 실제 사용된 내부 사진을 최종 리포트에 표시한다.
3. 이번 실행에서 추출한 리뷰 원문을 누락 없이 내부 스크롤 영역에 표시한다.
4. 장소별 네이버지도 원문 링크를 제공한다.
5. 기존의 상세 분석 근거는 제거하지 않고 접힌 상세 영역으로 이동한다.
6. 과거 리포트와 API 호환성을 유지한다.

## 2. 현재 상태와 원인

`PlaceDetail`에는 다음 원본 자료가 존재한다.

- `photoUrls`: 최대 5개
- `reviews`: 최대 50개
- `reviewCount`: 네이버지도에서 확인한 전체 리뷰 수

하지만 점수 계산 후 생성되는 `PlaceResult`에는 점수와 분석문만 복사된다. `currentPlaceDetail`은 장소 처리가 끝날 때 제거되므로 최종 `RunReport`와 JSON 저장 파일에는 원본 사진 URL과 리뷰가 남지 않는다.

따라서 프론트엔드 UI만 변경해서는 요구사항을 충족할 수 없다. 백엔드 결과 모델과 저장 리포트 계약을 함께 확장해야 한다.

## 3. 검토한 접근안

### A. 모든 정보를 항상 펼쳐서 표시

- 장점: 추가 클릭 없이 모든 내용을 확인 가능
- 단점: 장소 5개와 리뷰 최대 250건이 한 페이지에 노출되어 길이와 정보 밀도가 과도함
- 결론: 채택하지 않음

### B. 요약 카드와 장소별 상세 펼치기

- 장점: 점수와 핵심 판단을 먼저 비교하고, 필요한 장소의 근거만 확인 가능
- 단점: 원본 자료를 보려면 한 번 펼쳐야 함
- 결론: 채택

### C. 장소마다 별도 상세 페이지 제공

- 장점: 장소별 정보량을 충분히 수용 가능
- 단점: 순위 간 비교가 끊기고 route·상태 관리 범위가 불필요하게 커짐
- 결론: 이번 범위에서 제외

## 4. 확정 UX

### 4.1 기본 상태

리포트는 기존처럼 `finalScore` 내림차순으로 장소를 표시한다. 각 장소 카드는 기본적으로 요약 상태로 시작한다.

요약 카드에는 다음 정보를 표시한다.

- 순위
- 장소명, 카테고리, 주소
- 최종 점수
- 사진 점수와 리뷰 점수
- 사진·리뷰 분석 요약을 조합한 한눈에 보기
- 좋은 점 최대 4개
- 고려할 점 최대 4개
- 분석 사진 수와 추출 리뷰 수
- `네이버지도에서 보기` 외부 링크
- `상세 근거 보기` 버튼

### 4.2 상세 상태

`상세 근거 보기`를 누르면 같은 카드 안에서 다음 순서로 확장한다.

1. 실제 내부 사진 갤러리
2. 사진 분석 요약과 상세 근거
3. 리뷰 핵심 키워드
4. 실제 추출 리뷰 전체 목록
5. 리뷰 분석 요약과 상세 근거

한 번에 여러 장소를 펼칠 수 있다. 새로고침 시 펼침 상태는 복원하지 않는다. 브라우저 기본 스크롤 위치는 유지한다.

### 4.3 내부 사진

- 분석에 실제 전달한 URL만 표시한다.
- 최대 5장을 가로 썸네일 목록으로 표시한다.
- 썸네일 클릭 시 페이지 내부 확대 보기로 표시한다.
- 이미지에는 장소명과 순번을 이용한 대체 텍스트를 제공한다.
- `loading="lazy"`를 사용한다.
- 개별 이미지 로드 실패는 해당 썸네일의 실패 상태로만 처리한다.
- 이번 버전은 원격 사진 URL을 JSON에 저장하며 이미지 파일 자체를 복제하지 않는다.

원격 URL은 시간이 지나면 만료될 가능성이 있다. 영구 이미지 보관과 로컬 이미지 프록시는 이번 범위에서 제외한다.

### 4.4 실제 리뷰

- `PlaceDetail.reviews`에서 분석에 사용한 최대 50건을 순서대로 저장한다.
- 리포트에는 `추출 리뷰 N건 / 네이버 전체 M건`을 구분해 표시한다.
- 모든 추출 리뷰를 높이 약 360px의 독립 스크롤 영역에 표시한다.
- 각 리뷰에 추출 순번을 표시한다.
- 클라이언트 문자열 검색을 제공한다.
- 검색 결과가 없더라도 원본 리뷰 데이터는 변경하지 않는다.
- 긍정·부정 자동 분류는 별도 분류 근거가 필요하므로 이번 범위에서 제외한다.

### 4.5 네이버지도 링크

백엔드가 `placeId`로 다음 형태의 URL을 생성해 리포트에 저장한다.

```text
https://map.naver.com/p/entry/place/{placeId}
```

프론트엔드는 새 탭으로 열고 `rel="noreferrer noopener"`를 사용한다. URL이 없는 과거·실패 결과에는 버튼을 표시하지 않는다.

## 5. 데이터 모델

### 5.1 신규 공통 모델

```python
class AnalysisDigest(CamelModel):
    summary: str
    strengths: list[str]
    cautions: list[str]

class PlaceEvidence(CamelModel):
    provider: Literal["naver_map"] = "naver_map"
    place_url: str
    photo_urls: list[str]
    reviews: list[str]
    source_review_count: int
```

검증 규칙은 다음과 같다.

- `summary`: 빈 문자열 불가
- `strengths`: 최대 4개, 각 항목 빈 문자열 불가
- `cautions`: 최대 4개, 각 항목 빈 문자열 불가
- `placeUrl`: `https://map.naver.com/`으로 시작
- `photoUrls`: 최대 5개, `http` 또는 `https` URL만 허용
- `reviews`: 최대 50개, 빈 문자열 제거
- `sourceReviewCount`: 0 이상

### 5.2 분석 결과 확장

`PhotoAnalysis`와 `ReviewAnalysis`에 `digest: AnalysisDigest`를 추가한다. 구조화 응답 프롬프트는 기존 상세 `reason`과 함께 다음을 반환하도록 변경한다.

- 한두 문장의 `summary`
- 확인 가능한 `strengths` 최대 4개
- 감점 또는 근거 부족을 포함한 `cautions` 최대 4개

LLM 호출 횟수는 늘리지 않는다. 현재 사진 분석 호출과 리뷰 분석 호출의 구조화 응답만 확장한다.

### 5.3 장소 결과 확장

`PlaceResult`에 다음 optional 필드를 추가한다.

```python
photo_digest: AnalysisDigest | None = None
review_digest: AnalysisDigest | None = None
evidence: PlaceEvidence | None = None
```

기존 필드인 `photoScore`, `reviewScore`, `finalScore`, `photoReason`, `reviewReason`은 유지한다. 점수 계산 서비스는 현재 `PlaceDetail`, `PhotoAnalysis`, `ReviewAnalysis`에서 신규 필드를 복사한다.

실패 결과는 추출된 상세 정보가 있더라도 이번 범위에서는 `evidence`를 저장하지 않는다. 사용자가 비교하는 정상 분석 결과에만 원본 근거를 제공한다.

### 5.4 하위 호환성

- 신규 필드는 모두 기본값이 `None`인 optional 필드로 추가한다.
- 기존 JSON 리포트는 마이그레이션 없이 계속 읽을 수 있다.
- 원본 자료가 없는 기존 리포트에는 `이 리포트에는 원본 자료가 저장되지 않음`을 표시한다.
- 기존 API 필드와 정렬 규칙은 변경하지 않는다.

## 6. 데이터 흐름

```text
네이버지도 장소 상세 추출
  → PlaceDetail(photoUrls, reviews, reviewCount)
  → 사진 분석(PhotoAnalysis + digest)
  → 리뷰 분석(ReviewAnalysis + digest)
  → 점수 계산
  → PlaceResult(scores, reasons, digests, evidence)
  → RunReport
  → 날짜별 JSON 저장
  → GET /runs/:runId/report 또는 GET /reports/:runId
  → ReportView
```

SSE 진행 이벤트의 사진 썸네일 계약은 유지한다. 리뷰 원문은 실시간 이벤트에 싣지 않고 최종 리포트에만 포함한다. 이벤트 재생 버퍼와 로그에 리뷰 원문이 복제되는 것을 방지하기 위함이다.

## 7. 프론트엔드 구성

`ReportView`를 다음 단위로 분리한다.

```text
ReportView
  ├─ ReportHero
  └─ PlaceReportCard
       ├─ PlaceSummary
       ├─ EvidenceMeta
       └─ PlaceEvidenceDetails
            ├─ PhotoGallery
            ├─ AnalysisDigestView
            ├─ ReviewToolbar
            ├─ ReviewScrollList
            └─ AnalysisReasonDetails
```

각 컴포넌트의 책임은 다음과 같다.

- `PlaceReportCard`: 펼침 상태와 장소 단위 배치
- `PlaceSummary`: 점수, 장소 정보, 좋은 점, 고려할 점
- `EvidenceMeta`: 사진 수, 추출 리뷰 수, 네이버 전체 리뷰 수
- `PhotoGallery`: 썸네일, 확대 보기, 이미지 실패 처리
- `ReviewToolbar`: 리뷰 건수와 클라이언트 검색어
- `ReviewScrollList`: 전체 원문 리뷰와 내부 스크롤
- `AnalysisReasonDetails`: 기존 긴 분석문을 접힌 상세 근거로 표시

한눈에 보기의 좋은 점과 고려할 점은 `photoDigest`와 `reviewDigest` 배열을 사진·리뷰 순으로 합친 뒤 중복 제거하고 각각 최대 4개만 표시한다. 상세 영역에서는 각 분석의 전체 digest와 reason을 구분해 표시한다.

## 8. 가독성 규칙

- 긴 분석문은 기본 화면에 직접 노출하지 않는다.
- 요약 본문은 16px 이상, 상세 분석문은 14px 이상을 사용한다.
- 상세 분석문은 줄 길이를 제한하고 문단·목록의 줄 간격을 확보한다.
- 점수는 숫자만 크게 표시하고 `/10`은 보조 정보로 처리한다.
- 좋은 점과 고려할 점은 색상만으로 구분하지 않고 제목과 아이콘을 함께 사용한다.
- 리뷰 원문은 카드별 독립 스크롤을 사용하고 페이지 전체 스크롤을 가로채지 않는다.
- 1024px 이상 desktop 지원 범위는 기존과 동일하다.

## 9. 오류와 예외 처리

- 사진 URL 없음: `분석에 사용된 사진이 없음` 표시
- 사진 로드 실패: 해당 사진 위치에 실패 대체 화면 표시
- 리뷰 없음: `추출된 리뷰가 없음` 표시
- evidence 없음: 과거 리포트 안내 표시
- digest 없음: 기존 `reason`의 첫 문단을 보조 요약으로 사용하지 않고 `요약 정보 없음` 표시
- 네이버 URL 없음: 외부 링크 버튼 숨김
- 외부 리뷰 문자열은 React text node로만 렌더링하고 HTML로 삽입하지 않음
- 사진·리뷰 일부가 없어도 나머지 점수와 분석 결과는 정상 표시

## 10. 테스트

### Backend

- `AnalysisDigest` 개수와 빈 문자열 검증
- `PlaceEvidence` URL, 사진 5개, 리뷰 50개 상한 검증
- 점수 계산 결과에 digest와 evidence가 복사되는지 확인
- 완료 리포트 JSON에 사진·리뷰·네이버 URL이 저장되는지 확인
- 과거 필드만 가진 JSON 리포트를 계속 읽는지 확인
- 원문 리뷰가 SSE progress event와 일반 로그에 포함되지 않는지 확인

### Frontend

- 점수순 정렬 유지
- 요약 상태에서 긴 `photoReason`, `reviewReason`이 보이지 않는지 확인
- 상세 펼침 후 사진 5개와 전체 리뷰가 표시되는지 확인
- 리뷰 검색이 대소문자와 앞뒤 공백을 정규화하는지 확인
- 네이버지도 링크의 URL, 새 탭, `rel` 속성 확인
- evidence가 없는 과거 리포트 fallback 확인
- 이미지 실패 상태와 사진 확대 보기 확인
- 리뷰 목록 컨테이너에 내부 스크롤 스타일 적용 확인

### E2E

신규 실행 1건을 완료한 뒤 다음을 확인한다.

1. 리포트에 분석 완료 장소가 점수순으로 표시됨
2. 장소 카드에서 좋은 점과 고려할 점을 읽을 수 있음
3. 상세 펼침 시 실제 내부 사진이 표시됨
4. 추출 리뷰 건수와 실제 렌더링 건수가 일치함
5. 마지막 리뷰까지 내부 스크롤로 도달 가능함
6. 네이버지도 링크가 해당 `placeId`를 포함함

## 11. 비목표

- 네이버 사진 파일의 영구 로컬 보관
- 리뷰별 긍정·부정 자동 분류
- 리뷰 작성자, 방문일, 영수증 인증 등 추가 메타데이터 추출
- 모바일 전용 리포트 레이아웃
- 리포트 편집·삭제·공유 권한
- 기존 JSON 리포트에 원본 자료를 역으로 채우는 마이그레이션

## 12. 완료 조건

- 신규 리포트의 모든 정상 분석 장소에 네이버지도 링크가 있음
- 분석에 사용한 최대 5개 사진과 최대 50개 리뷰가 JSON 리포트에 보존됨
- 리포트 요약 화면에서 긴 분석문 없이 핵심 장점과 고려사항을 비교할 수 있음
- 장소 상세에서 사진 확대와 전체 리뷰 내부 스크롤이 가능함
- 기존 리포트가 오류 없이 fallback UI로 열림
- Python, frontend unit test, frontend build, Playwright E2E가 통과함
