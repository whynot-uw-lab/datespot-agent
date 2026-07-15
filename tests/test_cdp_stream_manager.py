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
        self.removed_listeners: list[tuple[str, object]] = []

    def on(self, event_name: str, callback) -> None:
        self.listeners[event_name] = callback

    def remove_listener(self, event_name: str, callback) -> None:
        self.removed_listeners.append((event_name, callback))
        if self.listeners.get(event_name) is callback:
            self.listeners.pop(event_name)

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
    async def test_clean_detaches_keep_only_bounded_tombstones(self):
        manager = CdpStreamManager(tombstone_capacity=3)

        for index in range(10):
            await manager.detach_page(f"run_{index}")

        self.assertEqual(len(manager._states), 0)
        self.assertEqual(len(manager._tombstones), 3)
        self.assertEqual(
            tuple(manager._tombstones),
            ("run_7", "run_8", "run_9"),
        )
        recent = await manager.subscribe("run_9")
        self.assertEqual(
            await recent.next_message(),
            BrowserStreamControl.ended(),
        )

        default_manager = CdpStreamManager()
        for index in range(105):
            await default_manager.detach_page(f"default_{index}")
        self.assertEqual(len(default_manager._tombstones), 100)

    async def test_repeated_attach_detach_releases_heavy_run_states(self):
        manager = CdpStreamManager(tombstone_capacity=2)

        for index in range(5):
            run_id = f"run_cycle_{index}"
            await manager.attach_page(run_id, _FakePage(_FakeCdpSession()))
            await manager.detach_page(run_id)

        self.assertEqual(len(manager._states), 0)
        self.assertEqual(
            tuple(manager._tombstones),
            ("run_cycle_3", "run_cycle_4"),
        )

    async def test_close_releases_waiting_states_with_bounded_tombstones(self):
        manager = CdpStreamManager(tombstone_capacity=2)
        viewers = []
        for index in range(4):
            viewer = await manager.subscribe(f"run_wait_{index}")
            await viewer.next_message()
            viewers.append(viewer)

        await manager.close()

        self.assertEqual(len(manager._states), 0)
        self.assertEqual(len(manager._tombstones), 2)
        for viewer in viewers:
            self.assertEqual(
                await viewer.next_message(),
                BrowserStreamControl.ended(),
            )

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

    async def test_frame_is_not_visible_until_ack_completes(self):
        ack_started = asyncio.Event()
        release_ack = asyncio.Event()
        session = _FakeCdpSession()

        async def send(method, *_args):
            if method == "Page.screencastFrameAck":
                ack_started.set()
                await release_ack.wait()

        session.send.side_effect = send
        manager = CdpStreamManager()
        await manager.attach_page("run_one", _FakePage(session))
        subscription = await manager.subscribe("run_one")
        await subscription.next_message()

        emitting = asyncio.create_task(
            session.emit_frame(base64.b64encode(b"frame").decode(), 3)
        )
        await ack_started.wait()

        self.assertFalse(subscription.has_pending_frame)
        release_ack.set()
        await emitting
        self.assertEqual(await subscription.next_message(), b"frame")

    async def test_frame_is_discarded_if_session_detaches_while_ack_pending(self):
        ack_started = asyncio.Event()
        release_ack = asyncio.Event()
        session = _FakeCdpSession()

        async def send(method, *_args):
            if method == "Page.screencastFrameAck":
                ack_started.set()
                await release_ack.wait()

        session.send.side_effect = send
        manager = CdpStreamManager()
        await manager.attach_page("run_one", _FakePage(session))
        subscription = await manager.subscribe("run_one")
        await subscription.next_message()
        emitting = asyncio.create_task(
            session.emit_frame(base64.b64encode(b"late").decode(), 4)
        )
        await ack_started.wait()

        await manager.detach_page("run_one")
        release_ack.set()
        await emitting

        self.assertEqual(
            await subscription.next_message(),
            BrowserStreamControl.ended(),
        )
        self.assertFalse(subscription.has_pending_frame)

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

    async def test_detach_removes_frame_listener_before_stop_and_session_detach(self):
        calls: list[str] = []
        session = _FakeCdpSession()

        def remove_listener(event_name, callback):
            calls.append(f"remove:{event_name}")
            session.listeners.pop(event_name, None)

        async def send(method, *_args):
            calls.append(method)

        async def detach():
            calls.append("detach")

        session.remove_listener = remove_listener
        session.send.side_effect = send
        session.detach.side_effect = detach
        manager = CdpStreamManager()
        await manager.attach_page("run_one", _FakePage(session))
        viewer = await manager.subscribe("run_one")
        await viewer.next_message()

        await manager.detach_page("run_one")

        self.assertLess(
            calls.index("remove:Page.screencastFrame"),
            calls.index("Page.stopScreencast"),
        )
        self.assertLess(
            calls.index("remove:Page.screencastFrame"),
            calls.index("detach"),
        )

    async def test_removed_listener_cannot_schedule_late_frame_task(self):
        session = _FakeCdpSession()
        manager = CdpStreamManager()
        await manager.attach_page("run_late_listener", _FakePage(session))
        viewer = await manager.subscribe("run_late_listener")
        await viewer.next_message()
        listener = session.listeners["Page.screencastFrame"]

        await manager.detach_page("run_late_listener")
        late_task = listener(
            {
                "data": base64.b64encode(b"late").decode(),
                "sessionId": 99,
            }
        )

        self.assertIsNone(late_task)
        self.assertNotIn(
            "Page.screencastFrameAck",
            [call.args[0] for call in session.send.await_args_list],
        )

    async def test_detach_retries_session_cleanup_after_detach_failure(self):
        session = _FakeCdpSession()
        session.detach.side_effect = [RuntimeError("busy"), None]
        manager = CdpStreamManager()
        await manager.attach_page("run_retry", _FakePage(session))
        viewer = await manager.subscribe("run_retry")
        await viewer.next_message()

        await manager.detach_page("run_retry")
        await manager.detach_page("run_retry")

        self.assertEqual(session.detach.await_count, 2)
        self.assertEqual(
            await viewer.next_message(),
            BrowserStreamControl.ended(),
        )

    async def test_cancelled_detach_finishes_cleanup_and_ends_viewers(self):
        stop_started = asyncio.Event()
        release_stop = asyncio.Event()
        session = _FakeCdpSession()

        async def send(method, *_args):
            if method == "Page.stopScreencast":
                stop_started.set()
                await release_stop.wait()

        session.send.side_effect = send
        manager = CdpStreamManager()
        await manager.attach_page("run_cancel_detach", _FakePage(session))
        viewer = await manager.subscribe("run_cancel_detach")
        await viewer.next_message()
        detaching = asyncio.create_task(
            manager.detach_page("run_cancel_detach")
        )
        await stop_started.wait()

        detaching.cancel()
        release_stop.set()
        with self.assertRaises(asyncio.CancelledError):
            await detaching

        session.detach.assert_awaited_once()
        self.assertEqual(
            await asyncio.wait_for(viewer.next_message(), 0.1),
            BrowserStreamControl.ended(),
        )

    async def test_detach_cancelled_while_waiting_for_lock_still_completes(self):
        start_entered = asyncio.Event()
        release_start = asyncio.Event()
        session = _FakeCdpSession()

        async def send(method, *_args):
            if method == "Page.startScreencast":
                start_entered.set()
                await release_start.wait()

        session.send.side_effect = send
        manager = CdpStreamManager()
        viewer = await manager.subscribe("run_lock_cancel")
        await viewer.next_message()
        attaching = asyncio.create_task(
            manager.attach_page("run_lock_cancel", _FakePage(session))
        )
        await start_entered.wait()
        detaching = asyncio.create_task(
            manager.detach_page("run_lock_cancel")
        )
        await asyncio.sleep(0)

        detaching.cancel()
        release_start.set()
        await attaching
        with self.assertRaises(asyncio.CancelledError):
            await detaching

        controls = [await viewer.next_message(), await viewer.next_message()]
        self.assertEqual(controls[-1], BrowserStreamControl.ended())
        session.detach.assert_awaited_once()

    async def test_post_detach_subscription_ends_and_page_cannot_resurrect(self):
        manager = CdpStreamManager()

        await manager.detach_page("run_done")
        await manager.attach_page("run_done", _FakePage(_FakeCdpSession()))
        late = await manager.subscribe("run_done")

        self.assertEqual(
            await asyncio.wait_for(late.next_message(), 0.1),
            BrowserStreamControl.ended(),
        )
        self.assertFalse(manager.has_page("run_done"))

    async def test_subscription_after_attached_page_detach_ends_immediately(self):
        manager = CdpStreamManager()
        await manager.attach_page("run_finished", _FakePage(_FakeCdpSession()))

        await manager.detach_page("run_finished")
        late = await manager.subscribe("run_finished")

        self.assertEqual(
            await asyncio.wait_for(late.next_message(), 0.1),
            BrowserStreamControl.ended(),
        )

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

    async def test_cancelled_close_completes_cleanup_and_can_be_retried(self):
        stop_started = asyncio.Event()
        release_stop = asyncio.Event()
        session = _FakeCdpSession()

        async def send(method, *_args):
            if method == "Page.stopScreencast":
                stop_started.set()
                await release_stop.wait()

        session.send.side_effect = send
        manager = CdpStreamManager()
        await manager.attach_page("run_close", _FakePage(session))
        viewer = await manager.subscribe("run_close")
        await viewer.next_message()
        closing = asyncio.create_task(manager.close())
        await stop_started.wait()

        closing.cancel()
        release_stop.set()
        with self.assertRaises(asyncio.CancelledError):
            await closing

        session.detach.assert_awaited_once()
        self.assertEqual(
            await asyncio.wait_for(viewer.next_message(), 0.1),
            BrowserStreamControl.ended(),
        )
        await manager.close()

    async def test_close_retries_transient_detach_failure_on_next_call(self):
        session = _FakeCdpSession()
        session.detach.side_effect = [RuntimeError("busy"), None]
        manager = CdpStreamManager()
        await manager.attach_page("run_close_retry", _FakePage(session))
        viewer = await manager.subscribe("run_close_retry")
        await viewer.next_message()

        await manager.close()
        await manager.close()

        self.assertEqual(session.detach.await_count, 2)
        self.assertEqual(
            await viewer.next_message(),
            BrowserStreamControl.ended(),
        )


if __name__ == "__main__":
    unittest.main()
