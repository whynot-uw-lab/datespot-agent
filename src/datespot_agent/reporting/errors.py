"""리포트 저장 계층의 공개 예외."""

from __future__ import annotations

from pathlib import Path


class ReportStorageError(RuntimeError):
    """리포트를 안전하게 저장하지 못했을 때 발생한다."""

    def __init__(
        self,
        message: str,
        *,
        run_id: str,
        path: Path | None = None,
    ) -> None:
        self.run_id = run_id
        self.path = path
        context = f"run_id={run_id}"
        if path is not None:
            context += f", path={path}"
        super().__init__(f"{message} ({context})")


class ReportCatalogError(RuntimeError):
    """저장 리포트 조회가 실패했을 때의 공개 가능한 기반 예외."""


class InvalidReportCursorError(ReportCatalogError):
    def __init__(self) -> None:
        super().__init__("리포트 페이지 cursor가 올바르지 않음")


class InvalidRunIdError(ReportCatalogError):
    def __init__(self) -> None:
        super().__init__("run_id가 올바르지 않음")


class ReportCatalogConflictError(ReportCatalogError):
    def __init__(self) -> None:
        super().__init__("동일한 실행의 리포트가 중복됨")


class ReportCorruptError(ReportCatalogError):
    def __init__(self) -> None:
        super().__init__("저장 리포트가 손상됨")


class ReportCatalogUnavailableError(ReportCatalogError):
    def __init__(self) -> None:
        super().__init__("저장 리포트 카탈로그를 사용할 수 없음")
