from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from datespot_agent.api.coordinator import RunCoordinator
from datespot_agent.api.runtime import (
    AppRuntime,
    RuntimeConfigurationError,
    create_runtime,
)
from datespot_agent.browser import BrowserService, ChromeCdpLauncher
from datespot_agent.config import Settings
from datespot_agent.graph import GraphRunService
from datespot_agent.reporting import JsonReportStore


class _CoordinatorProbe:
    def __init__(self, stop_error: Exception | None = None) -> None:
        self.started = False
        self.stopped = False
        self.stop_error = stop_error

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True
        if self.stop_error is not None:
            raise self.stop_error


class _BrowserProbe:
    def __init__(self, close_error: Exception | None = None) -> None:
        self.closed = False
        self.close_error = close_error

    async def close_all(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class _ClientProbe:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class ApiRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_settings_parse_api_paths(self):
        settings = Settings.model_validate(
            {
                "OPENAI_API_KEY": "key",
                "DATESPOT_REPORTS_ROOT": "custom-reports",
                "DATESPOT_CHROME_EXECUTABLE_PATH": "/tmp/chrome",
                "DATESPOT_BROWSER_USER_DATA_DIR": "~/.cache/test-profile",
            }
        )

        self.assertEqual(settings.reports_root, Path("custom-reports"))
        self.assertEqual(settings.chrome_executable_path, Path("/tmp/chrome"))
        self.assertEqual(
            settings.browser_user_data_dir,
            Path("~/.cache/test-profile"),
        )

    async def test_create_runtime_rejects_empty_api_key(self):
        with patch("datespot_agent.api.runtime.AsyncOpenAI") as client_type:
            with self.assertRaisesRegex(
                RuntimeConfigurationError,
                "OPENAI_API_KEY",
            ):
                await create_runtime(Settings(OPENAI_API_KEY="   "))

        client_type.assert_not_called()

    async def test_create_runtime_rejects_missing_chrome_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_chrome = Path(directory) / "missing-chrome"
            settings = Settings(
                OPENAI_API_KEY="key",
                DATESPOT_CHROME_EXECUTABLE_PATH=missing_chrome,
            )

            with patch("datespot_agent.api.runtime.AsyncOpenAI") as client_type:
                with self.assertRaisesRegex(
                    RuntimeConfigurationError,
                    str(missing_chrome),
                ):
                    await create_runtime(settings)

        client_type.assert_not_called()

    async def test_app_runtime_starts_and_stops_all_resources(self):
        coordinator = _CoordinatorProbe()
        browser = _BrowserProbe()
        client = _ClientProbe()
        runtime = AppRuntime(coordinator, browser, client)

        await runtime.start()
        await runtime.stop()

        self.assertTrue(coordinator.started)
        self.assertTrue(coordinator.stopped)
        self.assertTrue(browser.closed)
        self.assertTrue(client.closed)

    async def test_stop_continues_cleanup_when_coordinator_stop_raises(self):
        coordinator = _CoordinatorProbe(RuntimeError("coordinator stop failed"))
        browser = _BrowserProbe()
        client = _ClientProbe()
        runtime = AppRuntime(coordinator, browser, client)

        with self.assertRaisesRegex(RuntimeError, "coordinator stop failed"):
            await runtime.stop()

        self.assertTrue(coordinator.stopped)
        self.assertTrue(browser.closed)
        self.assertTrue(client.closed)

    async def test_stop_closes_client_when_browser_cleanup_raises(self):
        coordinator = _CoordinatorProbe()
        browser = _BrowserProbe(RuntimeError("browser cleanup failed"))
        client = _ClientProbe()
        runtime = AppRuntime(coordinator, browser, client)

        with self.assertRaisesRegex(RuntimeError, "browser cleanup failed"):
            await runtime.stop()

        self.assertTrue(coordinator.stopped)
        self.assertTrue(browser.closed)
        self.assertTrue(client.closed)

    async def test_create_runtime_expands_and_wires_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chrome = root / "Chrome"
            chrome.write_text("binary", encoding="utf-8")
            settings = Settings.model_validate(
                {
                    "OPENAI_API_KEY": " key ",
                    "DATESPOT_CHROME_EXECUTABLE_PATH": "~/Chrome",
                    "DATESPOT_BROWSER_USER_DATA_DIR": "~/profile",
                    "DATESPOT_REPORTS_ROOT": "~/reports",
                }
            )

            with (
                patch.dict(os.environ, {"HOME": str(root)}),
                patch("datespot_agent.api.runtime.AsyncOpenAI") as client_type,
            ):
                runtime = await create_runtime(settings)

        client_type.assert_called_once_with(api_key="key")
        self.assertIs(runtime.openai_client, client_type.return_value)
        self.assertIsInstance(runtime.browser_service, BrowserService)
        self.assertIsInstance(runtime.coordinator, RunCoordinator)

        launcher = runtime.browser_service._cdp_launcher
        self.assertIsInstance(launcher, ChromeCdpLauncher)
        self.assertEqual(launcher.executable_path, chrome)
        self.assertEqual(launcher.user_data_dir, root / "profile")

        runner = runtime.coordinator._runner
        report_store = runtime.coordinator._report_store
        self.assertIsInstance(runner, GraphRunService)
        self.assertIs(runner._browser_service, runtime.browser_service)
        self.assertIsInstance(report_store, JsonReportStore)
        self.assertEqual(report_store.root, root / "reports")
        self.assertIs(runner._photo_agent._client, runtime.openai_client)
        self.assertIs(runner._review_agent._client, runtime.openai_client)
        self.assertEqual(runner._photo_agent._model, settings.model)
        self.assertEqual(runner._review_agent._model, settings.model)

    async def test_create_runtime_closes_client_when_assembly_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            chrome = Path(directory) / "Chrome"
            chrome.write_text("binary", encoding="utf-8")
            settings = Settings(
                OPENAI_API_KEY="key",
                DATESPOT_CHROME_EXECUTABLE_PATH=chrome,
            )
            client = AsyncMock()

            with (
                patch(
                    "datespot_agent.api.runtime.AsyncOpenAI",
                    return_value=client,
                ),
                patch(
                    "datespot_agent.api.runtime.GraphRunService",
                    side_effect=RuntimeError("assembly failed"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "assembly failed"):
                    await create_runtime(settings)

        client.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
