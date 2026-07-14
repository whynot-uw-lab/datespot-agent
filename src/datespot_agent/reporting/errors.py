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
