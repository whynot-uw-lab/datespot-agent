"""API 서버의 실제 실행 의존성 조립과 lifecycle 관리."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

from datespot_agent.analysis import (
    PhotoAnalysisAgent,
    PlaceScoringService,
    ReviewAnalysisAgent,
)
from datespot_agent.api.coordinator import RunCoordinator
from datespot_agent.api.events import RunEventHub, RunEventPublisher
from datespot_agent.browser import (
    BrowserService,
    CdpStreamManager,
    ChromeCdpLauncher,
)
from datespot_agent.config import (
    Settings,
    get_settings,
    resolve_project_openai_api_key,
)
from datespot_agent.graph import GraphRunService
from datespot_agent.observability import RunLogManager, log_event
from datespot_agent.reporting import JsonReportCatalog, JsonReportStore


logger = logging.getLogger(__name__)


def _trace(event: str, component: str):
    return lambda message: log_event(
        logger,
        event,
        message,
        component=component,
    )


class RuntimeConfigurationError(RuntimeError):
    """API runtime 시작에 필요한 설정이 잘못됨."""


@dataclass
class AppRuntime:
    """API 실행 의존성과 lifecycle을 소유함."""

    coordinator: RunCoordinator
    browser_service: BrowserService
    event_hub: RunEventHub
    openai_client: AsyncOpenAI
    stream_manager: CdpStreamManager
    report_catalog: JsonReportCatalog
    run_log_manager: RunLogManager | None = None

    async def start(self) -> None:
        if self.run_log_manager is not None:
            self.run_log_manager.start()
        await self.coordinator.start()

    async def stop(self) -> None:
        first_error: BaseException | None = None
        cleanup_operations = (
            self.coordinator.stop,
            self.stream_manager.close,
            self.browser_service.close_all,
            self.event_hub.close,
            self.openai_client.close,
        )
        for cleanup in cleanup_operations:
            try:
                await cleanup()
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if self.run_log_manager is not None:
            try:
                self.run_log_manager.stop()
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error


async def create_runtime(settings: Settings | None = None) -> AppRuntime:
    """설정을 검증하고 운영용 의존성 graph를 조립함."""
    effective_settings = settings or get_settings()
    api_key = (
        resolve_project_openai_api_key(effective_settings.openai_api_key)
        if settings is None
        else effective_settings.openai_api_key.strip()
    )
    if not api_key:
        raise RuntimeConfigurationError("OPENAI_API_KEY가 비어 있음")

    chrome_path = effective_settings.chrome_executable_path.expanduser()
    if not chrome_path.is_file():
        raise RuntimeConfigurationError(f"Chrome 실행 파일을 찾지 못함: {chrome_path}")

    profile_path = effective_settings.browser_user_data_dir.expanduser()
    reports_root = effective_settings.reports_root.expanduser()
    diagnostic_logs_root = effective_settings.diagnostic_logs_root.expanduser()
    client = AsyncOpenAI(api_key=api_key)
    try:
        event_hub = RunEventHub()
        event_publisher = RunEventPublisher(event_hub)
        stream_manager = CdpStreamManager()
        browser = BrowserService(
            headless=False,
            cdp_launcher=ChromeCdpLauncher(
                executable_path=chrome_path,
                user_data_dir=profile_path,
            ),
            log=_trace("browser.trace", "browser"),
            event_publisher=event_publisher,
            stream_manager=stream_manager,
        )
        runner = GraphRunService(
            browser_service=browser,
            photo_agent=PhotoAnalysisAgent(
                client,
                model=effective_settings.model,
            ),
            review_agent=ReviewAnalysisAgent(
                client,
                model=effective_settings.model,
            ),
            scoring_service=PlaceScoringService(),
            log=_trace("graph.trace", "graph"),
            event_publisher=event_publisher,
        )
        report_store = JsonReportStore(reports_root)
        report_catalog = JsonReportCatalog(reports_root)
        run_log_manager = RunLogManager(diagnostic_logs_root, console=True)
        coordinator = RunCoordinator(
            runner,
            report_store,
            event_publisher=event_publisher,
        )
        return AppRuntime(
            coordinator,
            browser,
            event_hub,
            client,
            stream_manager,
            report_catalog,
            run_log_manager,
        )
    except BaseException:
        try:
            await client.close()
        except BaseException:
            logger.exception("runtime 조립 실패 후 OpenAI client 정리 실패")
        raise
