"""장소 분석 계층의 타입이 있는 예외."""


class AnalysisError(Exception):
    """장소 분석 계층의 기반 예외."""


class AnalysisInputError(AnalysisError):
    """분석에 필요한 입력 또는 중간 결과가 없는 경우."""


class AnalysisRequestError(AnalysisError):
    """외부 분석 모델 요청이 실패한 경우."""


class AnalysisResponseError(AnalysisError):
    """외부 분석 모델의 구조화 응답을 사용할 수 없는 경우."""
