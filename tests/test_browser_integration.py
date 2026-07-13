from __future__ import annotations

import unittest
from pathlib import Path

from playwright.async_api import async_playwright

from datespot_agent.browser.naver_map import NaverMapPage
from datespot_agent.browser.pacing import InteractionPacer
from datespot_agent.models import RunConfig

FIXTURES = Path(__file__).parent / "fixtures"


class BrowserIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_map_shell_candidate_and_detail_flow_without_network(self):
        now = 0.0
        sleeps: list[float] = []
        routed_urls: list[str] = []

        def clock() -> float:
            return now

        async def sleep(seconds: float) -> None:
            nonlocal now
            sleeps.append(seconds)
            now += seconds

        async with async_playwright() as runtime:
            browser = await runtime.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            async def route_handler(route):
                url = route.request.url
                routed_urls.append(url)
                if url.startswith("https://map.naver.com"):
                    path = FIXTURES / "naver_map_shell.html"
                    content_type = "text/html; charset=utf-8"
                elif "/list" in url:
                    path = FIXTURES / "naver_search_results.html"
                    content_type = "text/html; charset=utf-8"
                elif url.startswith("https://img/"):
                    await route.fulfill(
                        status=200,
                        content_type="image/gif",
                        body=b"GIF89a",
                    )
                    return
                else:
                    path = FIXTURES / "naver_entry.html"
                    content_type = "text/html; charset=utf-8"
                await route.fulfill(
                    status=200,
                    content_type=content_type,
                    body=path.read_text(encoding="utf-8"),
                )

            await page.route("**/*", route_handler)
            navigator = NaverMapPage(
                page,
                InteractionPacer(clock=clock, sleep=sleep),
            )
            await navigator.open()
            config = RunConfig(location="신사역", search_keyword="일식")
            await navigator.search_location(config.location)
            await navigator.select_station(config.location)
            await navigator.set_zoom(15)
            await navigator.search_keyword(config.search_keyword)
            candidates, targets = await navigator.extract_candidates()
            detail = await navigator.extract_place_detail(
                candidates[0],
                targets[candidates[0].place_id],
            )

            self.assertEqual(
                page.url.split("?")[0],
                "https://map.naver.com/p/search/%EC%9D%BC%EC%8B%9D",
            )
            self.assertEqual(len(candidates), 1)
            self.assertEqual(len(detail.photo_urls), 5)
            self.assertEqual(len(detail.reviews), 50)
            self.assertEqual(detail.review_count, 1234)
            self.assertIsNotNone(page.frame(name="searchIframe"))
            self.assertIsNone(page.frame(name="entryIframe"))
            self.assertTrue(sleeps)
            self.assertTrue(all(seconds == 3.0 for seconds in sleeps))
            self.assertTrue(
                all(
                    url.startswith(
                        (
                            "https://map.naver.com",
                            "https://pcmap.place.naver.com",
                            "https://img/",
                        )
                    )
                    for url in routed_urls
                )
            )
            await context.close()
            await browser.close()


if __name__ == "__main__":
    unittest.main()
