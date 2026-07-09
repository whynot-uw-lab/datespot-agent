# 1-2 네이버지도 탐색 PoC

1단계(기술 리스크 검증)의 두 번째 세부 단계. 네이버지도 자동화가 실제로 가능한지 검증한다.

> 단계별 조사 기록은 [GUIDE.md](GUIDE.md), 라우팅/추출 상세는 [naver-map-playwright-routing.md](naver-map-playwright-routing.md) 참고.

## 목표 흐름 (범위)

```
신사역 검색
  → 지도 이동
  → 음식점 검색
  → 목록 추출
  → 첫 번째 장소 선택
  → 카테고리 / 사진 / 리뷰 정보 조회
```

- 이번 단계는 **1개 장소**에 대해 흐름이 끝까지 도는지 확인하는 것이 목표.
- 여러 장소 순회·필터링·점수화는 이후 단계(2단계 에이전트 코어)에서 다룬다.

## 진행 순서

1. **수동 조사**: 브라우저로 직접 네이버지도를 조작하며 위 흐름의 각 단계마다 어떤 UI 요소를 클릭/입력해야 하는지 파악 (셀렉터, URL 패턴, iframe 구조 등)
2. **Playwright 스크립트화**: 조사한 흐름을 스크립트로 재현
3. **데이터 추출 검증**: 카테고리/사진 URL/리뷰 텍스트가 안정적으로 추출되는지 확인

## 산출물

- `GUIDE.md` — 수동 조사 기록 (단계별 스크린샷 설명, 셀렉터, 막힌 지점)
- `naver-map-playwright-routing.md` — 확인된 라우팅, iframe, Playwright 조작 메모
- `kainsendon-unido-poc-extraction-summary.json` — 카이센동 우니도 본점 추출 결과 요약
- `explore.py` — Playwright 재현 스크립트 (조사 완료 후 작성)
- `output/` — 실행 결과 (추출된 JSON, 스크린샷)

## 확인 결과

- `신사역` 검색 가능.
- `신사역 신분당선` 결과 선택으로 역 상세/지도 이동 가능.
- `카이센동 우니도 본점` 상세 진입 가능.
- `#searchIframe`, `#entryIframe` 구조 확인.
- 상세 데이터 추출은 map shell보다 direct `pcmap.place.naver.com` route가 안정적.
- 내부 사진은 `filterType=AI View&subFilter=INTERIOR`로 접근 가능.
- 최신 리뷰는 `reviewSort=recent` 적용 가능.
- 리뷰는 첫 번째 `펼쳐서 더보기` 클릭 후 20개까지 로드 확인.

## 이슈

- role/text click은 sticky header 또는 child span에 자주 막힘.
- DOM node click 또는 direct `pcmap` route fallback 필요.
- 사진 자산에는 지도 타일/아이콘/광고 이미지가 섞여 URL 필터링 필요.

## 상태

수동 조사 완료. 스크립트화 전 단계.
