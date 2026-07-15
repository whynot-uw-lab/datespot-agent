"""실행별 on-demand CDP screencast fan-out."""

from __future__ import annotations

import asyncio
import base64
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from pydantic import ConfigDict, Field

from datespot_agent.models import CamelModel

if TYPE_CHECKING:
    from playwright.async_api import CDPSession, Page


LOGGER = logging.getLogger(__name__)
SCREENCAST_OPTIONS: dict[str, str | int] = {
    "format": "jpeg",
    "quality": 70,
    "maxWidth": 1280,
    "maxHeight": 720,
    "everyNthFrame": 2,
}
_PUBLIC_STREAM_ERROR = "브라우저 스트림을 사용할 수 없음"


class BrowserStreamControl(CamelModel):
    """WebSocket route가 공개 JSON으로 변환하는 control message."""

    model_config = ConfigDict(frozen=True)

    type: Literal["waiting", "ready", "ended", "error"]
    format: Literal["jpeg"] | None = None
    max_width: int | None = Field(default=None, ge=1)
    max_height: int | None = Field(default=None, ge=1)
    code: str | None = None
    message: str | None = None

    @classmethod
    def waiting(cls) -> BrowserStreamControl:
        return cls(type="waiting")

    @classmethod
    def ready(cls) -> BrowserStreamControl:
        return cls(
            type="ready",
            format="jpeg",
            max_width=1280,
            max_height=720,
        )

    @classmethod
    def ended(cls) -> BrowserStreamControl:
        return cls(type="ended")

    @classmethod
    def unavailable(cls) -> BrowserStreamControl:
        return cls(
            type="error",
            code="stream_unavailable",
            message=_PUBLIC_STREAM_ERROR,
        )


BrowserStreamMessage = BrowserStreamControl | bytes


class BrowserStreamSubscription:
    """Control message와 최신 JPEG frame 하나를 제공하는 viewer 구독."""

    def __init__(
        self,
        manager: CdpStreamManager,
        run_id: str,
    ) -> None:
        self._manager = manager
        self._run_id = run_id
        self._controls: deque[BrowserStreamControl] = deque()
        self._frame: bytes | None = None
        self._wake = asyncio.Event()
        self._finished = False
        self._viewer_closed = False

    @property
    def has_pending_frame(self) -> bool:
        return self._frame is not None

    def __aiter__(self) -> BrowserStreamSubscription:
        return self

    async def __anext__(self) -> BrowserStreamMessage:
        try:
            return await self.next_message()
        except StopAsyncIteration:
            raise

    async def next_message(self) -> BrowserStreamMessage:
        while True:
            if self._controls:
                return self._controls.popleft()
            if self._frame is not None:
                frame = self._frame
                self._frame = None
                return frame
            if self._finished or self._viewer_closed:
                raise StopAsyncIteration
            self._wake.clear()
            if self._controls or self._frame is not None or self._finished:
                continue
            await self._wake.wait()

    async def close(self) -> None:
        if self._viewer_closed:
            return
        self._viewer_closed = True
        self._wake.set()
        await self._manager._unsubscribe(self._run_id, self)

    def _offer_control(self, control: BrowserStreamControl) -> None:
        if self._finished or self._viewer_closed:
            return
        self._controls.append(control)
        self._wake.set()

    def _offer_frame(self, frame: bytes) -> None:
        if self._finished or self._viewer_closed:
            return
        self._frame = frame
        self._wake.set()

    def _finish(self, control: BrowserStreamControl) -> None:
        if self._finished or self._viewer_closed:
            return
        self._frame = None
        self._controls.append(control)
        self._finished = True
        self._wake.set()


@dataclass(slots=True)
class _RunStreamState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    page: Page | None = None
    session: CDPSession | None = None
    subscribers: set[BrowserStreamSubscription] = field(default_factory=set)
    started: bool = False


class CdpStreamManager:
    """Playwright Page와 viewer 수명에 맞춰 CDP screencast를 관리함."""

    def __init__(self) -> None:
        self._states: dict[str, _RunStreamState] = {}
        self._frame_tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    def _state(self, run_id: str) -> _RunStreamState:
        state = self._states.get(run_id)
        if state is None:
            state = _RunStreamState()
            self._states[run_id] = state
        return state

    def has_page(self, run_id: str) -> bool:
        state = self._states.get(run_id.strip())
        return state is not None and state.page is not None

    async def attach_page(self, run_id: str, page: Page) -> None:
        normalized = run_id.strip()
        if not normalized or self._closed:
            return
        state = self._state(normalized)
        async with state.lock:
            if state.page is not None and state.page is not page:
                await self._stop_locked(state)
            state.page = page
            if state.subscribers and not state.started:
                await self._start_locked(normalized, state)

    async def detach_page(self, run_id: str) -> None:
        normalized = run_id.strip()
        state = self._states.get(normalized)
        if state is None:
            return
        async with state.lock:
            state.page = None
            await self._stop_locked(state)
            for subscription in tuple(state.subscribers):
                subscription._finish(BrowserStreamControl.ended())
            state.subscribers.clear()
        if self._states.get(normalized) is state:
            self._states.pop(normalized, None)

    async def subscribe(self, run_id: str) -> BrowserStreamSubscription:
        normalized = run_id.strip()
        if not normalized:
            raise ValueError("run_id가 비어 있음")
        if self._closed:
            raise RuntimeError("browser stream manager가 종료됨")
        state = self._state(normalized)
        subscription = BrowserStreamSubscription(self, normalized)
        async with state.lock:
            state.subscribers.add(subscription)
            if state.page is None:
                subscription._offer_control(BrowserStreamControl.waiting())
            elif state.started:
                subscription._offer_control(BrowserStreamControl.ready())
            else:
                await self._start_locked(normalized, state)
        return subscription

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for run_id in tuple(self._states):
            await self.detach_page(run_id)
        if self._frame_tasks:
            await asyncio.gather(*tuple(self._frame_tasks), return_exceptions=True)

    async def _unsubscribe(
        self,
        run_id: str,
        subscription: BrowserStreamSubscription,
    ) -> None:
        state = self._states.get(run_id)
        if state is None:
            return
        async with state.lock:
            state.subscribers.discard(subscription)
            if not state.subscribers:
                await self._stop_locked(state)
                if state.page is None and self._states.get(run_id) is state:
                    self._states.pop(run_id, None)

    async def _start_locked(
        self,
        run_id: str,
        state: _RunStreamState,
    ) -> None:
        page = state.page
        if page is None or state.started:
            return
        session: CDPSession | None = None
        try:
            session = await page.context.new_cdp_session(page)
            state.session = session

            def on_frame(params: dict[str, object]) -> asyncio.Task[None]:
                task = asyncio.create_task(
                    self._handle_frame(run_id, session, params),
                    name=f"datespot-cdp-frame-{run_id}",
                )
                self._frame_tasks.add(task)
                task.add_done_callback(self._frame_tasks.discard)
                return task

            session.on("Page.screencastFrame", on_frame)
            await session.send("Page.startScreencast", SCREENCAST_OPTIONS)
            state.started = True
            for subscription in tuple(state.subscribers):
                subscription._offer_control(BrowserStreamControl.ready())
        except BaseException as error:
            cancelled = isinstance(error, asyncio.CancelledError)
            if not cancelled:
                LOGGER.warning("CDP screencast 시작 실패")
            state.session = None
            state.started = False
            if session is not None:
                try:
                    await asyncio.shield(session.detach())
                except Exception:
                    LOGGER.warning("CDP session 시작 실패 후 정리 실패")
            for subscription in tuple(state.subscribers):
                subscription._finish(BrowserStreamControl.unavailable())
            state.subscribers.clear()
            if cancelled:
                raise

    async def _stop_locked(self, state: _RunStreamState) -> None:
        session = state.session
        started = state.started
        state.session = None
        state.started = False
        if session is None:
            return
        if started:
            try:
                await session.send("Page.stopScreencast")
            except Exception:
                LOGGER.warning("CDP screencast 종료 실패")
        try:
            await session.detach()
        except Exception:
            LOGGER.warning("CDP session 정리 실패")

    async def _handle_frame(
        self,
        run_id: str,
        session: CDPSession,
        params: dict[str, object],
    ) -> None:
        session_id = params.get("sessionId")
        failed = False
        try:
            raw_data = params.get("data")
            if not isinstance(raw_data, str):
                return
            frame = base64.b64decode(raw_data, validate=True)
            state = self._states.get(run_id)
            if state is None or state.session is not session or not state.started:
                return
            for subscription in tuple(state.subscribers):
                subscription._offer_frame(frame)
        except Exception:
            failed = True
            LOGGER.warning("CDP frame 처리 실패")
        finally:
            if isinstance(session_id, int):
                try:
                    await session.send(
                        "Page.screencastFrameAck",
                        {"sessionId": session_id},
                    )
                except Exception:
                    failed = True
                    LOGGER.warning("CDP frame ACK 실패")
        if failed:
            await self._fail_stream(run_id, session)

    async def _fail_stream(
        self,
        run_id: str,
        session: CDPSession,
    ) -> None:
        state = self._states.get(run_id)
        if state is None:
            return
        async with state.lock:
            if state.session is not session:
                return
            await self._stop_locked(state)
            for subscription in tuple(state.subscribers):
                subscription._finish(BrowserStreamControl.unavailable())
            state.subscribers.clear()
