from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from datespot_agent.api.app import create_app
from datespot_agent.models import RunConfig, RunReport, RunStatus
from datespot_agent.reporting import JsonReportCatalog, JsonReportStore


NOW = datetime(2026, 7, 15, 1, 2, 3, tzinfo=timezone.utc)


def make_report(
    run_id: str,
    *,
    created_at: datetime = NOW,
    location: str = "성수역",
    keyword: str = "일식",
    status: RunStatus = RunStatus.COMPLETED,
) -> RunReport:
    return RunReport(
        run_id=run_id,
        status=status,
        config=RunConfig(location=location, search_keyword=keyword),
        created_at=created_at,
    )


class _EmptyCoordinator:
    def get_status(self, run_id):
        return None


class _Runtime:
    def __init__(self, catalog: JsonReportCatalog) -> None:
        self.coordinator = _EmptyCoordinator()
        self.report_catalog = catalog
        self.start = AsyncMock()
        self.stop = AsyncMock()


class ApiReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = _Runtime(JsonReportCatalog(self.root))
        self.context = TestClient(create_app(lambda: self.runtime))
        self.client = self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.temp.cleanup()

    def save(self, report: RunReport) -> None:
        JsonReportStore(self.root).save(report)

    def test_list_returns_camel_case_summaries_and_paginates(self):
        self.save(make_report("run_a"))
        self.save(
            make_report(
                "run_b",
                created_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
            )
        )

        first = self.client.get("/reports", params={"limit": 1})
        self.assertEqual(first.status_code, 200)
        payload = first.json()
        self.assertEqual(payload["items"][0]["runId"], "run_b")
        self.assertEqual(payload["items"][0]["resultCount"], 0)
        self.assertEqual(payload["items"][0]["errorCount"], 0)
        self.assertEqual(payload["items"][0]["reportUrl"], "/reports/run_b")
        self.assertIn("nextCursor", payload)
        self.assertEqual(payload["invalidReportCount"], 0)

        second = self.client.get(
            "/reports",
            params={"limit": 1, "cursor": payload["nextCursor"]},
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["items"][0]["runId"], "run_a")

    def test_list_applies_all_public_filter_names(self):
        self.save(
            make_report(
                "run_match",
                location="성수역 서울숲",
                keyword="모던 일식",
                status=RunStatus.FAILED,
            )
        )
        response = self.client.get(
            "/reports",
            params={
                "status": "failed",
                "location": "서울숲",
                "searchKeyword": "일식",
                "dateFrom": "2026-07-15",
                "dateTo": "2026-07-15",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["runId"], "run_match")

    def test_invalid_filters_return_public_invalid_filter_error(self):
        invalid_queries = (
            {"location": "   "},
            {"searchKeyword": "   "},
            {"dateFrom": "not-a-date"},
            {"dateFrom": "2026-07-16", "dateTo": "2026-07-15"},
            {"status": "running"},
        )
        for query in invalid_queries:
            with self.subTest(query=query):
                response = self.client.get("/reports", params=query)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.json()["detail"]["code"],
                    "invalid_filter",
                )

    def test_invalid_cursor_returns_public_error(self):
        response = self.client.get("/reports", params={"cursor": "bad"})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"]["code"],
            "invalid_cursor",
        )

    def test_persisted_detail_does_not_depend_on_coordinator_job(self):
        self.save(make_report("run_saved"))

        response = self.client.get("/reports/run_saved")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["runId"], "run_saved")
        self.assertEqual(response.json()["createdAt"], "2026-07-15T01:02:03Z")

    def test_missing_and_unsafe_detail_ids_return_public_errors(self):
        missing = self.client.get("/reports/run_missing")
        unsafe = self.client.get("/reports/bad%20id")

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(
            missing.json()["detail"]["code"],
            "report_not_found",
        )
        self.assertEqual(unsafe.status_code, 422)
        self.assertEqual(
            unsafe.json()["detail"]["code"],
            "invalid_run_id",
        )

    def test_corrupt_and_conflicting_detail_return_public_errors(self):
        day = self.root / "2026" / "07" / "15"
        day.mkdir(parents=True)
        (day / "run_corrupt.json").write_text("{", encoding="utf-8")

        corrupt = self.client.get("/reports/run_corrupt")

        self.assertEqual(corrupt.status_code, 500)
        self.assertEqual(
            corrupt.json()["detail"]["code"],
            "report_corrupt",
        )

        self.save(make_report("run_dup"))
        JsonReportStore(self.root).save(
            make_report(
                "run_dup",
                created_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
            )
        )
        conflict = self.client.get("/reports/run_dup")
        self.assertEqual(conflict.status_code, 500)
        self.assertEqual(
            conflict.json()["detail"]["code"],
            "report_catalog_conflict",
        )

    def test_catalog_unavailable_error_does_not_expose_internal_path(self):
        blocked_root = self.root / "blocked-secret"
        blocked_root.write_text("not a directory", encoding="utf-8")
        self.runtime.report_catalog = JsonReportCatalog(blocked_root)

        response = self.client.get("/reports")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json()["detail"]["code"],
            "report_catalog_unavailable",
        )
        self.assertNotIn(str(blocked_root), response.text)
        self.assertNotIn("not a directory", response.text)


if __name__ == "__main__":
    unittest.main()
