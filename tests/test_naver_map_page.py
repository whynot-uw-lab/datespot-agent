from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from datespot_agent.browser.errors import (
    BrowserAccessBlockedError,
    BrowserNavigationError,
)
from datespot_agent.browser.naver_map import NaverMapPage
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


class FakeBodyLocator:
    def __init__(self, page) -> None:
        self.page = page

    async def inner_text(self, **_kwargs) -> str:
        return self.page.body_text


class FakeVisibleLocator:
    def __init__(self, frame, pattern) -> None:
        self.frame = frame
        self.pattern = pattern

    @property
    def first(self):
        return self

    async def count(self) -> int:
        return int(bool(self.pattern.search(self.frame.page.body_text)))

    async def is_visible(self) -> bool:
        return self.frame.visible


class FakeBlockFrame:
    def __init__(self, page, *, visible: bool) -> None:
        self.page = page
        self.visible = visible
        self.url = "https://map.naver.com/security-check"

    def locator(self, _selector: str) -> FakeBodyLocator:
        return FakeBodyLocator(self.page)

    def get_by_text(self, pattern) -> FakeVisibleLocator:
        return FakeVisibleLocator(self, pattern)


class FakeBlockPage:
    def __init__(self, *, visible: bool) -> None:
        self.url = "https://map.naver.com/p/search/일식"
        self.body_text = "보안 확인을 완료해 주세요. 실제 사용자임을 확인합니다."
        self.frames = [FakeBlockFrame(self, visible=visible)]
        self.waits: list[int] = []

    async def wait_for_timeout(self, timeout_ms: int) -> None:
        self.waits.append(timeout_ms)
        self.frames[0].visible = False

    async def screenshot(self, *, path: str, full_page: bool) -> None:
        Path(path).write_bytes(b"screenshot")

    async def content(self) -> str:
        return "<html><body>security check</body></html>"


class NaverMapPageContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_interior_photos_use_hash_filter_without_opening_viewer(self):
        calls: list[str] = []
        images = [
            {"alt": f"INTERIOR_{index}", "url": f"https://img/{index}.jpg"}
            for index in range(7)
        ]

        class PhotoTab:
            async def click(self, **_kwargs) -> None:
                calls.append("photo-tab")

        class InteriorFilter:
            @property
            def first(self):
                return self

            def filter(self, **_kwargs):
                return self

            async def count(self) -> int:
                return 1

            async def wait_for(self, **_kwargs) -> None:
                calls.append("filter-visible")

            async def click(self, **_kwargs) -> None:
                calls.append("interior-filter")

        class Frame:
            def locator(self, selector: str):
                calls.append(f"selector:{selector}")
                return InteriorFilter()

            async def wait_for_selector(self, selector: str, **_kwargs) -> None:
                calls.append(f"wait:{selector}")

            async def eval_on_selector_all(self, selector: str, _script: str):
                calls.append(f"images:{selector}")
                return images

        frame = Frame()
        navigator = object.__new__(NaverMapPage)

        async def wait_named_control(
            _place_id,
            _role,
            name,
            **_kwargs,
        ):
            if name != "사진":
                raise AssertionError("대표 사진 링크를 선택하면 안 됨")
            return frame, PhotoTab()

        async def entry_frame(_place_id):
            return frame

        async def mutate(action):
            return await action()

        navigator._wait_named_control = wait_named_control
        navigator._entry_frame = entry_frame
        navigator._mutate = mutate

        result = await navigator.extract_interior_photos("1141137916")

        self.assertEqual(result, [f"https://img/{index}.jpg" for index in range(5)])
        self.assertIn(
            'selector:a[role="button"][href="#"]',
            calls,
        )
        self.assertIn("interior-filter", calls)

    async def test_hidden_security_text_does_not_block_action(self):
        page = FakeBlockPage(visible=False)
        navigator = object.__new__(NaverMapPage)
        navigator.page = page
        navigator.pacer = FakePacer()
        navigator.run_id = "run-hidden"
        navigator.dump_dir = Path("artifacts/browser")
        navigator.log = lambda _message: None
        navigator._blocked_response = None
        navigator._blocked_message = None
        action_calls = 0

        async def action() -> None:
            nonlocal action_calls
            action_calls += 1

        try:
            await navigator._mutate(action)
        except BrowserAccessBlockedError as error:
            self.fail(f"숨겨진 보안 문구가 작업을 차단함: {error}")

        self.assertEqual(action_calls, 1)
        self.assertEqual(page.waits, [])

    async def test_visible_security_check_dumps_waits_and_resumes(self):
        page = FakeBlockPage(visible=True)
        events: list[str] = []
        action_calls = 0

        async def action() -> None:
            nonlocal action_calls
            action_calls += 1

        with tempfile.TemporaryDirectory() as temp_dir:
            navigator = object.__new__(NaverMapPage)
            navigator.page = page
            navigator.pacer = FakePacer()
            navigator.run_id = "run-visible"
            navigator.dump_dir = Path(temp_dir)
            navigator.log = events.append
            navigator._blocked_response = None
            navigator._blocked_message = None

            try:
                await navigator._mutate(action)
            except BrowserAccessBlockedError as error:
                self.fail(f"수동 해제 대기 없이 작업이 중단됨: {error}")

            dump_files = list((Path(temp_dir) / "run-visible").iterdir())

        self.assertEqual(action_calls, 1)
        self.assertEqual(page.waits, [10_000])
        self.assertEqual({path.suffix for path in dump_files}, {".png", ".html"})
        self.assertTrue(any("수동 해제 대기 시작" in event for event in events))
        self.assertTrue(any("작업 재개" in event for event in events))

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
        calls = {"click": 0, "evaluate": 0}

        class StationHandle:
            def __init__(self, page) -> None:
                self.page = page

            async def evaluate(self, _script: str) -> None:
                calls["evaluate"] += 1
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
                calls["click"] += 1
                self.page.url = (
                    "https://map.naver.com/p/subway-station/1907"
                )

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
        self.assertEqual(calls, {"click": 1, "evaluate": 0})

    async def test_station_selection_accepts_parenthesized_alias(self):
        station_label = "광교(경기대)역 신분당선지하철,전철"

        class StationControl:
            def __init__(self, page, matched: bool) -> None:
                self.page = page
                self.matched = matched

            @property
            def first(self):
                return self

            async def count(self) -> int:
                return int(self.matched)

            async def click(self, **_kwargs) -> None:
                self.page.url = (
                    "https://map.naver.com/p/subway-station/4314"
                )

        class StationFrame:
            def __init__(self, page) -> None:
                self.page = page

            def get_by_role(self, _role, *, name):
                return StationControl(
                    self.page,
                    bool(name.search(station_label)),
                )

        class Page:
            def __init__(self) -> None:
                self.url = "https://map.naver.com/p/search/광교역"
                self.frame = StationFrame(self)

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

        await navigator.select_station("광교역")

        self.assertIn("subway-station/4314", page.url)

    async def test_candidate_open_uses_locator_click(self):
        calls = {"click": 0, "evaluate": 0}

        class CandidateHandle:
            async def evaluate(self, _script: str) -> None:
                calls["evaluate"] += 1

        class CandidateLink:
            @property
            def first(self):
                return self

            def filter(self, **_kwargs):
                return self

            async def click(self, **_kwargs) -> None:
                calls["click"] += 1

            async def element_handle(self, **_kwargs):
                return CandidateHandle()

        class CandidateRow:
            def locator(self, _selector: str):
                return CandidateLink()

        class CandidateRows:
            def nth(self, _index: int):
                return CandidateRow()

        class CandidateFrame:
            def locator(self, _selector: str):
                return CandidateRows()

        navigator = object.__new__(NaverMapPage)

        async def frame_value(*_args, **_kwargs):
            return CandidateFrame()

        async def entry_value(*_args, **_kwargs):
            return type(
                "EntryFrame",
                (),
                {
                    "url": (
                        "https://pcmap.place.naver.com/restaurant/"
                        "1150149433/home"
                    )
                },
            )()

        async def mutate(action):
            return await action()

        navigator._wait_frame = frame_value
        navigator._entry_frame = entry_value
        navigator._mutate = mutate

        await navigator.open_candidate(
            CandidateTarget(
                place_id="1150149433",
                name="치보 신사점",
                dom_index=1,
            )
        )

        self.assertEqual(calls, {"click": 1, "evaluate": 0})


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
