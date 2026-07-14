# JSON Report Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LangGraph 실행 결과를 UTC 날짜별 JSON 파일로 안전하게 저장하고 라이브 실행 스크립트가 저장 결과와 실패를 명확히 보고하게 함.

**Architecture:** `GraphRunService`는 실행 결과인 `RunReport` 생성만 담당하고, 신규 `JsonReportStore`가 경로 계산·직렬화·원자적 파일 저장을 담당함. 라이브 실행 스크립트가 두 컴포넌트를 조합하며 저장 실패를 별도 종료 코드로 변환함.

**Tech Stack:** Python 3.13, Pydantic v2, standard library (`json`, `os`, `pathlib`, `re`, `tempfile`), `unittest`, `uv`

## Global Constraints

- `GraphRunService`와 `RunReport.status` 의미 변경 금지
- 저장 경로: `reports/YYYY/MM/DD/<run_id>.json`, `created_at`의 UTC 날짜 기준
- JSON 형식: camelCase, UTF-8, 한글 비이스케이프, 2칸 들여쓰기, 마지막 개행
- 동일 `run_id`와 동일 바이트 재저장 허용, 다른 내용 덮어쓰기 금지
- 임시 파일은 대상 디렉터리에 만들고 `fsync` 후 `os.replace` 적용
- 저장 오류는 `ReportStorageError`로 래핑하고 원인 예외 보존
- 사용자 변경 파일 `blind-date-recommend.iml` 제외

---

## Task 1: Add the reporting package and core JSON save behavior

**Files:**

- Create: `src/datespot_agent/reporting/__init__.py`
- Create: `src/datespot_agent/reporting/errors.py`
- Create: `src/datespot_agent/reporting/json_store.py`
- Create: `tests/test_json_report_store.py`

- [x] **Step 1: Write failing path, format, round-trip, validation, and failure-contract tests**

```python
class JsonReportStoreTest(unittest.TestCase):
    def test_save_uses_created_at_utc_date_and_expected_json_format(self) -> None:
        report = make_report(
            run_id="run_20260715_sample",
            created_at=datetime(2026, 7, 15, 1, 30, tzinfo=timezone(timedelta(hours=9))),
        )

        with tempfile.TemporaryDirectory() as directory:
            path = JsonReportStore(Path(directory)).save(report)
            payload = path.read_text(encoding="utf-8")

            self.assertEqual(
                path,
                Path(directory) / "2026" / "07" / "14" / "run_20260715_sample.json",
            )
            self.assertIn('  "runId": "run_20260715_sample"', payload)
            self.assertIn("서울", payload)
            self.assertNotIn(r"\uc11c\uc6b8", payload)
            self.assertTrue(payload.endswith("\n"))
            self.assertEqual(RunReport.model_validate_json(payload), report)

    def test_save_rejects_unsafe_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ReportStorageError):
                JsonReportStore(Path(directory)).save(make_report(run_id="../escape"))

    def test_save_is_idempotent_for_identical_report(self) -> None:
        report = make_report()
        with tempfile.TemporaryDirectory() as directory:
            store = JsonReportStore(Path(directory))
            first = store.save(report)
            second = store.save(report)
            self.assertEqual(second, first)

    def test_save_rejects_same_run_id_with_different_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JsonReportStore(Path(directory))
            store.save(make_report(location="서울"))
            with self.assertRaises(ReportStorageError):
                store.save(make_report(location="성수"))

    def test_save_wraps_filesystem_error_and_preserves_cause(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "not-a-directory"
            root.write_text("occupied", encoding="utf-8")
            with self.assertRaises(ReportStorageError) as caught:
                JsonReportStore(root).save(make_report())
            self.assertIsInstance(caught.exception.__cause__, OSError)

    def test_save_removes_temporary_file_after_replace_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "datespot_agent.reporting.json_store.os.replace",
                side_effect=OSError("replace failed"),
            ):
                with self.assertRaises(ReportStorageError):
                    JsonReportStore(root).save(make_report())
            self.assertEqual(list(root.rglob("*.tmp")), [])
```

- [x] **Step 2: Run the new tests and confirm RED**

Run: `uv run python -m unittest tests.test_json_report_store -v`

Expected: import failure because `datespot_agent.reporting` does not exist.

- [x] **Step 3: Implement the public error and store interface**

```python
# src/datespot_agent/reporting/errors.py
from pathlib import Path


class ReportStorageError(RuntimeError):
    def __init__(self, message: str, *, run_id: str, path: Path | None = None) -> None:
        self.run_id = run_id
        self.path = path
        context = f"run_id={run_id}"
        if path is not None:
            context += f", path={path}"
        super().__init__(f"{message} ({context})")
```

```python
# src/datespot_agent/reporting/json_store.py
from __future__ import annotations

from datetime import timezone
import json
import os
from pathlib import Path
import re
import tempfile

from datespot_agent.models import RunReport
from datespot_agent.reporting.errors import ReportStorageError


_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class JsonReportStore:
    def __init__(self, root: Path = Path("reports")) -> None:
        self.root = Path(root)

    def save(self, report: RunReport) -> Path:
        target = self._target_path(report)
        payload = self._serialize(report)
        temporary_path: Path | None = None

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                return self._resolve_existing(target, payload, report.run_id)

            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=f".{report.run_id}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())

            if target.exists():
                return self._resolve_existing(target, payload, report.run_id)

            os.replace(temporary_path, target)
            temporary_path = None
            return target
        except ReportStorageError:
            raise
        except (OSError, TypeError, ValueError) as error:
            raise ReportStorageError(
                "JSON 리포트 저장 실패",
                run_id=report.run_id,
                path=target,
            ) from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _target_path(self, report: RunReport) -> Path:
        if _SAFE_RUN_ID.fullmatch(report.run_id) is None:
            raise ReportStorageError(
                "안전하지 않은 run_id",
                run_id=report.run_id,
                path=self.root,
            )
        created_at = report.created_at.astimezone(timezone.utc)
        return (
            self.root
            / f"{created_at.year:04d}"
            / f"{created_at.month:02d}"
            / f"{created_at.day:02d}"
            / f"{report.run_id}.json"
        )

    @staticmethod
    def _serialize(report: RunReport) -> bytes:
        data = report.model_dump(mode="json", by_alias=True)
        return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    @staticmethod
    def _resolve_existing(target: Path, payload: bytes, run_id: str) -> Path:
        if target.read_bytes() == payload:
            return target
        raise ReportStorageError(
            "동일 run_id의 다른 리포트가 이미 존재함",
            run_id=run_id,
            path=target,
        )
```

```python
# src/datespot_agent/reporting/__init__.py
from datespot_agent.reporting.errors import ReportStorageError
from datespot_agent.reporting.json_store import JsonReportStore

__all__ = ["JsonReportStore", "ReportStorageError"]
```

- [x] **Step 4: Run focused tests and confirm GREEN**

Run: `uv run python -m unittest tests.test_json_report_store -v`

Expected: all new tests pass.

- [x] **Step 5: Commit the core store**

```bash
git add src/datespot_agent/reporting tests/test_json_report_store.py
git commit -m "feat: add JSON report store"
```

---

## Task 2: Integrate storage into the live graph runner

**Files:**

- Modify: `tests/run_graph_live.py`
- Modify: `tests/test_run_graph_live.py`

- [x] **Step 1: Add failing finalization tests**

```python
class FakeReportStore:
    def __init__(self, result: Path | Exception) -> None:
        self.result = result

    def save(self, report: RunReport) -> Path:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_finalize_report_prints_path_and_returns_status_exit_code(self) -> None:
    module = load_live_runner_module()
    output = io.StringIO()
    with redirect_stdout(output):
        code = module.finalize_report(make_report(), FakeReportStore(Path("saved.json")))
    self.assertEqual(code, 0)
    self.assertEqual(output.getvalue().strip(), "saved.json")

def test_finalize_report_returns_three_on_storage_failure(self) -> None:
    module = load_live_runner_module()
    report = make_report()
    error = ReportStorageError("저장 실패", run_id=report.run_id)
    output = io.StringIO()
    with redirect_stderr(output):
        code = module.finalize_report(report, FakeReportStore(error))
    self.assertEqual(code, 3)
    self.assertEqual(report.status, RunStatus.COMPLETED)
    self.assertIn("리포트 저장 실패", output.getvalue())
```

- [x] **Step 2: Run runner tests and confirm RED**

Run: `uv run python -m unittest tests.test_run_graph_live -v`

Expected: `finalize_report` and report-store integration are missing.

- [x] **Step 3: Replace manual output selection with explicit store finalization**

```python
from datespot_agent.reporting import JsonReportStore, ReportStorageError

REPORTS_ROOT = Path("reports")
REPORT_STORAGE_EXIT_CODE = 3


def finalize_report(report: RunReport, store: JsonReportStore) -> int:
    try:
        path = store.save(report)
    except ReportStorageError as error:
        print(f"리포트 저장 실패: {error}", file=sys.stderr)
        return REPORT_STORAGE_EXIT_CODE

    print(path)
    return 0 if report.status is RunStatus.COMPLETED else 2


async def run(report_store: JsonReportStore | None = None) -> int:
    store = report_store or JsonReportStore(REPORTS_ROOT)
    # Existing browser, API, graph-runner setup remains unchanged.
    report = await runner.run(config)
    return finalize_report(report, store)
```

Delete `OUTPUT_PATH`, `write_report`, and the direct `json.dumps` branch.

- [x] **Step 4: Run runner and store tests**

Run: `uv run python -m unittest tests.test_run_graph_live tests.test_json_report_store -v`

Expected: all focused tests pass.

- [x] **Step 5: Commit live runner integration**

```bash
git add tests/run_graph_live.py tests/test_run_graph_live.py
git commit -m "feat: persist live graph reports"
```

---

## Task 3: Update documentation and verify the complete workflow

**Files:**

- Modify: `README.md`
- Verify: `reports/YYYY/MM/DD/<run_id>.json`

- [x] **Step 1: Update README configuration and roadmap status**

Document:

- `OUTPUT_PATH` replacement with `REPORTS_ROOT`
- automatic UTC date path generation
- stdout saved-path output
- exit codes `0` completed, `2` graph failed, `3` report storage failed
- roadmap 2-7 completion

- [x] **Step 2: Run the full automated test suite**

Run: `uv run python -m unittest discover -s tests -p 'test_*.py'`

Expected: all tests pass with no failures or errors.

- [x] **Step 3: Run one real browser/graph cycle**

Run: `uv run python tests/run_graph_live.py`

Expected: browser cycle completes, command prints a `reports/YYYY/MM/DD/run_*.json` path, exit code is `0`.

- [x] **Step 4: Validate the generated report through the domain model**

```bash
uv run python - <<'PY'
from pathlib import Path
from datespot_agent.models import RunReport

path = max(Path("reports").rglob("run_*.json"), key=lambda item: item.stat().st_mtime)
report = RunReport.model_validate_json(path.read_text(encoding="utf-8"))
print(path)
print(report.run_id, report.status.value, len(report.results))
PY
```

Expected: model validation succeeds and status is `completed`.

- [x] **Step 5: Commit documentation and verification-ready state**

```bash
git add README.md
git commit -m "docs: complete JSON report storage"
```

- [x] **Step 6: Review final scope**

Run: `git status --short && git diff origin/main...HEAD --stat`

Expected: only intended commits are included; `blind-date-recommend.iml` remains unstaged.
