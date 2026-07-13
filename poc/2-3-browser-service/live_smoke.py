"""BrowserService 네이버 실사이트 수동 스모크."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from datespot_agent.browser import (
    BrowserAccessBlockedError,
    BrowserService,
)
from datespot_agent.browser.pacing import LiveSmokeGuard
from datespot_agent.models import RunConfig

STAMP_PATH = (
    Path.home()
    / ".cache"
    / "datespot-agent"
    / "naver-live-smoke-finished-at"
)


async def run(
    *,
    service_factory: Callable[..., Any] = BrowserService,
    guard_factory: Callable[..., Any] = LiveSmokeGuard,
) -> int:
    run_id = f"live-{uuid4()}"
    service = service_factory(headless=False)
    async with guard_factory(stamp_path=STAMP_PATH):
        try:
            await service.start_session(run_id)
            candidates = await service.search_candidates(
                run_id,
                RunConfig(
                    location="신사역",
                    search_keyword="일식",
                    max_places=1,
                ),
            )
            detail = await service.extract_place_detail(
                run_id,
                candidates[0],
            )
            print(
                json.dumps(
                    detail.model_dump(mode="json", by_alias=True),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        except BrowserAccessBlockedError as error:
            print(f"접근 제한 감지로 즉시 중단함: {error}")
            return 2
        finally:
            await service.close_all()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
