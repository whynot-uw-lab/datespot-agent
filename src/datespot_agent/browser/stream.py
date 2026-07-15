"""실행별 on-demand CDP screencast fan-out."""

from __future__ import annotations

import asyncio
import base64
import logging
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
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


class _PageLifecycle(str, Enum):
    WAITING = "waiting"
    ATTACHED = "attached"
    DETACHED = "detached"


@dataclass(slots=True)
class _RunStreamState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    page: Page | None = None
    session: CDPSession | None = None
    frame_listener: object | None = None
    subscribers: set[BrowserStreamSubscription] = field(default_factory=set)
    started: bool = False
    stopping: bool = False
    lifecycle: _PageLifecycle = _PageLifecycle.WAITING


class CdpStreamManager:
    """Playwright Page와 viewer 수명에 맞춰 CDP screencast를 관리함."""

    def __init__(self) -> None:
        self._states: dict[str, _RunStreamState] = {}
        self._frame_tasks: set[asyncio.Task[None]] = set()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    def _state(self, run_id: str) -> _RunStreamState:
        state = self._states.get(run_id)
        if state is None:
            state = _RunStreamState()
            self._states[run_id] = state
        return state

    def has_page(self, run_id: str) -> bool:
        state = self._states.get(run_id.strip())
        return (
            state is not None
            and state.lifecycle is _PageLifecycle.ATTACHED
            and state.page is not None
        )

    async def attach_page(self, run_id: str, page: Page) -> None:
        normalized = run_id.strip()
        if not normalized or self._closed:
            return
        state = self._state(normalized)
        async with state.lock:
            if state.lifecycle is _PageLifecycle.DETACHED:
                return
            if state.page is not None and state.page is not page:
                await self._stop_locked(state)
                if state.session is not None:
                    LOGGER.warning("기존 CDP session 정리 전 page 교체 무시")
                    return
            state.page = page
            state.lifecycle = _PageLifecycle.ATTACHED
            if state.subscribers and not state.started:
                await self._start_locked(normalized, state)

    async def detach_page(self, run_id: str) -> None:
        cleanup_task = asyncio.create_task(
            self._detach_page(run_id),
            name=f"datespot-cdp-detach-{run_id.strip()}",
        )
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            await cleanup_task
            raise

    async def _detach_page(self, run_id: str) -> None:
        normalized = run_id.strip()
        if not normalized:
            return
        state = self._state(normalized)
        cancelled: asyncio.CancelledError | None = None
        async with state.lock:
            state.lifecycle = _PageLifecycle.DETACHED
            state.page = None
            try:
                await self._stop_locked(state)
            except asyncio.CancelledError as error:
                cancelled = error
            for subscription in tuple(state.subscribers):
                subscription._finish(BrowserStreamControl.ended())
            state.subscribers.clear()
        if cancelled is not None:
            raise cancelled

    async def subscribe(self, run_id: str) -> BrowserStreamSubscription:
        normalized = run_id.strip()
        if not normalized:
            raise ValueError("run_id가 비어 있음")
        if self._closed:
            raise RuntimeError("browser stream manager가 종료됨")
        state = self._state(normalized)
        subscription = BrowserStreamSubscription(self, normalized)
        async with state.lock:
            if state.lifecycle is _PageLifecycle.DETACHED:
                subscription._finish(BrowserStreamControl.ended())
                return subscription
            state.subscribers.add(subscription)
            if state.lifecycle is _PageLifecycle.WAITING:
                subscription._offer_control(BrowserStreamControl.waiting())
            elif state.started and not state.stopping:
                subscription._offer_control(BrowserStreamControl.ready())
            else:
                await self._start_locked(normalized, state)
        return subscription

    async def close(self) -> None:
        if self._close_task is not None and self._close_task.done():
            self._close_task.result()
            if any(
                state.session is not None for state in self._states.values()
            ):
                self._close_task = None
            else:
                return
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(
                self._close_all(),
                name="datespot-cdp-stream-close",
            )
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await self._close_task
            raise

    async def _close_all(self) -> None:
        for run_id in tuple(self._states):
            await self.detach_page(run_id)
        while self._frame_tasks:
            await asyncio.gather(
                *tuple(self._frame_tasks),
                return_exceptions=True,
            )

    async def _unsubscribe(
        self,
        run_id: str,
        subscription: BrowserStreamSubscription,
    ) -> None:
        cleanup_task = asyncio.create_task(
            self._unsubscribe_locked(run_id, subscription),
            name=f"datespot-cdp-unsubscribe-{run_id}",
        )
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            await cleanup_task
            raise

    async def _unsubscribe_locked(
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

    async def _start_locked(
        self,
        run_id: str,
        state: _RunStreamState,
    ) -> None:
        page = state.page
        if (
            page is None
            or state.lifecycle is not _PageLifecycle.ATTACHED
            or (state.started and not state.stopping)
        ):
            return
        if state.session is not None:
            await self._stop_locked(state)
            if state.session is not None:
                for subscription in tuple(state.subscribers):
                    subscription._finish(BrowserStreamControl.unavailable())
                state.subscribers.clear()
                return
        session: CDPSession | None = None
        try:
            session = await page.context.new_cdp_session(page)
            state.session = session
            state.stopping = False

            def on_frame(
                params: dict[str, object],
            ) -> asyncio.Task[None] | None:
                if (
                    state.session is not session
                    or state.stopping
                    or state.lifecycle is not _PageLifecycle.ATTACHED
                ):
                    return None
                task = asyncio.create_task(
                    self._handle_frame(run_id, session, params),
                    name=f"datespot-cdp-frame-{run_id}",
                )
                self._frame_tasks.add(task)
                task.add_done_callback(self._frame_tasks.discard)
                return task

            state.frame_listener = on_frame
            session.on("Page.screencastFrame", on_frame)
            state.started = True
            await session.send("Page.startScreencast", SCREENCAST_OPTIONS)
            for subscription in tuple(state.subscribers):
                subscription._offer_control(BrowserStreamControl.ready())
        except BaseException as error:
            cancelled = isinstance(error, asyncio.CancelledError)
            if not cancelled:
                LOGGER.warning("CDP screencast 시작 실패")
            if session is not None:
                try:
                    await self._stop_locked(state)
                except asyncio.CancelledError:
                    cancelled = True
            for subscription in tuple(state.subscribers):
                subscription._finish(BrowserStreamControl.unavailable())
            state.subscribers.clear()
            if cancelled:
                raise

    async def _stop_locked(self, state: _RunStreamState) -> None:
        session = state.session
        if session is None:
            return
        state.stopping = True
        listener = state.frame_listener
        if listener is not None:
            try:
                session.remove_listener("Page.screencastFrame", listener)
                state.frame_listener = None
            except Exception:
                LOGGER.warning("CDP frame listener 제거 실패")
        cleanup_task = asyncio.create_task(
            self._cleanup_session(session, state.started),
            name="datespot-cdp-session-cleanup",
        )
        cancelled: asyncio.CancelledError | None = None
        try:
            stop_succeeded, detach_succeeded = await asyncio.shield(
                cleanup_task
            )
        except asyncio.CancelledError as error:
            cancelled = error
            stop_succeeded, detach_succeeded = await cleanup_task

        if stop_succeeded:
            state.started = False
        if detach_succeeded:
            state.session = None
            state.frame_listener = None
            state.started = False
            state.stopping = False
        if cancelled is not None:
            raise cancelled

    @staticmethod
    async def _cleanup_session(
        session: CDPSession,
        started: bool,
    ) -> tuple[bool, bool]:
        stop_succeeded = not started
        if started:
            try:
                await session.send("Page.stopScreencast")
                stop_succeeded = True
            except Exception:
                LOGGER.warning("CDP screencast 종료 실패")
        try:
            await session.detach()
            detach_succeeded = True
        except Exception:
            detach_succeeded = False
            LOGGER.warning("CDP session 정리 실패")
        return stop_succeeded, detach_succeeded

    async def _handle_frame(
        self,
        run_id: str,
        session: CDPSession,
        params: dict[str, object],
    ) -> None:
        session_id = params.get("sessionId")
        failed = False
        frame: bytes | None = None
        acked = False
        try:
            raw_data = params.get("data")
            if not isinstance(raw_data, str):
                failed = True
            else:
                frame = base64.b64decode(raw_data, validate=True)
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
                    acked = True
                except Exception:
                    failed = True
                    LOGGER.warning("CDP frame ACK 실패")
            else:
                failed = True
        if failed:
            await self._fail_stream(run_id, session)
            return
        state = self._states.get(run_id)
        if (
            acked
            and frame is not None
            and state is not None
            and state.session is session
            and state.started
            and not state.stopping
            and state.lifecycle is _PageLifecycle.ATTACHED
        ):
            for subscription in tuple(state.subscribers):
                subscription._offer_frame(frame)

    async def _fail_stream(
        self,
        run_id: str,
        session: CDPSession,
    ) -> None:
        state = self._states.get(run_id)
        if (
            state is None
            or state.session is not session
            or state.stopping
        ):
            return
        async with state.lock:
            if state.session is not session or state.stopping:
                return
            await self._stop_locked(state)
            for subscription in tuple(state.subscribers):
                subscription._finish(BrowserStreamControl.unavailable())
            state.subscribers.clear()
