from __future__ import annotations

import unittest

from datespot_agent.browser.errors import (
    BrowserAccessBlockedError,
    BrowserExtractionError,
    BrowserNavigationError,
    BrowserSessionError,
)
from datespot_agent.browser.parsers import CandidateTarget
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


class BrowserServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_uses_fixed_order_without_max_places_slice(self):
        service = BrowserService(pacer=FakePacer())
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


if __name__ == "__main__":
    unittest.main()
