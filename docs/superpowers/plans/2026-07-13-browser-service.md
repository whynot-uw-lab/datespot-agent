# BrowserService Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 네이버지도 우측 패널을 직접 조작해 후보 목록과 장소별 내부 사진 5장·최신 리뷰 50개를 추출하는 실행별 `BrowserService`를 구현한다.

**Architecture:** `BrowserService`가 `run_id`별 Playwright 자원과 후보 target을 소유하고, `NaverMapPage`가 네이버지도 frame과 UI를 조작한다. 순수 DOM 변환은 `parsers.py`, 타입 예외는 `errors.py`, 실사이트 최소 조작 간격과 스모크 실행 잠금은 `pacing.py`로 분리한다.

**Tech Stack:** Python 3.13+, Playwright async API 1.61+, Pydantic 2.13+, 표준 `unittest`

## Global Constraints

- 최상위 Page는 `https://map.naver.com`에 유지하고 `pcmap.place.naver.com` 직접 이동을 금지한다.
- 위치 검색 → 역 선택 → 줌 15 → 키워드 검색 순서를 고정한다.
- 후보 목록은 광고·중복·ID 없는 항목만 제거하고 `RunConfig.max_places`로 자르지 않는다.
- 거리 계산·거리 필드·거리 필터와 사진 유효성 판정은 구현하지 않는다.
- 상세 화면은 검색 결과 클릭으로 열고 `entryIframe`에서만 조작한다.
- 사진은 `내부` 분류의 DOM 순서 기준 첫 5장만 반환한다.
- 리뷰는 `최신순`으로 최대 50개 본문만 반환하고 전체 방문자 리뷰 수를 별도 저장한다.
- 네이버 실사이트 상태 변경 조작 사이에 최소 3초를 강제한다.
- 재시도 전 최소 5초, 동일 실사이트 스모크 실행 사이에 최소 30초를 강제한다.
- 실사이트 테스트를 병렬 실행하거나 기본 테스트 탐색·CI에 포함하지 않는다.
- 403, 429, CAPTCHA, 비정상 접근 제한 화면 감지 시 복원·재시도 없이 즉시 중단한다.
- `BrowserSessionError` 외 탐색·추출 실패는 동일한 결정적 경로를 1회만 재시도한다.
- `NavigationRecoveryAgent` 연동은 README 2-6 범위로 유지하고 이번 계획에서 구현하지 않는다.
- 로컬 테스트는 가짜 clock·sleeper와 route fixture를 사용하며 실제 네이버 요청을 만들지 않는다.
- 기존 `README.md` 변경과 `.playwright-cli/` 산출물은 해당 작업에서 작성한 hunk 외에는 수정·스테이징하지 않는다.

---

## File Structure

```text
src/datespot_agent/browser/
├── __init__.py       # BrowserService와 공개 예외 export
├── errors.py         # 타입 예외와 실행 컨텍스트
├── parsers.py        # 후보·장소 ID·메타데이터·사진·리뷰 순수 변환
├── pacing.py         # 조작 3초, 재시도 5초, 스모크 30초 정책
├── naver_map.py      # searchIframe/entryIframe UI 조작
└── service.py        # run_id별 Playwright 세션·재시도·정리

tests/
├── fixtures/
│   ├── naver_map_shell.html
│   ├── naver_search_results.html
│   └── naver_entry.html
├── test_browser_parsers.py
├── test_browser_pacing.py
├── test_naver_map_page.py
├── test_browser_service.py
└── test_browser_integration.py

poc/2-3-browser-service/
├── README.md
└── live_smoke.py
```

## Task 1: 순수 파서와 타입 예외

**Files:**
- Create: `src/datespot_agent/browser/__init__.py`
- Create: `src/datespot_agent/browser/errors.py`
- Create: `src/datespot_agent/browser/parsers.py`
- Create: `tests/test_browser_parsers.py`

**Interfaces:**
- Consumes: `datespot_agent.models.CandidatePlace`
- Produces: `BrowserServiceError` 계층, `CandidateTarget`, `HomeMetadata`, `parse_candidate_rows()`, `parse_home_text()`, `parse_zoom()`, `first_interior_urls()`, `normalize_review_bodies()`

- [ ] **Step 1: 실패하는 파서·예외 테스트 작성**

```python
# tests/test_browser_parsers.py
import unittest

from datespot_agent.browser.errors import BrowserExtractionError
from datespot_agent.browser.parsers import (
    first_interior_urls,
    normalize_review_bodies,
    parse_candidate_rows,
    parse_home_text,
    parse_zoom,
)


class BrowserParserTests(unittest.TestCase):
    def test_candidate_rows_drop_ads_missing_ids_and_duplicates(self):
        rows = [
            {"domIndex": 0, "name": "광고집", "rawText": "광고집 광고", "href": "/restaurant/1"},
            {"domIndex": 1, "name": "치보 신사점", "rawText": "치보 신사점 일식당", "href": "/restaurant/1150149433"},
            {"domIndex": 2, "name": "ID 없음", "rawText": "ID 없음 일식당", "href": ""},
            {"domIndex": 3, "name": "치보 신사점", "rawText": "치보 신사점", "href": "/restaurant/1150149433"},
        ]

        candidates, targets = parse_candidate_rows(rows, [])

        self.assertEqual([item.place_id for item in candidates], ["1150149433"])
        self.assertEqual(targets["1150149433"].dom_index, 1)

    def test_candidate_rows_can_use_apollo_id_by_normalized_name(self):
        candidates, _ = parse_candidate_rows(
            [{"domIndex": 4, "name": "카이센동 우니도 본점예약", "rawText": "일식당", "href": ""}],
            [{"id": "1720070048", "name": "카이센동 우니도 본점"}],
        )
        self.assertEqual(candidates[0].place_id, "1720070048")

    def test_home_photo_review_and_zoom_parsers(self):
        metadata = parse_home_text(
            ["치보 신사점", "일식당", "서울 강남구 도산대로 15", "방문자 리뷰 1,234"],
            "치보 신사점",
        )
        photos = first_interior_urls(
            [{"alt": f"INTERIOR_{index}", "url": f"https://img/{index}.jpg"} for index in range(7)]
            + [{"alt": "FOOD_0", "url": "https://img/food.jpg"}]
        )
        reviews = normalize_review_bodies([" 조용해요 ", "조용해요", " 음식이 좋아요 "])

        self.assertEqual((metadata.category, metadata.address, metadata.review_count), ("일식당", "서울 강남구 도산대로 15", 1234))
        self.assertEqual(len(photos), 5)
        self.assertEqual(reviews, ["조용해요", "음식이 좋아요"])
        self.assertEqual(
            first_interior_urls(
                [
                    {"alt": "INTERIOR_4", "url": "https://img/4.jpg"},
                    {"alt": "INTERIOR_1", "url": "https://img/1.jpg"},
                ]
            ),
            ["https://img/4.jpg", "https://img/1.jpg"],
        )
        self.assertEqual(first_interior_urls([]), [])
        self.assertEqual(normalize_review_bodies([]), [])
        self.assertEqual(parse_zoom("https://map.naver.com/?c=127.0,37.5,15,0,0,0,dh"), 15)
        self.assertEqual(parse_zoom("https://map.naver.com/?c=15.00,0,0,0,dh"), 15)

    def test_missing_review_count_is_extraction_failure_input(self):
        with self.assertRaises(ValueError):
            parse_home_text(["치보 신사점", "일식당"], "치보 신사점")

    def test_error_keeps_run_step_and_place_context(self):
        error = BrowserExtractionError("리뷰 수 파싱 실패", run_id="run-1", step="home", place_id="1150149433")
        self.assertIn("run-1", str(error))
        self.assertEqual(error.step, "home")
```

- [ ] **Step 2: 파서 테스트가 import 실패하는지 확인**

Run: `uv run python -m unittest tests.test_browser_parsers -v`

Expected: `ModuleNotFoundError: No module named 'datespot_agent.browser'`

- [ ] **Step 3: 타입 예외 구현**

```python
# src/datespot_agent/browser/__init__.py
"""네이버지도 브라우저 자동화 계층."""
```

```python
# src/datespot_agent/browser/errors.py
from __future__ import annotations


class BrowserServiceError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        run_id: str | None = None,
        step: str | None = None,
        place_id: str | None = None,
    ) -> None:
        self.run_id = run_id
        self.step = step
        self.place_id = place_id
        context = ", ".join(
            value
            for value in (
                f"run_id={run_id}" if run_id else "",
                f"step={step}" if step else "",
                f"place_id={place_id}" if place_id else "",
            )
            if value
        )
        super().__init__(f"{message} ({context})" if context else message)


class BrowserSessionError(BrowserServiceError):
    pass


class BrowserNavigationError(BrowserServiceError):
    pass


class BrowserExtractionError(BrowserServiceError):
    pass


class BrowserAccessBlockedError(BrowserServiceError):
    pass
```

- [ ] **Step 4: 순수 파서 구현**

```python
# src/datespot_agent/browser/parsers.py
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from datespot_agent.models import CandidatePlace

TITLE_SUFFIXES = ("플레이스 플러스", "예약", "쿠폰", "영업", "별점", "리뷰", "저장")


@dataclass(frozen=True, slots=True)
class CandidateTarget:
    place_id: str
    name: str
    dom_index: int


@dataclass(frozen=True, slots=True)
class HomeMetadata:
    category: str | None
    address: str | None
    review_count: int


def normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def clean_place_name(value: str) -> str:
    name = normalize_text(value)
    for suffix in TITLE_SUFFIXES:
        index = name.find(suffix)
        if index > 0:
            name = name[:index]
    return normalize_text(name)


def extract_place_id(value: str) -> str | None:
    match = re.search(r"/(?:place|restaurant)/(\d+)", value)
    return match.group(1) if match else None


def parse_candidate_rows(
    rows: list[dict[str, Any]],
    businesses: list[dict[str, Any]],
) -> tuple[list[CandidatePlace], dict[str, CandidateTarget]]:
    apollo_ids = {
        clean_place_name(str(item.get("name", ""))): str(item.get("id", ""))
        for item in businesses
        if item.get("id") and item.get("name")
    }
    candidates: list[CandidatePlace] = []
    targets: dict[str, CandidateTarget] = {}
    for row in rows:
        raw_text = normalize_text(str(row.get("rawText", "")))
        name = clean_place_name(str(row.get("name", "")))
        if not name or "광고" in raw_text:
            continue
        place_id = extract_place_id(str(row.get("href", ""))) or apollo_ids.get(name)
        if not place_id or place_id in targets:
            continue
        target = CandidateTarget(place_id=place_id, name=name, dom_index=int(row["domIndex"]))
        candidates.append(CandidatePlace(place_id=place_id, name=name))
        targets[place_id] = target
    return candidates, targets


def parse_home_text(lines: list[str], place_name: str) -> HomeMetadata:
    normalized = [normalize_text(line) for line in lines if normalize_text(line)]
    category = None
    address = next((line for line in normalized if line.startswith("서울 ")), None)
    for index, line in enumerate(normalized):
        if line == place_name and index + 1 < len(normalized):
            category = normalized[index + 1]
            break
    review_match = next(
        (re.search(r"방문자\s*리뷰\s*([\d,]+)", line) for line in normalized if "방문자" in line and "리뷰" in line),
        None,
    )
    if review_match is None:
        raise ValueError("방문자 리뷰 수를 찾지 못함")
    return HomeMetadata(category=category, address=address, review_count=int(review_match.group(1).replace(",", "")))


def first_interior_urls(images: list[dict[str, str]], limit: int = 5) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for image in images:
        match = re.fullmatch(r"INTERIOR_(\d+)", image.get("alt", ""))
        url = image.get("url", "")
        if match and url and url not in seen:
            seen.add(url)
            result.append(url)
    return result[:limit]


def normalize_review_bodies(values: list[str], limit: int = 50) -> list[str]:
    return list(dict.fromkeys(text for value in values if (text := normalize_text(value))))[:limit]


def parse_zoom(url: str) -> int | None:
    values = parse_qs(urlparse(url).query).get("c", [])
    if not values:
        return None
    parts = values[0].split(",")
    candidates = [parts[2], parts[0]] if len(parts) >= 3 else parts
    for value in candidates:
        try:
            zoom = int(float(value))
        except ValueError:
            continue
        if 1 <= zoom <= 21:
            return zoom
    return None
```

- [ ] **Step 5: 파서 테스트 통과 확인**

Run: `uv run python -m unittest tests.test_browser_parsers -v`

Expected: `Ran 5 tests`, `OK`

- [ ] **Step 6: 파서·예외 커밋**

```bash
git add src/datespot_agent/browser/__init__.py src/datespot_agent/browser/errors.py src/datespot_agent/browser/parsers.py tests/test_browser_parsers.py
git commit -m "feat: add browser parsing contracts"
```

## Task 2: 강제 pacing과 실사이트 실행 잠금

**Files:**
- Create: `src/datespot_agent/browser/pacing.py`
- Create: `tests/test_browser_pacing.py`

**Interfaces:**
- Consumes: async callable, 주입 가능한 단조 증가 clock·async sleeper
- Produces: `InteractionPacer.run()`, `InteractionPacer.wait_before_retry()`, `LiveSmokeGuard`

- [ ] **Step 1: 실패하는 pacing 테스트 작성**

```python
# tests/test_browser_pacing.py
import asyncio
import tempfile
import unittest
from pathlib import Path

from datespot_agent.browser.pacing import InteractionPacer, LiveSmokeGuard


class FakeTime:
    def __init__(self) -> None:
        self.monotonic_value = 100.0
        self.wall_value = 1_000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.monotonic_value

    def wall(self) -> float:
        return self.wall_value

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.monotonic_value += seconds
        self.wall_value += seconds


class InteractionPacerTests(unittest.IsolatedAsyncioTestCase):
    async def test_actions_are_serialized_with_three_second_minimum(self):
        fake = FakeTime()
        pacer = InteractionPacer(clock=fake.monotonic, sleep=fake.sleep)
        starts: list[float] = []

        async def action() -> None:
            starts.append(fake.monotonic())

        await asyncio.gather(pacer.run(action), pacer.run(action))

        self.assertEqual(starts, [100.0, 103.0])
        self.assertEqual(fake.sleeps, [3.0])

    async def test_retry_wait_is_five_seconds(self):
        fake = FakeTime()
        await InteractionPacer(clock=fake.monotonic, sleep=fake.sleep).wait_before_retry()
        self.assertEqual(fake.sleeps, [5.0])

    async def test_live_smoke_guard_enforces_thirty_second_cooldown(self):
        fake = FakeTime()
        with tempfile.TemporaryDirectory() as directory:
            stamp = Path(directory) / "last-finished"
            stamp.write_text("990.0", encoding="utf-8")
            guard = LiveSmokeGuard(stamp_path=stamp, wall_clock=fake.wall, sleep=fake.sleep)
            async with guard:
                pass
        self.assertEqual(fake.sleeps, [20.0])

    async def test_live_smoke_guard_rejects_parallel_process(self):
        fake = FakeTime()
        with tempfile.TemporaryDirectory() as directory:
            stamp = Path(directory) / "last-finished"
            first = LiveSmokeGuard(stamp_path=stamp, wall_clock=fake.wall, sleep=fake.sleep)
            second = LiveSmokeGuard(stamp_path=stamp, wall_clock=fake.wall, sleep=fake.sleep)
            async with first:
                with self.assertRaises(RuntimeError):
                    await second.__aenter__()
```

- [ ] **Step 2: pacing 테스트가 import 실패하는지 확인**

Run: `uv run python -m unittest tests.test_browser_pacing -v`

Expected: `ModuleNotFoundError` 또는 `ImportError`로 실패

- [ ] **Step 3: pacing과 실행 잠금 구현**

```python
# src/datespot_agent/browser/pacing.py
from __future__ import annotations

import asyncio
import fcntl
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import TracebackType
from typing import TypeVar

ACTION_INTERVAL_SECONDS = 3.0
RETRY_DELAY_SECONDS = 5.0
LIVE_SMOKE_COOLDOWN_SECONDS = 30.0
T = TypeVar("T")


class InteractionPacer:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._last_action_started: float | None = None

    async def run(self, action: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            now = self._clock()
            if self._last_action_started is not None:
                remaining = ACTION_INTERVAL_SECONDS - (now - self._last_action_started)
                if remaining > 0:
                    await self._sleep(remaining)
            self._last_action_started = self._clock()
            return await action()

    async def wait_before_retry(self) -> None:
        await self._sleep(RETRY_DELAY_SECONDS)


class LiveSmokeGuard:
    def __init__(
        self,
        *,
        stamp_path: Path,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._stamp_path = stamp_path
        self._lock_path = stamp_path.with_suffix(".lock")
        self._wall_clock = wall_clock
        self._sleep = sleep
        self._lock_file = None

    async def __aenter__(self) -> "LiveSmokeGuard":
        self._stamp_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = self._lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self._lock_file.close()
            self._lock_file = None
            raise RuntimeError("네이버 실사이트 테스트가 이미 실행 중임") from error
        if self._stamp_path.exists():
            last_finished = float(self._stamp_path.read_text(encoding="utf-8"))
            remaining = LIVE_SMOKE_COOLDOWN_SECONDS - (self._wall_clock() - last_finished)
            if remaining > 0:
                await self._sleep(remaining)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stamp_path.write_text(str(self._wall_clock()), encoding="utf-8")
        if self._lock_file is not None:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()
            self._lock_file = None
```

- [ ] **Step 4: pacing 테스트 통과 확인**

Run: `uv run python -m unittest tests.test_browser_pacing -v`

Expected: `Ran 4 tests`, `OK`

- [ ] **Step 5: pacing 커밋**

```bash
git add src/datespot_agent/browser/pacing.py tests/test_browser_pacing.py
git commit -m "feat: enforce live browser pacing"
```

## Task 3: 지도 검색·줌·후보 추출

**Files:**
- Create: `src/datespot_agent/browser/naver_map.py`
- Create: `tests/test_naver_map_page.py`

**Interfaces:**
- Consumes: Playwright `Page`, `InteractionPacer`, `RunConfig`, Task 1 파서
- Produces: `NaverMapPage.open()`, `search_location()`, `select_station()`, `set_zoom()`, `search_keyword()`, `extract_candidates()`

- [ ] **Step 1: 검색 순서·줌·차단 감지 실패 테스트 작성**

```python
# tests/test_naver_map_page.py
import unittest

from datespot_agent.browser.errors import BrowserAccessBlockedError, BrowserNavigationError
from datespot_agent.browser.naver_map import BLOCK_TEXT_PATTERN, NaverMapPage


class FakePacer:
    def __init__(self) -> None:
        self.actions = 0

    async def run(self, action):
        self.actions += 1
        return await action()

    async def wait_before_retry(self) -> None:
        return None


class NaverMapPageContractTests(unittest.IsolatedAsyncioTestCase):
    def test_access_limit_text_pattern_covers_captcha_and_korean_notice(self):
        self.assertIsNotNone(BLOCK_TEXT_PATTERN.search("CAPTCHA"))
        self.assertIsNotNone(BLOCK_TEXT_PATTERN.search("비정상적인 접근이 감지되었습니다"))

    async def test_blocked_response_stops_before_next_action(self):
        navigator = object.__new__(NaverMapPage)
        navigator.page = None
        navigator.pacer = FakePacer()
        navigator._blocked_response = (429, "https://map.naver.com/p/search/일식")

        with self.assertRaises(BrowserAccessBlockedError):
            await navigator._assert_access_allowed()
        self.assertEqual(navigator.pacer.actions, 0)

    async def test_unknown_zoom_is_navigation_error(self):
        navigator = object.__new__(NaverMapPage)
        navigator.page = type("Page", (), {"url": "https://map.naver.com/"})()
        navigator.pacer = FakePacer()
        navigator._blocked_response = None

        with self.assertRaises(BrowserNavigationError):
            await navigator.set_zoom(15)
```

- [ ] **Step 2: 검색 계약 테스트 실패 확인**

Run: `uv run python -m unittest tests.test_naver_map_page -v`

Expected: `ModuleNotFoundError` 또는 `ImportError`로 실패

- [ ] **Step 3: 검색·frame·접근 제한 기반 구현**

```python
# src/datespot_agent/browser/naver_map.py
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from playwright.async_api import Frame, Locator, Page, Response, TimeoutError as PlaywrightTimeoutError

from datespot_agent.browser.errors import (
    BrowserAccessBlockedError,
    BrowserExtractionError,
    BrowserNavigationError,
)
from datespot_agent.browser.pacing import InteractionPacer
from datespot_agent.browser.parsers import CandidateTarget, parse_candidate_rows, parse_zoom
from datespot_agent.models import CandidatePlace

MAP_URL = "https://map.naver.com"
LIST_FRAME_PATTERN = re.compile(r"pcmap\.place\.naver\.com/(?:restaurant|place)/list")
BLOCK_TEXT_PATTERN = re.compile(r"CAPTCHA|비정상적인 접근|서비스 이용이 제한|접근이 제한", re.IGNORECASE)
T = TypeVar("T")


class NaverMapPage:
    def __init__(self, page: Page, pacer: InteractionPacer) -> None:
        self.page = page
        self.pacer = pacer
        self._blocked_response: tuple[int, str] | None = None
        page.on("response", self._observe_response)

    def _observe_response(self, response: Response) -> None:
        if response.status in {403, 429} and "naver.com" in response.url:
            self._blocked_response = (response.status, response.url)

    async def _assert_access_allowed(self) -> None:
        if self._blocked_response is not None:
            status, url = self._blocked_response
            raise BrowserAccessBlockedError(f"네이버 접근 제한 응답: {status} {url}")
        if self.page is None:
            return
        for frame in self.page.frames:
            try:
                text = await frame.locator("body").inner_text(timeout=500)
            except Exception:
                continue
            if BLOCK_TEXT_PATTERN.search(text):
                raise BrowserAccessBlockedError("네이버 접근 제한 화면 감지")

    async def _mutate(self, action: Callable[[], Awaitable[T]]) -> T:
        await self._assert_access_allowed()
        result = await self.pacer.run(action)
        await self._assert_access_allowed()
        return result

    async def _wait_frame(self, pattern: re.Pattern[str], timeout_ms: int = 20_000) -> Frame:
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while asyncio.get_running_loop().time() < deadline:
            frame = next((item for item in self.page.frames if pattern.search(item.url)), None)
            if frame is not None:
                return frame
            await self.page.wait_for_timeout(250)
        raise BrowserNavigationError(f"frame을 찾지 못함: {pattern.pattern}")

    async def open(self) -> None:
        await self._mutate(lambda: self.page.goto(MAP_URL, wait_until="domcontentloaded", timeout=20_000))

    async def _submit_search(self, query: str) -> None:
        combobox = self.page.get_by_role("combobox")
        await self._mutate(lambda: combobox.fill(query, timeout=20_000))
        option = self.page.get_by_role("option", name=f"검색어 {query}", exact=True)
        await self._mutate(lambda: option.click(timeout=20_000))
        await self.page.wait_for_url(re.compile(r"/p/search/"), timeout=20_000)

    async def search_location(self, location: str) -> None:
        await self._submit_search(location)

    async def select_station(self, location: str) -> None:
        frame = await self._wait_frame(LIST_FRAME_PATTERN)
        station = frame.get_by_role("button", name=re.compile(rf"^{re.escape(location)}.*(?:지하철|전철|선)"))
        if await station.count() == 0:
            raise BrowserNavigationError(f"역 검색 결과를 찾지 못함: {location}")
        await self._mutate(lambda: station.first.click(force=True, timeout=10_000))
        await self.page.wait_for_url(re.compile(r"subway-station/"), timeout=20_000)

    async def set_zoom(self, target: int = 15) -> None:
        current = parse_zoom(self.page.url)
        if current is None:
            raise BrowserNavigationError("현재 지도 줌을 확인할 수 없음")
        for _ in range(12):
            if current == target:
                return
            name = "확대" if current < target else "축소"
            button = self.page.get_by_role("button", name=name, exact=True)
            await self._mutate(lambda: button.click(timeout=10_000))
            await self.page.wait_for_timeout(500)
            next_zoom = parse_zoom(self.page.url)
            if next_zoom is None or next_zoom == current:
                raise BrowserNavigationError(f"지도 줌 변경 실패: {current} -> {target}")
            current = next_zoom
        raise BrowserNavigationError(f"지도 줌 15 설정 실패: {current}")

    async def search_keyword(self, keyword: str) -> None:
        await self._submit_search(keyword)

    async def extract_candidates(self) -> tuple[list[CandidatePlace], dict[str, CandidateTarget]]:
        frame = await self._wait_frame(LIST_FRAME_PATTERN)
        await frame.wait_for_selector("li a[role=button]", timeout=20_000)
        rows = await frame.eval_on_selector_all(
            "li",
            """(items) => items.map((row, domIndex) => {
              const link = Array.from(row.querySelectorAll('a[role=button]'))
                .find((item) => !['저장', '더보기', '이전', '다음'].includes((item.textContent || '').trim()));
              return {
                domIndex,
                rawText: (row.innerText || '').replace(/\\s+/g, ' ').trim(),
                name: (link?.textContent || '').replace(/\\s+/g, ' ').trim(),
                href: link?.getAttribute('href') || '',
              };
            })""",
        )
        businesses = await frame.evaluate(
            """() => Object.entries(window.__APOLLO_STATE__ || {})
              .filter(([key, value]) => key.startsWith('PlaceListBusinessesItem:') && value)
              .map(([, value]) => ({id: value.id || value.apolloCacheId || '', name: value.name || ''}))"""
        )
        candidates, targets = parse_candidate_rows(rows, businesses)
        if not candidates:
            raise BrowserExtractionError("유효한 후보 장소를 찾지 못함")
        return candidates, targets
```

- [ ] **Step 4: 검색 계약 테스트 통과 확인**

Run: `uv run python -m unittest tests.test_naver_map_page -v`

Expected: `Ran 3 tests`, `OK`

- [ ] **Step 5: 검색·후보 추출 커밋**

```bash
git add src/datespot_agent/browser/naver_map.py tests/test_naver_map_page.py
git commit -m "feat: add naver map candidate navigation"
```

## Task 4: 우측 패널 상세·사진·리뷰 추출

**Files:**
- Modify: `src/datespot_agent/browser/naver_map.py`
- Modify: `tests/test_naver_map_page.py`

**Interfaces:**
- Consumes: `CandidatePlace`, `CandidateTarget`
- Produces: `NaverMapPage.extract_place_detail()`, `restore_search_list()`

- [ ] **Step 1: 상세 데이터 제한·복원 실패 테스트 추가**

```python
# tests/test_naver_map_page.py에 추가
from datespot_agent.browser.parsers import CandidateTarget
from datespot_agent.models import CandidatePlace


class NaverMapDetailContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_detail_result_keeps_five_photos_and_fifty_reviews(self):
        navigator = object.__new__(NaverMapPage)
        navigator.page = type("Page", (), {"frames": []})()
        navigator.pacer = FakePacer()
        navigator._blocked_response = None
        navigator.open_candidate = self._async_value(None)
        navigator.extract_home = self._async_value(("일식당", "서울 강남구 도산대로 15", 1234))
        navigator.extract_interior_photos = self._async_value([f"https://img/{i}.jpg" for i in range(5)])
        navigator.extract_recent_reviews = self._async_value([f"리뷰 {i}" for i in range(50)])
        navigator.restore_search_list = self._async_value(None)

        detail = await navigator.extract_place_detail(
            CandidatePlace(place_id="1150149433", name="치보 신사점"),
            CandidateTarget(place_id="1150149433", name="치보 신사점", dom_index=1),
        )

        self.assertEqual(detail.review_count, 1234)
        self.assertEqual(len(detail.photo_urls), 5)
        self.assertEqual(len(detail.reviews), 50)

    async def test_zero_review_count_is_normal_empty_data(self):
        navigator = object.__new__(NaverMapPage)
        self.assertEqual(await navigator.extract_recent_reviews("1150149433", 0), [])

    @staticmethod
    def _async_value(value):
        async def call(*_args, **_kwargs):
            return value
        return call
```

- [ ] **Step 2: 신규 상세 테스트 실패 확인**

Run: `uv run python -m unittest tests.test_naver_map_page.NaverMapDetailContractTests -v`

Expected: `AttributeError: 'NaverMapPage' object has no attribute 'extract_place_detail'`

- [ ] **Step 3: 우측 패널 상세 메서드 구현**

```python
# src/datespot_agent/browser/naver_map.py의 import에 추가
from datespot_agent.browser.parsers import (
    first_interior_urls,
    normalize_review_bodies,
    parse_home_text,
)
from datespot_agent.models import PlaceDetail

DETAIL_FRAME_TEMPLATE = r"pcmap\.place\.naver\.com/(?:restaurant|place)/{place_id}/"


# NaverMapPage에 추가
async def _entry_frame(self, place_id: str) -> Frame:
    return await self._wait_frame(re.compile(DETAIL_FRAME_TEMPLATE.format(place_id=re.escape(place_id))))

async def _wait_named_control(
    self,
    place_id: str,
    role: str,
    name: str,
    *,
    timeout_ms: int = 10_000,
    required: bool = True,
) -> tuple[Frame, Locator | None]:
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    frame = await self._entry_frame(place_id)
    while asyncio.get_running_loop().time() < deadline:
        frame = await self._entry_frame(place_id)
        roles = (role,) if role == "button" else (role, "button")
        for current_role in roles:
            control = frame.get_by_role(current_role, name=name, exact=True)
            if await control.count() and await control.first.is_visible():
                return frame, control.first
        await self.page.wait_for_timeout(250)
    if required:
        raise BrowserNavigationError(f"컨트롤을 찾지 못함: {role}/{name}", place_id=place_id)
    return frame, None

async def open_candidate(self, target: CandidateTarget) -> None:
    frame = await self._wait_frame(LIST_FRAME_PATTERN)
    row = frame.locator("li").nth(target.dom_index)
    link = row.locator("a[role=button]").filter(has_text=target.name).first
    await self._mutate(lambda: link.click(force=True, timeout=10_000))
    entry = await self._entry_frame(target.place_id)
    if f"/{target.place_id}/" not in entry.url:
        raise BrowserExtractionError(f"상세 장소 ID 불일치: {entry.url}")

async def extract_home(self, place_id: str, name: str) -> tuple[str | None, str | None, int]:
    frame = await self._entry_frame(place_id)
    lines = await frame.locator("body").inner_text(timeout=20_000)
    try:
        metadata = parse_home_text(lines.splitlines(), name)
    except ValueError as error:
        raise BrowserExtractionError("상세 홈 메타데이터 파싱 실패", place_id=place_id) from error
    return metadata.category, metadata.address, metadata.review_count

async def extract_interior_photos(self, place_id: str) -> list[str]:
    _, photo = await self._wait_named_control(place_id, "tab", "사진")
    assert photo is not None
    await self._mutate(lambda: photo.click(timeout=10_000))
    frame, interior = await self._wait_named_control(
        place_id,
        "button",
        "내부",
        timeout_ms=3_000,
        required=False,
    )
    if interior is None:
        return []
    await self._mutate(lambda: interior.click(timeout=10_000))
    try:
        await frame.wait_for_selector('img[alt^="INTERIOR_"]', timeout=3_000)
    except PlaywrightTimeoutError:
        return []
    images = await frame.eval_on_selector_all(
        'img[alt^="INTERIOR_"]',
        "(items) => items.map((item) => ({alt: item.alt || '', url: item.currentSrc || item.src || ''}))",
    )
    return first_interior_urls(images)

async def extract_recent_reviews(self, place_id: str, review_count: int) -> list[str]:
    if review_count == 0:
        return []
    _, review = await self._wait_named_control(place_id, "tab", "리뷰")
    assert review is not None
    await self._mutate(lambda: review.click(timeout=10_000))
    frame, recent = await self._wait_named_control(place_id, "option", "최신순")
    assert recent is not None
    await self._mutate(lambda: recent.click(timeout=10_000))
    for _ in range(20):
        selected = await recent.get_attribute("aria-selected")
        if "reviewSort=recent" in frame.url or selected == "true":
            break
        await self.page.wait_for_timeout(250)
    else:
        raise BrowserNavigationError("최신순 적용을 확인하지 못함", place_id=place_id)
    for _ in range(5):
        cards = frame.locator("li.place_apply_pui")
        if await cards.count() >= 50:
            break
        await self._mutate(lambda: frame.evaluate("window.scrollTo(0, document.body.scrollHeight)"))
        more = frame.get_by_role("button", name="펼쳐서 더보기", exact=True)
        if await more.count() == 0:
            break
        previous_count = await cards.count()
        await self._mutate(lambda: more.click(timeout=10_000))
        for _ in range(20):
            if await cards.count() > previous_count:
                break
            await self.page.wait_for_timeout(250)
    raw_reviews = await frame.locator("li.place_apply_pui").evaluate_all(
        """(rows) => rows.slice(0, 50).map((row) => {
          const semantic = row.querySelector('[data-pui-click-code="rvshowmore"]');
          const body = row.querySelector('div[class*="pui__vn15t2"]') || semantic?.parentElement;
          return (body?.innerText || '').replace(/더보기/g, '').replace(/\\s+/g, ' ').trim();
        })"""
    )
    return normalize_review_bodies(raw_reviews)

async def restore_search_list(self, place_id: str) -> None:
    frame = await self._entry_frame(place_id)
    close = frame.get_by_role("button", name=re.compile("페이지 닫기"))
    if await close.count() == 0:
        raise BrowserNavigationError("상세 패널 닫기 버튼을 찾지 못함", place_id=place_id)
    await self._mutate(lambda: close.first.click(timeout=10_000))
    await self._wait_frame(LIST_FRAME_PATTERN)
    for _ in range(20):
        if not any(f"/{place_id}/" in item.url for item in self.page.frames):
            return
        await self.page.wait_for_timeout(250)
    raise BrowserNavigationError("상세 패널이 닫히지 않음", place_id=place_id)

async def extract_place_detail(
    self,
    candidate: CandidatePlace,
    target: CandidateTarget,
) -> PlaceDetail:
    blocked = False
    try:
        await self.open_candidate(target)
        category, address, review_count = await self.extract_home(candidate.place_id, candidate.name)
        photos = await self.extract_interior_photos(candidate.place_id)
        reviews = await self.extract_recent_reviews(candidate.place_id, review_count)
        return PlaceDetail(
            place_id=candidate.place_id,
            name=candidate.name,
            category=category,
            address=address,
            photo_urls=photos[:5],
            reviews=reviews[:50],
            review_count=review_count,
        )
    except BrowserAccessBlockedError:
        blocked = True
        raise
    finally:
        entry_exists = any(f"/{candidate.place_id}/" in frame.url for frame in self.page.frames)
        if entry_exists and not blocked:
            await self.restore_search_list(candidate.place_id)
```

- [ ] **Step 4: 상세 계약 테스트 통과 확인**

Run: `uv run python -m unittest tests.test_naver_map_page -v`

Expected: `Ran 5 tests`, `OK`

- [ ] **Step 5: 상세 추출 커밋**

```bash
git add src/datespot_agent/browser/naver_map.py tests/test_naver_map_page.py
git commit -m "feat: extract naver place panel details"
```

## Task 5: 실행별 BrowserService 세션·재시도·정리

**Files:**
- Create: `src/datespot_agent/browser/service.py`
- Modify: `src/datespot_agent/browser/__init__.py`
- Create: `tests/test_browser_service.py`

**Interfaces:**
- Consumes: `NaverMapPage`, `InteractionPacer`, `RunConfig`, `CandidatePlace`
- Produces: 설계서의 공개 `BrowserService` 5개 async 메서드

- [ ] **Step 1: 세션·캐시·재시도·멱등 종료 실패 테스트 작성**

```python
# tests/test_browser_service.py
import unittest

from datespot_agent.browser.errors import BrowserAccessBlockedError, BrowserNavigationError, BrowserSessionError
from datespot_agent.browser.parsers import CandidateTarget
from datespot_agent.browser.service import BrowserService, BrowserSession
from datespot_agent.models import CandidatePlace, RunConfig


class FakeNavigator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def search_location(self, value): self.calls.append(f"location:{value}")
    async def select_station(self, value): self.calls.append(f"station:{value}")
    async def set_zoom(self, value): self.calls.append(f"zoom:{value}")
    async def search_keyword(self, value): self.calls.append(f"keyword:{value}")
    async def extract_candidates(self):
        candidate = CandidatePlace(place_id="1", name="치보")
        return [candidate], {"1": CandidateTarget(place_id="1", name="치보", dom_index=0)}


class FakePacer:
    def __init__(self) -> None:
        self.retry_waits = 0

    async def run(self, action):
        return await action()

    async def wait_before_retry(self) -> None:
        self.retry_waits += 1


class BrowserServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_uses_fixed_order_without_max_places_slice(self):
        service = BrowserService(pacer=FakePacer())
        navigator = FakeNavigator()
        service._sessions["run-1"] = BrowserSession(None, None, None, None, navigator, {})

        result = await service.search_candidates("run-1", RunConfig(location="신사역", search_keyword="일식", max_places=1))

        self.assertEqual([item.place_id for item in result], ["1"])
        self.assertEqual(navigator.calls, ["location:신사역", "station:신사역", "zoom:15", "keyword:일식"])

    async def test_navigation_failure_retries_once_after_wait(self):
        pacer = FakePacer()
        service = BrowserService(pacer=pacer)
        attempts = 0
        recoveries = 0

        async def operation():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("frame changed")
            return "ok"

        async def recover():
            nonlocal recoveries
            recoveries += 1

        result = await service._run_with_retry(
            "run-1",
            "search",
            operation,
            BrowserNavigationError,
            recover=recover,
        )
        self.assertEqual(result, "ok")
        self.assertEqual(attempts, 2)
        self.assertEqual(recoveries, 1)
        self.assertEqual(pacer.retry_waits, 1)

    async def test_access_block_is_never_retried(self):
        service = BrowserService(pacer=FakePacer())
        attempts = 0

        async def operation():
            nonlocal attempts
            attempts += 1
            raise BrowserAccessBlockedError("429")

        with self.assertRaises(BrowserAccessBlockedError):
            await service._run_with_retry("run-1", "search", operation, BrowserNavigationError)
        self.assertEqual(attempts, 1)

    async def test_missing_and_closed_sessions_are_safe(self):
        service = BrowserService(pacer=FakePacer())
        with self.assertRaises(BrowserSessionError):
            await service.search_candidates("missing", RunConfig(location="신사역", search_keyword="일식"))
        await service.close_session("missing")
        await service.close_all()
```

- [ ] **Step 2: service 테스트가 import 실패하는지 확인**

Run: `uv run python -m unittest tests.test_browser_service -v`

Expected: `ModuleNotFoundError` 또는 `ImportError`로 실패

- [ ] **Step 3: 세션 타입과 공개 서비스 구현**

```python
# src/datespot_agent/browser/service.py
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from datespot_agent.browser.errors import (
    BrowserAccessBlockedError,
    BrowserExtractionError,
    BrowserNavigationError,
    BrowserServiceError,
    BrowserSessionError,
)
from datespot_agent.browser.naver_map import NaverMapPage
from datespot_agent.browser.pacing import InteractionPacer
from datespot_agent.browser.parsers import CandidateTarget
from datespot_agent.models import CandidatePlace, PlaceDetail, RunConfig

T = TypeVar("T")


@dataclass(slots=True)
class BrowserSession:
    playwright: Playwright | None
    browser: Browser | None
    context: BrowserContext | None
    page: Page | None
    navigator: NaverMapPage
    candidate_targets: dict[str, CandidateTarget] = field(default_factory=dict)


class BrowserService:
    def __init__(self, *, headless: bool = True, pacer: InteractionPacer | None = None) -> None:
        self._headless = headless
        self._pacer = pacer or InteractionPacer()
        self._sessions: dict[str, BrowserSession] = {}

    def _session(self, run_id: str) -> BrowserSession:
        session = self._sessions.get(run_id)
        if session is None:
            raise BrowserSessionError("브라우저 세션을 찾지 못함", run_id=run_id)
        return session

    async def start_session(self, run_id: str) -> None:
        if run_id in self._sessions:
            raise BrowserSessionError("이미 존재하는 브라우저 세션", run_id=run_id)
        runtime = await async_playwright().start()
        browser = context = page = None
        try:
            browser = await runtime.chromium.launch(headless=self._headless)
            context = await browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul", viewport={"width": 1440, "height": 1000})
            page = await context.new_page()
            navigator = NaverMapPage(page, self._pacer)
            await navigator.open()
            self._sessions[run_id] = BrowserSession(runtime, browser, context, page, navigator)
        except Exception:
            if page is not None:
                await page.close()
            if context is not None:
                await context.close()
            if browser is not None:
                await browser.close()
            await runtime.stop()
            raise

    async def _run_with_retry(
        self,
        run_id: str,
        step: str,
        operation: Callable[[], Awaitable[T]],
        error_type: type[BrowserServiceError],
        *,
        place_id: str | None = None,
        recover: Callable[[], Awaitable[None]] | None = None,
    ) -> T:
        for attempt in (1, 2):
            try:
                return await operation()
            except (BrowserAccessBlockedError, BrowserSessionError):
                raise
            except Exception as error:
                if attempt == 2:
                    final_type = type(error) if isinstance(error, BrowserServiceError) else error_type
                    raise final_type(str(error), run_id=run_id, step=step, place_id=place_id) from error
                if recover is not None:
                    await recover()
                await self._pacer.wait_before_retry()
        raise AssertionError("unreachable retry state")

    async def search_candidates(self, run_id: str, config: RunConfig) -> list[CandidatePlace]:
        session = self._session(run_id)
        session.candidate_targets.clear()

        async def operation() -> list[CandidatePlace]:
            await session.navigator.search_location(config.location)
            await session.navigator.select_station(config.location)
            await session.navigator.set_zoom(15)
            await session.navigator.search_keyword(config.search_keyword)
            candidates, targets = await session.navigator.extract_candidates()
            session.candidate_targets = targets
            return candidates

        return await self._run_with_retry(
            run_id,
            "search_candidates",
            operation,
            BrowserNavigationError,
            recover=session.navigator.open,
        )

    async def extract_place_detail(self, run_id: str, candidate: CandidatePlace) -> PlaceDetail:
        session = self._session(run_id)
        target = session.candidate_targets.get(candidate.place_id)
        if target is None:
            raise BrowserExtractionError("캐시된 후보 target을 찾지 못함", run_id=run_id, step="extract_place_detail", place_id=candidate.place_id)
        return await self._run_with_retry(
            run_id,
            "extract_place_detail",
            lambda: session.navigator.extract_place_detail(candidate, target),
            BrowserExtractionError,
            place_id=candidate.place_id,
        )

    async def close_session(self, run_id: str) -> None:
        session = self._sessions.pop(run_id, None)
        if session is None:
            return
        for resource in (session.page, session.context, session.browser):
            if resource is None:
                continue
            try:
                await resource.close()
            except Exception:
                pass
        if session.playwright is not None:
            try:
                await session.playwright.stop()
            except Exception:
                pass

    async def close_all(self) -> None:
        for run_id in list(self._sessions):
            await self.close_session(run_id)
```

```python
# src/datespot_agent/browser/__init__.py
from datespot_agent.browser.errors import (
    BrowserAccessBlockedError,
    BrowserExtractionError,
    BrowserNavigationError,
    BrowserServiceError,
    BrowserSessionError,
)
from datespot_agent.browser.service import BrowserService

__all__ = [
    "BrowserAccessBlockedError",
    "BrowserExtractionError",
    "BrowserNavigationError",
    "BrowserService",
    "BrowserServiceError",
    "BrowserSessionError",
]
```

- [ ] **Step 4: service 테스트 통과 확인**

Run: `uv run python -m unittest tests.test_browser_service -v`

Expected: `Ran 4 tests`, `OK`

- [ ] **Step 5: 세션·서비스 커밋**

```bash
git add src/datespot_agent/browser/__init__.py src/datespot_agent/browser/service.py tests/test_browser_service.py
git commit -m "feat: add browser service sessions"
```

## Task 6: 네트워크 없는 iframe 통합 테스트

**Files:**
- Create: `tests/fixtures/naver_map_shell.html`
- Create: `tests/fixtures/naver_search_results.html`
- Create: `tests/fixtures/naver_entry.html`
- Create: `tests/test_browser_integration.py`

**Interfaces:**
- Consumes: 실제 Playwright Chromium, `NaverMapPage`, 가짜 clock·sleeper
- Produces: map shell → searchIframe → entryIframe → 목록 복원 전체 흐름 검증

- [ ] **Step 1: 로컬 route fixture 작성**

```html
<!-- tests/fixtures/naver_map_shell.html -->
<!doctype html><html><body>
<input role="combobox" aria-label="검색" id="search">
<button role="option" id="option"></button>
<button aria-label="확대" id="zoom-in">확대</button>
<button aria-label="축소" id="zoom-out">축소</button>
<iframe id="searchIframe" name="searchIframe" src="https://pcmap.place.naver.com/place/list?mode=station"></iframe>
<script>
let query = '';
const input = document.querySelector('#search');
const option = document.querySelector('#option');
input.addEventListener('input', () => { query = input.value; option.textContent = `검색어 ${query}`; option.setAttribute('aria-label', `검색어 ${query}`); });
option.addEventListener('click', () => { history.pushState({}, '', `/p/search/${encodeURIComponent(query)}?c=127,37,14,0,0,0,dh`); if (query === '일식') document.querySelector('#searchIframe').src = 'https://pcmap.place.naver.com/restaurant/list?mode=food'; });
document.querySelector('#zoom-in').addEventListener('click', () => history.replaceState({}, '', location.pathname + '?c=127,37,15,0,0,0,dh'));
window.addEventListener('message', (event) => {
  if (event.data.station) history.pushState({}, '', '/p/subway-station/1907?c=127,37,14,0,0,0,dh');
  if (event.data.openPlace) {
    const frame = document.createElement('iframe'); frame.id = 'entryIframe'; frame.name = 'entryIframe'; frame.src = `https://pcmap.place.naver.com/restaurant/${event.data.openPlace}/home`; document.body.append(frame);
  }
  if (event.data.closeEntry) document.querySelector('#entryIframe')?.remove();
});
</script></body></html>
```

```html
<!-- tests/fixtures/naver_search_results.html -->
<!doctype html><html><body><ul id="results"></ul><script>
const mode = new URL(location.href).searchParams.get('mode');
if (mode === 'station') {
  document.querySelector('#results').innerHTML = '<li><button aria-label="신사역 신분당선지하철,전철">신사역 신분당선</button></li>';
  document.querySelector('button').onclick = () => parent.postMessage({station: true}, '*');
} else {
  window.__APOLLO_STATE__ = {'PlaceListBusinessesItem:1': {id: '1150149433', name: '치보 신사점'}};
  document.querySelector('#results').innerHTML = '<li><a role="button">치보 신사점</a><span>일식당</span></li><li><a role="button" href="/restaurant/9">광고집</a><span>광고</span></li>';
  document.querySelector('a').onclick = () => parent.postMessage({openPlace: '1150149433'}, '*');
}
</script></body></html>
```

```html
<!-- tests/fixtures/naver_entry.html -->
<!doctype html><html><body>
<button id="photo">사진</button><button id="review">리뷰</button><button aria-label="페이지 닫기">닫기</button>
<main id="content"><h1>치보 신사점</h1><p>일식당</p><p>서울 강남구 도산대로 15</p><p>방문자 리뷰 1,234</p></main>
<script>
const content = document.querySelector('#content');
document.querySelector('#photo').onclick = () => { content.innerHTML = '<button id="interior">내부</button><section id="photos"></section>'; document.querySelector('#interior').onclick = () => { document.querySelector('#photos').innerHTML = Array.from({length: 7}, (_, i) => `<img alt="INTERIOR_${i}" src="https://img/${i}.jpg">`).join(''); }; };
document.querySelector('#review').onclick = () => { content.innerHTML = '<button role="option" id="recent">최신순</button><ul id="cards"></ul><button id="more">펼쳐서 더보기</button>'; let count = 0; const append = () => { for (let i = 0; i < 10; i += 1) document.querySelector('#cards').insertAdjacentHTML('beforeend', `<li class="place_apply_pui"><div class="pui__vn15t2">리뷰 ${count++}</div></li>`); if (count >= 50) document.querySelector('#more')?.remove(); }; append(); document.querySelector('#recent').onclick = () => document.querySelector('#recent').setAttribute('aria-selected', 'true'); document.querySelector('#more').onclick = append; };
document.querySelector('[aria-label="페이지 닫기"]').onclick = () => parent.postMessage({closeEntry: true}, '*');
</script></body></html>
```

- [ ] **Step 2: 전체 흐름 통합 테스트 작성**

```python
# tests/test_browser_integration.py
import unittest
from pathlib import Path

from playwright.async_api import async_playwright

from datespot_agent.browser.naver_map import NaverMapPage
from datespot_agent.browser.pacing import InteractionPacer
from datespot_agent.models import RunConfig

FIXTURES = Path(__file__).parent / "fixtures"


class BrowserIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_map_shell_candidate_and_detail_flow_without_network(self):
        now = 0.0

        def clock() -> float:
            return now

        async def sleep(seconds: float) -> None:
            nonlocal now
            now += seconds

        async with async_playwright() as runtime:
            browser = await runtime.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            async def route_handler(route):
                url = route.request.url
                if url.startswith("https://map.naver.com"):
                    path = FIXTURES / "naver_map_shell.html"
                elif "/list" in url:
                    path = FIXTURES / "naver_search_results.html"
                else:
                    path = FIXTURES / "naver_entry.html"
                await route.fulfill(status=200, content_type="text/html", body=path.read_text(encoding="utf-8"))

            await page.route("**/*", route_handler)
            navigator = NaverMapPage(page, InteractionPacer(clock=clock, sleep=sleep))
            await navigator.open()
            config = RunConfig(location="신사역", search_keyword="일식")
            await navigator.search_location(config.location)
            await navigator.select_station(config.location)
            await navigator.set_zoom(15)
            await navigator.search_keyword(config.search_keyword)
            candidates, targets = await navigator.extract_candidates()
            detail = await navigator.extract_place_detail(candidates[0], targets[candidates[0].place_id])

            self.assertEqual(page.url.split("?")[0], "https://map.naver.com/p/search/%EC%9D%BC%EC%8B%9D")
            self.assertEqual(len(detail.photo_urls), 5)
            self.assertEqual(len(detail.reviews), 50)
            self.assertEqual(detail.review_count, 1234)
            self.assertIsNotNone(page.frame(name="searchIframe"))
            self.assertIsNone(page.frame(name="entryIframe"))
            await context.close()
            await browser.close()
```

- [ ] **Step 3: 기존 단위 구현의 통합 동작 확인**

Run: `uv run python -m unittest tests.test_browser_integration -v`

Expected: 실사이트 요청 없이 전체 iframe 흐름 `OK`

- [ ] **Step 4: route fixture가 외부 요청을 만들지 않는지 확인**

Run: `rg -n 'route\.continue_|route\.fallback' tests/test_browser_integration.py`

Expected: 검색 결과 없음, 종료 코드 `1`

- [ ] **Step 5: 통합 및 전체 단위 테스트 통과 확인**

Run: `uv run python -m unittest tests.test_browser_integration tests.test_browser_parsers tests.test_browser_pacing tests.test_naver_map_page tests.test_browser_service -v`

Expected: 신규 BrowserService 테스트 전체 `OK`

- [ ] **Step 6: 로컬 통합 테스트 커밋**

```bash
git add tests/fixtures/naver_map_shell.html tests/fixtures/naver_search_results.html tests/fixtures/naver_entry.html tests/test_browser_integration.py src/datespot_agent/browser/naver_map.py
git commit -m "test: cover browser service iframe flow"
```

## Task 7: cooldown이 강제된 실사이트 스모크

**Files:**
- Create: `poc/2-3-browser-service/live_smoke.py`
- Create: `poc/2-3-browser-service/README.md`

**Interfaces:**
- Consumes: `BrowserService`, `LiveSmokeGuard`
- Produces: 신사역 → 줌 15 → 일식 → 첫 후보 상세 1건 수동 검증 명령

- [ ] **Step 1: 실사이트 스모크 실행기 작성**

```python
# poc/2-3-browser-service/live_smoke.py
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from datespot_agent.browser import BrowserAccessBlockedError, BrowserService
from datespot_agent.browser.pacing import LiveSmokeGuard
from datespot_agent.models import RunConfig

STAMP_PATH = Path.home() / ".cache" / "datespot-agent" / "naver-live-smoke-finished-at"


async def run() -> int:
    run_id = f"live-{uuid4()}"
    service = BrowserService(headless=False)
    async with LiveSmokeGuard(stamp_path=STAMP_PATH):
        try:
            await service.start_session(run_id)
            candidates = await service.search_candidates(
                run_id,
                RunConfig(location="신사역", search_keyword="일식", max_places=1),
            )
            detail = await service.extract_place_detail(run_id, candidates[0])
            print(json.dumps(detail.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2))
            return 0
        except BrowserAccessBlockedError as error:
            print(f"접근 제한 감지로 즉시 중단함: {error}")
            return 2
        finally:
            await service.close_all()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
```

- [ ] **Step 2: 수동 실행 정책 문서화**

```markdown
<!-- poc/2-3-browser-service/README.md -->
# 2-3 BrowserService 실사이트 스모크

기본 테스트 탐색과 CI에서는 실행하지 않음.

```bash
uv run python poc/2-3-browser-service/live_smoke.py
```

- UI 상태 변경 사이 최소 3초 자동 대기
- 실패 재시도 전 최소 5초 자동 대기
- 프로세스 잠금으로 병렬 실행 거부
- 이전 실행 종료 후 30초 미경과 시 남은 시간 자동 대기
- 403, 429, CAPTCHA, 접근 제한 화면 감지 시 추가 조작 없이 종료 코드 2
- 한 번 실행 후 결과를 확인하고 반복 실행을 피함
```

- [ ] **Step 3: 실사이트 요청 없이 import·컴파일 확인**

Run: `uv run python -m compileall -q src tests poc/2-3-browser-service`

Expected: 출력 없음, 종료 코드 `0`

- [ ] **Step 4: 사용자가 허용한 한 번의 실사이트 스모크 실행**

Run: `uv run python poc/2-3-browser-service/live_smoke.py`

Expected: 브라우저가 열린 뒤 모든 조작에 3초 간격이 적용되고, `PlaceDetail` JSON에 `photoUrls` 최대 5개·`reviews` 최대 50개·`reviewCount`가 출력되며 종료 코드 `0`

접근 제한 신호가 보이면 종료 코드 `2`를 그대로 기록하고 같은 작업 세션에서 다시 실행하지 않는다.

- [ ] **Step 5: 실사이트 스모크 커밋**

```bash
git add poc/2-3-browser-service/live_smoke.py poc/2-3-browser-service/README.md
git commit -m "test: add paced browser service smoke"
```

## Task 8: 전체 회귀 검증과 로드맵 변경 준비

**Files:**
- Modify: `README.md:82-86`

**Interfaces:**
- Consumes: Task 1~7 전체 결과
- Produces: 최종 검증 기록과 기존 사용자 변경을 보존한 README 완료 문구

- [ ] **Step 1: 전체 단위·로컬 통합 테스트 실행**

Run: `uv run python -m unittest discover -s tests -v`

Expected: 기존 48개와 신규 BrowserService 테스트 전부 통과, `FAILED`와 `ERROR` 0개

- [ ] **Step 2: 환경 스모크와 컴파일 실행**

Run: `uv run python poc/1-1-env/smoke_test.py`

Expected: 핵심 패키지 import, 설정 로드, Playwright Chromium 헤드리스 실행 모두 `[OK]`, 종료 코드 `0`

Run: `uv run python -m compileall -q src tests poc/2-3-browser-service`

Expected: 출력 없음, 종료 코드 `0`

- [ ] **Step 3: diff와 금지 계약 정적 확인**

Run: `git diff --check`

Expected: 출력 없음, 종료 코드 `0`

Run: `rg -n 'page\.goto\(.+pcmap\.place\.naver\.com|max_distance_m|distance_m' src/datespot_agent/browser tests/test_browser_*.py`

Expected: 검색 결과 없음, 종료 코드 `1`

- [ ] **Step 4: README의 2-3 항목만 완료 문구로 변경**

```markdown
- [x] **2-3 BrowserService 연동**: 우측 패널 기반 후보·내부 사진 5장·최신 리뷰 50개 추출과 실사이트 pacing 적용
```

기존 README 변경분을 먼저 `git diff -- README.md`로 확인하고 `apply_patch`로 위 한 줄만
변경한다. README는 기존 사용자 변경과 같은 hunk에 있으므로 스테이징하지 않는다.

- [ ] **Step 5: 최종 상태와 테스트 수 확인**

Run: `git status --short && git diff --check && uv run python -m unittest discover -s tests -v`

Expected: README와 기존 `.playwright-cli/`만 미커밋으로 남고, 전체 테스트 `OK`

- [ ] **Step 6: 사용자 소유 작업트리 보존 상태 보고**

Run: `git diff -- README.md && git status --short`

Expected: README에 기존 사용자 변경과 2-3 완료 문구가 함께 남고, `.playwright-cli/`가
스테이징되지 않은 상태로 표시됨
