# 1-2 네이버지도 탐색 PoC — 조사 기록

수동으로 브라우저를 조작하며 목표 흐름의 각 단계를 파악한 기록.
상세 라우팅/추출 메모는 [naver-map-playwright-routing.md](naver-map-playwright-routing.md)에 정리했다.

## 목표 흐름

```
신사역 검색 → 지도 이동 → 음식점 검색 → 목록 추출 → 첫 번째 장소 선택 → 카테고리/사진/리뷰 조회
```

## 조사 환경

- URL: https://map.naver.com
- 조사일: 2026-07-09

---

## 단계별 기록

### 1. 신사역 검색

- 메인 검색 combobox에 `신사역` 입력 후 Enter.
- URL이 `/p/search/신사역` 형태로 변경됨.
- role 기반 click은 한 차례 timeout 발생. DOM node click이 더 안정적.

### 2. 지도 이동

- 검색 결과의 `신사역 신분당선` 선택.
- 결과 목록은 `#searchIframe` 안에 있음.
- 선택 후 `/subway-station/1907` route 확인.

### 3. 음식점 검색

- `신사역` 검색 결과에 주변 음식점이 함께 노출됨.
- 이번 PoC 대상은 `카이센동 우니도 본점`.

### 4. 목록 추출

- `#searchIframe`에서 장소명, 카테고리, 주소, 영업 상태 텍스트 확인 가능.
- 결과 항목 클릭은 child span pointer intercept가 있어 DOM node click fallback 필요.

### 5. 첫 번째 장소 선택

- `카이센동 우니도 본점` 선택.
- Place ID: `1720070048`.
- 상세 정보는 `#entryIframe`에 로드됨.

### 6. 카테고리 / 사진 / 리뷰 조회

- 카테고리: `일식당`.
- 사진: direct `pcmap` route에서 `filterType=AI View&subFilter=INTERIOR` 적용 후 내부 사진 URL 추출.
- 리뷰: direct `pcmap` route에서 `reviewSort=recent` 적용 후 최신순 리뷰 확인.
- 리뷰 20개는 첫 번째 `펼쳐서 더보기` 클릭 후 로드 확인.

---

## 이슈 / 막힌 지점

- Naver Map은 `searchIframe`, `entryIframe`을 분리해서 사용함.
- embedded map shell은 클릭 안정성이 낮음.
- direct `pcmap.place.naver.com` route가 추출에 더 안정적임.
- 사진 자산에는 지도 타일/광고/아이콘이 섞여 URL 필터링 필요.

## 결론

- PoC 흐름은 가능함.
- 다음 단계는 직접 route 기반 Playwright 스크립트화.
