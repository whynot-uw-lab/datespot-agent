from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from datespot_agent.api.app import create_app
from datespot_agent.api.models import RunJobStatus, RunStatusResponse
from datespot_agent.browser.stream import BrowserStreamControl
from datespot_agent.models import RunConfig


NOW = datetime(2026, 7, 15, 1, 2, 3, tzinfo=timezone.utc)


class _Coordinator:
    def __init__(self, statuses: dict[str, RunJobStatus]) -> None:
        self.statuses = statuses

    def get_status(self, run_id: str) -> RunStatusResponse | None:
        status = self.statuses.get(run_id)
        if status is None:
            return None
        terminal = status in (RunJobStatus.COMPLETED, RunJobStatus.FAILED)
        return RunStatusResponse(
            run_id=run_id,
            status=status,
            config=RunConfig(location="성수역", search_keyword="일식"),
            created_at=NOW,
            finished_at=NOW if terminal else None,
        )


class _Subscription:
    def __init__(self, messages: list[BrowserStreamControl | bytes]) -> None:
        self.messages = iter(messages)
        self.close = AsyncMock()

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.messages)
        except StopIteration:
            raise StopAsyncIteration from None


class _BlockingSubscription:
    def __init__(self) -> None:
        self.close = AsyncMock()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.Future()


class _StreamManager:
    def __init__(self, subscription, *, pages: set[str] | None = None) -> None:
        self.subscription = subscription
        self.pages = pages or set()
        self.subscribe = AsyncMock(return_value=subscription)

    def has_page(self, run_id: str) -> bool:
        return run_id in self.pages


class _Runtime:
    def __init__(self, coordinator, stream_manager) -> None:
        self.coordinator = coordinator
        self.stream_manager = stream_manager
        self.start = AsyncMock()
        self.stop = AsyncMock()


class ApiBrowserStreamTests(unittest.TestCase):
    def test_stream_relays_controls_and_binary_jpeg_then_closes_normally(self):
        subscription = _Subscription(
            [
                BrowserStreamControl.waiting(),
                BrowserStreamControl.ready(),
                b"\xff\xd8jpeg",
                BrowserStreamControl.ended(),
            ]
        )
        manager = _StreamManager(subscription)
        runtime = _Runtime(
            _Coordinator({"run_live": RunJobStatus.RUNNING}),
            manager,
        )

        with TestClient(create_app(lambda: runtime)) as client:
            with client.websocket_connect(
                "/runs/run_live/browser-stream"
            ) as websocket:
                self.assertEqual(websocket.receive_json(), {"type": "waiting"})
                self.assertEqual(
                    websocket.receive_json(),
                    {
                        "type": "ready",
                        "format": "jpeg",
                        "maxWidth": 1280,
                        "maxHeight": 720,
                    },
                )
                self.assertEqual(websocket.receive_bytes(), b"\xff\xd8jpeg")
                self.assertEqual(websocket.receive_json(), {"type": "ended"})
                close_message = websocket.receive()

        self.assertEqual(
            close_message,
            {"type": "websocket.close", "code": 1000, "reason": ""},
        )
        subscription.close.assert_awaited_once()

    def test_unknown_run_closes_with_4404(self):
        manager = _StreamManager(_Subscription([]))
        runtime = _Runtime(_Coordinator({}), manager)

        with TestClient(create_app(lambda: runtime)) as client:
            with self.assertRaises(WebSocketDisconnect) as raised:
                with client.websocket_connect(
                    "/runs/missing/browser-stream"
                ):
                    pass

        self.assertEqual(raised.exception.code, 4404)
        manager.subscribe.assert_not_awaited()

    def test_terminal_run_without_page_closes_with_4409(self):
        manager = _StreamManager(_Subscription([]))
        runtime = _Runtime(
            _Coordinator({"run_done": RunJobStatus.COMPLETED}),
            manager,
        )

        with TestClient(create_app(lambda: runtime)) as client:
            with self.assertRaises(WebSocketDisconnect) as raised:
                with client.websocket_connect(
                    "/runs/run_done/browser-stream"
                ):
                    pass

        self.assertEqual(raised.exception.code, 4409)
        manager.subscribe.assert_not_awaited()

    def test_stream_error_control_is_public_and_closes_with_1011(self):
        subscription = _Subscription([BrowserStreamControl.unavailable()])
        manager = _StreamManager(subscription)
        runtime = _Runtime(
            _Coordinator({"run_error": RunJobStatus.RUNNING}),
            manager,
        )

        with TestClient(create_app(lambda: runtime)) as client:
            with client.websocket_connect(
                "/runs/run_error/browser-stream"
            ) as websocket:
                control = websocket.receive_json()
                close_message = websocket.receive()

        self.assertEqual(
            control,
            {
                "type": "error",
                "code": "stream_unavailable",
                "message": "브라우저 스트림을 사용할 수 없음",
            },
        )
        self.assertEqual(
            close_message,
            {"type": "websocket.close", "code": 1011, "reason": ""},
        )
        subscription.close.assert_awaited_once()

    def test_client_disconnect_closes_only_its_subscription(self):
        subscription = _BlockingSubscription()
        manager = _StreamManager(subscription)
        runtime = _Runtime(
            _Coordinator({"run_disconnect": RunJobStatus.QUEUED}),
            manager,
        )

        with TestClient(create_app(lambda: runtime)) as client:
            with client.websocket_connect(
                "/runs/run_disconnect/browser-stream"
            ) as websocket:
                websocket.close()

        subscription.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
