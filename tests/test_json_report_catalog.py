from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from datespot_agent.api.models import ReportQuery
from datespot_agent.models import (
    PlaceResult,
    PlaceResultStatus,
    RunConfig,
    RunReport,
    RunStatus,
)
from datespot_agent.reporting.catalog import (
    InvalidReportCursorError,
    InvalidRunIdError,
    JsonReportCatalog,
    ReportCatalogConflictError,
    ReportCatalogUnavailableError,
    ReportCorruptError,
)
from datespot_agent.reporting.json_store import JsonReportStore


UTC = timezone.utc
DAY_1 = datetime(2026, 7, 14, 1, 0, tzinfo=UTC)
DAY_2 = datetime(2026, 7, 15, 1, 0, tzinfo=UTC)


def make_report(
    run_id: str,
    *,
    created_at: datetime = DAY_2,
    status: RunStatus = RunStatus.COMPLETED,
    location: str = "성수역",
    keyword: str = "일식",
    result_count: int = 0,
    errors: list[str] | None = None,
) -> RunReport:
    results = [
        PlaceResult(
            status=PlaceResultStatus.ANALYZED,
            place_id=f"place_{index}",
            name=f"장소 {index}",
            final_score=8.0,
        )
        for index in range(result_count)
    ]
    return RunReport(
        run_id=run_id,
        status=status,
        config=RunConfig(location=location, search_keyword=keyword),
        results=results,
        errors=errors or [],
        created_at=created_at,
    )


def write_report(root: Path, report: RunReport) -> Path:
    return JsonReportStore(root).save(report)


class ReportQueryTests(unittest.TestCase):
    def test_normalizes_text_filters_and_rejects_empty_values(self):
        query = ReportQuery(location="  성수  ", search_keyword="  일식 ")
        self.assertEqual(query.location, "성수")
        self.assertEqual(query.search_keyword, "일식")

        for field in ("location", "search_keyword"):
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    ReportQuery(**{field: "   "})

    def test_rejects_inverted_date_range(self):
        with self.assertRaises(ValidationError):
            ReportQuery(
                date_from=date(2026, 7, 16),
                date_to=date(2026, 7, 15),
            )


class JsonReportCatalogTests(unittest.TestCase):
    def test_missing_root_returns_empty_page_and_missing_detail(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = JsonReportCatalog(Path(directory) / "missing")

            page = catalog.list_reports(ReportQuery())

            self.assertEqual(page.items, [])
            self.assertIsNone(page.next_cursor)
            self.assertEqual(page.invalid_report_count, 0)
            self.assertIsNone(catalog.get_report("run_missing"))

    def test_lists_newest_first_and_builds_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(root, make_report("run_old", created_at=DAY_1))
            write_report(
                root,
                make_report(
                    "run_new",
                    result_count=2,
                    errors=["one"],
                ),
            )

            page = JsonReportCatalog(root).list_reports(ReportQuery())

            self.assertEqual(
                [item.run_id for item in page.items],
                ["run_new", "run_old"],
            )
            summary = page.items[0]
            self.assertEqual(summary.result_count, 2)
            self.assertEqual(summary.error_count, 1)
            self.assertEqual(summary.report_url, "/reports/run_new")
            self.assertIsNot(summary.config, page.items[1].config)

    def test_same_timestamp_is_sorted_by_run_id_descending(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for run_id in ("run_a", "run_c", "run_b"):
                write_report(root, make_report(run_id))

            page = JsonReportCatalog(root).list_reports(ReportQuery())

            self.assertEqual(
                [item.run_id for item in page.items],
                ["run_c", "run_b", "run_a"],
            )

    def test_filters_case_insensitive_substrings_and_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(
                root,
                make_report(
                    "run_match",
                    location="SeONGsu Station",
                    keyword="Japanese Dining",
                    status=RunStatus.FAILED,
                ),
            )
            write_report(root, make_report("run_other", location="홍대"))

            page = JsonReportCatalog(root).list_reports(
                ReportQuery(
                    status=RunStatus.FAILED,
                    location="ongsu",
                    search_keyword="DINI",
                )
            )

            self.assertEqual([item.run_id for item in page.items], ["run_match"])

    def test_date_range_is_utc_and_inclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(root, make_report("run_day_1", created_at=DAY_1))
            write_report(root, make_report("run_day_2", created_at=DAY_2))
            write_report(
                root,
                make_report(
                    "run_day_3",
                    created_at=datetime(2026, 7, 16, 0, 0, tzinfo=UTC),
                ),
            )

            page = JsonReportCatalog(root).list_reports(
                ReportQuery(
                    date_from=date(2026, 7, 14),
                    date_to=date(2026, 7, 15),
                )
            )

            self.assertEqual(
                [item.run_id for item in page.items],
                ["run_day_2", "run_day_1"],
            )

    def test_cursor_paginates_without_duplicates_and_allows_limit_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(5):
                write_report(
                    root,
                    make_report(
                        f"run_{index}",
                        created_at=DAY_1 + timedelta(hours=index),
                    ),
                )
            catalog = JsonReportCatalog(root)

            first = catalog.list_reports(ReportQuery(limit=2))
            second = catalog.list_reports(
                ReportQuery(limit=1, cursor=first.next_cursor)
            )
            third = catalog.list_reports(
                ReportQuery(limit=10, cursor=second.next_cursor)
            )

            ids = [
                *(item.run_id for item in first.items),
                *(item.run_id for item in second.items),
                *(item.run_id for item in third.items),
            ]
            self.assertEqual(ids, ["run_4", "run_3", "run_2", "run_1", "run_0"])
            self.assertIsNone(third.next_cursor)

    def test_cursor_from_other_filters_and_tampering_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(root, make_report("run_a", created_at=DAY_1))
            write_report(root, make_report("run_b", created_at=DAY_2))
            catalog = JsonReportCatalog(root)
            cursor = catalog.list_reports(
                ReportQuery(location="성수", limit=1)
            ).next_cursor
            self.assertIsNotNone(cursor)

            with self.assertRaises(InvalidReportCursorError):
                catalog.list_reports(
                    ReportQuery(location="홍대", cursor=cursor)
                )

            tampered = cursor[:-1] + ("A" if cursor[-1] != "A" else "B")
            with self.assertRaises(InvalidReportCursorError):
                catalog.list_reports(
                    ReportQuery(location="성수", cursor=tampered)
                )

    def test_invalid_reports_count_before_content_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(root, make_report("run_other", location="홍대"))
            day_dir = root / "2026" / "07" / "15"
            (day_dir / "broken.json").write_text("{", encoding="utf-8")
            mismatch = make_report("run_mismatch", created_at=DAY_1)
            (day_dir / "mismatch.json").write_text(
                mismatch.model_dump_json(by_alias=True),
                encoding="utf-8",
            )

            page = JsonReportCatalog(root).list_reports(
                ReportQuery(location="성수", date_from=date(2026, 7, 15))
            )

            self.assertEqual(page.items, [])
            self.assertEqual(page.invalid_report_count, 2)

    def test_ignores_non_numeric_paths_dot_files_and_tmp_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ignored = root / "year" / "07" / "15"
            ignored.mkdir(parents=True)
            (ignored / "bad.json").write_text("{", encoding="utf-8")
            day_dir = root / "2026" / "07" / "15"
            day_dir.mkdir(parents=True)
            (day_dir / ".hidden.json").write_text("{", encoding="utf-8")
            (day_dir / "unfinished.tmp").write_text("{", encoding="utf-8")

            page = JsonReportCatalog(root).list_reports(ReportQuery())

            self.assertEqual(page.invalid_report_count, 0)

    def test_duplicate_run_ids_are_excluded_and_detail_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(root, make_report("run_dup", created_at=DAY_1))
            write_report(root, make_report("run_dup", created_at=DAY_2))
            catalog = JsonReportCatalog(root)

            page = catalog.list_reports(ReportQuery())

            self.assertEqual(page.items, [])
            self.assertEqual(page.invalid_report_count, 0)
            with self.assertRaises(ReportCatalogConflictError):
                catalog.get_report("run_dup")

    def test_detail_returns_deep_copy_and_rejects_unsafe_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = make_report("run_one")
            write_report(root, expected)
            catalog = JsonReportCatalog(root)

            actual = catalog.get_report("run_one")

            self.assertEqual(actual, expected)
            self.assertIsNot(actual, expected)
            for run_id in ("../escape", "run/path", "run id", ""):
                with self.subTest(run_id=run_id):
                    with self.assertRaises(InvalidRunIdError):
                        catalog.get_report(run_id)

    def test_detail_reports_corrupt_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day_dir = root / "2026" / "07" / "15"
            day_dir.mkdir(parents=True)
            (day_dir / "run_corrupt.json").write_text("{", encoding="utf-8")

            with self.assertRaises(ReportCorruptError):
                JsonReportCatalog(root).get_report("run_corrupt")

    def test_filesystem_failure_is_catalog_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_report(root, make_report("run_one"))
            catalog = JsonReportCatalog(root)

            with patch.object(Path, "read_text", side_effect=OSError("secret path")):
                with self.assertRaises(ReportCatalogUnavailableError) as caught:
                    catalog.list_reports(ReportQuery())

            self.assertNotIn("secret path", str(caught.exception))
            self.assertNotIn(str(root), str(caught.exception))


if __name__ == "__main__":
    unittest.main()
