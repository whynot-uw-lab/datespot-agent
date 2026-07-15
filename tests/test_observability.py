from __future__ import annotations

import json
import logging
from io import StringIO
import tempfile
import unittest
from pathlib import Path

from datespot_agent.observability import (
    RunLogManager,
    bind_log_context,
    log_event,
)


class RunLogManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.logs_root = Path(self.temporary_directory.name)
        self.manager = RunLogManager(self.logs_root)
        self.manager.start()
        self.logger = logging.getLogger("datespot_agent.tests.observability")

    def tearDown(self) -> None:
        self.manager.stop()
        self.temporary_directory.cleanup()

    def _records(self, run_id: str) -> list[dict[str, object]]:
        path = self.logs_root / f"{run_id}.jsonl"
        return [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        ]

    def test_writes_each_bound_run_to_its_own_jsonl_file(self) -> None:
        for run_id in ("run_first", "run_second"):
            with bind_log_context(run_id=run_id, component="coordinator"):
                log_event(
                    self.logger,
                    "run.started",
                    "실행 시작",
                    status="running",
                )

        first = self._records("run_first")
        second = self._records("run_second")

        self.assertEqual(first[0]["runId"], "run_first")
        self.assertEqual(second[0]["runId"], "run_second")
        self.assertEqual(first[0]["event"], "run.started")
        self.assertEqual(first[0]["component"], "coordinator")
        self.assertEqual(first[0]["status"], "running")

    def test_records_exception_type_message_and_traceback(self) -> None:
        with bind_log_context(run_id="run_failed", component="photo_analysis"):
            try:
                raise RuntimeError("model request failed")
            except RuntimeError:
                log_event(
                    self.logger,
                    "analysis.photo.failed",
                    "사진 분석 실패",
                    level=logging.ERROR,
                    exc_info=True,
                )

        record = self._records("run_failed")[0]

        self.assertEqual(record["errorType"], "RuntimeError")
        self.assertEqual(record["errorMessage"], "model request failed")
        self.assertIn("RuntimeError: model request failed", record["traceback"])

    def test_redacts_secrets_raw_inputs_and_nested_values(self) -> None:
        with bind_log_context(run_id="run_redacted"):
            log_event(
                self.logger,
                "analysis.review.requested",
                "Authorization: Bearer secret-token",
                api_key="sk-secret-value",
                photo_urls=["https://images.example/private.jpg"],
                nested={
                    "cookie": "session=secret-cookie",
                    "reviews": ["원문 리뷰 비밀"],
                    "safe_count": 3,
                },
            )

        serialized = json.dumps(
            self._records("run_redacted")[0],
            ensure_ascii=False,
        )

        self.assertNotIn("secret-token", serialized)
        self.assertNotIn("sk-secret-value", serialized)
        self.assertNotIn("images.example", serialized)
        self.assertNotIn("secret-cookie", serialized)
        self.assertNotIn("원문 리뷰 비밀", serialized)
        self.assertIn("[REDACTED]", serialized)
        self.assertIn('"safeCount": 3', serialized)

    def test_does_not_create_a_file_without_a_valid_run_id(self) -> None:
        log_event(self.logger, "server.started", "서버 시작")
        with bind_log_context(run_id="../escape"):
            log_event(self.logger, "run.started", "잘못된 실행")

        self.assertEqual(list(self.logs_root.glob("*.jsonl")), [])

    def test_safe_console_handler_includes_context_and_redacts_message(self) -> None:
        self.manager.stop()
        stream = StringIO()
        self.manager = RunLogManager(
            self.logs_root,
            console=True,
            console_stream=stream,
        )
        self.manager.start()

        with bind_log_context(run_id="run_console", component="review_analysis"):
            log_event(
                self.logger,
                "analysis.review.requested",
                "Authorization: Bearer console-secret",
            )

        output = stream.getvalue()
        self.assertIn("run_console", output)
        self.assertIn("review_analysis", output)
        self.assertIn("analysis.review.requested", output)
        self.assertNotIn("console-secret", output)

    def test_exception_text_redacts_urls_and_labeled_raw_inputs(self) -> None:
        with bind_log_context(run_id="run_exception_redacted"):
            try:
                raise RuntimeError(
                    "photo=https://images.example/private.jpg, "
                    "리뷰 원문: 조용하고 비밀스러운 후기"
                )
            except RuntimeError:
                log_event(
                    self.logger,
                    "analysis.photo.failed",
                    "분석 실패",
                    level=logging.ERROR,
                    exc_info=True,
                )

        serialized = json.dumps(
            self._records("run_exception_redacted")[0],
            ensure_ascii=False,
        )
        self.assertNotIn("images.example", serialized)
        self.assertNotIn("조용하고 비밀스러운 후기", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_exception_text_redacts_multiline_raw_input_block(self) -> None:
        with bind_log_context(run_id="run_multiline_redacted"):
            try:
                raise RuntimeError(
                    "prompt: 소개팅 장소를 평가해줘\n"
                    "리뷰 목록:\n"
                    "1. 다시 노출되면 안 되는 실제 리뷰\n"
                    "2. 두 번째 실제 리뷰"
                )
            except RuntimeError:
                log_event(
                    self.logger,
                    "analysis.review.failed",
                    "리뷰 분석 실패",
                    level=logging.ERROR,
                    exc_info=True,
                )

        serialized = json.dumps(
            self._records("run_multiline_redacted")[0],
            ensure_ascii=False,
        )
        self.assertNotIn("소개팅 장소를 평가해줘", serialized)
        self.assertNotIn("다시 노출되면 안 되는 실제 리뷰", serialized)
        self.assertNotIn("두 번째 실제 리뷰", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_writer_failure_reports_once_without_raising(self) -> None:
        self.manager.stop()
        blocked_root = self.logs_root / "blocked"
        blocked_root.write_text("not a directory", encoding="utf-8")
        stream = StringIO()
        self.manager = RunLogManager(
            blocked_root,
            console=False,
            console_stream=stream,
        )
        self.manager.start()

        for index in range(2):
            with bind_log_context(run_id="run_write_failed"):
                log_event(
                    self.logger,
                    "run.started",
                    f"실행 시작 {index}",
                )

        self.assertEqual(stream.getvalue().count("observability.write.failed"), 1)


if __name__ == "__main__":
    unittest.main()
