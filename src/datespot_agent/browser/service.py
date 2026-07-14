"""실행별 Playwright 자원을 소유하는 BrowserService."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

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
    """브라우저 세션과 네이버지도 작업을 run_id 단위로 제공한다."""

    def __init__(
        self,
        *,
        headless: bool = True,
        browser_channel: str | None = None,
        pacer: InteractionPacer | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._headless = headless
        self._browser_channel = browser_channel
        self._pacer = pacer or InteractionPacer()
        self._log = log
        self._sessions: dict[str, BrowserSession] = {}

    def _session(self, run_id: str) -> BrowserSession:
        session = self._sessions.get(run_id)
        if session is None:
            raise BrowserSessionError(
                "브라우저 세션을 찾지 못함",
                run_id=run_id,
            )
        return session

    async def start_session(self, run_id: str) -> None:
        if run_id in self._sessions:
            raise BrowserSessionError(
                "이미 존재하는 브라우저 세션",
                run_id=run_id,
            )

        runtime = await async_playwright().start()
        browser: Browser | None = None
        context: BrowserContext | None = None
        page: Page | None = None
        try:
            launch_options: dict[str, object] = {
                "headless": self._headless,
            }
            if self._browser_channel is not None:
                launch_options["channel"] = self._browser_channel
            browser = await runtime.chromium.launch(**launch_options)
            context = await browser.new_context(
                locale="ko-KR",
                timezone_id="Asia/Seoul",
                viewport={"width": 1440, "height": 1000},
            )
            page = await context.new_page()
            navigator = NaverMapPage(
                page,
                self._pacer,
                run_id=run_id,
                log=self._log,
            )
            await navigator.open()
            self._sessions[run_id] = BrowserSession(
                runtime,
                browser,
                context,
                page,
                navigator,
            )
        except Exception:
            await self._safe_close(page)
            await self._safe_close(context)
            await self._safe_close(browser)
            await self._safe_stop(runtime)
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
                    final_type = (
                        type(error)
                        if isinstance(error, BrowserServiceError)
                        else error_type
                    )
                    raise final_type(
                        str(error),
                        run_id=run_id,
                        step=step,
                        place_id=place_id,
                    ) from error
                if recover is not None:
                    await recover()
                await self._pacer.wait_before_retry()
        raise AssertionError("unreachable retry state")

    async def search_candidates(
        self,
        run_id: str,
        config: RunConfig,
    ) -> list[CandidatePlace]:
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

    async def extract_place_detail(
        self,
        run_id: str,
        candidate: CandidatePlace,
    ) -> PlaceDetail:
        session = self._session(run_id)
        target = session.candidate_targets.get(candidate.place_id)
        if target is None:
            raise BrowserExtractionError(
                "캐시된 후보 target을 찾지 못함",
                run_id=run_id,
                step="extract_place_detail",
                place_id=candidate.place_id,
            )
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
        await self._safe_close(session.page)
        await self._safe_close(session.context)
        await self._safe_close(session.browser)
        await self._safe_stop(session.playwright)

    async def close_all(self) -> None:
        for run_id in list(self._sessions):
            await self.close_session(run_id)

    @staticmethod
    async def _safe_close(resource) -> None:
        if resource is None:
            return
        try:
            await resource.close()
        except Exception:
            return

    @staticmethod
    async def _safe_stop(runtime) -> None:
        if runtime is None:
            return
        try:
            await runtime.stop()
        except Exception:
            return
