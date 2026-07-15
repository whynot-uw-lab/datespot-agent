from __future__ import annotations

import asyncio
import base64
import unittest
from inspect import isawaitable
from unittest.mock import AsyncMock

from datespot_agent.browser.stream import (
    SCREENCAST_OPTIONS,
    BrowserStreamControl,
    CdpStreamManager,
)


class _FakeCdpSession:
    def __init__(self) -> None:
        self.send = AsyncMock()
        self.detach = AsyncMock()
        self.listeners: dict[str, object] = {}

    def on(self, event_name: str, callback) -> None:
        self.listeners[event_name] = callback

    async def emit_frame(self, data: str, session_id: int) -> None:
        result = self.listeners["Page.screencastFrame"](
            {"data": data, "sessionId": session_id}
        )
        if isawaitable(result):
            await result


class _FakeContext:
    def __init__(self, session: _FakeCdpSession) -> None:
        self.session = session
        self.new_cdp_session = AsyncMock(return_value=session)


class _FakePage:
    def __init__(self, session: _FakeCdpSession) -> None:
        self.context = _FakeContext(session)


class CdpStreamManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_viewer_waits_then_page_attach_starts_stream(self):
        session = _FakeCdpSession()
        page = _FakePage(session)
        manager = CdpStreamManager()

        subscription = await manager.subscribe("run_one")
        self.assertEqual(
            await subscription.next_message(),
            BrowserStreamControl.waiting(),
        )

        await manager.attach_page("run_one", page)

        self.assertEqual(
            await subscription.next_message(),
            BrowserStreamControl.ready(),
        )
        page.context.new_cdp_session.assert_awaited_once_with(page)
        session.send.assert_any_await(
            "Page.startScreencast",
            SCREENCAST_OPTIONS,
        )

    async def test_first_viewer_starts_and_last_viewer_stops_screencast(self):
        session = _FakeCdpSession()
        page = _FakePage(session)
        manager = CdpStreamManager()
        await manager.attach_page("run_one", page)

        first = await manager.subscribe("run_one")
        second = await manager.subscribe("run_one")
        self.assertEqual(await first.next_message(), BrowserStreamControl.ready())
        self.assertEqual(await second.next_message(), BrowserStreamControl.ready())
        self.assertEqual(
            [call.args[0] for call in session.send.await_args_list].count(
                "Page.startScreencast"
            ),
            1,
        )

        await first.close()
        self.assertNotIn(
            "Page.stopScreencast",
            [call.args[0] for call in session.send.await_args_list],
        )
        await second.close()

        session.send.assert_any_await("Page.stopScreencast")
        session.detach.assert_awaited_once()

    async def test_latest_frame_replaces_old_frame_and_every_frame_is_acked(self):
        session = _FakeCdpSession()
        manager = CdpStreamManager()
        await manager.attach_page("run_one", _FakePage(session))
        subscription = await manager.subscribe("run_one")
        await subscription.next_message()

        await session.emit_frame(base64.b64encode(b"old").decode(), 1)
        await session.emit_frame(base64.b64encode(b"new").decode(), 2)

        self.assertEqual(await subscription.next_message(), b"new")
        session.send.assert_any_await(
            "Page.screencastFrameAck",
            {"sessionId": 1},
        )
        session.send.assert_any_await(
            "Page.screencastFrameAck",
            {"sessionId": 2},
        )

    async def test_invalid_frame_is_acked_and_not_delivered(self):
        session = _FakeCdpSession()
        manager = CdpStreamManager()
        await manager.attach_page("run_one", _FakePage(session))
        subscription = await manager.subscribe("run_one")
        await subscription.next_message()

        await session.emit_frame("%%%not-base64%%%", 7)

        session.send.assert_any_await(
            "Page.screencastFrameAck",
            {"sessionId": 7},
        )
        control = await asyncio.wait_for(subscription.next_message(), 0.1)
        self.assertEqual(control, BrowserStreamControl.unavailable())
        self.assertFalse(subscription.has_pending_frame)

    async def test_frame_ack_failure_ends_stream_without_frame_delivery(self):
        session = _FakeCdpSession()

        async def send(method, *_args):
            if method == "Page.screencastFrameAck":
                raise RuntimeError("ack unavailable")

        session.send.side_effect = send
        manager = CdpStreamManager()
        await manager.attach_page("run_one", _FakePage(session))
        subscription = await manager.subscribe("run_one")
        await subscription.next_message()

        await session.emit_frame(base64.b64encode(b"frame").decode(), 8)

        self.assertEqual(
            await subscription.next_message(),
            BrowserStreamControl.unavailable(),
        )
        self.assertFalse(subscription.has_pending_frame)

    async def test_detach_stops_before_detaching_and_ends_all_viewers(self):
        calls: list[str] = []
        session = _FakeCdpSession()
        session.send.side_effect = lambda method, *_args: calls.append(method)
        session.detach.side_effect = lambda: calls.append("detach")
        manager = CdpStreamManager()
        await manager.attach_page("run_one", _FakePage(session))
        first = await manager.subscribe("run_one")
        second = await manager.subscribe("run_one")
        await first.next_message()
        await second.next_message()

        await manager.detach_page("run_one")

        self.assertEqual(await first.next_message(), BrowserStreamControl.ended())
        self.assertEqual(await second.next_message(), BrowserStreamControl.ended())
        self.assertLess(calls.index("Page.stopScreencast"), calls.index("detach"))
        with self.assertRaises(StopAsyncIteration):
            await first.next_message()

    async def test_start_failure_emits_public_error_and_detaches_session(self):
        session = _FakeCdpSession()
        session.send.side_effect = RuntimeError("secret cdp details")
        manager = CdpStreamManager()
        await manager.attach_page("run_one", _FakePage(session))

        subscription = await manager.subscribe("run_one")
        message = await subscription.next_message()

        self.assertEqual(message.type, "error")
        self.assertEqual(message.code, "stream_unavailable")
        self.assertNotIn("secret", message.message or "")
        session.detach.assert_awaited_once()

    async def test_cancelled_start_detaches_created_cdp_session(self):
        start_entered = asyncio.Event()
        session = _FakeCdpSession()

        async def send(method, *_args):
            if method == "Page.startScreencast":
                start_entered.set()
                await asyncio.Future()

        session.send.side_effect = send
        manager = CdpStreamManager()
        subscription = await manager.subscribe("run_cancel")
        await subscription.next_message()
        attach = asyncio.create_task(
            manager.attach_page("run_cancel", _FakePage(session))
        )
        await start_entered.wait()

        attach.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await attach

        session.detach.assert_awaited_once()

    async def test_close_ends_waiting_viewer_and_rejects_new_subscribers(self):
        manager = CdpStreamManager()
        subscription = await manager.subscribe("run_waiting")
        await subscription.next_message()

        await manager.close()

        self.assertEqual(
            await subscription.next_message(),
            BrowserStreamControl.ended(),
        )
        with self.assertRaises(RuntimeError):
            await manager.subscribe("run_late")


if __name__ == "__main__":
    unittest.main()
