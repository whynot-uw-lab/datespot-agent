"""실행 리포트의 원자적 JSON 파일 저장소."""

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
    """`RunReport`를 UTC 날짜별 JSON 파일로 저장한다."""

    def __init__(self, root: Path = Path("reports")) -> None:
        self.root = Path(root)

    def save(self, report: RunReport) -> Path:
        """리포트를 저장하고 생성되었거나 이미 존재하는 경로를 반환한다."""
        target = self._target_path(report)
        temporary_path: Path | None = None

        try:
            payload = self._serialize(report)
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
        return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )

    @staticmethod
    def _resolve_existing(target: Path, payload: bytes, run_id: str) -> Path:
        if target.read_bytes() == payload:
            return target
        raise ReportStorageError(
            "동일 run_id의 다른 리포트가 이미 존재함",
            run_id=run_id,
            path=target,
        )
