"""실행별 typed event와 bounded replay fan-out."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict, deque
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from typing import TypeAlias, TypeVar, cast

from pydantic import ConfigDict, Field, field_validator, model_validator

from datespot_agent.api.models import RunJobStatus, RunStatusResponse
from datespot_agent.models import CamelModel, PlaceResult


LOGGER = logging.getLogger(__name__)
_CLOSE_SENTINEL = object()
_T = TypeVar("_T")


class RunEventType(str, Enum):
    SNAPSHOT = "snapshot"
    QUEUED = "queued"
    RUNNING = "running"
    PROGRESS = "progress"
    PLACE_RESULT = "place_result"
    BROWSER_READY = "browser_ready"
    BROWSER_CLOSED = "browser_closed"
    REPORT_SAVED = "report_saved"
    COMPLETED = "completed"
    FAILED = "failed"
    REPLAY_RESET = "replay_reset"


class ProgressStage(str, Enum):
    SESSION_START = "session_start"
    CANDIDATE_SEARCH = "candidate_search"
    PLACE_DETAIL = "place_detail"
    SECURITY_CHECK = "security_check"
    PHOTO_ANALYSIS = "photo_analysis"
    REVIEW_ANALYSIS = "review_analysis"
    SCORING = "scoring"
    REPORT_BUILD = "report_build"


class _FrozenCamelModel(CamelModel):
    model_config = ConfigDict(frozen=True)


class RunLifecycleData(_FrozenCamelModel):
    status: RunJobStatus
    report_available: bool = False
    error: str | None = None


class RunProgressData(_FrozenCamelModel):
    stage: ProgressStage
    message: str = Field(min_length=1)
    place_id: str | None = Field(default=None, min_length=1)
    place_name: str | None = Field(default=None, min_length=1)


class RunReportSavedData(_FrozenCamelModel):
    report_url: str = Field(min_length=1)


class RunBrowserData(_FrozenCamelModel):
    pass


class RunReplayResetData(_FrozenCamelModel):
    latest_sequence: int = Field(ge=0)


RunEventPayload: TypeAlias = (
    RunStatusResponse
    | RunLifecycleData
    | RunProgressData
    | PlaceResult
    | RunReportSavedData
    | RunBrowserData
    | RunReplayResetData
)


class RunEvent(_FrozenCamelModel):
    run_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    occurred_at: datetime
    type: RunEventType
    data: RunEventPayload

    @field_validator("occurred_at")
    @classmethod
    def normalize_occurred_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at에는 timezone 정보가 필요하다")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_payload_type(self) -> RunEvent:
        expected: dict[RunEventType, type[object]] = {
            RunEventType.SNAPSHOT: RunStatusResponse,
            RunEventType.QUEUED: RunLifecycleData,
            RunEventType.RUNNING: RunLifecycleData,
            RunEventType.PROGRESS: RunProgressData,
            RunEventType.PLACE_RESULT: PlaceResult,
            RunEventType.BROWSER_READY: RunBrowserData,
            RunEventType.BROWSER_CLOSED: RunBrowserData,
            RunEventType.REPORT_SAVED: RunReportSavedData,
            RunEventType.COMPLETED: RunLifecycleData,
            RunEventType.FAILED: RunLifecycleData,
            RunEventType.REPLAY_RESET: RunReplayResetData,
        }
        if not isinstance(self.data, expected[self.type]):
            raise ValueError(f"{self.type.value} event payload가 올바르지 않다")
        if isinstance(self.data, RunLifecycleData):
            if self.data.status.value != self.type.value:
                raise ValueError("lifecycle event type과 status가 일치해야 한다")
        return self


class RunEventSubscription:
    """Replay snapshot과 이후 live event를 제공하는 구독."""

    def __init__(
        self,
        *,
        replay: tuple[RunEvent, ...],
        reset_required: bool,
        latest_sequence: int,
        capacity: int,
        on_close: Callable[[RunEventSubscription], None] | None,
    ) -> None:
        self.replay = replay
        self.reset_required = reset_required
        self.latest_sequence = latest_sequence
        self.overflowed = False
        self._queue: asyncio.Queue[RunEvent | object] = asyncio.Queue(
            maxsize=capacity
        )
        self._on_close = on_close
        self._draining = False
        self._ended = False

    def __aiter__(self) -> RunEventSubscription:
        return self

    async def __anext__(self) -> RunEvent:
        if self._ended:
            raise StopAsyncIteration
        if self._draining and self._queue.empty():
            self._ended = True
            raise StopAsyncIteration
        item = await self._queue.get()
        if item is _CLOSE_SENTINEL:
            self._ended = True
            raise StopAsyncIteration
        return cast(RunEvent, item)

    def close(self) -> None:
        self._finish(drain=False)

    async def aclose(self) -> None:
        self.close()

    def _put(self, event: RunEvent) -> None:
        self._queue.put_nowait(event)

    def _finish(self, *, drain: bool, overflowed: bool = False) -> None:
        if self._ended:
            return
        callback, self._on_close = self._on_close, None
        if callback is not None:
            callback(self)
        self.overflowed = self.overflowed or overflowed
        self._draining = drain
        if drain and not self._queue.empty():
            return
        while not self._queue.empty():
            self._queue.get_nowait()
        self._queue.put_nowait(_CLOSE_SENTINEL)


class _RunBuffer:
    def __init__(self, replay_capacity: int) -> None:
        self.events: deque[RunEvent] = deque(maxlen=replay_capacity)
        self.subscribers: set[RunEventSubscription] = set()
        self.latest_sequence = 0


class RunEventHub:
    """단일 event loop에서 non-blocking fan-out을 수행함."""

    def __init__(
        self,
        *,
        replay_capacity: int = 1_000,
        subscriber_capacity: int = 128,
        terminal_capacity: int = 100,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        for name, value in (
            ("replay_capacity", replay_capacity),
            ("subscriber_capacity", subscriber_capacity),
            ("terminal_capacity", terminal_capacity),
        ):
            if value < 1:
                raise ValueError(f"{name}은 1 이상이어야 한다")
        self._replay_capacity = replay_capacity
        self._subscriber_capacity = subscriber_capacity
        self._terminal_capacity = terminal_capacity
        self._clock = clock
        self._active: dict[str, _RunBuffer] = {}
        self._terminal: OrderedDict[str, _RunBuffer] = OrderedDict()
        self._closed = False

    def open_run(self, run_id: str) -> None:
        self._ensure_open()
        if not run_id.strip():
            raise ValueError("run_id는 비어 있을 수 없다")
        if run_id in self._active or run_id in self._terminal:
            raise ValueError(f"이미 열린 run: {run_id}")
        self._active[run_id] = _RunBuffer(self._replay_capacity)

    def publish(
        self,
        run_id: str,
        event_type: RunEventType,
        data: object,
    ) -> RunEvent:
        self._ensure_open()
        if run_id in self._terminal:
            raise RuntimeError(f"terminal run에는 publish할 수 없음: {run_id}")
        buffer = self._active.get(run_id)
        if buffer is None:
            raise KeyError(run_id)
        sequence = buffer.latest_sequence + 1
        event = RunEvent.model_validate(
            {
                "run_id": run_id,
                "sequence": sequence,
                "occurred_at": self._clock(),
                "type": event_type,
                "data": data,
            }
        )
        buffer.latest_sequence = sequence
        buffer.events.append(event)
        for subscription in tuple(buffer.subscribers):
            try:
                subscription._put(event)
            except asyncio.QueueFull:
                subscription._finish(drain=False, overflowed=True)
        return event

    def mark_terminal(self, run_id: str) -> None:
        self._ensure_open()
        if run_id in self._terminal:
            self._terminal.move_to_end(run_id)
            return
        buffer = self._active.pop(run_id, None)
        if buffer is None:
            raise KeyError(run_id)
        self._terminal[run_id] = buffer
        for subscription in tuple(buffer.subscribers):
            subscription._finish(drain=True)
        while len(self._terminal) > self._terminal_capacity:
            self._terminal.popitem(last=False)

    def subscribe(
        self,
        run_id: str,
        last_event_id: int | None,
    ) -> RunEventSubscription:
        self._ensure_open()
        if last_event_id is not None and last_event_id < 0:
            raise ValueError("last_event_id는 0 이상이어야 한다")
        buffer = self._active.get(run_id) or self._terminal.get(run_id)
        if buffer is None:
            raise KeyError(run_id)
        events = tuple(buffer.events)
        latest = buffer.latest_sequence
        reset_required = False
        if last_event_id is None:
            replay = events
        elif last_event_id > latest:
            reset_required = True
            replay = ()
        elif events and last_event_id < events[0].sequence - 1:
            reset_required = True
            replay = ()
        else:
            replay = tuple(
                event for event in events if event.sequence > last_event_id
            )

        def unregister(subscription: RunEventSubscription) -> None:
            buffer.subscribers.discard(subscription)

        is_terminal = run_id in self._terminal
        subscription = RunEventSubscription(
            replay=replay,
            reset_required=reset_required,
            latest_sequence=latest,
            capacity=self._subscriber_capacity,
            on_close=None if is_terminal else unregister,
        )
        if is_terminal:
            subscription._finish(drain=True)
        else:
            buffer.subscribers.add(subscription)
        return subscription

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        buffers = (*self._active.values(), *self._terminal.values())
        for buffer in buffers:
            for subscription in tuple(buffer.subscribers):
                subscription._finish(drain=True)
        self._active.clear()
        self._terminal.clear()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("run event hub가 닫힘")


class RunEventPublisher:
    """Hub 오류를 실행 workflow에서 격리하는 typed publisher."""

    def __init__(self, hub: RunEventHub) -> None:
        self._hub = hub

    def open_run(self, run_id: str) -> None:
        self._safe(run_id, "open_run", lambda: self._hub.open_run(run_id))

    def lifecycle(
        self,
        run_id: str,
        event_type: RunEventType,
        status: RunStatusResponse,
    ) -> RunEvent | None:
        data = RunLifecycleData(
            status=status.status,
            report_available=status.report_available,
            error=status.error,
        )
        return self._safe(
            run_id,
            event_type.value,
            lambda: self._hub.publish(run_id, event_type, data),
        )

    def progress(
        self,
        run_id: str,
        stage: ProgressStage,
        message: str,
        *,
        place_id: str | None = None,
        place_name: str | None = None,
    ) -> RunEvent | None:
        data = RunProgressData(
            stage=stage,
            message=message,
            place_id=place_id,
            place_name=place_name,
        )
        return self._safe(
            run_id,
            RunEventType.PROGRESS.value,
            lambda: self._hub.publish(run_id, RunEventType.PROGRESS, data),
        )

    def place_result(self, run_id: str, result: PlaceResult) -> RunEvent | None:
        return self._safe(
            run_id,
            RunEventType.PLACE_RESULT.value,
            lambda: self._hub.publish(
                run_id, RunEventType.PLACE_RESULT, result.model_copy(deep=True)
            ),
        )

    def browser_ready(self, run_id: str) -> RunEvent | None:
        return self._browser(run_id, RunEventType.BROWSER_READY)

    def browser_closed(self, run_id: str) -> RunEvent | None:
        return self._browser(run_id, RunEventType.BROWSER_CLOSED)

    def report_saved(self, run_id: str, report_url: str) -> RunEvent | None:
        data = RunReportSavedData(report_url=report_url)
        return self._safe(
            run_id,
            RunEventType.REPORT_SAVED.value,
            lambda: self._hub.publish(run_id, RunEventType.REPORT_SAVED, data),
        )

    def terminal(
        self,
        run_id: str,
        event_type: RunEventType,
        status: RunStatusResponse,
    ) -> RunEvent | None:
        event = self.lifecycle(run_id, event_type, status)
        self._safe(
            run_id,
            "mark_terminal",
            lambda: self._hub.mark_terminal(run_id),
        )
        return event

    def _browser(
        self, run_id: str, event_type: RunEventType
    ) -> RunEvent | None:
        data = RunBrowserData()
        return self._safe(
            run_id,
            event_type.value,
            lambda: self._hub.publish(run_id, event_type, data),
        )

    def _safe(
        self,
        run_id: str,
        action: str,
        operation: Callable[[], _T],
    ) -> _T | None:
        try:
            return operation()
        except Exception:
            LOGGER.warning(
                "run event 처리 실패: run_id=%s action=%s",
                run_id,
                action,
                exc_info=True,
            )
            return None


__all__ = [
    "ProgressStage",
    "RunBrowserData",
    "RunEvent",
    "RunEventHub",
    "RunEventPayload",
    "RunEventPublisher",
    "RunEventSubscription",
    "RunEventType",
    "RunLifecycleData",
    "RunProgressData",
    "RunReplayResetData",
    "RunReportSavedData",
]
