from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from datespot_agent.models import RunConfig, RunReport, RunStatus
from datespot_agent.reporting import JsonReportStore, ReportStorageError


def make_report(
    *,
    run_id: str = "run_20260715_sample",
    location: str = "서울",
    created_at: datetime | None = None,
) -> RunReport:
    return RunReport(
        run_id=run_id,
        status=RunStatus.COMPLETED,
        config=RunConfig(
            location=location,
            search_keyword="일식",
            max_places=1,
        ),
        created_at=created_at
        or datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc),
    )


class JsonReportStoreTests(unittest.TestCase):
    def test_save_uses_created_at_utc_date(self):
        report = make_report(
            created_at=datetime(
                2026,
                7,
                15,
                1,
                30,
                tzinfo=timezone(timedelta(hours=9)),
            )
        )

        with tempfile.TemporaryDirectory() as directory:
            path = JsonReportStore(Path(directory)).save(report)

            self.assertEqual(
                path,
                Path(directory)
                / "2026"
                / "07"
                / "14"
                / "run_20260715_sample.json",
            )

    def test_save_writes_canonical_readable_json(self):
        report = make_report()

        with tempfile.TemporaryDirectory() as directory:
            path = JsonReportStore(Path(directory)).save(report)
            payload = path.read_text(encoding="utf-8")

            self.assertIn('  "runId": "run_20260715_sample"', payload)
            self.assertIn('    "searchKeyword": "일식"', payload)
            self.assertIn("서울", payload)
            self.assertNotIn(r"\uc11c\uc6b8", payload)
            self.assertTrue(payload.endswith("\n"))
            self.assertEqual(RunReport.model_validate_json(payload), report)

    def test_save_creates_parent_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "nested" / "reports"

            path = JsonReportStore(root).save(make_report())

            self.assertTrue(path.is_file())

    def test_save_rejects_unsafe_run_id(self):
        unsafe_run_ids = ("../escape", "run/path", r"run\path", "run id")

        with tempfile.TemporaryDirectory() as directory:
            store = JsonReportStore(Path(directory))
            for run_id in unsafe_run_ids:
                with self.subTest(run_id=run_id):
                    with self.assertRaises(ReportStorageError):
                        store.save(make_report(run_id=run_id))

    def test_save_is_idempotent_for_identical_report(self):
        report = make_report()

        with tempfile.TemporaryDirectory() as directory:
            store = JsonReportStore(Path(directory))
            first = store.save(report)
            first_bytes = first.read_bytes()

            second = store.save(report)

            self.assertEqual(second, first)
            self.assertEqual(second.read_bytes(), first_bytes)

    def test_save_rejects_collision_without_overwriting_existing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonReportStore(Path(directory))
            path = store.save(make_report(location="서울"))
            existing_bytes = path.read_bytes()

            with self.assertRaises(ReportStorageError):
                store.save(make_report(location="성수"))

            self.assertEqual(path.read_bytes(), existing_bytes)

    def test_save_wraps_filesystem_error_and_preserves_cause(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "not-a-directory"
            root.write_text("occupied", encoding="utf-8")

            with self.assertRaises(ReportStorageError) as caught:
                JsonReportStore(root).save(make_report())

            self.assertIsInstance(caught.exception.__cause__, OSError)

    def test_save_removes_temporary_file_after_replace_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            with patch(
                "datespot_agent.reporting.json_store.os.replace",
                side_effect=OSError("replace failed"),
            ):
                with self.assertRaises(ReportStorageError):
                    JsonReportStore(root).save(make_report())

            self.assertEqual(list(root.rglob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
