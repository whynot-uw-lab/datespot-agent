"""네이버지도 map shell과 우측 패널 UI 조작."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import TypeVar

from playwright.async_api import Frame, Page, Response

from datespot_agent.browser.errors import (
    BrowserAccessBlockedError,
    BrowserExtractionError,
    BrowserNavigationError,
)
from datespot_agent.browser.pacing import InteractionPacer
from datespot_agent.browser.parsers import (
    CandidateTarget,
    parse_candidate_rows,
    parse_zoom,
)
from datespot_agent.models import CandidatePlace

MAP_URL = "https://map.naver.com"
LIST_FRAME_PATTERN = re.compile(
    r"pcmap\.place\.naver\.com/(?:restaurant|place)/list"
)
BLOCK_TEXT_PATTERN = re.compile(
    r"CAPTCHA|비정상적인 접근|서비스 이용이 제한|접근이 제한",
    re.IGNORECASE,
)
T = TypeVar("T")


class NaverMapPage:
    """네이버지도 UI 경로를 결정적으로 실행한다."""

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
            raise BrowserAccessBlockedError(
                f"네이버 접근 제한 응답: {status} {url}"
            )
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

    async def _wait_frame(
        self,
        pattern: re.Pattern[str],
        timeout_ms: int = 20_000,
    ) -> Frame:
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while asyncio.get_running_loop().time() < deadline:
            frame = next(
                (item for item in self.page.frames if pattern.search(item.url)),
                None,
            )
            if frame is not None:
                return frame
            await self.page.wait_for_timeout(250)
        raise BrowserNavigationError(
            f"frame을 찾지 못함: {pattern.pattern}"
        )

    async def open(self) -> None:
        await self._mutate(
            lambda: self.page.goto(
                MAP_URL,
                wait_until="domcontentloaded",
                timeout=20_000,
            )
        )

    async def _submit_search(self, query: str) -> None:
        combobox = self.page.get_by_role("combobox")
        await self._mutate(
            lambda: combobox.fill(query, timeout=20_000)
        )
        option = self.page.get_by_role(
            "option",
            name=f"검색어 {query}",
            exact=True,
        )
        await self._mutate(lambda: option.click(timeout=20_000))
        await self.page.wait_for_url(
            re.compile(r"/p/search/"),
            timeout=20_000,
        )

    async def search_location(self, location: str) -> None:
        await self._submit_search(location)

    async def select_station(self, location: str) -> None:
        frame = await self._wait_frame(LIST_FRAME_PATTERN)
        station = frame.get_by_role(
            "button",
            name=re.compile(
                rf"^{re.escape(location)}.*(?:지하철|전철|선)"
            ),
        )
        if await station.count() == 0:
            raise BrowserNavigationError(
                f"역 검색 결과를 찾지 못함: {location}"
            )
        await self._mutate(
            lambda: station.first.click(force=True, timeout=10_000)
        )
        await self.page.wait_for_url(
            re.compile(r"subway-station/"),
            timeout=20_000,
        )

    async def set_zoom(self, target: int = 15) -> None:
        current = parse_zoom(self.page.url)
        if current is None:
            raise BrowserNavigationError("현재 지도 줌을 확인할 수 없음")
        for _ in range(12):
            if current == target:
                return
            name = "확대" if current < target else "축소"
            button = self.page.get_by_role(
                "button",
                name=name,
                exact=True,
            )
            await self._mutate(lambda: button.click(timeout=10_000))
            await self.page.wait_for_timeout(500)
            next_zoom = parse_zoom(self.page.url)
            if next_zoom is None or next_zoom == current:
                raise BrowserNavigationError(
                    f"지도 줌 변경 실패: {current} -> {target}"
                )
            current = next_zoom
        raise BrowserNavigationError(
            f"지도 줌 {target} 설정 실패: {current}"
        )

    async def search_keyword(self, keyword: str) -> None:
        await self._submit_search(keyword)

    async def extract_candidates(
        self,
    ) -> tuple[list[CandidatePlace], dict[str, CandidateTarget]]:
        frame = await self._wait_frame(LIST_FRAME_PATTERN)
        await frame.wait_for_selector(
            "li a[role=button]",
            timeout=20_000,
        )
        rows = await frame.eval_on_selector_all(
            "li",
            """(items) => items.map((row, domIndex) => {
              const link = Array.from(row.querySelectorAll('a[role=button]'))
                .find((item) => !['저장', '더보기', '이전', '다음']
                  .includes((item.textContent || '').trim()));
              return {
                domIndex,
                rawText: (row.innerText || '').replace(/\\s+/g, ' ').trim(),
                name: (link?.textContent || '').replace(/\\s+/g, ' ').trim(),
                href: link?.getAttribute('href') || '',
              };
            })""",
        )
        try:
            businesses = await frame.evaluate(
                """() => Object.entries(window.__APOLLO_STATE__ || {})
                  .filter(([key, value]) => key.startsWith('PlaceListBusinessesItem:') && value)
                  .map(([, value]) => ({
                    id: value.id || value.apolloCacheId || '',
                    name: value.name || '',
                  }))"""
            )
        except Exception:
            businesses = []
        candidates, targets = parse_candidate_rows(rows, businesses)
        if not candidates:
            raise BrowserExtractionError("유효한 후보 장소를 찾지 못함")
        return candidates, targets
