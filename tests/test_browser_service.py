from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from datespot_agent.browser.errors import (
    BrowserAccessBlockedError,
    BrowserExtractionError,
    BrowserNavigationError,
    BrowserSessionError,
)
from datespot_agent.browser.parsers import CandidateTarget
from datespot_agent.browser.naver_map import NaverMapPage
from datespot_agent.browser.service import BrowserService, BrowserSession
from datespot_agent.browser.stream import (
    BrowserStreamControl,
    CdpStreamManager,
)
from datespot_agent.models import CandidatePlace, RunConfig


class FakeNavigator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def open(self) -> None:
        self.calls.append("open")

    async def search_location(self, value: str) -> None:
        self.calls.append(f"location:{value}")

    async def select_station(self, value: str) -> None:
        self.calls.append(f"station:{value}")

    async def set_zoom(self, value: int) -> None:
        self.calls.append(f"zoom:{value}")

    async def search_keyword(self, value: str) -> None:
        self.calls.append(f"keyword:{value}")

    async def extract_candidates(self):
        candidates = [
            CandidatePlace(place_id="1", name="치보"),
            CandidatePlace(place_id="2", name="우니도"),
        ]
        return candidates, {
            candidate.place_id: CandidateTarget(
                place_id=candidate.place_id,
                name=candidate.name,
                dom_index=index,
            )
            for index, candidate in enumerate(candidates)
        }


class FakePacer:
    def __init__(self) -> None:
        self.retry_waits = 0

    async def run(self, action):
        return await action()

    async def wait_before_retry(self) -> None:
        self.retry_waits += 1


class RecordingEventPublisher:
    def __init__(
        self,
        lifecycle_calls: list[str] | None = None,
        *,
        fail_browser_events: bool = False,
    ) -> None:
        self.progress_events: list[tuple[str, str, str]] = []
        self.browser_events: list[tuple[str, str]] = []
        self.lifecycle_calls = lifecycle_calls
        self.fail_browser_events = fail_browser_events

    def progress(
        self,
        run_id,
        stage,
        message,
        *,
        place_id=None,
        place_name=None,
    ) -> None:
        self.progress_events.append((run_id, stage.value, message))

    def browser_ready(self, run_id: str) -> None:
        self.browser_events.append((run_id, "browser_ready"))
        if self.lifecycle_calls is not None:
            self.lifecycle_calls.append("event:browser_ready")
        if self.fail_browser_events:
            raise RuntimeError("publisher ready failed")

    def browser_closed(self, run_id: str) -> None:
        self.browser_events.append((run_id, "browser_closed"))
        if self.lifecycle_calls is not None:
            self.lifecycle_calls.append("event:browser_closed")
        if self.fail_browser_events:
            raise RuntimeError("publisher closed failed")


class FakeLaunchContext:
    def __init__(self, *, pages=None, browser=None) -> None:
        self.pages = list(pages or [])
        self.browser = browser
        self.new_page_calls = 0
        self.context_options = None

    async def new_page(self):
        self.new_page_calls += 1
        page = object()
        self.pages.append(page)
        return page


class FakeLaunchBrowser:
    def __init__(self, context: FakeLaunchContext) -> None:
        self.context = context
        self.contexts = [context]

    async def new_context(self, **options):
        self.context.context_options = options
        return self.context


class FakeChromium:
    def __init__(self, browser, persistent_context) -> None:
        self.browser = browser
        self.persistent_context = persistent_context
        self.launch_calls = []
        self.persistent_calls = []
        self.cdp_calls = []
        self.cdp_error: Exception | None = None

    async def launch(self, **options):
        self.launch_calls.append(options)
        return self.browser

    async def launch_persistent_context(self, user_data_dir, **options):
        self.persistent_calls.append((user_data_dir, options))
        return self.persistent_context

    async def connect_over_cdp(self, endpoint_url, **options):
        self.cdp_calls.append((endpoint_url, options))
        if self.cdp_error is not None:
            raise self.cdp_error
        return self.browser


class FakeRuntime:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium


class FakeCdpProcess:
    def __init__(self) -> None:
        self.endpoint_url = "http://127.0.0.1:43891"
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


class FakeCdpLauncher:
    def __init__(self, process: FakeCdpProcess) -> None:
        self.process = process
        self.launch_calls = 0

    async def launch(self) -> FakeCdpProcess:
        self.launch_calls += 1
        return self.process


class RecordingStreamManager:
    def __init__(
        self,
        calls: list[str],
        *,
        attach_error: Exception | None = None,
        detach_error: Exception | None = None,
    ) -> None:
        self.calls = calls
        self.attach_error = attach_error
        self.detach_error = detach_error

    async def attach_page(self, run_id: str, page) -> None:
        self.calls.append(f"attach:{run_id}")
        if self.attach_error is not None:
            raise self.attach_error

    async def detach_page(self, run_id: str) -> None:
        self.calls.append(f"detach:{run_id}")
        if self.detach_error is not None:
            raise self.detach_error


class BrowserServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_page_attaches_before_navigation_and_detaches_before_close(
        self,
    ):
        calls: list[str] = []
        publisher = RecordingEventPublisher(calls)

        class Closeable:
            def __init__(self, name: str) -> None:
                self.name = name

            async def close(self) -> None:
                calls.append(self.name)

        page = Closeable("page")

        class Context(Closeable):
            def __init__(self) -> None:
                super().__init__("context")
                self.pages = [page]

        class Runtime:
            async def stop(self) -> None:
                calls.append("playwright")

        class RuntimeManager:
            async def start(self):
                return Runtime()

        class Navigator:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            async def open(self) -> None:
                calls.append("open")

        service = BrowserService(
            pacer=FakePacer(),
            stream_manager=RecordingStreamManager(calls),
            event_publisher=publisher,
        )
        context = Context()

        async def launch_browser_context(_runtime):
            return Closeable("browser"), context, None

        service._launch_browser_context = launch_browser_context
        with self.assertLogs(
            "datespot_agent.browser.service",
            level="INFO",
        ) as captured:
            with (
                patch(
                    "datespot_agent.browser.service.async_playwright",
                    return_value=RuntimeManager(),
                ),
                patch("datespot_agent.browser.service.NaverMapPage", Navigator),
            ):
                await service.start_session("run-stream")
                await service.close_session("run-stream")
                await service.close_session("run-stream")

        self.assertLess(calls.index("attach:run-stream"), calls.index("open"))
        self.assertLess(calls.index("detach:run-stream"), calls.index("page"))
        self.assertLess(calls.index("open"), calls.index("event:browser_ready"))
        self.assertLess(
            calls.index("playwright"),
            calls.index("event:browser_closed"),
        )
        self.assertEqual(
            publisher.browser_events,
            [
                ("run-stream", "browser_ready"),
                ("run-stream", "browser_closed"),
            ],
        )
        self.assertEqual(
            [record.datespot_event for record in captured.records],
            [
                "browser.launch.started",
                "browser.launch.completed",
                "browser.cleanup.started",
                "browser.cleanup.completed",
            ],
        )

    async def test_stream_and_event_failures_do_not_fail_browser_lifecycle(self):
        calls: list[str] = []
        publisher = RecordingEventPublisher(fail_browser_events=True)

        class Closeable:
            def __init__(self, name: str) -> None:
                self.name = name

            async def close(self) -> None:
                calls.append(self.name)

        page = Closeable("page")

        class Context(Closeable):
            def __init__(self) -> None:
                super().__init__("context")
                self.pages = [page]

        class Runtime:
            async def stop(self) -> None:
                calls.append("playwright")

        class RuntimeManager:
            async def start(self):
                return Runtime()

        class Navigator:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            async def open(self) -> None:
                calls.append("open")

        service = BrowserService(
            pacer=FakePacer(),
            stream_manager=RecordingStreamManager(
                calls,
                attach_error=RuntimeError("attach failed"),
                detach_error=RuntimeError("detach failed"),
            ),
            event_publisher=publisher,
        )
        context = Context()

        async def launch_browser_context(_runtime):
            return Closeable("browser"), context, None

        service._launch_browser_context = launch_browser_context
        with (
            patch(
                "datespot_agent.browser.service.async_playwright",
                return_value=RuntimeManager(),
            ),
            patch("datespot_agent.browser.service.NaverMapPage", Navigator),
        ):
            await service.start_session("run-stream-errors")
            await service.close_session("run-stream-errors")

        self.assertIn("open", calls)
        self.assertIn("page", calls)
        self.assertEqual(
            publisher.browser_events,
            [
                ("run-stream-errors", "browser_ready"),
                ("run-stream-errors", "browser_closed"),
            ],
        )

    async def test_navigation_failure_detaches_stream_before_page_cleanup(self):
        calls: list[str] = []
        publisher = RecordingEventPublisher(calls)

        class Closeable:
            def __init__(self, name: str) -> None:
                self.name = name

            async def close(self) -> None:
                calls.append(self.name)

        page = Closeable("page")

        class Context(Closeable):
            def __init__(self) -> None:
                super().__init__("context")
                self.pages = [page]

        class Runtime:
            async def stop(self) -> None:
                calls.append("playwright")

        class RuntimeManager:
            async def start(self):
                return Runtime()

        class Navigator:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            async def open(self) -> None:
                raise RuntimeError("navigation failed")

        service = BrowserService(
            pacer=FakePacer(),
            stream_manager=RecordingStreamManager(calls),
            event_publisher=publisher,
        )
        context = Context()

        async def launch_browser_context(_runtime):
            return Closeable("browser"), context, None

        service._launch_browser_context = launch_browser_context
        with (
            patch(
                "datespot_agent.browser.service.async_playwright",
                return_value=RuntimeManager(),
            ),
            patch("datespot_agent.browser.service.NaverMapPage", Navigator),
        ):
            with self.assertRaisesRegex(RuntimeError, "navigation failed"):
                await service.start_session("run-stream-failure")

        self.assertLess(
            calls.index("detach:run-stream-failure"),
            calls.index("page"),
        )
        self.assertEqual(publisher.browser_events, [])

    async def test_default_launch_uses_isolated_context_and_new_page(self):
        context = FakeLaunchContext()
        browser = FakeLaunchBrowser(context)
        chromium = FakeChromium(browser, FakeLaunchContext())
        service = BrowserService(headless=True)

        (
            launched_browser,
            launched_context,
            cdp_process,
        ) = await service._launch_browser_context(FakeRuntime(chromium))
        page = await service._initial_page(launched_context)

        self.assertIs(launched_browser, browser)
        self.assertIsNone(cdp_process)
        self.assertEqual(chromium.launch_calls, [{"headless": True}])
        self.assertEqual(chromium.persistent_calls, [])
        self.assertEqual(
            context.context_options,
            {
                "locale": "ko-KR",
                "timezone_id": "Asia/Seoul",
                "viewport": {"width": 1440, "height": 1000},
            },
        )
        self.assertIs(page, context.pages[0])
        self.assertEqual(context.new_page_calls, 1)

    async def test_persistent_launch_reuses_existing_page_and_profile(self):
        existing_page = object()
        persistent_browser = object()
        context = FakeLaunchContext(
            pages=[existing_page],
            browser=persistent_browser,
        )
        browser = FakeLaunchBrowser(FakeLaunchContext())
        chromium = FakeChromium(browser, context)

        with TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir) / "chrome-profile"
            service = BrowserService(
                headless=False,
                browser_channel="chrome",
                user_data_dir=profile_dir,
            )
            (
                launched_browser,
                launched_context,
                cdp_process,
            ) = await service._launch_browser_context(FakeRuntime(chromium))
            page = await service._initial_page(launched_context)

            self.assertTrue(profile_dir.is_dir())
            self.assertIs(launched_browser, persistent_browser)
            self.assertIsNone(cdp_process)
            self.assertEqual(chromium.launch_calls, [])
            self.assertEqual(
                chromium.persistent_calls,
                [
                    (
                        profile_dir,
                        {
                            "headless": False,
                            "channel": "chrome",
                            "locale": "ko-KR",
                            "timezone_id": "Asia/Seoul",
                            "viewport": {"width": 1440, "height": 1000},
                        },
                    )
                ],
            )
            self.assertIs(page, existing_page)
            self.assertEqual(context.new_page_calls, 0)

    async def test_cdp_launch_reuses_default_context_without_overrides(self):
        existing_page = object()
        context = FakeLaunchContext(pages=[existing_page])
        browser = FakeLaunchBrowser(context)
        chromium = FakeChromium(browser, FakeLaunchContext())
        cdp_process = FakeCdpProcess()
        launcher = FakeCdpLauncher(cdp_process)
        service = BrowserService(cdp_launcher=launcher)

        (
            launched_browser,
            launched_context,
            launched_process,
        ) = await service._launch_browser_context(FakeRuntime(chromium))
        page = await service._initial_page(launched_context)

        self.assertEqual(launcher.launch_calls, 1)
        self.assertEqual(
            chromium.cdp_calls,
            [
                (
                    cdp_process.endpoint_url,
                    {"no_defaults": True, "is_local": True},
                )
            ],
        )
        self.assertIs(launched_browser, browser)
        self.assertIs(launched_context, context)
        self.assertIs(launched_process, cdp_process)
        self.assertIs(page, existing_page)
        self.assertEqual(chromium.launch_calls, [])
        self.assertEqual(chromium.persistent_calls, [])

    async def test_cdp_connect_failure_closes_external_chrome(self):
        context = FakeLaunchContext()
        browser = FakeLaunchBrowser(context)
        chromium = FakeChromium(browser, FakeLaunchContext())
        chromium.cdp_error = RuntimeError("CDP unavailable")
        cdp_process = FakeCdpProcess()
        service = BrowserService(
            cdp_launcher=FakeCdpLauncher(cdp_process),
        )

        with self.assertRaisesRegex(RuntimeError, "CDP unavailable"):
            await service._launch_browser_context(FakeRuntime(chromium))

        self.assertEqual(cdp_process.close_calls, 1)

    async def test_cdp_connect_cancellation_closes_external_chrome(self):
        connect_started = asyncio.Event()
        context = FakeLaunchContext()
        browser = FakeLaunchBrowser(context)

        class BlockingChromium(FakeChromium):
            async def connect_over_cdp(self, endpoint_url, **options):
                self.cdp_calls.append((endpoint_url, options))
                connect_started.set()
                await asyncio.Future()

        chromium = BlockingChromium(browser, FakeLaunchContext())
        cdp_process = FakeCdpProcess()
        service = BrowserService(
            cdp_launcher=FakeCdpLauncher(cdp_process),
        )
        task = asyncio.create_task(
            service._launch_browser_context(FakeRuntime(chromium))
        )
        await connect_started.wait()
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(cdp_process.close_calls, 1)

    async def test_search_uses_fixed_order_without_max_places_slice(self):
        publisher = RecordingEventPublisher()
        service = BrowserService(
            pacer=FakePacer(),
            event_publisher=publisher,
        )
        navigator = FakeNavigator()
        service._sessions["run-1"] = BrowserSession(
            None,
            None,
            None,
            None,
            navigator,
            {},
        )

        result = await service.search_candidates(
            "run-1",
            RunConfig(
                location="신사역",
                search_keyword="일식",
                max_places=1,
            ),
        )

        self.assertEqual([item.place_id for item in result], ["1", "2"])
        self.assertEqual(
            navigator.calls,
            [
                "location:신사역",
                "station:신사역",
                "zoom:15",
                "keyword:일식",
            ],
        )
        self.assertEqual(
            [(run_id, stage) for run_id, stage, _ in publisher.progress_events],
            [("run-1", "candidate_search")] * 5,
        )

    async def test_security_check_publishes_direct_run_id_and_keeps_log(self):
        publisher = RecordingEventPublisher()
        logs: list[str] = []

        class Page:
            def on(self, event_name, callback) -> None:
                return None

            async def wait_for_timeout(self, timeout_ms: int) -> None:
                return None

        navigator = NaverMapPage(
            Page(),
            FakePacer(),
            run_id="run-security-direct",
            log=logs.append,
            event_publisher=publisher,
        )
        reasons = iter((None,))

        async def current_reason():
            return next(reasons)

        navigator._current_block_reason = current_reason

        await navigator._wait_until_access_restored("CAPTCHA")

        self.assertEqual(
            [(run_id, stage) for run_id, stage, _ in publisher.progress_events],
            [
                ("run-security-direct", "security_check"),
                ("run-security-direct", "security_check"),
            ],
        )
        self.assertEqual(
            logs,
            [
                "[run:run-security-direct] 보안 확인 감지, 수동 해제 대기 시작: "
                "CAPTCHA, 10초 후 재확인",
                "[run:run-security-direct] 보안 확인 해제 감지, 작업 재개",
            ],
        )

    async def test_navigation_failure_retries_once_after_recovery_and_wait(self):
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

        with self.assertLogs(
            "datespot_agent.browser.service",
            level="INFO",
        ) as captured:
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
        self.assertEqual(
            [record.datespot_event for record in captured.records],
            [
                "browser.operation.started",
                "browser.operation.retrying",
                "browser.operation.started",
                "browser.operation.completed",
            ],
        )
        self.assertEqual(
            [record.datespot_fields["attempt"] for record in captured.records],
            [1, 1, 2, 2],
        )
        self.assertIsNotNone(captured.records[1].exc_info)

    async def test_access_block_is_never_retried_or_recovered(self):
        pacer = FakePacer()
        service = BrowserService(pacer=pacer)
        attempts = 0
        recoveries = 0

        async def operation():
            nonlocal attempts
            attempts += 1
            raise BrowserAccessBlockedError("429")

        async def recover():
            nonlocal recoveries
            recoveries += 1

        with self.assertRaises(BrowserAccessBlockedError):
            await service._run_with_retry(
                "run-1",
                "search",
                operation,
                BrowserNavigationError,
                recover=recover,
            )

        self.assertEqual(attempts, 1)
        self.assertEqual(recoveries, 0)
        self.assertEqual(pacer.retry_waits, 0)

    async def test_typed_extraction_error_is_preserved_after_retry(self):
        service = BrowserService(pacer=FakePacer())

        async def operation():
            raise BrowserExtractionError("candidate id missing")

        with self.assertRaises(BrowserExtractionError) as raised:
            await service._run_with_retry(
                "run-1",
                "search",
                operation,
                BrowserNavigationError,
            )

        self.assertEqual(raised.exception.run_id, "run-1")

    async def test_missing_sessions_and_repeated_close_are_safe(self):
        service = BrowserService(pacer=FakePacer())

        with self.assertRaises(BrowserSessionError):
            await service.search_candidates(
                "missing",
                RunConfig(location="신사역", search_keyword="일식"),
            )

        await service.close_session("missing")
        await service.close_all()

    async def test_close_missing_session_detaches_waiting_stream_viewer(self):
        stream_manager = CdpStreamManager()
        viewer = await stream_manager.subscribe("run-waiting-close")
        self.assertEqual(
            await viewer.next_message(),
            BrowserStreamControl.waiting(),
        )
        service = BrowserService(
            pacer=FakePacer(),
            stream_manager=stream_manager,
        )

        await service.close_session("run-waiting-close")

        self.assertEqual(
            await asyncio.wait_for(viewer.next_message(), 0.1),
            BrowserStreamControl.ended(),
        )
        self.assertEqual(len(stream_manager._states), 0)

    async def test_cancelled_close_session_finishes_all_owned_resources(self):
        calls: list[str] = []
        detach_started = asyncio.Event()
        release_detach = asyncio.Event()

        class BlockingStreamManager:
            async def detach_page(self, run_id: str) -> None:
                calls.append(f"detach:{run_id}")
                detach_started.set()
                await release_detach.wait()

        class Closeable:
            def __init__(self, name: str) -> None:
                self.name = name

            async def close(self) -> None:
                calls.append(self.name)

        class Runtime:
            async def stop(self) -> None:
                calls.append("playwright")

        service = BrowserService(
            pacer=FakePacer(),
            stream_manager=BlockingStreamManager(),
            event_publisher=RecordingEventPublisher(calls),
        )
        service._sessions["run-cancel-close"] = BrowserSession(
            Runtime(),
            Closeable("browser"),
            Closeable("context"),
            Closeable("page"),
            FakeNavigator(),
            {},
            Closeable("chrome"),
        )
        closing = asyncio.create_task(service.close_session("run-cancel-close"))
        await detach_started.wait()
        self.assertNotIn("run-cancel-close", service._sessions)

        closing.cancel()
        release_detach.set()
        with self.assertRaises(asyncio.CancelledError):
            await closing

        self.assertEqual(
            calls,
            [
                "detach:run-cancel-close",
                "page",
                "context",
                "browser",
                "chrome",
                "playwright",
                "event:browser_closed",
            ],
        )
        self.assertNotIn("run-cancel-close", service._sessions)

    async def test_cancelled_close_all_finishes_every_tracked_session(self):
        calls: list[str] = []
        first_detach_started = asyncio.Event()
        release_first_detach = asyncio.Event()

        class BlockingStreamManager:
            async def detach_page(self, run_id: str) -> None:
                calls.append(f"detach:{run_id}")
                if run_id == "run-one":
                    first_detach_started.set()
                    await release_first_detach.wait()

        class Closeable:
            def __init__(self, name: str) -> None:
                self.name = name

            async def close(self) -> None:
                calls.append(self.name)

        class Runtime:
            def __init__(self, name: str) -> None:
                self.name = name

            async def stop(self) -> None:
                calls.append(self.name)

        service = BrowserService(
            pacer=FakePacer(),
            stream_manager=BlockingStreamManager(),
        )
        for run_id, suffix in (("run-one", "one"), ("run-two", "two")):
            service._sessions[run_id] = BrowserSession(
                Runtime(f"playwright:{suffix}"),
                Closeable(f"browser:{suffix}"),
                Closeable(f"context:{suffix}"),
                Closeable(f"page:{suffix}"),
                FakeNavigator(),
                {},
            )
        closing = asyncio.create_task(service.close_all())
        await first_detach_started.wait()

        closing.cancel()
        release_first_detach.set()
        with self.assertRaises(asyncio.CancelledError):
            await closing

        self.assertEqual(service._sessions, {})
        self.assertEqual(
            calls,
            [
                "detach:run-one",
                "page:one",
                "context:one",
                "browser:one",
                "playwright:one",
                "detach:run-two",
                "page:two",
                "context:two",
                "browser:two",
                "playwright:two",
            ],
        )

    async def test_concurrent_close_callers_share_one_in_flight_cleanup(self):
        calls: list[str] = []
        page_close_started = asyncio.Event()
        release_page_close = asyncio.Event()
        publisher = RecordingEventPublisher(calls)

        class StreamManager:
            async def detach_page(self, run_id: str) -> None:
                calls.append(f"detach:{run_id}")

        class BlockingPage:
            async def close(self) -> None:
                calls.append("page:start")
                page_close_started.set()
                await release_page_close.wait()
                calls.append("page:end")

        class Closeable:
            def __init__(self, name: str) -> None:
                self.name = name

            async def close(self) -> None:
                calls.append(self.name)

        class Runtime:
            async def stop(self) -> None:
                calls.append("playwright")

        service = BrowserService(
            pacer=FakePacer(),
            stream_manager=StreamManager(),
            event_publisher=publisher,
        )
        service._sessions["run-concurrent"] = BrowserSession(
            Runtime(),
            Closeable("browser"),
            Closeable("context"),
            BlockingPage(),
            FakeNavigator(),
            {},
            Closeable("chrome"),
        )
        first = asyncio.create_task(service.close_session("run-concurrent"))
        await page_close_started.wait()

        second = asyncio.create_task(service.close_session("run-concurrent"))
        close_all = asyncio.create_task(service.close_all())
        await asyncio.sleep(0)

        self.assertFalse(second.done())
        self.assertFalse(close_all.done())
        self.assertEqual(calls.count("detach:run-concurrent"), 1)

        release_page_close.set()
        await asyncio.gather(first, second, close_all)

        self.assertEqual(
            calls,
            [
                "detach:run-concurrent",
                "page:start",
                "page:end",
                "context",
                "browser",
                "chrome",
                "playwright",
                "event:browser_closed",
            ],
        )
        self.assertEqual(
            publisher.browser_events,
            [("run-concurrent", "browser_closed")],
        )
        self.assertEqual(service._sessions, {})
        self.assertEqual(service._closing, {})

    async def test_start_rejects_run_while_session_cleanup_is_in_flight(self):
        page_close_started = asyncio.Event()
        release_page_close = asyncio.Event()

        class Page:
            async def close(self) -> None:
                page_close_started.set()
                await release_page_close.wait()

        class RuntimeManager:
            async def start(self):
                raise AssertionError("Playwright start must not run")

        service = BrowserService(pacer=FakePacer())
        service._sessions["run-closing"] = BrowserSession(
            None,
            None,
            None,
            Page(),
            FakeNavigator(),
            {},
        )
        closing = asyncio.create_task(service.close_session("run-closing"))
        await page_close_started.wait()

        with patch(
            "datespot_agent.browser.service.async_playwright",
            return_value=RuntimeManager(),
        ):
            with self.assertRaises(BrowserSessionError):
                await service.start_session("run-closing")

        release_page_close.set()
        await closing

    async def test_concurrent_missing_closes_share_one_stream_detach(self):
        detach_started = asyncio.Event()
        release_detach = asyncio.Event()
        detach_calls = 0

        class StreamManager:
            async def detach_page(self, run_id: str) -> None:
                nonlocal detach_calls
                detach_calls += 1
                detach_started.set()
                await release_detach.wait()

        service = BrowserService(
            pacer=FakePacer(),
            stream_manager=StreamManager(),
        )
        first = asyncio.create_task(service.close_session("run-missing"))
        await detach_started.wait()
        second = asyncio.create_task(service.close_session("run-missing"))
        await asyncio.sleep(0)

        self.assertEqual(detach_calls, 1)
        self.assertFalse(second.done())

        release_detach.set()
        await asyncio.gather(first, second)
        self.assertEqual(detach_calls, 1)
        self.assertEqual(service._closing, {})

    async def test_close_session_uses_page_context_browser_runtime_order(self):
        calls: list[str] = []

        class Closeable:
            def __init__(self, name: str) -> None:
                self.name = name

            async def close(self) -> None:
                calls.append(self.name)

        class Runtime:
            async def stop(self) -> None:
                calls.append("playwright")

        service = BrowserService(pacer=FakePacer())
        service._sessions["run-1"] = BrowserSession(
            Runtime(),
            Closeable("browser"),
            Closeable("context"),
            Closeable("page"),
            FakeNavigator(),
            {},
        )

        await service.close_session("run-1")
        await service.close_session("run-1")

        self.assertEqual(
            calls,
            ["page", "context", "browser", "playwright"],
        )

    async def test_close_session_closes_external_chrome_before_runtime(self):
        calls: list[str] = []

        class Closeable:
            def __init__(self, name: str) -> None:
                self.name = name

            async def close(self) -> None:
                calls.append(self.name)

        class Runtime:
            async def stop(self) -> None:
                calls.append("playwright")

        service = BrowserService(pacer=FakePacer())
        service._sessions["run-1"] = BrowserSession(
            Runtime(),
            Closeable("browser"),
            Closeable("context"),
            Closeable("page"),
            FakeNavigator(),
            {},
            Closeable("chrome"),
        )

        await service.close_session("run-1")

        self.assertEqual(
            calls,
            ["page", "context", "browser", "chrome", "playwright"],
        )

    async def test_start_session_cancellation_closes_all_started_resources(self):
        calls: list[str] = []
        navigator_started = asyncio.Event()

        class Closeable:
            def __init__(self, name: str) -> None:
                self.name = name

            async def close(self) -> None:
                calls.append(self.name)

        page = Closeable("page")

        class Context(Closeable):
            def __init__(self) -> None:
                super().__init__("context")
                self.pages = [page]

        class Runtime:
            async def stop(self) -> None:
                calls.append("playwright")

        runtime = Runtime()

        class RuntimeManager:
            async def start(self):
                return runtime

        class BlockingNavigator:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            async def open(self) -> None:
                navigator_started.set()
                await asyncio.Future()

        browser = Closeable("browser")
        context = Context()
        chrome = Closeable("chrome")
        service = BrowserService(pacer=FakePacer())

        async def launch_browser_context(_runtime):
            return browser, context, chrome

        service._launch_browser_context = launch_browser_context

        with (
            patch(
                "datespot_agent.browser.service.async_playwright",
                return_value=RuntimeManager(),
            ),
            patch(
                "datespot_agent.browser.service.NaverMapPage",
                BlockingNavigator,
            ),
        ):
            task = asyncio.create_task(service.start_session("run-cancel"))
            await navigator_started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual(
            calls,
            ["page", "context", "browser", "chrome", "playwright"],
        )
        self.assertNotIn("run-cancel", service._sessions)


if __name__ == "__main__":
    unittest.main()
