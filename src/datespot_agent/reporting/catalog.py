"""날짜별 JSON 실행 리포트의 읽기 전용 카탈로그."""

from __future__ import annotations

import base64
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import stat
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from datespot_agent.models import (
    CamelModel,
    RunConfig,
    RunReport,
    RunStatus,
)
from datespot_agent.reporting.errors import (
    InvalidReportCursorError,
    InvalidRunIdError,
    ReportCatalogConflictError,
    ReportCatalogUnavailableError,
    ReportCorruptError,
)


_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_YEAR = re.compile(r"^[0-9]{4}$")
_MONTH_OR_DAY = re.compile(r"^[0-9]{2}$")
_CURSOR_TEXT = re.compile(r"^[A-Za-z0-9_-]+$")
_CURSOR_VERSION = 1


class ReportQuery(CamelModel):
    """저장 리포트 목록의 검증·정규화된 조회 조건."""

    limit: int = Field(default=20, ge=1, le=100)
    status: Literal[RunStatus.COMPLETED, RunStatus.FAILED] | None = None
    location: str | None = None
    search_keyword: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    cursor: str | None = None

    @field_validator("location", "search_keyword", mode="before")
    @classmethod
    def normalize_text_filter(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("빈 문자열 필터는 사용할 수 없음")
        return normalized

    @model_validator(mode="after")
    def validate_date_range(self) -> "ReportQuery":
        if (
            self.date_from is not None
            and self.date_to is not None
            and self.date_from > self.date_to
        ):
            raise ValueError("dateFrom은 dateTo 이후일 수 없음")
        return self


class ReportSummary(CamelModel):
    run_id: str
    status: RunStatus
    config: RunConfig
    created_at: datetime
    result_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    report_url: str


class ReportPage(CamelModel):
    items: list[ReportSummary] = Field(default_factory=list)
    next_cursor: str | None = None
    invalid_report_count: int = Field(default=0, ge=0)


@dataclass(frozen=True, slots=True)
class _ReportEntry:
    report: RunReport
    path: Path

    @property
    def key(self) -> tuple[datetime, str]:
        return (self.report.created_at, self.report.run_id)


@dataclass(frozen=True, slots=True)
class _ScanResult:
    entries: tuple[_ReportEntry, ...]
    invalid_stems: tuple[str, ...]


class JsonReportCatalog:
    """매 요청마다 JSON 파일을 다시 읽어 검증하는 O(N) 카탈로그."""

    def __init__(self, root: Path = Path("reports")) -> None:
        self.root = Path(root)

    def list_reports(self, query: ReportQuery) -> ReportPage:
        fingerprint = self._filter_fingerprint(query)
        cursor_key = (
            self._decode_cursor(query.cursor, fingerprint)
            if query.cursor is not None
            else None
        )
        scanned = self._scan(query.date_from, query.date_to)
        groups: dict[str, list[_ReportEntry]] = defaultdict(list)
        for entry in scanned.entries:
            groups[entry.report.run_id].append(entry)

        invalid_count = len(scanned.invalid_stems)
        invalid_stem_counts: dict[str, int] = defaultdict(int)
        for stem in scanned.invalid_stems:
            invalid_stem_counts[stem] += 1

        valid: list[_ReportEntry] = []
        for run_id, entries in groups.items():
            conflicting_invalid = invalid_stem_counts.get(run_id, 0)
            if len(entries) > 1 or conflicting_invalid:
                continue
            valid.append(entries[0])

        filtered = [entry for entry in valid if self._matches(entry, query)]
        filtered.sort(key=lambda entry: entry.key, reverse=True)
        if cursor_key is not None:
            filtered = [entry for entry in filtered if entry.key < cursor_key]

        page_entries = filtered[: query.limit]
        next_cursor = None
        if len(filtered) > query.limit:
            next_cursor = self._encode_cursor(
                page_entries[-1].key,
                fingerprint,
            )

        return ReportPage(
            items=[self._summary(entry.report) for entry in page_entries],
            next_cursor=next_cursor,
            invalid_report_count=invalid_count,
        )

    def get_report(self, run_id: str) -> RunReport | None:
        normalized = run_id.strip() if isinstance(run_id, str) else ""
        if normalized != run_id or _SAFE_RUN_ID.fullmatch(normalized) is None:
            raise InvalidRunIdError()

        scanned = self._scan(None, None)
        matches = [
            entry
            for entry in scanned.entries
            if entry.report.run_id == normalized
        ]
        corrupt_targets = sum(
            stem == normalized for stem in scanned.invalid_stems
        )
        if len(matches) + corrupt_targets > 1:
            raise ReportCatalogConflictError()
        if corrupt_targets:
            raise ReportCorruptError()
        if not matches:
            return None
        return matches[0].report.model_copy(deep=True)

    def _scan(
        self,
        date_from: date | None,
        date_to: date | None,
    ) -> _ScanResult:
        try:
            try:
                root_stat = self.root.stat()
            except FileNotFoundError:
                return _ScanResult((), ())
            if not stat.S_ISDIR(root_stat.st_mode):
                raise OSError("reports root is not a directory")

            entries: list[_ReportEntry] = []
            invalid_stems: list[str] = []
            for report_date, path in self._candidate_files(date_from, date_to):
                try:
                    report = RunReport.model_validate_json(
                        path.read_text(encoding="utf-8")
                    )
                except (ValidationError, UnicodeError):
                    invalid_stems.append(path.stem)
                    continue
                if (
                    report.created_at.astimezone(timezone.utc).date()
                    != report_date
                    or _SAFE_RUN_ID.fullmatch(report.run_id) is None
                ):
                    invalid_stems.append(path.stem)
                    continue
                entries.append(_ReportEntry(report=report, path=path))
            return _ScanResult(tuple(entries), tuple(invalid_stems))
        except ReportCatalogUnavailableError:
            raise
        except OSError as error:
            raise ReportCatalogUnavailableError() from error

    def _candidate_files(
        self,
        date_from: date | None,
        date_to: date | None,
    ):
        for year_path in self.root.iterdir():
            if not self._date_directory(year_path, _YEAR):
                continue
            for month_path in year_path.iterdir():
                if not self._date_directory(month_path, _MONTH_OR_DAY):
                    continue
                for day_path in month_path.iterdir():
                    if not self._date_directory(day_path, _MONTH_OR_DAY):
                        continue
                    try:
                        report_date = date(
                            int(year_path.name),
                            int(month_path.name),
                            int(day_path.name),
                        )
                    except ValueError:
                        continue
                    if date_from is not None and report_date < date_from:
                        continue
                    if date_to is not None and report_date > date_to:
                        continue
                    for path in day_path.iterdir():
                        if (
                            path.name.startswith(".")
                            or path.suffix != ".json"
                            or not stat.S_ISREG(
                                path.stat(follow_symlinks=False).st_mode
                            )
                        ):
                            continue
                        yield report_date, path

    @staticmethod
    def _date_directory(path: Path, pattern: re.Pattern[str]) -> bool:
        return (
            pattern.fullmatch(path.name) is not None
            and stat.S_ISDIR(path.stat(follow_symlinks=False).st_mode)
        )

    @staticmethod
    def _matches(entry: _ReportEntry, query: ReportQuery) -> bool:
        report = entry.report
        if query.status is not None and report.status != query.status:
            return False
        if (
            query.location is not None
            and query.location.casefold() not in report.config.location.casefold()
        ):
            return False
        return not (
            query.search_keyword is not None
            and query.search_keyword.casefold()
            not in report.config.search_keyword.casefold()
        )

    @staticmethod
    def _summary(report: RunReport) -> ReportSummary:
        return ReportSummary(
            run_id=report.run_id,
            status=report.status,
            config=report.config.model_copy(deep=True),
            created_at=report.created_at,
            result_count=len(report.results),
            error_count=len(report.errors),
            report_url=f"/reports/{report.run_id}",
        )

    @classmethod
    def _filter_fingerprint(cls, query: ReportQuery) -> str:
        status_value = (
            query.status.value if query.status is not None else None
        )
        filters = {
            "dateFrom": query.date_from.isoformat()
            if query.date_from is not None
            else None,
            "dateTo": query.date_to.isoformat()
            if query.date_to is not None
            else None,
            "location": query.location.casefold()
            if query.location is not None
            else None,
            "searchKeyword": query.search_keyword.casefold()
            if query.search_keyword is not None
            else None,
            "status": status_value,
        }
        return hashlib.sha256(cls._canonical_json(filters)).hexdigest()

    @classmethod
    def _encode_cursor(
        cls,
        key: tuple[datetime, str],
        fingerprint: str,
    ) -> str:
        occurred_at, run_id = key
        core: dict[str, Any] = {
            "filter": fingerprint,
            "last": {
                "createdAt": occurred_at.astimezone(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "runId": run_id,
            },
            "version": _CURSOR_VERSION,
        }
        payload = {
            **core,
            "checksum": hashlib.sha256(cls._canonical_json(core)).hexdigest(),
        }
        return base64.urlsafe_b64encode(cls._canonical_json(payload)).decode(
            "ascii"
        ).rstrip("=")

    @classmethod
    def _decode_cursor(
        cls,
        cursor: str,
        fingerprint: str,
    ) -> tuple[datetime, str]:
        try:
            if not cursor or _CURSOR_TEXT.fullmatch(cursor) is None:
                raise ValueError("invalid cursor text")
            padding = "=" * (-len(cursor) % 4)
            raw = base64.b64decode(
                cursor + padding,
                altchars=b"-_",
                validate=True,
            )
            if (
                base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
                != cursor
            ):
                raise ValueError("non-canonical cursor encoding")
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict) or set(payload) != {
                "checksum",
                "filter",
                "last",
                "version",
            }:
                raise ValueError("invalid cursor shape")
            if payload["version"] != _CURSOR_VERSION:
                raise ValueError("unsupported cursor version")
            if payload["filter"] != fingerprint:
                raise ValueError("cursor filter mismatch")
            last = payload["last"]
            if not isinstance(last, dict) or set(last) != {
                "createdAt",
                "runId",
            }:
                raise ValueError("invalid last key")
            run_id = last["runId"]
            if not isinstance(run_id, str) or _SAFE_RUN_ID.fullmatch(run_id) is None:
                raise ValueError("invalid cursor run id")
            created_text = last["createdAt"]
            if not isinstance(created_text, str):
                raise ValueError("invalid cursor timestamp")
            created_at = datetime.fromisoformat(
                created_text.replace("Z", "+00:00")
            )
            if created_at.tzinfo is None or created_at.utcoffset() is None:
                raise ValueError("naive cursor timestamp")
            core = {
                "filter": payload["filter"],
                "last": last,
                "version": payload["version"],
            }
            expected = hashlib.sha256(cls._canonical_json(core)).hexdigest()
            checksum = payload["checksum"]
            if not isinstance(checksum, str) or not hmac.compare_digest(
                checksum,
                expected,
            ):
                raise ValueError("cursor checksum mismatch")
            return created_at.astimezone(timezone.utc), run_id
        except (
            UnicodeError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as error:
            raise InvalidReportCursorError() from error

    @staticmethod
    def _canonical_json(value: object) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
