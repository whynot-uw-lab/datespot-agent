"""실행별 Playwright 자원을 소유하는 BrowserService."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from datespot_agent.browser.chrome_cdp import (
    ChromeCdpLauncher,
    ChromeCdpProcess,
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
    cdp_process: ChromeCdpProcess | None = None


class BrowserService:
    """브라우저 세션과 네이버지도 작업을 run_id 단위로 제공한다."""

    def __init__(
        self,
        *,
        headless: bool = True,
        browser_channel: str | None = None,
        user_data_dir: Path | None = None,
        cdp_launcher: ChromeCdpLauncher | None = None,
        pacer: InteractionPacer | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._headless = headless
        self._browser_channel = browser_channel
        self._user_data_dir = user_data_dir
        self._cdp_launcher = cdp_launcher
        self._pacer = pacer or InteractionPacer()
        self._log = log
        self._sessions: dict[str, BrowserSession] = {}

    async def _launch_browser_context(
        self,
        runtime: Playwright,
    ) -> tuple[Browser | None, BrowserContext, ChromeCdpProcess | None]:
        if self._cdp_launcher is not None:
            cdp_process = await self._cdp_launcher.launch()
            try:
                browser = await runtime.chromium.connect_over_cdp(
                    cdp_process.endpoint_url,
                    no_defaults=True,
                    is_local=True,
                )
                if not browser.contexts:
                    raise BrowserSessionError(
                        "CDP 브라우저 기본 컨텍스트를 찾지 못함"
                    )
                return browser, browser.contexts[0], cdp_process
            except Exception:
                await self._safe_close(cdp_process)
                raise

        launch_options: dict[str, object] = {
            "headless": self._headless,
        }
        if self._browser_channel is not None:
            launch_options["channel"] = self._browser_channel
        context_options: dict[str, object] = {
            "locale": "ko-KR",
            "timezone_id": "Asia/Seoul",
            "viewport": {"width": 1440, "height": 1000},
        }
        if self._user_data_dir is not None:
            self._user_data_dir.mkdir(parents=True, exist_ok=True)
            context = await runtime.chromium.launch_persistent_context(
                self._user_data_dir,
                **launch_options,
                **context_options,
            )
            return context.browser, context, None
        browser = await runtime.chromium.launch(**launch_options)
        context = await browser.new_context(**context_options)
        return browser, context, None

    @staticmethod
    async def _initial_page(context: BrowserContext) -> Page:
        if context.pages:
            return context.pages[0]
        return await context.new_page()

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
        cdp_process: ChromeCdpProcess | None = None
        try:
            browser, context, cdp_process = (
                await self._launch_browser_context(runtime)
            )
            page = await self._initial_page(context)
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
                cdp_process=cdp_process,
            )
        except Exception:
            await self._safe_close(page)
            await self._safe_close(context)
            await self._safe_close(browser)
            await self._safe_close(cdp_process)
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
        await self._safe_close(session.cdp_process)
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
