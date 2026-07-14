"""네이버지도 map shell과 우측 패널 UI 조작."""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import TypeVar

from playwright.async_api import (
    Frame,
    Locator,
    Page,
    Response,
    TimeoutError as PlaywrightTimeoutError,
)

from datespot_agent.browser.errors import (
    BrowserAccessBlockedError,
    BrowserExtractionError,
    BrowserNavigationError,
)
from datespot_agent.browser.pacing import InteractionPacer
from datespot_agent.browser.parsers import (
    CandidateTarget,
    first_interior_urls,
    normalize_review_bodies,
    parse_candidate_rows,
    parse_home_text,
    parse_zoom,
)
from datespot_agent.models import CandidatePlace, PlaceDetail

MAP_URL = "https://map.naver.com"
LIST_FRAME_PATTERN = re.compile(
    r"pcmap\.place\.naver\.com/(?:restaurant|place)/list"
)
VISIBLE_BLOCK_PATTERNS = (
    re.compile(r"CAPTCHA", re.IGNORECASE),
    re.compile(r"비정상적인 접근", re.IGNORECASE),
    re.compile(r"서비스 이용이 제한", re.IGNORECASE),
    re.compile(r"접근이 제한", re.IGNORECASE),
    re.compile(r"보안 확인을 완료", re.IGNORECASE),
    re.compile(r"실제 사용자임을 확인", re.IGNORECASE),
    re.compile(r"스팸을 방지", re.IGNORECASE),
)
BLOCKED_DUMP_DIR = Path("artifacts/browser")
BLOCK_RECHECK_SECONDS = 10.0
BLOCK_RESPONSE_TTL_SECONDS = 15.0
T = TypeVar("T")
DETAIL_FRAME_TEMPLATE = (
    r"pcmap\.place\.naver\.com/(?:restaurant|place)/{place_id}/"
)


class NaverMapPage:
    """네이버지도 UI 경로를 결정적으로 실행한다."""

    def __init__(
        self,
        page: Page,
        pacer: InteractionPacer,
        *,
        run_id: str,
        dump_dir: Path = BLOCKED_DUMP_DIR,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.page = page
        self.pacer = pacer
        self.run_id = run_id
        self.dump_dir = dump_dir
        self.log = log or (lambda _: None)
        self._blocked_response: tuple[int, str, float] | None = None
        self._blocked_message: str | None = None
        page.on("response", self._observe_response)

    def _observe_response(self, response: Response) -> None:
        if response.status in {403, 429} and "naver.com" in response.url:
            self._blocked_response = (
                response.status,
                response.url,
                time.monotonic(),
            )

    # /** 브라우저 내부 로그를 run 기준으로 남김. */
    def _emit(self, message: str) -> None:
        self.log(f"[run:{self.run_id}] {message}")

    # /** 차단 시점의 페이지 상태를 파일로 남김. */
    async def _dump_blocked_state(self) -> tuple[Path | None, Path | None]:
        if self.page is None:
            return None, None
        dump_root = self.dump_dir / self.run_id
        dump_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")
        screenshot_path = dump_root / f"blocked_{stamp}.png"
        html_path = dump_root / f"blocked_{stamp}.html"
        try:
            await self.page.screenshot(
                path=str(screenshot_path),
                full_page=True,
            )
        except Exception:
            screenshot_path = None
        try:
            html_path.write_text(
                await self.page.content(),
                encoding="utf-8",
            )
        except Exception:
            html_path = None
        return screenshot_path, html_path

    # /** 최근 403/429 응답을 현재 차단 신호로 간주할지 판단함. */
    def _recent_blocked_response(self) -> tuple[int, str] | None:
        if self._blocked_response is None:
            return None
        status, url, observed_at = self._blocked_response
        if time.monotonic() - observed_at > BLOCK_RESPONSE_TTL_SECONDS:
            self._blocked_response = None
            return None
        return status, url

    # /** 실제로 보이는 차단 UI 텍스트만 찾음. */
    async def _visible_block_signal(self) -> tuple[Frame, str] | None:
        if self.page is None:
            return None
        for frame in self.page.frames:
            for pattern in VISIBLE_BLOCK_PATTERNS:
                locator = frame.get_by_text(pattern)
                try:
                    if await locator.count() == 0:
                        continue
                    if await locator.first.is_visible():
                        return frame, pattern.pattern
                except Exception:
                    continue
        return None

    # /** 현재 시점의 차단 사유를 요약함. */
    async def _current_block_reason(self) -> str | None:
        visible_block = await self._visible_block_signal()
        if visible_block is not None:
            frame, signal = visible_block
            return (
                "네이버 접근 제한 화면 감지: "
                f"page={self.page.url}, frame={frame.url}, signal={signal}"
            )
        blocked_response = self._recent_blocked_response()
        if blocked_response is None:
            return None
        status, url = blocked_response
        return f"네이버 접근 제한 응답: {status} {url}"

    # /** 수동 해제될 때까지 차단 상태를 주기적으로 다시 확인함. */
    async def _wait_until_access_restored(self, reason: str) -> None:
        self._emit(
            "보안 확인 감지, 수동 해제 대기 시작: "
            f"{reason}, {int(BLOCK_RECHECK_SECONDS)}초 후 재확인"
        )
        while True:
            await self.page.wait_for_timeout(
                int(BLOCK_RECHECK_SECONDS * 1000)
            )
            current_reason = await self._current_block_reason()
            if current_reason is None:
                self._blocked_response = None
                self._blocked_message = None
                self._emit("보안 확인 해제 감지, 작업 재개")
                return
            self._emit(
                "보안 확인 대기 중, "
                f"{int(BLOCK_RECHECK_SECONDS)}초 후 재확인"
            )

    async def _assert_access_allowed(self) -> None:
        if self.page is None:
            return
        current_reason = await self._current_block_reason()
        if current_reason is None:
            return
        if self._blocked_message is None:
            screenshot_path, html_path = await self._dump_blocked_state()
            details = [current_reason]
            if screenshot_path is not None:
                details.append(f"screenshot={screenshot_path}")
            if html_path is not None:
                details.append(f"html={html_path}")
            self._blocked_message = ", ".join(details)
        await self._wait_until_access_restored(self._blocked_message)

    async def _mutate(self, action: Callable[[], Awaitable[T]]) -> T:
        await self._assert_access_allowed()

        async def guarded_action() -> T:
            await self._assert_access_allowed()
            return await action()

        result = await self.pacer.run(guarded_action)
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

    async def _wait_page_url(
        self,
        pattern: re.Pattern[str],
        timeout_ms: int = 20_000,
    ) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while asyncio.get_running_loop().time() < deadline:
            if pattern.search(self.page.url):
                return
            await self.page.wait_for_timeout(100)
        raise BrowserNavigationError(
            f"URL 변경 실패: pattern={pattern.pattern}, current={self.page.url}"
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
        await self._wait_page_url(re.compile(r"/p/search/"))

    async def search_location(self, location: str) -> None:
        await self._submit_search(location)

    async def select_station(self, location: str) -> None:
        frame = await self._wait_frame(LIST_FRAME_PATTERN)
        station_prefix = re.escape(location)
        if location.endswith("역"):
            station_prefix = (
                rf"{re.escape(location[:-1])}(?:\([^)]*\))?역"
            )
        station = frame.get_by_role(
            "button",
            name=re.compile(
                rf"^{station_prefix}.*(?:지하철|전철|선)"
            ),
        )
        if await station.count() == 0:
            raise BrowserNavigationError(
                f"역 검색 결과를 찾지 못함: {location}"
            )
        await self._mutate(
            lambda: station.first.click(timeout=10_000)
        )
        await self._wait_page_url(re.compile(r"subway-station/"))

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

    async def _entry_frame(self, place_id: str) -> Frame:
        return await self._wait_frame(
            re.compile(
                DETAIL_FRAME_TEMPLATE.format(
                    place_id=re.escape(place_id)
                )
            )
        )

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
                control = frame.get_by_role(
                    current_role,
                    name=name,
                    exact=True,
                )
                if await control.count() and await control.first.is_visible():
                    return frame, control.first
            await self.page.wait_for_timeout(250)
        if required:
            raise BrowserNavigationError(
                f"컨트롤을 찾지 못함: {role}/{name}",
                place_id=place_id,
            )
        return frame, None

    async def open_candidate(self, target: CandidateTarget) -> None:
        frame = await self._wait_frame(LIST_FRAME_PATTERN)
        row = frame.locator("li").nth(target.dom_index)
        link = (
            row.locator("a[role=button]")
            .filter(has_text=target.name)
            .first
        )
        await self._mutate(lambda: link.click(timeout=10_000))
        entry = await self._entry_frame(target.place_id)
        if f"/{target.place_id}/" not in entry.url:
            raise BrowserExtractionError(
                f"상세 장소 ID 불일치: {entry.url}",
                place_id=target.place_id,
            )

    async def extract_home(
        self,
        place_id: str,
        name: str,
    ) -> tuple[str | None, str | None, int]:
        frame = await self._entry_frame(place_id)
        lines = await frame.locator("body").inner_text(timeout=20_000)
        try:
            metadata = parse_home_text(lines.splitlines(), name)
        except ValueError as error:
            raise BrowserExtractionError(
                "상세 홈 메타데이터 파싱 실패",
                place_id=place_id,
            ) from error
        return metadata.category, metadata.address, metadata.review_count

    async def extract_interior_photos(self, place_id: str) -> list[str]:
        _, photo = await self._wait_named_control(
            place_id,
            "tab",
            "사진",
        )
        if photo is None:
            raise BrowserNavigationError(
                "사진 탭을 찾지 못함",
                place_id=place_id,
            )
        await self._mutate(lambda: photo.click(timeout=10_000))
        frame = await self._entry_frame(place_id)
        interior = frame.locator(
            'a[role="button"][href="#"]'
        ).filter(
            has_text=re.compile(r"^\s*내부\s*$")
        )
        try:
            await interior.first.wait_for(
                state="visible",
                timeout=3_000,
            )
        except PlaywrightTimeoutError:
            return []
        await self._mutate(
            lambda: interior.first.click(timeout=10_000)
        )
        frame = await self._entry_frame(place_id)
        try:
            await frame.wait_for_selector(
                'img[alt^="INTERIOR_"]',
                timeout=3_000,
            )
        except PlaywrightTimeoutError:
            return []
        images = await frame.eval_on_selector_all(
            'img[alt^="INTERIOR_"]',
            """(items) => items.map((item) => ({
              alt: item.alt || '',
              url: item.currentSrc || item.src || '',
            }))""",
        )
        return first_interior_urls(images)

    async def extract_recent_reviews(
        self,
        place_id: str,
        review_count: int,
    ) -> list[str]:
        if review_count == 0:
            return []
        _, review = await self._wait_named_control(
            place_id,
            "tab",
            "리뷰",
        )
        if review is None:
            raise BrowserNavigationError(
                "리뷰 탭을 찾지 못함",
                place_id=place_id,
            )
        await self._mutate(lambda: review.click(timeout=10_000))
        frame, recent = await self._wait_named_control(
            place_id,
            "option",
            "최신순",
        )
        if recent is None:
            raise BrowserNavigationError(
                "최신순 옵션을 찾지 못함",
                place_id=place_id,
            )
        await self._mutate(lambda: recent.click(timeout=10_000))
        for _ in range(20):
            selected = await recent.get_attribute("aria-selected")
            if "reviewSort=recent" in frame.url or selected == "true":
                break
            await self.page.wait_for_timeout(250)
        else:
            raise BrowserNavigationError(
                "최신순 적용을 확인하지 못함",
                place_id=place_id,
            )

        for _ in range(5):
            cards = frame.locator("li.place_apply_pui")
            if await cards.count() >= 50:
                break
            await self._mutate(
                lambda: frame.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
            )
            more = frame.get_by_role(
                "button",
                name="펼쳐서 더보기",
                exact=True,
            )
            if await more.count() == 0:
                break
            previous_count = await cards.count()
            await self._mutate(lambda: more.click(timeout=10_000))
            for _ in range(20):
                if await cards.count() > previous_count:
                    break
                await self.page.wait_for_timeout(250)

        raw_reviews = await frame.locator(
            "li.place_apply_pui"
        ).evaluate_all(
            """(rows) => rows.slice(0, 50).map((row) => {
              const semantic = row.querySelector(
                '[data-pui-click-code="rvshowmore"]'
              );
              const body = row.querySelector(
                'div[class*="pui__vn15t2"]'
              ) || semantic?.parentElement;
              return (body?.innerText || '')
                .replace(/더보기/g, '')
                .replace(/\\s+/g, ' ')
                .trim();
            })"""
        )
        return normalize_review_bodies(raw_reviews)

    async def restore_search_list(self, place_id: str) -> None:
        frame = await self._entry_frame(place_id)
        close = frame.get_by_role(
            "button",
            name=re.compile("페이지 닫기"),
        )
        if await close.count() == 0:
            raise BrowserNavigationError(
                "상세 패널 닫기 버튼을 찾지 못함",
                place_id=place_id,
            )
        await self._mutate(
            lambda: close.first.click(timeout=10_000)
        )
        await self._wait_frame(LIST_FRAME_PATTERN)
        for _ in range(20):
            if not any(
                f"/{place_id}/" in item.url for item in self.page.frames
            ):
                return
            await self.page.wait_for_timeout(250)
        raise BrowserNavigationError(
            "상세 패널이 닫히지 않음",
            place_id=place_id,
        )

    async def extract_place_detail(
        self,
        candidate: CandidatePlace,
        target: CandidateTarget,
    ) -> PlaceDetail:
        blocked = False
        try:
            await self.open_candidate(target)
            category, address, review_count = await self.extract_home(
                candidate.place_id,
                candidate.name,
            )
            photos = await self.extract_interior_photos(candidate.place_id)
            reviews = await self.extract_recent_reviews(
                candidate.place_id,
                review_count,
            )
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
            entry_exists = any(
                f"/{candidate.place_id}/" in frame.url
                for frame in self.page.frames
            )
            if entry_exists and not blocked:
                await self.restore_search_list(candidate.place_id)
