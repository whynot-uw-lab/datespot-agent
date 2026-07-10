"""1-5 CDP 스트리밍 최소 검증 PoC.

실행: uv run python poc/1-5-cdp-streaming/stream_browser.py

Playwright Chromium 화면을 CDP screencast frame으로 받아 WebSocket viewer에 중계하고
프레임 수/FPS를 JSON으로 저장한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import websockets
from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = Path(__file__).parent / "output"
DEFAULT_OUTPUT = OUTPUT_DIR / "cdp_stream_result.json"
DEFAULT_VIEWER = Path(__file__).parent / "viewer.html"
DEFAULT_TARGET_URL = "https://map.naver.com"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_DURATION_SECONDS = 5.0
DEFAULT_MIN_FRAMES = 30


@dataclass
class StreamStats:
    started_at: float
    frames_received: int = 0
    frames_broadcast: int = 0
    finished_at: float | None = None

    def record_received(self) -> None:
        self.frames_received += 1

    def record_broadcast(self, count: int) -> None:
        self.frames_broadcast += count

    def duration_seconds(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return max(0.0, end - self.started_at)

    def average_fps(self) -> float:
        duration = self.duration_seconds()
        if duration <= 0:
            return 0.0
        return round(self.frames_received / duration, 2)


def build_frame_message(frame: str, session_id: int, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "frame",
        "sessionId": session_id,
        "data": frame,
        "metadata": metadata,
    }


def build_result(
    *,
    target_url: str,
    viewer_url: str,
    websocket_url: str,
    stats: StreamStats,
    errors: list[str],
) -> dict[str, Any]:
    return {
        "ok": False,
        "ranAt": datetime.now(timezone.utc).isoformat(),
        "targetUrl": target_url,
        "viewerUrl": viewer_url,
        "websocketUrl": websocket_url,
        "framesReceived": stats.frames_received,
        "framesBroadcast": stats.frames_broadcast,
        "durationSeconds": round(stats.duration_seconds(), 2),
        "averageFps": stats.average_fps(),
        "errors": errors,
    }


def validate_result(result: dict[str, Any], min_frames: int) -> dict[str, Any]:
    errors = result.setdefault("errors", [])
    if result.get("framesReceived", 0) < min_frames:
        errors.append(f"threshold: framesReceived < {min_frames}")
    if result.get("framesBroadcast", 0) < min_frames:
        errors.append(f"threshold: framesBroadcast < {min_frames}")
    result["ok"] = not errors
    return result


async def broadcast_frame(clients: set[Any], message: dict[str, Any]) -> int:
    if not clients:
        return 0

    payload = json.dumps(message, ensure_ascii=False)
    results = await asyncio.gather(
        *(client.send(payload) for client in list(clients)),
        return_exceptions=True,
    )
    return sum(1 for result in results if not isinstance(result, Exception))


async def auto_viewer_client(websocket_url: str, stop_event: asyncio.Event) -> None:
    async with websockets.connect(websocket_url) as websocket:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(websocket.recv(), timeout=0.5)
            except TimeoutError:
                continue


def save_result(result: dict[str, Any], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def parse_bool(value: str) -> bool:
    if value.lower() in {"1", "true", "yes", "y"}:
        return True
    if value.lower() in {"0", "false", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid bool: {value}")


async def run_stream(args: argparse.Namespace) -> dict[str, Any]:
    stats = StreamStats(started_at=time.monotonic())
    errors: list[str] = []
    clients: set[Any] = set()
    frame_tasks: set[asyncio.Task] = set()
    stop_event = asyncio.Event()
    client_connected = asyncio.Event()
    websocket_url = f"ws://{args.host}:{args.port}"
    viewer_url = DEFAULT_VIEWER.resolve().as_uri() + f"?ws={websocket_url}"

    async def websocket_handler(websocket: Any) -> None:
        clients.add(websocket)
        client_connected.set()
        try:
            await websocket.wait_closed()
        finally:
            clients.discard(websocket)

    try:
        server = await websockets.serve(websocket_handler, args.host, args.port)
    except Exception as e:  # noqa: BLE001
        errors.append(f"websocket: {type(e).__name__}: {e}")
        stats.finished_at = time.monotonic()
        return validate_result(
            build_result(
                target_url=args.url,
                viewer_url=viewer_url,
                websocket_url=websocket_url,
                stats=stats,
                errors=errors,
            ),
            args.min_frames,
        )

    auto_client_task: asyncio.Task | None = None
    try:
        async with server:
            if args.auto_client:
                auto_client_task = asyncio.create_task(auto_viewer_client(websocket_url, stop_event))
                try:
                    await asyncio.wait_for(client_connected.wait(), timeout=2)
                except TimeoutError:
                    errors.append("websocket: auto client did not connect within 2s")

            try:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=args.headless)
                    page = await browser.new_page(viewport={"width": 1280, "height": 720})
                    await page.goto(args.url, wait_until="domcontentloaded", timeout=30_000)
                    session = await page.context.new_cdp_session(page)

                    async def process_frame(event: dict[str, Any]) -> None:
                        stats.record_received()
                        session_id = int(event.get("sessionId", 0))
                        message = build_frame_message(
                            frame=str(event.get("data", "")),
                            session_id=session_id,
                            metadata=event.get("metadata") or {},
                        )
                        sent = await broadcast_frame(clients, message)
                        stats.record_broadcast(sent)
                        if session_id:
                            await session.send("Page.screencastFrameAck", {"sessionId": session_id})

                    def on_frame(event: dict[str, Any]) -> None:
                        task = asyncio.create_task(process_frame(event))
                        frame_tasks.add(task)
                        task.add_done_callback(frame_tasks.discard)

                    session.on("Page.screencastFrame", on_frame)
                    await session.send(
                        "Page.startScreencast",
                        {
                            "format": "jpeg",
                            "quality": 70,
                            "maxWidth": 1280,
                            "maxHeight": 720,
                            "everyNthFrame": 1,
                        },
                    )
                    await asyncio.sleep(args.duration)
                    await session.send("Page.stopScreencast")
                    if frame_tasks:
                        await asyncio.gather(*frame_tasks, return_exceptions=True)
                    await browser.close()
            except Exception as e:  # noqa: BLE001
                errors.append(f"playwright: {type(e).__name__}: {e}")
    finally:
        stats.finished_at = time.monotonic()
        stop_event.set()
        if auto_client_task:
            auto_client_task.cancel()
            await asyncio.gather(auto_client_task, return_exceptions=True)

    return validate_result(
        build_result(
            target_url=args.url,
            viewer_url=viewer_url,
            websocket_url=websocket_url,
            stats=stats,
            errors=errors,
        ),
        args.min_frames,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="1-5 CDP 스트리밍 최소 검증 PoC")
    parser.add_argument("--url", default=DEFAULT_TARGET_URL)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION_SECONDS)
    parser.add_argument("--min-frames", type=int, default=DEFAULT_MIN_FRAMES)
    parser.add_argument("--headless", type=parse_bool, default=True)
    parser.add_argument("--auto-client", type=parse_bool, default=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = asyncio.run(run_stream(args))
    out = save_result(result, args.output)

    status = "성공" if result["ok"] else "실패"
    print(f"=== 1-5 CDP 스트리밍 PoC {status} ===")
    print(f"결과: {out}")
    print(f"대상 URL: {result['targetUrl']}")
    print(f"Viewer: {result['viewerUrl']}")
    print(f"WebSocket: {result['websocketUrl']}")
    print(f"수신 프레임: {result['framesReceived']}")
    print(f"전송 프레임: {result['framesBroadcast']}")
    print(f"평균 FPS: {result['averageFps']}")
    if result["errors"]:
        print("오류:")
        for error in result["errors"]:
            print(f"- {error}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
