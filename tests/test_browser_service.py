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
    def __init__(self) -> None:
        self.progress_events: list[tuple[str, str, str]] = []

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


class BrowserServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_launch_uses_isolated_context_and_new_page(self):
        context = FakeLaunchContext()
        browser = FakeLaunchBrowser(context)
        chromium = FakeChromium(browser, FakeLaunchContext())
        service = BrowserService(headless=True)

        launched_browser, launched_context, cdp_process = (
            await service._launch_browser_context(FakeRuntime(chromium))
        )
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
            launched_browser, launched_context, cdp_process = (
                await service._launch_browser_context(FakeRuntime(chromium))
            )
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

        launched_browser, launched_context, launched_process = (
            await service._launch_browser_context(FakeRuntime(chromium))
        )
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
