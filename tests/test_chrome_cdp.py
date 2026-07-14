from __future__ import annotations

import asyncio
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from datespot_agent.browser.chrome_cdp import (
    ChromeCdpLauncher,
    ChromeCdpProcess,
)
from datespot_agent.browser.errors import BrowserSessionError


class FakeProcess:
    def __init__(self, *, returncode: int | None = None) -> None:
        self.returncode = returncode
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0
        self._wait_never_finishes = False

    def terminate(self) -> None:
        self.terminate_calls += 1
        if not self._wait_never_finishes:
            self.returncode = 0

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9

    async def wait(self) -> int:
        self.wait_calls += 1
        if self._wait_never_finishes and self.kill_calls == 0:
            await asyncio.Future()
        return self.returncode or 0


class ChromeCdpProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_terminates_owned_process(self):
        process = FakeProcess()
        owner = ChromeCdpProcess(
            endpoint_url="http://127.0.0.1:9222",
            process=process,
            shutdown_timeout=0.01,
        )

        await owner.close()

        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.kill_calls, 0)
        self.assertEqual(process.wait_calls, 1)

    async def test_close_kills_process_after_shutdown_timeout(self):
        process = FakeProcess()
        process._wait_never_finishes = True
        owner = ChromeCdpProcess(
            endpoint_url="http://127.0.0.1:9222",
            process=process,
            shutdown_timeout=0.001,
        )

        await owner.close()

        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.kill_calls, 1)
        self.assertEqual(process.wait_calls, 2)


class ChromeCdpLauncherTests(unittest.IsolatedAsyncioTestCase):
    async def test_launch_uses_nonzero_port_profile_and_waits_for_cdp(self):
        process = FakeProcess()
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        probes: list[str] = []

        async def process_factory(*args, **kwargs):
            calls.append((args, kwargs))
            return process

        async def readiness_probe(endpoint_url: str) -> bool:
            probes.append(endpoint_url)
            return len(probes) == 2

        with TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "Google Chrome"
            executable.write_bytes(b"")
            profile = Path(temp_dir) / "profile"
            launcher = ChromeCdpLauncher(
                executable_path=executable,
                user_data_dir=profile,
                startup_timeout=0.1,
                poll_interval=0,
                process_factory=process_factory,
                port_factory=lambda: 43891,
                readiness_probe=readiness_probe,
            )

            launched = await launcher.launch()
            self.assertTrue(profile.is_dir())

        self.assertEqual(launched.endpoint_url, "http://127.0.0.1:43891")
        self.assertEqual(probes, [launched.endpoint_url, launched.endpoint_url])
        args, kwargs = calls[0]
        self.assertEqual(args[0], str(executable))
        self.assertIn("--remote-debugging-address=127.0.0.1", args)
        self.assertIn("--remote-debugging-port=43891", args)
        self.assertIn(f"--user-data-dir={profile}", args)
        self.assertNotIn("--enable-automation", args)
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)

    async def test_launch_rejects_missing_chrome_executable(self):
        with TemporaryDirectory() as temp_dir:
            launcher = ChromeCdpLauncher(
                executable_path=Path(temp_dir) / "missing",
                user_data_dir=Path(temp_dir) / "profile",
            )

            with self.assertRaisesRegex(
                BrowserSessionError,
                "Chrome 실행 파일",
            ):
                await launcher.launch()

    async def test_launch_cleans_up_when_chrome_exits_early(self):
        process = FakeProcess(returncode=21)

        async def process_factory(*_args, **_kwargs):
            return process

        with TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "Chrome"
            executable.write_bytes(b"")
            launcher = ChromeCdpLauncher(
                executable_path=executable,
                user_data_dir=Path(temp_dir) / "profile",
                startup_timeout=0.1,
                poll_interval=0,
                process_factory=process_factory,
                port_factory=lambda: 43892,
                readiness_probe=lambda _endpoint: asyncio.sleep(
                    0,
                    result=False,
                ),
            )

            with self.assertRaisesRegex(
                BrowserSessionError,
                "조기 종료",
            ):
                await launcher.launch()

        self.assertEqual(process.terminate_calls, 0)

    async def test_launch_cleans_up_after_cdp_timeout(self):
        process = FakeProcess()

        async def process_factory(*_args, **_kwargs):
            return process

        async def never_ready(_endpoint: str) -> bool:
            return False

        with TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "Chrome"
            executable.write_bytes(b"")
            launcher = ChromeCdpLauncher(
                executable_path=executable,
                user_data_dir=Path(temp_dir) / "profile",
                startup_timeout=0,
                poll_interval=0,
                process_factory=process_factory,
                port_factory=lambda: 43893,
                readiness_probe=never_ready,
            )

            with self.assertRaisesRegex(
                BrowserSessionError,
                "준비 시간 초과",
            ):
                await launcher.launch()

        self.assertEqual(process.terminate_calls, 1)


if __name__ == "__main__":
    unittest.main()
