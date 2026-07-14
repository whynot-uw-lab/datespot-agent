from __future__ import annotations

import unittest
from datetime import datetime, timezone

from datespot_agent.graph import GraphRunService
from datespot_agent.models import RunConfig


class EmptyBrowserService:
    def __init__(self) -> None:
        self.closed_run_ids: list[str] = []

    async def start_session(self, run_id: str) -> None:
        return None

    async def search_candidates(self, run_id: str, config: RunConfig):
        return []

    async def close_session(self, run_id: str) -> None:
        self.closed_run_ids.append(run_id)


def build_service(browser: EmptyBrowserService) -> GraphRunService:
    return GraphRunService(
        browser_service=browser,
        photo_agent=object(),
        review_agent=object(),
        scoring_service=object(),
        clock=lambda: datetime(2026, 7, 15, 1, 2, 3, tzinfo=timezone.utc),
    )


class GraphRunIdTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_uses_caller_provided_run_id(self):
        browser = EmptyBrowserService()
        report = await build_service(browser).run(
            RunConfig(location="성수역", search_keyword="일식", max_places=1),
            run_id="run_20260715_010203_api00001",
        )
        self.assertEqual(report.run_id, "run_20260715_010203_api00001")
        self.assertIn(report.run_id, browser.closed_run_ids)

    async def test_run_generates_existing_safe_format_when_id_is_omitted(self):
        report = await build_service(EmptyBrowserService()).run(
            RunConfig(location="성수역", search_keyword="일식", max_places=1)
        )
        self.assertRegex(report.run_id, r"^run_20260715_010203_[0-9a-f]{8}$")
