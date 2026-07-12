from __future__ import annotations

import asyncio
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "poc" / "1-2-naver-map-flow" / "explore.py"


def load_module():
    spec = importlib.util.spec_from_file_location("naver_map_flow_explore", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class NaverMapFlowTests(unittest.TestCase):
    def test_parse_list_rows_uses_structure_and_filters_ads(self):
        module = load_module()
        rows = [
            {
                "domIndex": 0,
                "rawText": "명우한우 신사역 본점 예약 톡톡 쿠폰 육류,고기요리 광고",
                "controls": [
                    {"tag": "a", "role": "button", "text": "명우한우 신사역 본점"},
                    {"tag": "button", "role": None, "text": "저장"},
                ],
            },
            {
                "domIndex": 1,
                "rawText": "카이센동 우니도 본점 쿠폰 일식당",
                "controls": [
                    {"tag": "a", "role": "button", "text": "카이센동 우니도 본점"},
                    {"tag": "button", "role": None, "text": "더보기"},
                ],
            },
        ]

        items = module.parse_list_rows(rows)
        organic = [item for item in items if not item["isAd"]]

        self.assertEqual(items[0]["clickText"], "명우한우 신사역 본점")
        self.assertIs(items[0]["isAd"], True)
        self.assertEqual(
            organic,
            [
                {
                    "domIndex": 1,
                    "rawText": "카이센동 우니도 본점 쿠폰 일식당",
                    "clickText": "카이센동 우니도 본점",
                    "isAd": False,
                }
            ],
        )

    def test_parse_list_rows_trims_action_and_category_suffixes_from_title(self):
        module = load_module()
        rows = [
            {
                "domIndex": 0,
                "rawText": "치보 신사점 예약 쿠폰 일식당 네이버 예약시 평일 오픈런 이벤트",
                "controls": [
                    {"tag": "a", "role": "button", "text": "치보 신사점예약쿠폰일식당"},
                ],
            }
        ]

        self.assertEqual(module.parse_list_rows(rows)[0]["clickText"], "치보 신사점")

    def test_photo_url_filter_keeps_place_assets_and_excludes_map_tiles(self):
        module = load_module()
        urls = [
            "https://search.pstatic.net/common/?src=https%3A%2F%2Fldb-phinf.pstatic.net%2Fx.jpg",
            "https://blogfiles.pstatic.net/MjAyNjAx/example.jpg",
            "https://ssl.pstatic.net/static/maps/mantle/1x/tile.png",
            "",
        ]

        self.assertEqual(module.filter_photo_urls(urls), urls[:2])

    def test_place_routes_are_direct_pcmap_routes(self):
        module = load_module()

        routes = module.build_place_routes("1720070048")

        self.assertEqual(routes["home"], "https://pcmap.place.naver.com/restaurant/1720070048/home")
        self.assertIn("subFilter=INTERIOR", routes["photos"])
        self.assertTrue(routes["reviews"].endswith("/review/visitor?reviewSort=recent"))

    def test_extract_place_id_from_map_url(self):
        module = load_module()

        self.assertEqual(
            module.extract_place_id_from_url("https://map.naver.com/p/search/x/place/33585987?c=1"),
            "33585987",
        )
        self.assertIsNone(module.extract_place_id_from_url("https://map.naver.com/p/search/x"))

    def test_build_map_search_url_encodes_query_without_ui_dependency(self):
        module = load_module()

        self.assertEqual(
            module.build_map_search_url("신사역 음식점"),
            "https://map.naver.com/p/search/%EC%8B%A0%EC%82%AC%EC%97%AD%20%EC%9D%8C%EC%8B%9D%EC%A0%90",
        )

    def test_build_category_queries_includes_fallback_for_empty_results(self):
        module = load_module()

        self.assertEqual(
            module.build_category_queries("신사역", "음식점"),
            ["신사역 음식점", "신사역 맛집"],
        )

    def test_category_search_modes_prioritize_direct_when_station_search_failed(self):
        module = load_module()

        modes = module.build_category_search_modes(
            {"selectedStation": False, "error": "station search: TimeoutError"}
        )

        self.assertEqual(modes, ["direct_pcmap", "map_shell"])

    def test_category_search_modes_try_map_shell_first_after_station_selected(self):
        module = load_module()

        modes = module.build_category_search_modes({"selectedStation": True, "error": ""})

        self.assertEqual(modes, ["map_shell", "direct_pcmap"])

    def test_safe_close_ignores_already_closed_playwright_targets(self):
        module = load_module()
        calls = {"close": 0}

        class ClosedTarget:
            async def close(self):
                calls["close"] += 1
                raise RuntimeError("Target page, context or browser has been closed")

        asyncio.run(module.safe_close(ClosedTarget()))

        self.assertEqual(calls["close"], 1)

    def test_build_direct_pcmap_list_url_uses_station_coordinates(self):
        module = load_module()

        url = module.build_direct_pcmap_list_url("신사역 음식점")

        self.assertTrue(url.startswith("https://pcmap.place.naver.com/restaurant/list?"))
        self.assertIn("query=%EC%8B%A0%EC%82%AC%EC%97%AD%20%EC%9D%8C%EC%8B%9D%EC%A0%90", url)
        self.assertIn("x=127.019566", url)
        self.assertIn("y=37.516036", url)
        self.assertIn("clientX=127.019566", url)
        self.assertIn("clientY=37.516036", url)

    def test_browser_launch_options_are_headful_for_visible_poc(self):
        module = load_module()

        self.assertEqual(module.build_browser_launch_options(), {"headless": False})

    def test_merge_place_ids_matches_apollo_businesses_by_clean_name(self):
        module = load_module()
        items = [
            {"clickText": "카이센동 우니도 본점", "isAd": False},
            {"clickText": "치보 신사점", "isAd": False},
            {"clickText": "광고 가게", "isAd": True},
        ]
        businesses = [
            {"id": "1720070048", "name": "카이센동 우니도 본점"},
            {"id": "1150149433", "name": "치보 신사점"},
        ]

        merged = module.merge_place_ids_from_businesses(items, businesses)

        self.assertEqual(merged[0]["placeId"], "1720070048")
        self.assertEqual(merged[1]["placeId"], "1150149433")
        self.assertNotIn("placeId", merged[2])

    def test_click_review_more_uses_dom_click_without_actionability_wait(self):
        module = load_module()
        calls = {"click": 0, "evaluate": 0, "wait": 0}

        class FakeHandle:
            async def evaluate(self, _script):
                calls["evaluate"] += 1

        class FakeLocator:
            @property
            def first(self):
                return self

            async def count(self):
                return 1

            async def click(self, **_kwargs):
                calls["click"] += 1

            async def element_handle(self, **_kwargs):
                return FakeHandle()

        class FakePage:
            def get_by_role(self, role, name):
                self.role = role
                self.name = name
                return FakeLocator()

            async def wait_for_timeout(self, timeout):
                calls["wait"] = timeout

        clicked = asyncio.run(module.click_review_more(FakePage()))

        self.assertTrue(clicked)
        self.assertEqual(calls["click"], 0)
        self.assertEqual(calls["evaluate"], 1)
        self.assertEqual(calls["wait"], 1500)

    def test_click_list_item_uses_dom_click_before_reading_place_id(self):
        module = load_module()
        calls = {"click": 0, "evaluate": 0, "wait": 0}

        class FakeHandle:
            async def evaluate(self, _script):
                calls["evaluate"] += 1

        class FakeTitleLink:
            @property
            def first(self):
                return self

            async def click(self, **_kwargs):
                calls["click"] += 1

            async def element_handle(self, **_kwargs):
                return FakeHandle()

        class FakeRow:
            def locator(self, _selector):
                return self

            def filter(self, **_kwargs):
                return FakeTitleLink()

        class FakeRows:
            def nth(self, _index):
                return FakeRow()

        class FakeListFrame:
            def locator(self, _selector):
                return FakeRows()

        class FakePage:
            url = "https://map.naver.com/p/search/x/place/1720070048"
            frames = []

            async def wait_for_timeout(self, timeout):
                calls["wait"] = timeout

        place_id = asyncio.run(
            module.click_list_item_for_place_id(
                FakePage(),
                FakeListFrame(),
                {"domIndex": 3, "clickText": "카이센동 우니도 본점"},
            )
        )

        self.assertEqual(place_id, "1720070048")
        self.assertEqual(calls["click"], 0)
        self.assertEqual(calls["evaluate"], 1)
        self.assertEqual(calls["wait"], 2500)


if __name__ == "__main__":
    unittest.main()
