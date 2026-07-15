"""실행별 Playwright 자원을 소유하는 BrowserService."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, TypeVar

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
from datespot_agent.observability import log_event

if TYPE_CHECKING:
    from datespot_agent.api.events import RunEventPublisher
    from datespot_agent.browser.stream import CdpStreamManager

T = TypeVar("T")
LOGGER = logging.getLogger(__name__)


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
        event_publisher: RunEventPublisher | None = None,
        stream_manager: CdpStreamManager | None = None,
    ) -> None:
        self._headless = headless
        self._browser_channel = browser_channel
        self._user_data_dir = user_data_dir
        self._cdp_launcher = cdp_launcher
        self._pacer = pacer or InteractionPacer()
        self._log = log
        self._events = event_publisher
        self._stream_manager = stream_manager
        self._sessions: dict[str, BrowserSession] = {}
        self._closing: dict[str, asyncio.Task[None]] = {}

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
                    raise BrowserSessionError("CDP 브라우저 기본 컨텍스트를 찾지 못함")
                return browser, browser.contexts[0], cdp_process
            except BaseException:
                await asyncio.shield(self._safe_close(cdp_process))
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
        if run_id in self._sessions or run_id in self._closing:
            raise BrowserSessionError(
                "이미 존재하거나 정리 중인 브라우저 세션",
                run_id=run_id,
            )

        started_at = monotonic()
        log_event(
            LOGGER,
            "browser.launch.started",
            "브라우저 세션 시작",
            run_id=run_id,
            component="browser",
            stage="session_start",
            launch_mode=(
                "cdp"
                if self._cdp_launcher is not None
                else "persistent"
                if self._user_data_dir is not None
                else "ephemeral"
            ),
            headless=self._headless,
        )
        runtime = await async_playwright().start()
        browser: Browser | None = None
        context: BrowserContext | None = None
        page: Page | None = None
        cdp_process: ChromeCdpProcess | None = None
        try:
            browser, context, cdp_process = await self._launch_browser_context(runtime)
            page = await self._initial_page(context)
            navigator = NaverMapPage(
                page,
                self._pacer,
                run_id=run_id,
                log=self._log,
                event_publisher=self._events,
            )
            await self._safe_stream_attach(run_id, page)
            self._progress(run_id, "session_start", "네이버지도 열기 시작")
            await navigator.open()
            self._progress(run_id, "session_start", "네이버지도 열기 완료")
            self._sessions[run_id] = BrowserSession(
                runtime,
                browser,
                context,
                page,
                navigator,
                cdp_process=cdp_process,
            )
            self._publish_browser_event(run_id, ready=True)
            log_event(
                LOGGER,
                "browser.launch.completed",
                "브라우저 세션 준비 완료",
                run_id=run_id,
                component="browser",
                stage="session_start",
                duration_ms=self._elapsed_ms(started_at),
            )
        except BaseException:
            log_event(
                LOGGER,
                "browser.launch.failed",
                "브라우저 세션 시작 실패",
                run_id=run_id,
                component="browser",
                stage="session_start",
                level=logging.ERROR,
                exc_info=True,
                duration_ms=self._elapsed_ms(started_at),
            )
            await asyncio.shield(
                self._close_started_resources(
                    run_id,
                    page,
                    context,
                    browser,
                    cdp_process,
                    runtime,
                )
            )
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
            started_at = monotonic()
            log_event(
                LOGGER,
                "browser.operation.started",
                "브라우저 작업 시작",
                run_id=run_id,
                component="browser",
                step=step,
                place_id=place_id,
                attempt=attempt,
                max_attempts=2,
            )
            try:
                result = await operation()
                log_event(
                    LOGGER,
                    "browser.operation.completed",
                    "브라우저 작업 완료",
                    run_id=run_id,
                    component="browser",
                    step=step,
                    place_id=place_id,
                    attempt=attempt,
                    max_attempts=2,
                    duration_ms=self._elapsed_ms(started_at),
                )
                return result
            except (BrowserAccessBlockedError, BrowserSessionError):
                log_event(
                    LOGGER,
                    "browser.operation.failed",
                    "브라우저 작업 중단",
                    run_id=run_id,
                    component="browser",
                    step=step,
                    place_id=place_id,
                    attempt=attempt,
                    max_attempts=2,
                    level=logging.ERROR,
                    exc_info=True,
                    duration_ms=self._elapsed_ms(started_at),
                )
                raise
            except Exception as error:
                if attempt == 2:
                    log_event(
                        LOGGER,
                        "browser.operation.failed",
                        "브라우저 작업 재시도 후 실패",
                        run_id=run_id,
                        component="browser",
                        step=step,
                        place_id=place_id,
                        attempt=attempt,
                        max_attempts=2,
                        level=logging.ERROR,
                        exc_info=True,
                        duration_ms=self._elapsed_ms(started_at),
                    )
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
                log_event(
                    LOGGER,
                    "browser.operation.retrying",
                    "브라우저 작업 재시도 예정",
                    run_id=run_id,
                    component="browser",
                    step=step,
                    place_id=place_id,
                    attempt=attempt,
                    max_attempts=2,
                    level=logging.WARNING,
                    exc_info=True,
                    duration_ms=self._elapsed_ms(started_at),
                )
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
            self._progress(run_id, "candidate_search", "검색 지역 입력 중")
            await session.navigator.search_location(config.location)
            self._progress(run_id, "candidate_search", "검색 역 선택 중")
            await session.navigator.select_station(config.location)
            self._progress(run_id, "candidate_search", "지도 확대 수준 조정 중")
            await session.navigator.set_zoom(15)
            self._progress(run_id, "candidate_search", "검색 키워드 입력 중")
            await session.navigator.search_keyword(config.search_keyword)
            self._progress(run_id, "candidate_search", "후보 목록 읽는 중")
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
        self._progress(
            run_id,
            "place_detail",
            "장소 상세 페이지 탐색 중",
            place_id=candidate.place_id,
            place_name=candidate.name,
        )
        return await self._run_with_retry(
            run_id,
            "extract_place_detail",
            lambda: session.navigator.extract_place_detail(candidate, target),
            BrowserExtractionError,
            place_id=candidate.place_id,
        )

    async def close_session(self, run_id: str) -> None:
        cleanup_task = self._closing.get(run_id)
        if cleanup_task is None:
            session = self._sessions.pop(run_id, None)
            if session is None:
                cleanup_task = asyncio.create_task(
                    self._safe_stream_detach(run_id),
                    name=f"datespot-browser-detach-{run_id}",
                )
            else:
                cleanup_task = asyncio.create_task(
                    self._finalize_session(run_id, session),
                    name=f"datespot-browser-close-{run_id}",
                )
            self._closing[run_id] = cleanup_task
            cleanup_task.add_done_callback(
                lambda task, owned_run_id=run_id: self._forget_cleanup(
                    owned_run_id,
                    task,
                )
            )
        await self._await_cleanup(cleanup_task)

    def _forget_cleanup(
        self,
        run_id: str,
        cleanup_task: asyncio.Task[None],
    ) -> None:
        if self._closing.get(run_id) is cleanup_task:
            self._closing.pop(run_id, None)

    async def _finalize_session(
        self,
        run_id: str,
        session: BrowserSession,
    ) -> None:
        started_at = monotonic()
        log_event(
            LOGGER,
            "browser.cleanup.started",
            "브라우저 세션 정리 시작",
            run_id=run_id,
            component="browser",
            stage="session_start",
        )
        await self._safe_stream_detach(run_id)
        await self._safe_close(session.page)
        await self._safe_close(session.context)
        await self._safe_close(session.browser)
        await self._safe_close(session.cdp_process)
        await self._safe_stop(session.playwright)
        self._publish_browser_event(run_id, ready=False)
        log_event(
            LOGGER,
            "browser.cleanup.completed",
            "브라우저 세션 정리 완료",
            run_id=run_id,
            component="browser",
            stage="session_start",
            duration_ms=self._elapsed_ms(started_at),
        )

    async def close_all(self) -> None:
        cleanup_task = asyncio.create_task(
            self._close_all_sessions(),
            name="datespot-browser-close-all",
        )
        await self._await_cleanup(cleanup_task)

    async def _close_all_sessions(self) -> None:
        while self._sessions or self._closing:
            in_flight = tuple(self._closing.values())
            if in_flight:
                await asyncio.gather(*in_flight)
                for run_id, task in tuple(self._closing.items()):
                    if task.done() and self._closing.get(run_id) is task:
                        self._closing.pop(run_id, None)
                continue
            await self.close_session(next(iter(self._sessions)))

    @staticmethod
    async def _await_cleanup(cleanup_task: asyncio.Task[None]) -> None:
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            await cleanup_task
            raise

    def _progress(
        self,
        run_id: str,
        stage: str,
        message: str,
        *,
        place_id: str | None = None,
        place_name: str | None = None,
    ) -> None:
        if self._events is None:
            return
        from datespot_agent.api.events import ProgressStage

        self._events.progress(
            run_id,
            ProgressStage(stage),
            message,
            place_id=place_id,
            place_name=place_name,
        )

    def _publish_browser_event(self, run_id: str, *, ready: bool) -> None:
        if self._events is None:
            return
        try:
            if ready:
                self._events.browser_ready(run_id)
            else:
                self._events.browser_closed(run_id)
        except Exception:
            LOGGER.warning("browser lifecycle event 발행 실패")

    async def _close_started_resources(
        self,
        run_id,
        page,
        context,
        browser,
        cdp_process,
        runtime,
    ) -> None:
        await self._safe_stream_detach(run_id)
        await self._safe_close(page)
        await self._safe_close(context)
        await self._safe_close(browser)
        await self._safe_close(cdp_process)
        await self._safe_stop(runtime)

    async def _safe_stream_attach(self, run_id: str, page: Page) -> None:
        if self._stream_manager is None:
            return
        try:
            await self._stream_manager.attach_page(run_id, page)
        except Exception:
            LOGGER.warning("browser stream page 연결 실패")

    async def _safe_stream_detach(self, run_id: str) -> None:
        if self._stream_manager is None:
            return
        try:
            await self._stream_manager.detach_page(run_id)
        except Exception:
            LOGGER.warning("browser stream page 정리 실패")

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

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, int((monotonic() - started_at) * 1_000))
