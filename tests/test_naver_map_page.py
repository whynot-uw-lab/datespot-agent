from __future__ import annotations

import unittest

from datespot_agent.browser.errors import (
    BrowserAccessBlockedError,
    BrowserNavigationError,
)
from datespot_agent.browser.naver_map import BLOCK_TEXT_PATTERN, NaverMapPage
from datespot_agent.browser.parsers import CandidateTarget
from datespot_agent.models import CandidatePlace


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
        self.assertIsNotNone(
            BLOCK_TEXT_PATTERN.search(
                "보안 확인을 완료해 주세요. 실제 사용자임을 확인하여 "
                "계정을 안전하게 보호하고 스팸을 방지합니다."
            )
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

    async def test_station_route_uses_url_condition_not_load_lifecycle(self):
        class StationHandle:
            def __init__(self, page) -> None:
                self.page = page

            async def evaluate(self, _script: str) -> None:
                self.page.url = (
                    "https://map.naver.com/p/subway-station/1907"
                )

        class StationControl:
            def __init__(self, page) -> None:
                self.page = page

            @property
            def first(self):
                return self

            async def count(self) -> int:
                return 1

            async def click(self, **_kwargs) -> None:
                return None

            async def element_handle(self, **_kwargs):
                return StationHandle(self.page)

        class StationFrame:
            def __init__(self, page) -> None:
                self.control = StationControl(page)

            def get_by_role(self, *_args, **_kwargs):
                return self.control

        class Page:
            def __init__(self) -> None:
                self.url = "https://map.naver.com/p/search/신사역"
                self.frame = StationFrame(self)

            async def wait_for_url(self, *_args, **_kwargs):
                raise AssertionError("SPA route에서 load lifecycle 대기 금지")

            async def wait_for_timeout(self, _timeout: int) -> None:
                return None

        page = Page()
        navigator = object.__new__(NaverMapPage)
        navigator.page = page
        navigator.pacer = FakePacer()
        navigator._blocked_response = None

        async def frame_value(*_args, **_kwargs):
            return page.frame

        async def mutate(action):
            return await action()

        async def wait_page_url(pattern, *_args, **_kwargs):
            if not pattern.search(page.url):
                raise BrowserNavigationError("역 route 미변경")

        navigator._wait_frame = frame_value
        navigator._mutate = mutate
        navigator._wait_page_url = wait_page_url

        await navigator.select_station("신사역")

        self.assertIn("subway-station/1907", page.url)


class NaverMapDetailContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_detail_result_limits_photos_and_reviews_and_restores_list(self):
        navigator = object.__new__(NaverMapPage)
        navigator.page = type(
            "Page",
            (),
            {
                "frames": [
                    type(
                        "Frame",
                        (),
                        {
                            "url": "https://pcmap.place.naver.com/restaurant/1150149433/home"
                        },
                    )()
                ]
            },
        )()
        navigator.pacer = FakePacer()
        navigator._blocked_response = None
        restore_calls = 0
        navigator.open_candidate = self._async_value(None)
        navigator.extract_home = self._async_value(
            ("일식당", "서울 강남구 도산대로 15", 1234)
        )
        navigator.extract_interior_photos = self._async_value(
            [f"https://img/{index}.jpg" for index in range(7)]
        )
        navigator.extract_recent_reviews = self._async_value(
            [f"리뷰 {index}" for index in range(60)]
        )

        async def restore(*_args, **_kwargs):
            nonlocal restore_calls
            restore_calls += 1

        navigator.restore_search_list = restore

        detail = await navigator.extract_place_detail(
            CandidatePlace(place_id="1150149433", name="치보 신사점"),
            CandidateTarget(
                place_id="1150149433",
                name="치보 신사점",
                dom_index=1,
            ),
        )

        self.assertEqual(detail.review_count, 1234)
        self.assertEqual(len(detail.photo_urls), 5)
        self.assertEqual(len(detail.reviews), 50)
        self.assertEqual(restore_calls, 1)

    async def test_zero_review_count_is_normal_empty_data(self):
        navigator = object.__new__(NaverMapPage)

        self.assertEqual(
            await navigator.extract_recent_reviews("1150149433", 0),
            [],
        )

    async def test_access_block_does_not_restore_panel_or_make_more_requests(self):
        navigator = object.__new__(NaverMapPage)
        navigator.page = type(
            "Page",
            (),
            {
                "frames": [
                    type(
                        "Frame",
                        (),
                        {
                            "url": "https://pcmap.place.naver.com/restaurant/1150149433/home"
                        },
                    )()
                ]
            },
        )()
        restore_calls = 0

        async def blocked(*_args, **_kwargs):
            raise BrowserAccessBlockedError("429")

        async def restore(*_args, **_kwargs):
            nonlocal restore_calls
            restore_calls += 1

        navigator.open_candidate = blocked
        navigator.restore_search_list = restore

        with self.assertRaises(BrowserAccessBlockedError):
            await navigator.extract_place_detail(
                CandidatePlace(place_id="1150149433", name="치보 신사점"),
                CandidateTarget(
                    place_id="1150149433",
                    name="치보 신사점",
                    dom_index=1,
                ),
            )

        self.assertEqual(restore_calls, 0)

    @staticmethod
    def _async_value(value):
        async def call(*_args, **_kwargs):
            return value

        return call


if __name__ == "__main__":
    unittest.main()
