# /** LangGraph 실행 루프 수동 실행 스크립트. */

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from dotenv import dotenv_values
from openai import AsyncOpenAI

from datespot_agent.analysis import (
    PhotoAnalysisAgent,
    PlaceScoringService,
    ReviewAnalysisAgent,
)
from datespot_agent.browser import BrowserService, ChromeCdpLauncher
from datespot_agent.config import get_settings
from datespot_agent.graph import GraphRunService
from datespot_agent.models import RunConfig


# /** 수동 실행용 상수 설정값. */
LOCATION = "신사역"
SEARCH_KEYWORD = "일식"
MAX_PLACES = 1
PHOTO_PERCENT = 50
REVIEW_PERCENT = 50
PHOTO_CRITERIA = "어두운 분위기, 좌석 간격이 넓고 대화하기 좋은 구조"
REVIEW_CRITERIA = "음식이 맛있음, 대화하기 좋음이 드러나는 리뷰"
MODEL_OVERRIDE: str | None = None
PROJECT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
BROWSER_USER_DATA_DIR = (
    Path.home() / ".cache" / "datespot-agent" / "chrome-profile"
)
CHROME_EXECUTABLE_PATH = Path(
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
)
HEADED = True
OUTPUT_PATH: Path | None = None


# /** 상수 설정값으로 RunConfig를 만듦. */
def build_run_config() -> RunConfig:
    return RunConfig(
        location=LOCATION,
        search_keyword=SEARCH_KEYWORD,
        max_places=MAX_PLACES,
        weights={
            "photoPercent": PHOTO_PERCENT,
            "reviewPercent": REVIEW_PERCENT,
        },
        scoring={
            "photo": PHOTO_CRITERIA,
            "review": REVIEW_CRITERIA,
        },
    )


# /** 결과 JSON을 stdout 또는 파일로 기록함. */
def write_report(payload: str, output_path: Path | None) -> None:
    if output_path is None:
        print(payload)
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(payload + "\n", encoding="utf-8")
    print(output_path)


# /** 진행 로그를 stderr로 출력함. */
def log_line(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


# /** 수동 통합 실행은 프로젝트 .env 키를 우선 사용함. */
def resolve_live_api_key(
    configured_key: str,
    *,
    env_path: Path = PROJECT_ENV_PATH,
) -> str:
    dotenv_key = dotenv_values(env_path).get("OPENAI_API_KEY")
    if isinstance(dotenv_key, str) and dotenv_key.strip():
        return dotenv_key.strip()
    return configured_key.strip()


# /** 라이브 실행용 외부 Chrome CDP 서비스를 만듦. */
def build_browser_service(default_headless: bool) -> BrowserService:
    return BrowserService(
        headless=False if HEADED else default_headless,
        cdp_launcher=ChromeCdpLauncher(
            executable_path=CHROME_EXECUTABLE_PATH,
            user_data_dir=BROWSER_USER_DATA_DIR,
        ),
        log=log_line,
    )


# /** 실제 graph 실행을 수행함. */
async def run() -> int:
    settings = get_settings()
    model = MODEL_OVERRIDE or settings.model
    api_key = resolve_live_api_key(settings.openai_api_key)
    if not api_key:
        print("OPENAI_API_KEY가 비어 있음", file=sys.stderr)
        return 1

    client = AsyncOpenAI(api_key=api_key)
    runner = GraphRunService(
        browser_service=build_browser_service(settings.headless),
        photo_agent=PhotoAnalysisAgent(client, model=model),
        review_agent=ReviewAnalysisAgent(client, model=model),
        scoring_service=PlaceScoringService(),
        log=log_line,
    )
    report = await runner.run(build_run_config())
    payload = json.dumps(
        report.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        indent=2,
    )
    write_report(payload, OUTPUT_PATH)
    return 0 if report.status.value == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
