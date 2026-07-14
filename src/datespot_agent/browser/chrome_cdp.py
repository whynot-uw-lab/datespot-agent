"""일반 Chrome 프로세스를 실행하고 CDP endpoint를 제공함."""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.error import URLError
from urllib.request import urlopen

from datespot_agent.browser.errors import BrowserSessionError


class AsyncProcess(Protocol):
    """ChromeCdpProcess가 사용하는 asyncio process 최소 계약."""

    returncode: int | None

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


ProcessFactory = Callable[..., Awaitable[AsyncProcess]]
ReadinessProbe = Callable[[str], Awaitable[bool]]


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _read_cdp_version(endpoint_url: str) -> bool:
    try:
        with urlopen(f"{endpoint_url}/json/version", timeout=0.5) as response:
            payload = json.load(response)
    except (OSError, URLError, ValueError):
        return False
    return bool(payload.get("webSocketDebuggerUrl"))


async def _cdp_ready(endpoint_url: str) -> bool:
    return await asyncio.to_thread(_read_cdp_version, endpoint_url)


@dataclass(slots=True)
class ChromeCdpProcess:
    """실행한 Chrome 프로세스와 CDP endpoint의 소유권."""

    endpoint_url: str
    process: AsyncProcess
    shutdown_timeout: float = 5.0
    _closed: bool = field(default=False, init=False)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.process.returncode is not None:
            return
        self.process.terminate()
        try:
            await asyncio.wait_for(
                self.process.wait(),
                timeout=self.shutdown_timeout,
            )
        except TimeoutError:
            self.process.kill()
            await self.process.wait()


class ChromeCdpLauncher:
    """자동화 플래그 없이 일반 Chrome을 전용 프로필로 실행함."""

    def __init__(
        self,
        *,
        executable_path: Path,
        user_data_dir: Path,
        startup_timeout: float = 10.0,
        shutdown_timeout: float = 5.0,
        poll_interval: float = 0.1,
        process_factory: ProcessFactory = asyncio.create_subprocess_exec,
        port_factory: Callable[[], int] = _available_port,
        readiness_probe: ReadinessProbe = _cdp_ready,
    ) -> None:
        self.executable_path = executable_path
        self.user_data_dir = user_data_dir
        self.startup_timeout = startup_timeout
        self.shutdown_timeout = shutdown_timeout
        self.poll_interval = poll_interval
        self._process_factory = process_factory
        self._port_factory = port_factory
        self._readiness_probe = readiness_probe

    async def launch(self) -> ChromeCdpProcess:
        if not self.executable_path.is_file():
            raise BrowserSessionError(
                f"Chrome 실행 파일을 찾지 못함: {self.executable_path}"
            )
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        port = self._port_factory()
        if port <= 0:
            raise BrowserSessionError(f"유효하지 않은 CDP 포트: {port}")
        endpoint_url = f"http://127.0.0.1:{port}"
        try:
            process = await self._process_factory(
                str(self.executable_path),
                "--remote-debugging-address=127.0.0.1",
                f"--remote-debugging-port={port}",
                f"--user-data-dir={self.user_data_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as error:
            raise BrowserSessionError(
                f"Chrome 프로세스 시작 실패: {error}"
            ) from error

        owner = ChromeCdpProcess(
            endpoint_url=endpoint_url,
            process=process,
            shutdown_timeout=self.shutdown_timeout,
        )
        deadline = asyncio.get_running_loop().time() + self.startup_timeout
        while True:
            if process.returncode is not None:
                await owner.close()
                raise BrowserSessionError(
                    f"Chrome 프로세스 조기 종료: code={process.returncode}"
                )
            if await self._readiness_probe(endpoint_url):
                return owner
            if asyncio.get_running_loop().time() >= deadline:
                await owner.close()
                raise BrowserSessionError(
                    f"Chrome CDP 준비 시간 초과: {endpoint_url}"
                )
            await asyncio.sleep(self.poll_interval)
