from __future__ import annotations

import unittest

from datespot_agent.browser.errors import (
    BrowserAccessBlockedError,
    BrowserNavigationError,
)
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
        self.assertIsNotNone(
            BLOCK_TEXT_PATTERN.search("비정상적인 접근이 감지되었습니다")
        )

    async def test_blocked_response_stops_before_next_action(self):
        navigator = object.__new__(NaverMapPage)
        navigator.page = None
        navigator.pacer = FakePacer()
        navigator._blocked_response = (
            429,
            "https://map.naver.com/p/search/일식",
        )

        with self.assertRaises(BrowserAccessBlockedError):
            await navigator._assert_access_allowed()

        self.assertEqual(navigator.pacer.actions, 0)

    async def test_unknown_zoom_is_navigation_error(self):
        navigator = object.__new__(NaverMapPage)
        navigator.page = type(
            "Page",
            (),
            {"url": "https://map.naver.com/"},
        )()
        navigator.pacer = FakePacer()
        navigator._blocked_response = None

        with self.assertRaises(BrowserNavigationError):
            await navigator.set_zoom(15)


if __name__ == "__main__":
    unittest.main()
