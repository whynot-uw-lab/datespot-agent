from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import unittest
from pathlib import Path

from datespot_agent.browser import BrowserAccessBlockedError
from datespot_agent.models import CandidatePlace, PlaceDetail

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "poc" / "2-3-browser-service" / "live_smoke.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "browser_service_live_smoke",
        MODULE_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("live smoke module spec 생성 실패")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeGuard:
    def __init__(self, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class FakeService:
    latest = None

    def __init__(self, *, headless: bool) -> None:
        self.headless = headless
        self.closed = False
        self.extracted_place_id: str | None = None
        type(self).latest = self

    async def start_session(self, _run_id: str) -> None:
        return None

    async def search_candidates(self, _run_id: str, _config):
        return [
            CandidatePlace(place_id="1", name="첫 장소"),
            CandidatePlace(place_id="2", name="둘째 장소"),
        ]

    async def extract_place_detail(self, _run_id: str, candidate: CandidatePlace):
        self.extracted_place_id = candidate.place_id
        return PlaceDetail(
            place_id=candidate.place_id,
            name=candidate.name,
            photo_urls=["https://img/1.jpg"],
            reviews=["조용해요"],
            review_count=10,
        )

    async def close_all(self) -> None:
        self.closed = True


class BlockedService(FakeService):
    async def search_candidates(self, _run_id: str, _config):
        raise BrowserAccessBlockedError("429")


class BrowserLiveSmokeTests(unittest.TestCase):
    def test_success_uses_first_candidate_and_always_closes(self):
        module = load_module()
        with contextlib.redirect_stdout(io.StringIO()):
            exit_code = asyncio.run(
                module.run(
                    service_factory=FakeService,
                    guard_factory=FakeGuard,
                )
            )

        service = FakeService.latest
        self.assertEqual(exit_code, 0)
        self.assertFalse(service.headless)
        self.assertEqual(service.extracted_place_id, "1")
        self.assertTrue(service.closed)

    def test_access_block_returns_two_and_always_closes(self):
        module = load_module()
        with contextlib.redirect_stdout(io.StringIO()):
            exit_code = asyncio.run(
                module.run(
                    service_factory=BlockedService,
                    guard_factory=FakeGuard,
                )
            )

        service = BlockedService.latest
        self.assertEqual(exit_code, 2)
        self.assertTrue(service.closed)


if __name__ == "__main__":
    unittest.main()
