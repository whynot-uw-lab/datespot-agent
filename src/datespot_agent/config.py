"""환경변수 기반 앱 설정과 실행 설정 호환 export."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from datespot_agent.models import Filters, RunConfig, ScoringCriteria, Weights


class Settings(BaseSettings):
    """환경변수 기반 앱 전역 설정."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        extra="ignore",
    )

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    model: str = Field(default="gpt-5.4-nano", alias="DATESPOT_MODEL")
    headless: bool = Field(default=True, alias="DATESPOT_HEADLESS")


@lru_cache
def get_settings() -> Settings:
    """캐시된 Settings 인스턴스."""
    return Settings()


SearchConfig = RunConfig

__all__ = [
    "Filters",
    "RunConfig",
    "ScoringCriteria",
    "SearchConfig",
    "Settings",
    "Weights",
    "get_settings",
]
