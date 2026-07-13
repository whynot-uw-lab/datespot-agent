"""네이버 실사이트 조작 간격과 스모크 실행 잠금."""

from __future__ import annotations

import asyncio
import fcntl
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import TracebackType
from typing import IO, TypeVar

ACTION_INTERVAL_SECONDS = 3.0
RETRY_DELAY_SECONDS = 5.0
LIVE_SMOKE_COOLDOWN_SECONDS = 30.0
T = TypeVar("T")


class InteractionPacer:
    """모든 상태 변경 조작을 직렬화하고 최소 간격을 보장한다."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._last_action_started: float | None = None

    async def run(self, action: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            now = self._clock()
            if self._last_action_started is not None:
                remaining = ACTION_INTERVAL_SECONDS - (
                    now - self._last_action_started
                )
                if remaining > 0:
                    await self._sleep(remaining)
            self._last_action_started = self._clock()
            return await action()

    async def wait_before_retry(self) -> None:
        await self._sleep(RETRY_DELAY_SECONDS)


class LiveSmokeGuard:
    """프로세스 간 직렬 실행과 이전 실행 종료 후 cooldown을 강제한다."""

    def __init__(
        self,
        *,
        stamp_path: Path,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._stamp_path = stamp_path
        self._lock_path = stamp_path.with_suffix(".lock")
        self._wall_clock = wall_clock
        self._sleep = sleep
        self._lock_file: IO[str] | None = None

    async def __aenter__(self) -> "LiveSmokeGuard":
        self._stamp_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = self._lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(
                self._lock_file.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError as error:
            self._lock_file.close()
            self._lock_file = None
            raise RuntimeError("네이버 실사이트 테스트가 이미 실행 중임") from error

        try:
            if self._stamp_path.exists():
                last_finished = float(
                    self._stamp_path.read_text(encoding="utf-8")
                )
                remaining = LIVE_SMOKE_COOLDOWN_SECONDS - (
                    self._wall_clock() - last_finished
                )
                if remaining > 0:
                    await self._sleep(remaining)
        except Exception:
            self._release_lock()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stamp_path.write_text(
            str(self._wall_clock()),
            encoding="utf-8",
        )
        self._release_lock()

    def _release_lock(self) -> None:
        if self._lock_file is None:
            return
        fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
        self._lock_file.close()
        self._lock_file = None
