from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from datespot_agent.browser.pacing import InteractionPacer, LiveSmokeGuard


class FakeTime:
    def __init__(self) -> None:
        self.monotonic_value = 100.0
        self.wall_value = 1_000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.monotonic_value

    def wall(self) -> float:
        return self.wall_value

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.monotonic_value += seconds
        self.wall_value += seconds


class InteractionPacerTests(unittest.IsolatedAsyncioTestCase):
    async def test_actions_are_serialized_with_three_second_minimum(self):
        fake = FakeTime()
        pacer = InteractionPacer(clock=fake.monotonic, sleep=fake.sleep)
        starts: list[float] = []

        async def action() -> None:
            starts.append(fake.monotonic())

        await asyncio.gather(pacer.run(action), pacer.run(action))

        self.assertEqual(starts, [100.0, 103.0])
        self.assertEqual(fake.sleeps, [3.0])

    async def test_retry_wait_is_five_seconds(self):
        fake = FakeTime()

        await InteractionPacer(
            clock=fake.monotonic,
            sleep=fake.sleep,
        ).wait_before_retry()

        self.assertEqual(fake.sleeps, [5.0])

    async def test_live_smoke_guard_enforces_thirty_second_cooldown(self):
        fake = FakeTime()
        with tempfile.TemporaryDirectory() as directory:
            stamp = Path(directory) / "last-finished"
            stamp.write_text("990.0", encoding="utf-8")
            guard = LiveSmokeGuard(
                stamp_path=stamp,
                wall_clock=fake.wall,
                sleep=fake.sleep,
            )

            async with guard:
                pass

        self.assertEqual(fake.sleeps, [20.0])

    async def test_live_smoke_guard_rejects_parallel_process(self):
        fake = FakeTime()
        with tempfile.TemporaryDirectory() as directory:
            stamp = Path(directory) / "last-finished"
            first = LiveSmokeGuard(
                stamp_path=stamp,
                wall_clock=fake.wall,
                sleep=fake.sleep,
            )
            second = LiveSmokeGuard(
                stamp_path=stamp,
                wall_clock=fake.wall,
                sleep=fake.sleep,
            )

            async with first:
                with self.assertRaises(RuntimeError):
                    await second.__aenter__()


if __name__ == "__main__":
    unittest.main()
