"""FastAPI 애플리케이션 조립과 실행 HTTP 계약."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from inspect import isawaitable
from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    WebSocket,
    status,
)
from fastapi.sse import EventSourceResponse, ServerSentEvent
from starlette.websockets import WebSocketDisconnect

from datespot_agent.api.coordinator import RunCoordinator
from datespot_agent.api.errors import CoordinatorUnavailableError
from datespot_agent.api.events import (
    RunEvent,
    RunEventSubscription,
    RunEventType,
)
from datespot_agent.api.models import (
    HealthResponse,
    RunAccepted,
    RunJobStatus,
    RunStatusResponse,
)
from datespot_agent.api.runtime import AppRuntime, create_runtime
from datespot_agent.browser.stream import BrowserStreamControl
from datespot_agent.models import RunConfig, RunReport


RuntimeFactory = Callable[[], AppRuntime | Awaitable[AppRuntime]]
_SSE_RETRY_MILLISECONDS = 2_000
_PUBLIC_EXECUTION_ERROR = "실행 처리 중 오류가 발생함"
_TERMINAL_EVENTS = {RunEventType.COMPLETED, RunEventType.FAILED}


@dataclass(frozen=True, slots=True)
class _PreparedRunEvents:
    snapshot: RunStatusResponse
    subscription: RunEventSubscription | None
    latest_sequence: int


def _parse_last_event_id(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_detail("invalid_event_id", "Last-Event-ID가 올바르지 않음"),
        ) from error
    if parsed < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_detail("invalid_event_id", "Last-Event-ID가 올바르지 않음"),
        )
    return parsed


def _public_snapshot(snapshot: RunStatusResponse) -> RunStatusResponse:
    public = snapshot.model_copy(deep=True)
    if public.error is not None:
        public.error = _PUBLIC_EXECUTION_ERROR
    return public


def _synthetic_event(
    *,
    run_id: str,
    sequence: int,
    event_type: RunEventType,
    data: dict[str, object],
) -> dict[str, object]:
    return {
        "runId": run_id,
        "sequence": sequence,
        "occurredAt": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "type": event_type.value,
        "data": data,
    }


def _sse_message(
    event_type: RunEventType,
    payload: dict[str, object],
    *,
    event_id: int | None = None,
) -> ServerSentEvent:
    return ServerSentEvent(
        data=payload,
        event=event_type.value,
        id=None if event_id is None else str(event_id),
        retry=_SSE_RETRY_MILLISECONDS,
    )


def _canonical_sse(event: RunEvent) -> ServerSentEvent:
    return _sse_message(
        event.type,
        event.model_dump(mode="json", by_alias=True),
        event_id=event.sequence,
    )


def _detail(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def create_app(runtime_factory: RuntimeFactory = create_runtime) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime_or_awaitable = runtime_factory()
        runtime = (
            await runtime_or_awaitable
            if isawaitable(runtime_or_awaitable)
            else runtime_or_awaitable
        )
        app.state.runtime = runtime
        try:
            await runtime.start()
            yield
        finally:
            await runtime.stop()

    app = FastAPI(title="datespot-agent", lifespan=lifespan)

    def coordinator(request: Request) -> RunCoordinator:
        return request.app.state.runtime.coordinator

    async def prepare_run_events(
        run_id: str,
        request: Request,
        last_event_id: Annotated[
            str | None,
            Header(alias="Last-Event-ID"),
        ] = None,
    ) -> _PreparedRunEvents:
        snapshot = coordinator(request).get_status(run_id)
        if snapshot is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_detail("run_not_found", "실행을 찾을 수 없음"),
            )
        parsed_event_id = _parse_last_event_id(last_event_id)
        try:
            subscription = request.app.state.runtime.event_hub.subscribe(
                run_id,
                parsed_event_id,
            )
        except KeyError:
            subscription = None
        return _PreparedRunEvents(
            snapshot=_public_snapshot(snapshot),
            subscription=subscription,
            latest_sequence=(
                subscription.latest_sequence
                if subscription is not None
                else 0
            ),
        )

    @app.get("/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        return coordinator(request).health()

    @app.post(
        "/runs",
        response_model=RunAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_run(config: RunConfig, request: Request) -> RunAccepted:
        try:
            return coordinator(request).submit(config)
        except CoordinatorUnavailableError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=_detail("coordinator_unavailable", str(error)),
            ) from error

    @app.get("/runs/{run_id}", response_model=RunStatusResponse)
    async def get_run(run_id: str, request: Request) -> RunStatusResponse:
        snapshot = coordinator(request).get_status(run_id)
        if snapshot is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_detail("run_not_found", "실행을 찾을 수 없음"),
            )
        return snapshot

    @app.get(
        "/runs/{run_id}/events",
        response_class=EventSourceResponse,
    )
    async def get_run_events(
        prepared: _PreparedRunEvents = Depends(prepare_run_events),
    ) -> AsyncIterator[ServerSentEvent]:
        subscription = prepared.subscription
        try:
            if subscription is None:
                yield _sse_message(
                    RunEventType.SNAPSHOT,
                    _synthetic_event(
                        run_id=prepared.snapshot.run_id,
                        sequence=prepared.latest_sequence,
                        event_type=RunEventType.SNAPSHOT,
                        data=prepared.snapshot.model_dump(
                            mode="json",
                            by_alias=True,
                        ),
                    ),
                )
                return

            if subscription.reset_required:
                yield _sse_message(
                    RunEventType.REPLAY_RESET,
                    _synthetic_event(
                        run_id=prepared.snapshot.run_id,
                        sequence=prepared.latest_sequence,
                        event_type=RunEventType.REPLAY_RESET,
                        data={"latestSequence": prepared.latest_sequence},
                    ),
                )
                yield _sse_message(
                    RunEventType.SNAPSHOT,
                    _synthetic_event(
                        run_id=prepared.snapshot.run_id,
                        sequence=prepared.latest_sequence,
                        event_type=RunEventType.SNAPSHOT,
                        data=prepared.snapshot.model_dump(
                            mode="json",
                            by_alias=True,
                        ),
                    ),
                )

            for event in subscription.replay:
                yield _canonical_sse(event)
                if event.type in _TERMINAL_EVENTS:
                    return

            async for event in subscription:
                yield _canonical_sse(event)
                if event.type in _TERMINAL_EVENTS:
                    return
        finally:
            if subscription is not None:
                subscription.close()

    @app.get("/runs/{run_id}/report", response_model=RunReport)
    async def get_report(run_id: str, request: Request) -> RunReport:
        run_coordinator = coordinator(request)
        snapshot = run_coordinator.get_status(run_id)
        if snapshot is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=_detail("run_not_found", "실행을 찾을 수 없음"),
            )
        report = run_coordinator.get_report(run_id)
        if report is not None:
            return report
        if snapshot.status in (RunJobStatus.QUEUED, RunJobStatus.RUNNING):
            code = "report_not_ready"
            message = "리포트가 아직 준비되지 않음"
        else:
            code = "report_unavailable"
            message = "실행 또는 저장 실패로 리포트를 사용할 수 없음"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_detail(code, message),
        )

    @app.websocket("/runs/{run_id}/browser-stream")
    async def browser_stream(websocket: WebSocket, run_id: str) -> None:
        runtime = websocket.app.state.runtime
        snapshot = runtime.coordinator.get_status(run_id)
        if snapshot is None:
            await websocket.close(code=4404)
            return
        if snapshot.status in (
            RunJobStatus.COMPLETED,
            RunJobStatus.FAILED,
        ) and not runtime.stream_manager.has_page(run_id):
            await websocket.close(code=4409)
            return

        await websocket.accept()
        subscription = None
        receive_task: asyncio.Task[dict[str, object]] | None = None
        message_task: asyncio.Task[object] | None = None
        try:
            try:
                subscription = await runtime.stream_manager.subscribe(run_id)
            except Exception:
                await websocket.send_json(
                    BrowserStreamControl.unavailable().model_dump(
                        mode="json",
                        by_alias=True,
                        exclude_none=True,
                    )
                )
                await websocket.close(code=1011)
                return

            receive_task = asyncio.create_task(websocket.receive())
            while True:
                if message_task is None:
                    message_task = asyncio.create_task(anext(subscription))
                done, _ = await asyncio.wait(
                    {receive_task, message_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if receive_task in done:
                    incoming = receive_task.result()
                    receive_task = None
                    if incoming["type"] == "websocket.disconnect":
                        return
                    receive_task = asyncio.create_task(websocket.receive())
                if message_task not in done:
                    continue
                try:
                    message = message_task.result()
                except StopAsyncIteration:
                    await websocket.close(code=1000)
                    return
                finally:
                    message_task = None

                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                    continue
                await websocket.send_json(
                    message.model_dump(
                        mode="json",
                        by_alias=True,
                        exclude_none=True,
                    )
                )
                if message.type == "ended":
                    await websocket.close(code=1000)
                    return
                if message.type == "error":
                    await websocket.close(code=1011)
                    return
        except (WebSocketDisconnect, asyncio.CancelledError):
            return
        except Exception:
            try:
                await websocket.send_json(
                    BrowserStreamControl.unavailable().model_dump(
                        mode="json",
                        by_alias=True,
                        exclude_none=True,
                    )
                )
                await websocket.close(code=1011)
            except (RuntimeError, WebSocketDisconnect):
                return
        finally:
            for task in (receive_task, message_task):
                if task is not None and not task.done():
                    task.cancel()
            pending = [
                task
                for task in (receive_task, message_task)
                if task is not None
            ]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if subscription is not None:
                await subscription.close()

    return app


app = create_app()
