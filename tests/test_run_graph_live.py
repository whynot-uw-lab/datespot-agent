from __future__ import annotations

import asyncio
import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from datespot_agent.models import RunConfig, RunReport, RunStatus
from datespot_agent.reporting import ReportStorageError

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tests" / "run_graph_live.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "run_graph_live",
        MODULE_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("graph live module spec 생성 실패")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_report(*, status: RunStatus = RunStatus.COMPLETED) -> RunReport:
    return RunReport(
        run_id="run_20260715_sample",
        status=status,
        config=RunConfig(
            location="신사역",
            search_keyword="일식",
            max_places=1,
        ),
        created_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )


class FakeReportStore:
    def __init__(self, result: Path | Exception) -> None:
        self.result = result
        self.saved_reports: list[RunReport] = []

    def save(self, report: RunReport) -> Path:
        self.saved_reports.append(report)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class GraphLiveBrowserConfigTests(unittest.TestCase):
    def test_resolve_live_api_key_prefers_project_dotenv(self):
        module = load_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "OPENAI_API_KEY=dotenv-key\n",
                encoding="utf-8",
            )

            api_key = module.resolve_live_api_key(
                "inherited-shell-key",
                env_path=env_path,
            )

        self.assertEqual(api_key, "dotenv-key")

    def test_build_browser_service_uses_external_chrome_cdp_profile(self):
        module = load_module()

        service = module.build_browser_service(default_headless=True)

        self.assertFalse(service._headless)
        self.assertIsNone(service._user_data_dir)
        self.assertIsNotNone(service._cdp_launcher)
        self.assertEqual(
            service._cdp_launcher.executable_path,
            Path(
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            ),
        )
        self.assertEqual(
            service._cdp_launcher.user_data_dir,
            Path.home()
            / ".cache"
            / "datespot-agent"
            / "chrome-profile",
        )


class GraphLiveReportStorageTests(unittest.TestCase):
    def test_finalize_report_prints_path_and_returns_completed_exit_code(self):
        module = load_module()
        report = make_report()
        store = FakeReportStore(Path("reports/2026/07/15/report.json"))
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = module.finalize_report(report, store)

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            output.getvalue().strip(),
            "reports/2026/07/15/report.json",
        )
        self.assertEqual(store.saved_reports, [report])

    def test_finalize_report_returns_failed_exit_code_after_successful_save(self):
        module = load_module()
        report = make_report(status=RunStatus.FAILED)
        store = FakeReportStore(Path("reports/failed.json"))

        with redirect_stdout(io.StringIO()):
            exit_code = module.finalize_report(report, store)

        self.assertEqual(exit_code, 2)

    def test_finalize_report_returns_storage_exit_code_without_mutating_status(self):
        module = load_module()
        report = make_report()
        error = ReportStorageError("저장 실패", run_id=report.run_id)
        output = io.StringIO()

        with redirect_stderr(output):
            exit_code = module.finalize_report(
                report,
                FakeReportStore(error),
            )

        self.assertEqual(exit_code, 3)
        self.assertEqual(report.status, RunStatus.COMPLETED)
        self.assertIn("리포트 저장 실패", output.getvalue())

    def test_run_saves_graph_report_with_injected_store(self):
        module = load_module()
        report = make_report()
        store = FakeReportStore(Path("reports/report.json"))

        class FakeRunner:
            async def run(self, config):
                return report

        settings = SimpleNamespace(
            model="test-model",
            openai_api_key="test-key",
            headless=True,
        )
        with (
            patch.object(module, "get_settings", return_value=settings),
            patch.object(module, "AsyncOpenAI", return_value=object()),
            patch.object(module, "GraphRunService", return_value=FakeRunner()),
            patch.object(module, "build_browser_service", return_value=object()),
            patch.object(module, "PhotoAnalysisAgent", return_value=object()),
            patch.object(module, "ReviewAnalysisAgent", return_value=object()),
            patch.object(module, "PlaceScoringService", return_value=object()),
            redirect_stdout(io.StringIO()),
        ):
            exit_code = asyncio.run(module.run(report_store=store))

        self.assertEqual(exit_code, 0)
        self.assertEqual(store.saved_reports, [report])


if __name__ == "__main__":
    unittest.main()
