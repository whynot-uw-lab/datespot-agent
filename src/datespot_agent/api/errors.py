"""실행 coordinator 예외."""


class CoordinatorUnavailableError(RuntimeError):
    """Coordinator가 신규 실행을 접수할 수 없음."""
