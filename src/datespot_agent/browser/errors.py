"""BrowserService가 외부에 노출하는 타입 예외."""

from __future__ import annotations


class BrowserServiceError(RuntimeError):
    """브라우저 작업 실패와 실행 컨텍스트를 함께 보존한다."""

    def __init__(
        self,
        message: str,
        *,
        run_id: str | None = None,
        step: str | None = None,
        place_id: str | None = None,
    ) -> None:
        self.run_id = run_id
        self.step = step
        self.place_id = place_id
        context = ", ".join(
            value
            for value in (
                f"run_id={run_id}" if run_id else "",
                f"step={step}" if step else "",
                f"place_id={place_id}" if place_id else "",
            )
            if value
        )
        super().__init__(f"{message} ({context})" if context else message)


class BrowserSessionError(BrowserServiceError):
    """브라우저 세션 수명주기 위반."""


class BrowserNavigationError(BrowserServiceError):
    """네이버지도 UI 탐색 실패."""


class BrowserExtractionError(BrowserServiceError):
    """네이버지도 DOM 데이터 변환 실패."""


class BrowserAccessBlockedError(BrowserServiceError):
    """접근 제한 신호가 감지되어 즉시 중단된 상태."""
