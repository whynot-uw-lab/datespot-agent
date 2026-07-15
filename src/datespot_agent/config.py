"""환경변수 기반 앱 설정과 실행 설정 호환 export."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from datespot_agent.models import RunConfig, ScoringCriteria, Weights


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
    reports_root: Path = Field(
        default=Path("reports"),
        alias="DATESPOT_REPORTS_ROOT",
    )
    diagnostic_logs_root: Path = Field(
        default=Path("artifacts/logs"),
        alias="DATESPOT_DIAGNOSTIC_LOGS_ROOT",
    )
    chrome_executable_path: Path = Field(
        default=Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        alias="DATESPOT_CHROME_EXECUTABLE_PATH",
    )
    browser_user_data_dir: Path = Field(
        default=Path("~/.cache/datespot-agent/chrome-profile"),
        alias="DATESPOT_BROWSER_USER_DATA_DIR",
    )


def resolve_project_openai_api_key(
    configured_key: str,
    *,
    env_path: Path = Path(".env"),
) -> str:
    """프로젝트 .env 키가 있으면 상속된 셸 키보다 우선함."""
    dotenv_key = dotenv_values(env_path).get("OPENAI_API_KEY")
    if isinstance(dotenv_key, str) and dotenv_key.strip():
        return dotenv_key.strip()
    return configured_key.strip()


@lru_cache
def get_settings() -> Settings:
    """캐시된 Settings 인스턴스."""
    return Settings()


SearchConfig = RunConfig

__all__ = [
    "RunConfig",
    "ScoringCriteria",
    "SearchConfig",
    "Settings",
    "Weights",
    "get_settings",
    "resolve_project_openai_api_key",
]
