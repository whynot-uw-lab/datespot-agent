# Naver Map Flow PoC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 1-2 네이버지도 탐색 PoC를 재현 가능한 Playwright 스크립트와 결과 JSON으로 완료한다.

**Architecture:** `poc/1-2-naver-map-flow/explore.py`가 브라우저 흐름 실행, 목록/상세/사진/리뷰 추출, 결과 저장을 담당한다. 동적 클래스 의존을 줄이기 위해 URL 패턴, iframe URL, role/text, DOM 구조 순서, 직접 `pcmap.place.naver.com` route를 우선 사용한다.

**Tech Stack:** Python, Playwright, uv, pytest-free script smoke verification, JSON artifacts.

## Global Constraints

- 네이버 DOM 클래스명은 동적으로 바뀔 수 있으므로 클래스명 selector는 최후 fallback 외 사용하지 않는다.
- 실행 결과는 `poc/1-2-naver-map-flow/output/naver_map_flow_result.json`에 저장한다.
- 기본 검증 대상은 광고 제외 상위 2개 장소다.
- 사진/리뷰 분석 점수화는 1-3/1-4 범위이므로 이번 단계에서 제외한다.

---

### Task 1: Build Reproducible Explore Script

**Files:**
- Create: `poc/1-2-naver-map-flow/explore.py`
- Output: `poc/1-2-naver-map-flow/output/naver_map_flow_result.json`

**Interfaces:**
- Consumes: Playwright Chromium installed by `uv run playwright install chromium`
- Produces: `main() -> int`, result JSON with `ok`, `query`, `places`, `errors`

- [ ] **Step 1: Write script skeleton**

Create an async Playwright script with constants for station query, category query, target count, output path, timeout, and headless mode.

- [ ] **Step 2: Implement stable navigation helpers**

Add helpers:
- `find_frame(page, pattern: Pattern[str]) -> Frame`
- `wait_for_frame(page, pattern: Pattern[str], timeout_ms: int) -> Frame`
- `click_search_option(page, query: str) -> None`
- `search_station(page) -> None`
- `search_category(page) -> Frame`

Use role/text and iframe URL patterns rather than dynamic class names.

- [ ] **Step 3: Implement list extraction**

Extract `li` rows from the restaurant list frame. Determine title links by `a[role=button]` and text exclusion list: `저장`, `더보기`, `광고`, `이전`, `다음`. Mark ads by `rawText.includes("광고")`.

- [ ] **Step 4: Implement direct detail extraction**

For each organic place, open list item to capture `placeId`, then use direct routes:
- `https://pcmap.place.naver.com/restaurant/{placeId}/home`
- `https://pcmap.place.naver.com/restaurant/{placeId}/photo?filterType=AI%20View&subFilter=INTERIOR`
- `https://pcmap.place.naver.com/restaurant/{placeId}/review/visitor?reviewSort=recent`

Extract category/address from visible text when available, photo URLs from image `src/currentSrc` filtered by known asset hosts, and review text from review list items.

- [ ] **Step 5: Add error capture and JSON output**

Write partial results even on failure. Include per-place errors so one failed place does not discard prior results.

- [ ] **Step 6: Verify manually**

Run:

```bash
uv run python poc/1-2-naver-map-flow/explore.py
```

Expected:
- command exits `0`
- output JSON has `ok: true`
- at least one place has `placeId`
- at least one place has non-empty `photoUrls`
- at least one place has non-empty `reviews`

### Task 2: Update PoC Docs

**Files:**
- Modify: `poc/1-2-naver-map-flow/README.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: successful `naver_map_flow_result.json`
- Produces: docs reflecting scriptable 1-2 status

- [ ] **Step 1: Update 1-2 README status**

Change status from “수동 조사 완료. 스크립트화 전 단계.” to script execution status, including output path and known caveats.

- [ ] **Step 2: Update root roadmap**

Mark 1-2 complete only if the script ran successfully and output JSON contains extracted places.

- [ ] **Step 3: Verify docs**

Run:

```bash
rg -n "1-2|explore.py|naver_map_flow_result" README.md poc/1-2-naver-map-flow/README.md
```

Expected: root roadmap and 1-2 README point to the script and output.
